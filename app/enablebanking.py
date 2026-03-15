import logging
import time
import uuid
import requests
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from . import config, db

logger = logging.getLogger(__name__)

EB_BASE = "https://api.enablebanking.com"

def _make_jwt():
    import jwt as pyjwt
    key_data = open(config.EB_PRIVATE_KEY_PATH, "rb").read()
    private_key = load_pem_private_key(key_data, password=None)
    now = int(time.time())
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": now,
        "exp": now + 3600,
        "jti": str(uuid.uuid4()),
        "sub": config.EB_APP_ID,
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": config.EB_APP_ID})

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
    valid_until = (datetime.now(timezone.utc) + timedelta(days=89)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "access": {
            "balances": True,
            "transactions": True,
            "valid_until": valid_until,
        },
        "aspsp": {
            "name": bank_name,
            "country": bank_country,
        },
        "state": "klartion-auth",
        "redirect_url": "https://klartion.com/",
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

def complete_auth(code: str, state: str) -> bool:
    bank_name    = db.get_setting("pending_bank_name")
    bank_country = db.get_setting("pending_bank_country")
    valid_until  = db.get_setting("pending_valid_until") or ""

    if not code or not state:
        raise ValueError("Missing code or state from redirect URL.")

    resp = requests.post(
        f"{EB_BASE}/sessions",
        headers=_headers(),
        json={"code": code, "state": state},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    session_id  = data["session_id"]
    accounts    = data.get("accounts", [])

    if not accounts:
        raise ValueError("No accounts returned. Check your bank connection.")

    account = accounts[0]
    account_uid = (
        account.get("uid")
        or account.get("account_uid")
        or account.get("resource_id")
        or ""
    )

    db.save_tokens(
        session_id=session_id,
        access_token=account_uid,
        bank_name=bank_name,
        bank_country=bank_country,
        expires_at=valid_until,
    )

    db.set_setting("pending_session_id", "")
    db.set_setting("pending_bank_name", "")
    db.set_setting("pending_bank_country", "")
    db.set_setting("pending_valid_until", "")

    logger.info("Auth completed for %s (%s), account_uid=%s", bank_name, bank_country, account_uid)
    return True

def get_accounts(session_id: str) -> list:
    resp = requests.get(
        f"{EB_BASE}/accounts",
        headers={**_headers(), "Authorization-Session": session_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("accounts", [])

def get_transactions(session_id: str, account_id: str, date_from: str, date_to: str) -> list:
    resp = requests.get(
        f"{EB_BASE}/accounts/{account_id}/transactions",
        headers={**_headers(), "Authorization-Session": session_id},
        params={"date_from": date_from, "date_to": date_to},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return [t for t in data.get("transactions", []) if t.get("status") == "booked"]

def check_token_expiry():
    tokens = db.get_tokens()
    if not tokens or not tokens.get("expires_at"):
        return None
    try:
        expires = datetime.fromisoformat(tokens["expires_at"].replace("Z", "+00:00"))
        delta = expires - datetime.now(timezone.utc)
        return max(0, delta.days)
    except Exception:
        return None
