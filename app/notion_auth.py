"""Sign in with Notion through api.klartion.com.

Notion's OAuth needs a client secret and an https redirect URI, which a
self-hosted instance cannot have, so the licence API does the code exchange.
It hands the tokens back encrypted to this instance only: start() registers a
fresh P-256 public key, the API encrypts the token response to it (ECDH,
HKDF-SHA256 with info "klartion-notion-v1", AES-256-GCM), and poll() claims
and decrypts it with the private key that never leaves this machine.

The older integration-key setup keeps working; NOTION_AUTH_MODE tells them apart.
"""
import base64
import json
import logging
import time
import uuid

import requests

from . import config, db

logger = logging.getLogger(__name__)

API_BASE = "https://api.klartion.com"
HKDF_INFO = b"klartion-notion-v1"
REFRESH_MARGIN_SECONDS = 600
_available_cache = {"at": 0.0, "value": False}


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _generate_keypair():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = priv.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return pem, _b64u(pub)


def _decrypt(private_pem: str, epk: str, iv: str, ct: str) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    priv = load_pem_private_key(private_pem.encode(), password=None)
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _b64u_decode(epk))
    shared = priv.exchange(ec.ECDH(), peer)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO).derive(shared)
    return AESGCM(key).decrypt(_b64u_decode(iv), _b64u_decode(ct), None).decode()


def _owner():
    from . import licence
    return {
        "license_key": config.LICENCE_KEY,
        "machine_fingerprint": licence.get_machine_fingerprint(),
    }


def _post(path, payload, timeout=10):
    r = requests.post(API_BASE + path, json=payload, timeout=timeout)
    try:
        return r, r.json()
    except ValueError:
        return r, {}


def available() -> bool:
    """Whether Sign in with Notion is switched on. Cached for five minutes."""
    now = time.time()
    if now - _available_cache["at"] < 300:
        return _available_cache["value"]
    try:
        r = requests.get(API_BASE + "/notion-auth/available", timeout=5)
        value = bool(r.json().get("available"))
    except (requests.RequestException, ValueError):
        value = False
    _available_cache.update(at=now, value=value)
    return value


def start() -> dict:
    """Register a new sign-in attempt. Returns {"url": ...} or {"error": ...}."""
    state_id = str(uuid.uuid4())
    pem, pubkey = _generate_keypair()
    payload = _owner()
    payload.update(state_id=state_id, pubkey=pubkey)
    try:
        r, data = _post("/notion-auth/register", payload)
    except requests.RequestException:
        return {"error": "Could not reach the Klartion server. Check your internet connection and try again."}
    if data.get("status") != "registered":
        return {"error": data.get("error") or "Sign in with Notion is not available right now. Use an integration key instead."}
    db.set_setting("notion_oauth_state", state_id)
    db.set_setting("notion_oauth_key", pem)
    return {"url": f"{API_BASE}/notion-auth/start?state={state_id}"}


def _clear_attempt():
    db.set_setting("notion_oauth_state", "")
    db.set_setting("notion_oauth_key", "")


def _save_tokens(tokens: dict):
    config.set("NOTION_API_KEY", tokens["access_token"])
    if tokens.get("refresh_token"):
        config.set("NOTION_REFRESH_TOKEN", tokens["refresh_token"])
    expires_in = tokens.get("expires_in")
    config.set("NOTION_TOKEN_EXPIRES_AT", str(int(time.time() + int(expires_in))) if expires_in else "")


def poll() -> dict:
    """
    Check the pending sign-in. Returns {"status": "pending"}, {"status":
    "connected", "workspace": ...} or {"status": "error", "error": ...}.
    """
    state_id = db.get_setting("notion_oauth_state") or ""
    pem = db.get_setting("notion_oauth_key") or ""
    if not state_id or not pem:
        return {"status": "error", "error": "No sign-in in progress. Click Connect Notion to start."}
    payload = _owner()
    payload["state_id"] = state_id
    try:
        r, data = _post("/notion-auth/claim", payload)
    except requests.RequestException:
        return {"status": "pending"}
    status = data.get("status")
    if status == "pending":
        return {"status": "pending"}
    if status != "ready":
        _clear_attempt()
        if status == "cancelled":
            return {"status": "error", "error": "Notion access was not allowed. Click Connect Notion to try again."}
        return {"status": "error", "error": data.get("error") or "The sign-in expired. Click Connect Notion to try again."}

    _clear_attempt()
    try:
        tokens = json.loads(_decrypt(pem, data["epk"], data["iv"], data["ct"]))
    except Exception as e:
        logger.error("Could not decrypt Notion sign-in: %s", e)
        return {"status": "error", "error": "The sign-in could not be completed. Click Connect Notion to try again."}

    previous = {k: getattr(config, k) for k in ("NOTION_API_KEY", "NOTION_DATABASE_ID")}
    _save_tokens(tokens)
    from . import notion
    try:
        db_id = notion.find_or_create_database(tokens.get("duplicated_template_id"))
        config.set("NOTION_DATABASE_ID", db_id)
        error = notion.ensure_schema()
    except Exception as e:
        logger.error("Notion database setup failed: %s", e)
        error = str(e)
    if error:
        for k, v in previous.items():
            config.set(k, v)
        return {"status": "error", "error": error}

    config.set("NOTION_AUTH_MODE", "oauth")
    config.set("NOTION_WORKSPACE", tokens.get("workspace_name", ""))
    logger.info("Connected to Notion workspace %s via sign-in", tokens.get("workspace_name", ""))
    return {"status": "connected", "workspace": tokens.get("workspace_name", "")}


def use_integration_key():
    """The user pasted an integration key, so forget any sign-in tokens."""
    config.set("NOTION_AUTH_MODE", "key")
    config.set("NOTION_REFRESH_TOKEN", "")
    config.set("NOTION_TOKEN_EXPIRES_AT", "")
    config.set("NOTION_WORKSPACE", "")


def refresh(force=False) -> bool:
    """Renew the access token when it is close to expiring (or now, if forced)."""
    if config.NOTION_AUTH_MODE != "oauth" or not config.NOTION_REFRESH_TOKEN:
        return False
    expires_at = config.NOTION_TOKEN_EXPIRES_AT
    if not force and (not expires_at or int(expires_at) - time.time() > REFRESH_MARGIN_SECONDS):
        return False
    payload = _owner()
    payload["refresh_token"] = config.NOTION_REFRESH_TOKEN
    try:
        r, data = _post("/notion-auth/refresh", payload)
    except requests.RequestException as e:
        logger.warning("Notion token refresh unavailable: %s", e)
        return False
    if not data.get("access_token"):
        logger.warning("Notion token refresh failed: %s", data.get("error"))
        return False
    _save_tokens(data)
    logger.info("Renewed Notion access token")
    return True
