import datetime
import smtplib
import logging
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from . import config, email_design as design

logger = logging.getLogger(__name__)

_unsubscribed_cache = {}

def _is_unsubscribed(email: str) -> bool:
    import time
    now = time.time()
    if email in _unsubscribed_cache and now - _unsubscribed_cache[email][1] < 3600:
        return _unsubscribed_cache[email][0]
    try:
        resp = requests.post("https://api.klartion.com/is-unsubscribed", json={"email": email}, timeout=5)
        result = resp.ok and resp.json().get("unsubscribed", False)
    except Exception:
        result = False
    _unsubscribed_cache[email] = (result, now)
    return result


def _smtp_host_for(email: str) -> str:
    domain = email.split("@")[-1].lower() if "@" in email else ""
    mapping = {
        "gmail.com":      "smtp.gmail.com",
        "googlemail.com": "smtp.gmail.com",
        "icloud.com":     "smtp.mail.me.com",
        "me.com":         "smtp.mail.me.com",
        "mac.com":        "smtp.mail.me.com",
        "outlook.com":    "smtp.office365.com",
        "hotmail.com":    "smtp.office365.com",
        "live.com":       "smtp.office365.com",
        "yahoo.com":      "smtp.mail.yahoo.com",
    }
    return mapping.get(domain, config.SMTP_HOST or "smtp.gmail.com")


def build_message(subject: str, body: str, html: str = None, sender: str = "", to: str = ""):
    """The email itself: plain text, plus HTML for mail apps that show it."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if html is not None:
        msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _smtp_host() -> str:
    return config.SMTP_HOST if (config.SMTP_HOST and config.SMTP_HOST != "smtp.mail.me.com") else _smtp_host_for(config.SMTP_USER)


def send(subject: str, body: str, html: str = None):
    if not config.SMTP_USER or not config.NOTIFY_EMAIL:
        logger.warning("Email not configured, skipping notification.")
        return
    if _is_unsubscribed(config.NOTIFY_EMAIL):
        logger.info("Skipping email for unsubscribed %s: %s", config.NOTIFY_EMAIL, subject)
        return

    sender = config.SMTP_FROM or config.SMTP_USER
    msg = build_message(subject, body, html, sender, config.NOTIFY_EMAIL)

    try:
        host = _smtp_host()
        port = int(config.SMTP_PORT or 587)
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(sender, config.NOTIFY_EMAIL, msg.as_string())
        logger.info("Email sent: %s", subject)
    except Exception as e:
        logger.error("Failed to send email: %s", e)




# ═══════════════════════════════════════════════════════════════════
# The alerts
#
# Each alert is described once as content and email_design renders it
# twice: HTML as a Notion page in Klartion's look, and the plain-text part.
# build_* returns (subject, content) so every alert can be rendered and
# checked without sending anything.
# ═══════════════════════════════════════════════════════════════════

SUPPORT = "support@david-alves.com"

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _now() -> datetime.datetime:
    """Now in the time zone set on the Sync page (UTC when none is set)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(config.TIMEZONE or "UTC"))
    except Exception:
        return datetime.datetime.now(datetime.timezone.utc)


def _day_long(d) -> str:   # "Thursday 29 October"
    return f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def _day_short(d) -> str:  # "29 Oct"
    return f"{d.day} {_MONTHS[d.month - 1][:3]}"


def _stamp(dt) -> str:     # "26 Oct 2026, 7:00 AM"
    return f"{_day_short(dt)} {dt.year}, {dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _app_url(path: str = "") -> str:
    base = (config.KLARTION_URL or "").strip().rstrip("/")
    return base + path if base else ""


def _page_link(label: str, path: str) -> str:
    url = _app_url(path)
    return f"[{label}]({url})" if url else label


def _notion_url() -> str:
    db_id = (config.NOTION_DATABASE_ID or "").replace("-", "").strip()
    return f"https://www.notion.so/{db_id}" if db_id else ""


