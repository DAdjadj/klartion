import requests
import hashlib
import socket
import logging
from . import config, db

logger = logging.getLogger(__name__)

LICENCE_BASE = "https://api.klartion.com"

def _get_fingerprint():
    stored = db.get_setting("machine_fingerprint")
    if stored:
        return stored
    raw = socket.gethostname()
    fp = hashlib.sha256(raw.encode()).hexdigest()[:32]
    db.set_setting("machine_fingerprint", fp)
    return fp

def activate(key):
    fp = _get_fingerprint()
    try:
        resp = requests.post(
            LICENCE_BASE + "/activate",
            json={"license_key": key, "machine_fingerprint": fp, "instance_name": "klartion"},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("valid"):
            db.set_setting("licence_key", key)
            return {"valid": True, "error": None}
        elif resp.status_code == 409:
            db.set_setting("licence_key", key)
            return {"valid": True, "error": None}
        else:
            msg = data.get("error") or "Invalid licence key."
            return {"valid": False, "error": msg}
    except requests.RequestException as e:
        logger.warning("Licence activate failed (network): %s", e)
        return {"valid": True, "error": None, "offline": True}

def deactivate():
    key = config.LICENCE_KEY
    fp = _get_fingerprint()
    if not key:
        return {"success": False, "error": "No active licence to deactivate."}
    try:
        resp = requests.post(
            LICENCE_BASE + "/deactivate",
            json={"license_key": key, "machine_fingerprint": fp},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200:
            db.set_setting("licence_key", "")
            db.set_setting("machine_fingerprint", "")
            return {"success": True, "error": None}
        else:
            msg = data.get("error") or "Deactivation failed."
            return {"success": False, "error": msg}
    except requests.RequestException as e:
        logger.warning("Licence deactivate failed (network): %s", e)
        return {"success": False, "error": str(e)}

def validate(key=None):
    key = key or config.LICENCE_KEY
    if not key:
        return {"valid": False, "error": "No licence key configured."}
    fp = _get_fingerprint()
    try:
        resp = requests.post(
            LICENCE_BASE + "/validate",
            json={"license_key": key, "machine_fingerprint": fp},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("valid"):
            return {"valid": True, "error": None}
        else:
            msg = data.get("error") or "Invalid licence key."
            return {"valid": False, "error": msg}
    except requests.RequestException as e:
        logger.warning("Licence check failed (network): %s", e)
        return {"valid": True, "error": None, "offline": True}
