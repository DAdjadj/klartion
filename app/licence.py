import requests
import logging
from . import config

logger = logging.getLogger(__name__)

LEMON_VALIDATE_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"

def validate(key: str = None) -> dict:
    """
    Validate a Lemon Squeezy licence key.
    Returns {"valid": True/False, "error": str or None}
    """
    key = key or config.LICENCE_KEY
    if not key:
        return {"valid": False, "error": "No licence key configured."}

    try:
        resp = requests.post(
            LEMON_VALIDATE_URL,
            json={"license_key": key},
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
        # Allow operation if Lemon Squeezy is unreachable (offline tolerance)
        return {"valid": True, "error": None, "offline": True}
