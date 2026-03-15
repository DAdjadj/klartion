import requests
import logging
from . import config, db

logger = logging.getLogger(__name__)

LEMON_BASE = "https://api.lemonsqueezy.com/v1/licenses"

def _instance_id():
    return db.get_setting("licence_instance_id")

def activate(key: str) -> dict:
    """Activate a licence key. Reuse existing instance if already active."""
    existing = _instance_id()
    if existing:
        result = validate(key)
        if result["valid"]:
            return result
        # Instance invalid/deactivated - clear it and re-activate
        db.set_setting("licence_instance_id", "")
    try:
        resp = requests.post(
            f"{LEMON_BASE}/activate",
            json={"license_key": key, "instance_name": "klartion"},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("activated"):
            instance_id = data["instance"]["id"]
            db.set_setting("licence_instance_id", instance_id)
            db.set_setting("licence_key", key)
            return {"valid": True, "error": None}
        else:
            msg = data.get("error") or data.get("message") or "Invalid licence key."
            return {"valid": False, "error": msg}
    except requests.RequestException as e:
        logger.warning("Licence activate failed (network): %s", e)
        return {"valid": True, "error": None, "offline": True}

def deactivate() -> dict:
    """Deactivate the current instance."""
    key         = config.LICENCE_KEY
    instance_id = _instance_id()
    if not key or not instance_id:
        return {"success": False, "error": "No active licence to deactivate."}
    try:
        resp = requests.post(
            f"{LEMON_BASE}/deactivate",
            json={"license_key": key, "instance_id": instance_id},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("deactivated"):
            db.set_setting("licence_instance_id", "")
            return {"success": True, "error": None}
        else:
            msg = data.get("error") or data.get("message") or "Deactivation failed."
            return {"success": False, "error": msg}
    except requests.RequestException as e:
        logger.warning("Licence deactivate failed (network): %s", e)
        return {"success": False, "error": str(e)}

def validate(key: str = None) -> dict:
    """
    Validate the current licence. If no instance ID exists yet, activate first.
    Returns {"valid": True/False, "error": str or None}
    """
    key = key or config.LICENCE_KEY
    if not key:
        return {"valid": False, "error": "No licence key configured."}

    instance_id = _instance_id()

    # No instance yet — try to activate
    if not instance_id:
        return activate(key)

    # Already have instance — validate it
    try:
        resp = requests.post(
            f"{LEMON_BASE}/validate",
            json={"license_key": key, "instance_id": instance_id},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("valid"):
            return {"valid": True, "error": None}
        else:
            msg = data.get("error") or data.get("message") or "Invalid licence key."
            return {"valid": False, "error": msg}
    except requests.RequestException as e:
        logger.warning("Licence check failed (network): %s", e)
        return {"valid": True, "error": None, "offline": True}
