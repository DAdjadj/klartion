"""Diagnostics: short, safe lines about what Klartion did, for support.

note(area, text) always writes a line; detail(area, text) only while that
area's "Detailed log" switch is on (it turns itself off 24 hours after it was
switched on). Each line goes into the log Klartion already keeps (the
container log that Download log reads) and into a small rotating file next to
the database, which the Diagnostics page reads:

    [diag] 2026-11-25T08:00:00Z Sync: Done: 2 accounts, 6 new, 18 s

Lines carry counts, codes and times only. Every line still goes through
redact(), so an IBAN, email, address, key or ID can't slip into one. Nothing
here may ever break a sync: every public function swallows its own errors.
"""
import logging
import logging.handlers
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, db

logger = logging.getLogger("app.diag")

# Detailed log switches: key, title, explanation (from the approved mockup).
SWITCHES = [
    ("bank", "Bank connection", "Each Enable Banking or SimpleFIN request and its answer code"),
    ("notion", "Writing to Notion", "Pages added and updated, as counts"),
    ("email", "Email", ""),
    ("license", "License and updates", ""),
]
# The name each area carries in a log line.
LABELS = {"app": "App", "sync": "Sync", "bank": "Bank", "notion": "Notion",
          "email": "Email", "license": "License", "update": "Update"}
# Which switch turns on the detailed lines of an area.
SWITCH_FOR = {"bank": "bank", "notion": "notion", "email": "email", "license": "license", "update": "license"}
DETAIL_HOURS = 24

_SETTING_ON = "diag_detail_{}"
_SETTING_CLEARED = "diag_cleared_at"
_LINE = re.compile(r"\[diag\] (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) ([A-Za-z]+): (.*)")
_MAX_BYTES = 512 * 1024
_BACKUPS = 2
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_handler_lock = threading.Lock()
_handler = {"path": None, "handler": None}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value):
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def redact_basic(text):
    """The redaction Download log has always applied: IBANs, email addresses and
    Enable Banking account dumps."""
    text = re.sub(r'\b([A-Z]{2}\d{2})\w{4,}(\w{4})\b', r'\1****\2', text)
    text = re.sub(r'\b(\w{2})\w*(@\w+\.\w+)', r'\1***\2', text)
    text = re.sub(r"\[?\{'account_id':.*?\}\]?", '[account data redacted]', text)
    return text


_STRICT = [
    (re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b"), "[email]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I), "[link]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), "[iban]"),
    (re.compile(r"\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b", re.I), "[id]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[address]"),
    # IPv6: needs a hex letter, so a time like 08:00:42 stays.
    (re.compile(r"(?<![\w:])(?=[0-9a-f:]*[a-f])(?=[0-9a-f:]*:[0-9a-f:]*:)[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}(?![\w:])", re.I), "[address]"),
    # Keys and tokens: long runs of letters and digits (secret_..., ntn_..., JWTs, hashes).
    (re.compile(r"\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{20,}\b"), "[key]"),
    # Account and card numbers.
    (re.compile(r"\d(?:[ -]?\d){7,}"), "[number]"),
]


def redact(text):
    """redact_basic plus everything a diagnostics line must never carry:
    email addresses, links, IBANs, IDs, IP addresses, keys and long numbers."""
    text = str(text if text is not None else "")
    for pattern, replacement in _STRICT:
        text = pattern.sub(replacement, text)
    return redact_basic(text).replace("\n", " ").replace("\r", " ")


def bank_label(token_or_name):
    """A bank's name without the account label Klartion adds after ' · '."""
    name = token_or_name.get("bank_name") if isinstance(token_or_name, dict) else token_or_name
    return (name or "Unknown bank").split(" · ")[0].strip() or "Unknown bank"


def error_name(exc):
    """What went wrong, without str(exc) (which can carry URLs and keys):
    the error's kind and its HTTP or SMTP code when it has one."""
    name = type(exc).__name__
    smtp = getattr(exc, "smtp_code", None)
    if smtp:
        return f"{name}, SMTP {smtp}"
    http = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status", None)
    return f"{name}, HTTP {http}" if isinstance(http, int) else name


# ---------------------------------------------------------------------------
# Writing lines
# ---------------------------------------------------------------------------

