import glob
import logging
import os
import time
import uuid
import requests
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from . import config, db

logger = logging.getLogger(__name__)

EB_BASE = "https://api.enablebanking.com"

def _get_app_id():
    """Extract app ID from UUID-named .pem file in /app/data/"""
    if config.EB_APP_ID:
        return config.EB_APP_ID
    for f in glob.glob("/app/data/*.pem"):
        name = os.path.splitext(os.path.basename(f))[0]
        if len(name) == 36:
            config.set("EB_APP_ID", name)
            return name
    raise RuntimeError("Could not determine Enable Banking App ID. Make sure your .pem file is named with your Application ID (e.g. aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.pem) and placed in the data/ folder.")

def _make_jwt():
    import jwt as pyjwt
    # Find the key file - either the default path or a UUID-named .pem
    key_path = config.EB_PRIVATE_KEY_PATH
    if not os.path.exists(key_path):
        for f in glob.glob("/app/data/*.pem"):
            key_path = f
            break
    key_data = open(key_path, "rb").read()
    private_key = load_pem_private_key(key_data, password=None)
    now = int(time.time())
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": now,
        "exp": now + 3600,
        "jti": str(uuid.uuid4()),
        "sub": _get_app_id(),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": _get_app_id()})

def resolve_public_ip() -> str:
    """Detect the server's public IP via an external service.
    For self-hosted Klartion, the server's public IP IS the user's IP (same
    home network). Result is cached in DB for 12 hours."""
    cached = (db.get_setting("resolved_public_ip") or "").strip()
    cached_at = db.get_setting("resolved_public_ip_at") or ""
    if cached and cached_at:
        try:
            updated = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
            if age_h < 12:
                return cached
        except Exception:
            pass
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            resp = requests.get(url, timeout=5)
            if resp.ok:
                ip = resp.text.strip()
                if ip:
                    db.set_setting("resolved_public_ip", ip)
                    db.set_setting("resolved_public_ip_at", datetime.now(timezone.utc).isoformat())
                    logger.info("Resolved server public IP: %s", ip)
                    return ip
        except Exception:
            continue
    return cached


def _is_public_ip(ip: str) -> bool:
    """Return True if ip is a valid, publicly routable address."""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        return not (addr.is_private or addr.is_loopback or addr.is_link_local)
    except ValueError:
        return False


def _psu_signals() -> dict:
    """Return PSU-* values currently stored, plus their freshness in seconds.
    If the stored IP is private (e.g. Docker bridge 192.168.x.x), falls back
    to the server's resolved public IP so that the bank sees a valid address
    and treats the access as attended (bypassing the PSD2 4-call daily cap)."""
    psu_ip = (db.get_setting("psu_ip") or "").strip()
    psu_ua = (db.get_setting("psu_user_agent") or "").strip()
    psu_updated_at = db.get_setting("psu_updated_at") or ""
    ip_source = "browser"
    # Reject stored IP if it's private/non-routable
    if psu_ip and not _is_public_ip(psu_ip):
        logger.debug("Stored PSU IP %s is private, will use resolved public IP", psu_ip)
        psu_ip = ""
    # Fall back to server's own public IP (same home network as user)
    if not psu_ip:
        psu_ip = resolve_public_ip()
        ip_source = "resolved"
        if psu_ip:
            logger.info("PSU IP: using resolved public IP %s (no valid browser IP stored)", psu_ip)
    age_seconds = None
    if psu_updated_at:
        try:
            updated = datetime.fromisoformat(psu_updated_at.replace("Z", "+00:00"))
            age_seconds = int((datetime.now(timezone.utc) - updated).total_seconds())
        except Exception:
            pass
    return {"ip": psu_ip, "ua": psu_ua, "age_seconds": age_seconds, "ip_source": ip_source}

def _headers(include_psu: bool = False):
    h = {
        "Authorization": f"Bearer {_make_jwt()}",
        "Content-Type": "application/json",
    }
    if include_psu:
        psu = _psu_signals()
        if psu["ip"]:
            h["Psu-Ip-Address"] = psu["ip"]
        if psu["ua"]:
            h["Psu-User-Agent"] = psu["ua"]
    return h