def _reason(kind: str) -> str:
    notifications = _page_link("Notifications page", "/setup/notifications")
    if kind == "every-sync":
        return f"Sent by your Klartion after every sync. To hear only about problems, choose Errors only on its {notifications}."
    if kind == "problem":
        return f"Sent by your Klartion when a sync has a problem. You can change this on its {notifications}."
    return f"Sent by your Klartion to the address on its {notifications}."


def _split(line: str):
    """'ING (NL): 1,234.56 EUR' into ('ING (NL)', '1,234.56 EUR')."""
    line = (line or "").strip()
    idx = line.find(": ")
    if idx <= 0:
        return "", line
    return line[:idx].strip(), line[idx + 2:].strip()


def _sentence(s: str) -> str:
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s


def _problems(errors: list) -> list:
    out = []
    for line in errors or []:
        name, rest = _split(line)
        out.append({"name": name, "detail": _sentence(rest)})
    return out


def _balances(balance_lines: list):
    rows = []
    for line in balance_lines or []:
        label, value = line.rsplit(": ", 1) if ": " in line else (line, "")
        rows.append((label.strip(), value.strip()))
    return {"title": "Balances", "column": "Balance", "rows": rows} if rows else None


def _open_buttons(with_notion: bool) -> list:
    buttons = [{"label": "Open Klartion", "href": _app_url()}]
    if with_notion:
        buttons.append({"label": "Open in Notion", "href": _notion_url()})
    return buttons


_TRY_STEPS = [
    "Open the Status page in Klartion. Its sync log shows what happened.",
    "If your bank asks to be re-authorized, click Re-authorize on the Bank page.",
    f"Still stuck? Write to [{SUPPORT}](mailto:{SUPPORT}) and say what the sync log shows.",
]


def build_success(tx_count: int, balance_lines: list = None):
    now = _now()
    if tx_count:
        lede = f"{_plural(tx_count, 'new transaction is', 'new transactions are')} in your Notion database."
    else:
        lede = "Everything is up to date. There were no new transactions this time."
    return "Klartion: sync complete", {
        "subject": "Klartion: sync complete",
        "preheader": lede,
        "chip": "Sync",
        "title": "Sync complete",
        "facts": [
            {"label": "Status", "value": "Synced", "tag": "green", "text_skip": True},
            {"label": "Written", "value": _plural(tx_count, "transaction", "transactions")},
            {"label": "Date", "value": _stamp(now)},
        ],
        "intro": [lede],
        "table": _balances(balance_lines),
        "ctas": _open_buttons(True),
        "reason": _reason("every-sync"),
    }


def build_failure(error: str, errors: list = None):
    now = _now()
    problems = _problems(errors) if errors else [{"name": "", "detail": _sentence(error or "Unknown error")}]
    return "Klartion: sync failed", {
        "subject": "Klartion: sync failed",
        "preheader": "Nothing was written to Notion this time. Here is what went wrong.",
        "chip": "Sync",
        "title": "Sync failed",
        "facts": [
            {"label": "Status", "value": "Failed", "tag": "red"},
            {"label": "Written", "value": "0 transactions"},
            {"label": "Date", "value": _stamp(now)},
        ],
        "intro": ["Nothing was written to Notion this time. Here is what went wrong."],
        "problems": problems,
        "steps_title": "What to try",
        "steps": _TRY_STEPS,
        "ctas": _open_buttons(False),
        "reason": _reason("problem"),
    }


def build_partial(tx_count: int, balance_lines: list, errors: list):
    now = _now()
    n = len(errors or [])
    went_wrong = "one thing went wrong" if n == 1 else f"{n} things went wrong"
    lede = f"{_plural(tx_count, 'new transaction is', 'new transactions are')} in your Notion database, but {went_wrong}."
    return "Klartion: sync partially complete", {
        "subject": "Klartion: sync partially complete",
        "preheader": lede,
        "chip": "Sync",
        "title": "Sync partly complete",
        "facts": [
            {"label": "Status", "value": "Partly synced", "tag": "orange"},
            {"label": "Written", "value": _plural(tx_count, "transaction", "transactions")},
            {"label": "Date", "value": _stamp(now)},
        ],
        "intro": [lede],
        "problems": _problems(errors),
        "table": _balances(balance_lines),
        "steps_title": "What to try",
        "steps": _TRY_STEPS,
        "ctas": _open_buttons(False),
        "reason": _reason("problem"),
    }


