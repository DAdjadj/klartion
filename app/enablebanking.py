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

def _headers():
    return {
        "Authorization": f"Bearer {_make_jwt()}",
        "Content-Type": "application/json",
    }

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
    """
    all_txns = []
    params = {"date_from": date_from, "date_to": date_to}
    url = f"{EB_BASE}/accounts/{account_uid}/transactions"
    while url:
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        all_txns.extend(data.get("transactions", []))
        ck = data.get("continuation_key")
        if ck:
            url = f"{EB_BASE}/accounts/{account_uid}/transactions"
            params = {"continuation_key": ck}
        else:
            url = None
    return [t for t in all_txns if t.get("status") in ("BOOK", "booked", "PDNG", "pending")]

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