def _log_eb_request(method: str, path: str, *, session_id: str = "", account_uid: str = "", params: dict = None, with_psu: bool = False) -> None:
    """Trace each Enable Banking call so failures can be reproduced from logs.
    Includes session_id and account_uid for correlating with the Enable Banking
    control panel request log."""
    psu_summary = "off"
    if with_psu:
        psu = _psu_signals()
        if psu["ip"] or psu["ua"]:
            age = f"{psu['age_seconds']}s" if psu["age_seconds"] is not None else "?"
            src = psu.get("ip_source", "?")
            psu_summary = f"ip={psu['ip'] or '<empty>'}({src}) ua_len={len(psu['ua'])} age={age}"
        else:
            psu_summary = "requested-but-empty"
    logger.info(
        "EB %s %s session=%s account=%s params=%s psu=%s",
        method, path,
        session_id or "<none>",
        account_uid or "<none>",
        params or {},
        psu_summary,
    )

def _raise_with_body(resp, context: str) -> None:
    """Like resp.raise_for_status(), but logs the response body first.
    Enable Banking returns specific error codes (e.g. EXPIRED_SESSION) in the
    JSON body that raise_for_status() would otherwise discard. Also logs the
    Enable Banking request ID header (when present) so the matching entry
    can be found in the EB control panel."""
    if resp.ok:
        return
    body = (resp.text or "")[:1000]
    eb_req_id = resp.headers.get("X-Request-Id") or resp.headers.get("Request-Id") or "<none>"
    # Log all response headers on failure — may contain diagnostic info
    resp_headers = {k: v for k, v in resp.headers.items()}
    logger.error(
        "Enable Banking %s failed: HTTP %d eb_request_id=%s headers=%s body=%s",
        context, resp.status_code, eb_req_id, resp_headers, body,
    )
    resp.raise_for_status()

def get_banks() -> list:
    resp = requests.get(f"{EB_BASE}/aspsps", headers=_headers(), timeout=15)
    resp.raise_for_status()
    banks = resp.json().get("aspsps", [])
    result = []
    for b in banks:
        if "personal" in b.get("psu_types", []):
            result.append({"name": b["name"], "country": b["country"]})
    result.sort(key=lambda x: x["name"].lower())
    return result

