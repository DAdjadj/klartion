import os
from dotenv import load_dotenv

load_dotenv()

LICENCE_KEY         = os.environ.get("LICENCE_KEY", "")
EB_APP_ID           = os.environ.get("EB_APP_ID", "")
EB_PRIVATE_KEY_PATH = os.environ.get("EB_PRIVATE_KEY_PATH", "/app/data/eb_private.key")
NOTION_API_KEY      = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID  = os.environ.get("NOTION_DATABASE_ID", "")
SMTP_HOST           = os.environ.get("SMTP_HOST", "smtp.mail.me.com")
SMTP_PORT           = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER           = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD       = os.environ.get("SMTP_PASSWORD", "")
NOTIFY_EMAIL        = os.environ.get("NOTIFY_EMAIL", "")
SYNC_TIME           = os.environ.get("SYNC_TIME", "08:00")
SECRET_KEY          = os.environ.get("SECRET_KEY", "dev-secret-key")
KLARTION_URL        = os.environ.get("KLARTION_URL", "http://localhost:3001")
DB_PATH             = os.environ.get("DB_PATH", "/app/data/klartion.db")
REDIRECT_URI        = "http://localhost:3000/callback"

def validate():
    required = {
        "LICENCE_KEY": LICENCE_KEY,
        "EB_APP_ID": EB_APP_ID,
        "NOTION_API_KEY": NOTION_API_KEY,
        "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
        "SMTP_USER": SMTP_USER,
        "SMTP_PASSWORD": SMTP_PASSWORD,
        "NOTIFY_EMAIL": NOTIFY_EMAIL,
    }
    missing = [k for k, v in required.items() if not v]
    return missing
