import logging
import json
from datetime import datetime, timedelta, timezone
from . import config, db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enable Banking uses JWT-signed requests. The SDK wraps this for us.
# We use the REST API directly via requests for simplicity and full control.
# ---------------------------------------------------------------------------

import requests

EB_BASE = "https://api.enablebanking.com"

def _load_private_key():
    with open(config.EB_PRIVATE_KEY_PATH, "r") as f:
        return f.read()

def _make_jwt():
    """Create a JWT for Enable Banking API authentication."""
    import jwt as pyjwt
    import time
    private_key = _load_private_key()
    now = int(time.time())
    payload = {
        "iss": config.EB_APP_ID,
        "iat": now,
        "exp": now + 3600,
    }
    token = pyjwt.encode(payload, private_key, algorithm="RS256")
    return token

def _headers():
    return {
        "Authorization": f"Bearer {_make_jwt()}",
        "Content-Type": "application/json",
    }

def get_auth_url(bank_name: str, bank_country: str) -> str:
    """
    Start an Enable Banking session and return the auth URL to redirect the user to.
    Stores the session state in the DB for the callback to retrieve.
    """
    payload = {
        "access": {
            "balances": True,
            "transactions": True,
            "valid_until": (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "aspsp": {
            "name": bank_name,
            "country": bank_country,
        },
        "state": "klartion-auth",
        "redirect_url": config.REDIRECT_URI,
        "psu_type": "personal",
    }
    resp = requests.post(f"{EB_BASE}/auth", headers=_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    session_id = data["session_id"]
    auth_url   = data["url"]

    # Persist session_id so the callback can reference it
    db.set_setting("pending_session_id", session_id)
    db.set_setting("pending_bank_name", bank_name)
    db.set_setting("pending_bank_country", bank_country)

    return auth_url

def complete_auth(code: str) -> bool:
    """
    Called from the OAuth callback. Exchanges the code for an access token.
    """
    session_id   = db.get_setting("pending_session_id")
    bank_name    = db.get_setting("pending_bank_name")
    bank_country = db.get_setting("pending_bank_country")

    if not session_id:
        logger.error("No pending session found for OAuth callback.")
        return False

    resp = requests.get(
        f"{EB_BASE}/auth/{session_id}",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    access = data.get("access", {})
    valid_until = access.get("valid_until", "")

    db.save_tokens(
        session_id=session_id,
        access_token=session_id,  # EB uses session_id as the bearer token handle
        bank_name=bank_name,
        bank_country=bank_country,
        expires_at=valid_until,
    )

    # Clear pending state
    db.set_setting("pending_session_id", "")
    db.set_setting("pending_bank_name", "")
    db.set_setting("pending_bank_country", "")

    logger.info("Enable Banking auth completed for %s (%s)", bank_name, bank_country)
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
    """
    Fetch booked transactions only (MVP: skip pending).
    date_from / date_to in YYYY-MM-DD format.
    """
    resp = requests.get(
        f"{EB_BASE}/accounts/{account_id}/transactions",
        headers={**_headers(), "Authorization-Session": session_id},
        params={
            "date_from": date_from,
            "date_to": date_to,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    # Return only booked transactions for MVP
    return [t for t in data.get("transactions", []) if t.get("status") == "booked"]

def check_token_expiry():
    """
    Returns number of days until token expiry, or None if no token.
    """
    tokens = db.get_tokens()
    if not tokens or not tokens.get("expires_at"):
        return None
    try:
        expires = datetime.fromisoformat(tokens["expires_at"].replace("Z", "+00:00"))
        delta = expires - datetime.now(timezone.utc)
        return max(0, delta.days)
    except Exception:
        return None
