import requests
import hashlib
import socket
import subprocess
import platform
import logging
import uuid
from . import config, db

logger = logging.getLogger(__name__)

LICENCE_BASE = "https://api.klartion.com"

def _get_hw_uuid():
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                timeout=5, stderr=subprocess.DEVNULL,
            ).decode()
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
        elif system == "Windows":
            out = subprocess.check_output(
                ["reg", "query", "HKLM\\SOFTWARE\\Microsoft\\Cryptography", "/v", "MachineGuid"],
                timeout=5, stderr=subprocess.DEVNULL,
            ).decode()
            for line in out.splitlines():
                if "MachineGuid" in line:
                    return line.strip().split()[-1]
        elif system == "Linux":
            try:
                return open("/etc/machine-id").read().strip()
            except FileNotFoundError:
                pass
    except Exception:
        pass
    return ""

def _get_fingerprint():
    stored = db.get_setting("machine_fingerprint_v2")
    if stored:
        return stored
    # Migrating from v1: deactivate old fingerprint to free the activation slot
    old_fp = db.get_setting("machine_fingerprint")
    if old_fp:
        key = db.get_setting("licence_key")
        if key:
            try:
                requests.post(
                    LICENCE_BASE + "/deactivate",
                    json={"license_key": key, "machine_fingerprint": old_fp},
                    timeout=10,
                )
            except requests.RequestException:
                pass
        db.set_setting("machine_fingerprint", "")
    parts = [
        str(uuid.getnode()),
        _get_hw_uuid(),
    ]
    raw = "|".join(parts)
    fp = hashlib.sha256(raw.encode()).hexdigest()[:32]
    db.set_setting("machine_fingerprint_v2", fp)
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
        if db.get_setting("licence_key") == key:
            return {"valid": True, "error": None, "offline": True}
        return {"valid": False, "error": "Could not reach the licence server. Check your internet connection and try again."}

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
            # Fingerprint may have changed after update — try re-activating
            reactivation = activate(key)
            if reactivation.get("valid"):
                return {"valid": True, "error": None}
            msg = data.get("error") or "Invalid licence key."
            return {"valid": False, "error": msg}
    except requests.RequestException as e:
        logger.warning("Licence check failed (network): %s", e)
        if db.get_setting("licence_key") == key:
            return {"valid": True, "error": None, "offline": True}
        return {"valid": False, "error": "Could not reach the licence server. Check your internet connection."}

def get_activation_info():
    key = config.LICENCE_KEY
    if not key:
        return {"usage": 0, "limit": 2, "bank_account_limit": 2, "is_trial": False, "expires_at": None}
    try:
        resp = requests.post(
            LICENCE_BASE + "/info",
            json={"license_key": key},
            timeout=5,
        )
        if resp.status_code == 200:
            d = resp.json()
            return {
                "usage": d.get("activation_usage", 0),
                "limit": d.get("activation_limit", 2),
                "bank_account_limit": d.get("bank_account_limit", 2),
                "is_trial": d.get("is_trial", False),
                "expires_at": d.get("expires_at"),
            }
    except Exception:
        pass
    return {"usage": 0, "limit": 2, "bank_account_limit": 2, "is_trial": False, "expires_at": None}