def log_path():
    return os.environ.get("DIAG_LOG_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(config.DB_PATH)), "klartion-diagnostics.log")


def _file_handler():
    path = log_path()
    with _handler_lock:
        if _handler["path"] != path:
            for old in [h for h in logger.handlers if getattr(h, "klartion_diag", False)]:
                logger.removeHandler(old)
                old.close()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            h = logging.handlers.RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS,
                                                     encoding="utf-8", delay=True)
            h.setFormatter(logging.Formatter("%(message)s"))
            h.klartion_diag = True
            logger.addHandler(h)
            logger.setLevel(logging.INFO)
            _handler["path"], _handler["handler"] = path, h
        return _handler["handler"]


def _write(area, text, error=False):
    label = LABELS.get(area, "App")
    text = redact(text)
    if error:
        label, text = "Error", f"{label}: {text}"
    _file_handler()
    line = f"[diag] {_iso(_now())} {label}: {text}"
    (logger.error if error else logger.info)(line)


def note(area, text, error=False):
    """A main line: always written."""
    try:
        _write(area, text, error)
    except Exception:
        pass


def detail(area, text, error=False):
    """A detailed line: only while that area's switch is on."""
    try:
        if is_on(SWITCH_FOR.get(area, area)):
            _write(area, text, error)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Detailed log switches (kept in the database, so the web pages and the sync
# read the same state, and it survives a restart)
# ---------------------------------------------------------------------------

def on_since(key):
    since = _parse_iso(db.get_setting(_SETTING_ON.format(key)) or "")
    if since and _now() - since < timedelta(hours=DETAIL_HOURS):
        return since
    return None


def is_on(key):
    try:
        return on_since(key) is not None
    except Exception:
        return False


def set_switch(key, on):
    if key not in {k for k, _, _ in SWITCHES}:
        raise ValueError(key)
    db.set_setting(_SETTING_ON.format(key), _iso(_now()) if on else "")
    title = next(t for k, t, _ in SWITCHES if k == key)
    note("app", f"Detailed log for {title} {'on for 24 h' if on else 'off'}")


def switches():
    """The switches with their state, for the page."""
    out = []
    for key, title, sub in SWITCHES:
        since = on_since(key)
        out.append({"key": key, "title": title, "sub": sub, "on": since is not None,
                    "until": display_time(since + timedelta(hours=DETAIL_HOURS)) if since else ""})
    return out


# ---------------------------------------------------------------------------
# Reading lines back for the page
# ---------------------------------------------------------------------------

def clear():
    """Hide the lines written so far. The file itself stays whole for support."""
    db.set_setting(_SETTING_CLEARED, _iso(_now()))


def _user_tz():
    try:
        name = getattr(config, "TIMEZONE", "") or ""
        return ZoneInfo(name) if name else None
    except Exception:
        return None


def _local(dt):
    tz = _user_tz()
    return dt.astimezone(tz) if tz else dt.astimezone()


def display_date(dt):
    """'25 Nov 2026' in the timezone the browser reported."""
    local = _local(dt)
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}"


def display_clock(dt, with_seconds=True):
    """'8:00:42 AM' (12-hour) in the timezone the browser reported."""
    local = _local(dt)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d}" + (f":{local.second:02d}" if with_seconds else "")
    return f"{clock} {'AM' if local.hour < 12 else 'PM'}"


def display_time(dt, with_seconds=True):
    """'25 Nov 2026 8:00:42 AM', day first, 12-hour clock."""
    if dt is None:
        return ""
    return f"{display_date(dt)} {display_clock(dt, with_seconds)}"


def _raw_lines():
    path = log_path()
    lines = []
    for p in [f"{path}.{i}" for i in range(_BACKUPS, 0, -1)] + [path]:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                lines.extend(f.read().splitlines())
        except OSError:
            continue
    return lines


def read(limit=500):
    """Diagnostics lines after the last Clear, newest first."""
    cleared = _parse_iso(db.get_setting(_SETTING_CLEARED) or "")
    out = []
    for raw in _raw_lines():
        m = _LINE.search(raw)
        if not m:
            continue
        when = _parse_iso(m.group(1))
        if when is None or (cleared and when <= cleared):
            continue
        area, text = m.group(2), redact(m.group(3))
        topic = area
        if area == "Error":
            topic = text.split(":", 1)[0].strip()
        out.append({"time": display_time(when), "area": area, "topic": topic, "text": text,
                    "error": area == "Error"})
    out.reverse()
    return out[:limit]


def export_text():
    """The whole diagnostics file (not just what Clear left), redacted again."""
    out = []
    for raw in _raw_lines():
        m = _LINE.search(raw)
        if m:
            out.append(f"[diag] {m.group(1)} {m.group(2)}: {redact(m.group(3))}")
    return "\n".join(out)