def start_auth(bank_name: str, bank_country: str) -> dict:
    valid_until = (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_val   = str(uuid.uuid4())
    payload = {
        "access": {
            "valid_until": valid_until,
        },
        "aspsp": {
            "name": bank_name,
            "country": bank_country,
        },
        "state": f"klartion-auth|{config.KLARTION_URL}|{state_val}",
        "redirect_url": "https://klartion.com/callback",
        "psu_type": "personal",
    }
    resp = requests.post(f"{EB_BASE}/auth", headers=_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    session_id = data["authorization_id"]
    auth_url   = data["url"]

    db.set_setting("pending_session_id", session_id)
    db.set_setting("pending_bank_name", bank_name)
    db.set_setting("pending_bank_country", bank_country)
    db.set_setting("pending_valid_until", valid_until)

    logger.info("Auth session started: %s for %s (%s)", session_id, bank_name, bank_country)
    return {"session_id": session_id, "url": auth_url}

def extract_account_uid(account):
    return account.get("uid") or account.get("account_uid") or account.get("resource_id") or ""

def complete_auth(code: str, state: str) -> dict:
    bank_name    = db.get_setting("pending_bank_name")
    bank_country = db.get_setting("pending_bank_country")
    valid_until  = db.get_setting("pending_valid_until") or ""

    if not code or not state:
        raise ValueError("Missing code or state from redirect URL.")

    # Strip the embedded KLARTION_URL from state before sending to Enable Banking
    clean_state = state.split("|")[-1] if "|" in state else state

    resp = requests.post(
        f"{EB_BASE}/sessions",
        headers=_headers(),
        json={"code": code, "state": clean_state},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    session_id  = data["session_id"]
    accounts    = data.get("accounts", [])

    if not accounts:
        raise ValueError("No accounts returned. Check your bank connection.")

    logger.info("Auth completed for %s (%s), %d account(s) returned", bank_name, bank_country, len(accounts))
    return {
        "session_id": session_id,
        "accounts": accounts,
        "bank_name": bank_name,
        "bank_country": bank_country,
        "valid_until": valid_until,
    }

def get_session_status(session_id: str) -> dict:
    """Query Enable Banking's view of the session state without hitting the
    bank. Returns the full session metadata (status, valid_until, accounts...)
    or {"status": "ERROR", "error": "..."} on failure. Used as a diagnostic
    probe before sync attempts so we can tell if a 400 from the bank is
    session-level or request-level."""
    try:
        resp = requests.get(
            f"{EB_BASE}/sessions/{session_id}",
            headers=_headers(),
            timeout=10,
        )
        if not resp.ok:
            return {"status": "ERROR", "http": resp.status_code, "body": (resp.text or "")[:300]}
        data = resp.json()
        return {
            "status": data.get("status"),
            "authorized": data.get("authorized"),
            "valid_until": (data.get("access") or {}).get("valid_until"),
            "psu_type": data.get("psu_type"),
            "created": data.get("created"),
        }
    except Exception as e:
        return {"status": "ERROR", "error": str(e)[:200]}


def get_accounts(session_id: str) -> list:
    resp = requests.get(
        f"{EB_BASE}/accounts",
        headers={**_headers(), "Authorization-Session": session_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("accounts", [])

def get_transactions(session_id: str, account_uid: str, date_from: str, date_to: str) -> list:
    """
    Fetch booked transactions for an account by UID.
    Uses pagination via continuation_key if present.
    Retries with exponential backoff on 429 rate-limit responses.
    """
    all_txns = []
    params = {"date_from": date_from, "date_to": date_to}
    url = f"{EB_BASE}/accounts/{account_uid}/transactions"
    page = 0
    while url:
        if page > 0:
            time.sleep(1)
        _log_eb_request("GET", f"/accounts/{account_uid}/transactions", session_id=session_id, account_uid=account_uid, params=params, with_psu=True)
        req_headers = _headers(include_psu=True)
        # Log actual outgoing headers (redact JWT for brevity)
        safe_headers = {k: (v[:30] + "...") if k == "Authorization" else v for k, v in req_headers.items()}
        logger.info("Outgoing request headers: %s", safe_headers)
        for attempt in range(4):
            try:
                resp = requests.get(url, headers=req_headers, params=params, timeout=30)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < 3:
                    wait = min(2 ** attempt * 5, 60)
                    logger.warning("Connection error, retrying in %ds (attempt %d/4): %s", wait, attempt + 1, e)
                    time.sleep(wait)
                    continue
                raise
            if resp.status_code == 429:
                wait = min(2 ** attempt * 5, 60)
                logger.warning("Rate limited (429), retrying in %ds (attempt %d/4)", wait, attempt + 1)
                time.sleep(wait)
                continue
            break
        _raise_with_body(resp, f"GET /accounts/{account_uid}/transactions")
        data = resp.json()
        all_txns.extend(data.get("transactions", []))
        ck = data.get("continuation_key")
        if ck:
            url = f"{EB_BASE}/accounts/{account_uid}/transactions"
            params = {"continuation_key": ck}
        else:
            url = None
        page += 1
    return [t for t in all_txns if t.get("status") in ("BOOK", "booked", "PDNG", "pending")]

def get_balances(session_id: str, account_uid: str) -> list:
    """
    Fetch balances for an account by UID.
    Returns a list of balance objects from Enable Banking.
    """
    _log_eb_request("GET", f"/accounts/{account_uid}/balances", session_id=session_id, account_uid=account_uid, with_psu=True)
    req_headers = _headers(include_psu=True)
    safe_headers = {k: (v[:30] + "...") if k == "Authorization" else v for k, v in req_headers.items()}
    logger.info("Outgoing request headers: %s", safe_headers)
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{EB_BASE}/accounts/{account_uid}/balances",
                headers=req_headers,
                timeout=15,
            )
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < 2:
                wait = min(2 ** attempt * 5, 30)
                logger.warning("Balance connection error, retrying in %ds (attempt %d/3): %s", wait, attempt + 1, e)
                time.sleep(wait)
                continue
            raise
    _raise_with_body(resp, f"GET /accounts/{account_uid}/balances")
    return resp.json().get("balances", [])


def check_token_expiry():
    """Returns days until the soonest-expiring token, or None."""
    all_tokens = db.get_all_tokens()
    if not all_tokens:
        return None
    min_days = None
    for tokens in all_tokens:
        if not tokens.get("expires_at"):
            continue
        try:
            expires = datetime.fromisoformat(tokens["expires_at"].replace("Z", "+00:00"))
            days = max(0, (expires - datetime.now(timezone.utc)).days)
            if min_days is None or days < min_days:
                min_days = days
        except Exception:
            continue
    return min_days