def build_token_expiry_warning(bank_name: str, days_left: int):
    bank = (bank_name or "").strip() or "your bank"
    ends = _now().date() + datetime.timedelta(days=max(days_left, 0))
    when = "today" if days_left <= 0 else "tomorrow" if days_left == 1 else f"in {days_left} days"
    subject = f"Klartion: your {bank} connection expires {'today' if days_left <= 0 else 'in ' + _plural(days_left, 'day', 'days')}"
    status = "Expires today" if days_left <= 0 else "Expires tomorrow" if days_left == 1 else f"Expires in {days_left} days"
    return subject, {
        "subject": subject,
        "preheader": "Re-authorize it on the Bank page. It takes about a minute.",
        "chip": "Bank connection",
        "title": f"Your {bank} connection expires {when}",
        "facts": [
            {"label": "Status", "value": status, "tag": "red" if days_left <= 1 else "orange", "text_skip": True},
            {"label": "Bank", "value": bank},
            {"label": "Expires", "value": _day_long(ends)},
            {"label": "After that", "value": "Syncing stops"},
        ],
        "intro": ["Banks ask you to approve Klartion's access again from time to time. It takes about a minute, and syncing carries on as before."],
        "steps_title": "Renew it in three steps",
        "steps": [
            f"Open Klartion and go to the {_page_link('Bank page', '/connect')}.",
            f"Click Re-authorize under {bank}.",
            "Sign in at your bank and approve access.",
        ],
        "cta": {"label": "Open the Bank page", "href": _app_url("/connect")},
        "reason": _reason("account"),
    }


_NOTIFY_ON = {"all": "All syncs", "errors": "Errors only", "never": "Never"}


def build_test(notify_email: str = None, notify_on: str = None, host: str = None):
    """The Notifications page's test email. The page can pass settings it hasn't saved yet."""
    notify_on = (notify_on or config.NOTIFY_ON or "all").strip().lower()
    then = {
        "all": "From now on, the result of every sync arrives here, looking like this.",
        "errors": "When a sync has a problem, you'll hear about it here, looking like this.",
    }.get(notify_on, "")
    return "Klartion: test email", {
        "subject": "Klartion: test email",
        "preheader": "If you can read this, Klartion's emails reach you.",
        "chip": "Test",
        "title": "Email alerts are working",
        "facts": [
            {"label": "Status", "value": "Working", "tag": "green", "text_skip": True},
            {"label": "Sent to", "value": notify_email or config.NOTIFY_EMAIL or ""},
            {"label": "When to notify", "value": _NOTIFY_ON.get(notify_on, notify_on.title())},
            {"label": "Sent through", "value": host or _smtp_host() or "Not set"},
        ],
        "intro": [f"This is a test from your Klartion. {then}".strip()],
        "cta": {"label": "Open Klartion", "href": _app_url()},
        "reason": _reason("account"),
    }


def render(built):
    """(subject, text, html) for a built alert."""
    subject, e = built
    return subject, design.render_text(e), design.render_html(e)


def _send(built):
    subject, text, html = render(built)
    send(subject, text, html=html)


def send_success(tx_count: int, balance_lines: list = None):
    if config.NOTIFY_ON in ("errors", "never"):
        return
    _send(build_success(tx_count, balance_lines))


def send_failure(error: str, errors: list = None):
    if config.NOTIFY_ON == "never":
        return
    _send(build_failure(error, errors))


def send_partial(tx_count: int, balance_lines: list, errors: list):
    if config.NOTIFY_ON == "never":
        return
    _send(build_partial(tx_count, balance_lines, errors))


def send_token_expiry_warning(bank_name: str, days_left: int):
    _send(build_token_expiry_warning(bank_name, days_left))


def send_test():
    _send(build_test())
