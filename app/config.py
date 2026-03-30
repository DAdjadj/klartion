import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

DB_PATH      = os.environ.get("DB_PATH", "/app/data/klartion.db")
SECRET_KEY   = os.environ.get("SECRET_KEY", "dev-secret-key")
REDIRECT_URI = "https://klartion.com/callback"

def _db_get(key):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (f"config:{key}",)
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None

def _get(key, default=""):
    return _db_get(key) or os.environ.get(key, default)

def set(key, value):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"config:{key}", str(value) if value is not None else "")
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def is_configured():
    licence = _get("LICENCE_KEY")
    if not licence or licence == "pending":
        return False
    required = [
        licence,
        _get("NOTION_API_KEY"), _get("NOTION_DATABASE_ID"),
        _get("SMTP_USER"), _get("SMTP_PASSWORD"), _get("NOTIFY_EMAIL"),
    ]
    return all(required)

def validate():
    missing = []
    for key in ["LICENCE_KEY", "EB_APP_ID", "NOTION_API_KEY",
                "NOTION_DATABASE_ID", "SMTP_USER", "SMTP_PASSWORD", "NOTIFY_EMAIL"]:
        if not _get(key):
            missing.append(key)
    return missing

def __getattr__(name):
    defaults = {
        "EB_PRIVATE_KEY_PATH": "/app/data/eb_private.key",
        "SMTP_HOST":           "smtp.mail.me.com",
        "SMTP_PORT":           "587",
        "SYNC_TIME":           "08:00",
        "SYNC_FREQUENCY":      "24",
        "KLARTION_URL":        "http://localhost:3001",
    }
    if name in ("LICENCE_KEY", "EB_APP_ID", "EB_PRIVATE_KEY_PATH",
                "NOTION_API_KEY", "NOTION_DATABASE_ID", "SMTP_HOST",
                "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
                "NOTIFY_EMAIL", "NOTIFY_ON",
                "SYNC_TIME", "SYNC_FREQUENCY", "KLARTION_URL"):
        val = _get(name, defaults.get(name, ""))
        if name == "SMTP_PORT":
            return int(val or 587)
        return val
    raise AttributeError(f"module 'config' has no attribute {name!r}")
