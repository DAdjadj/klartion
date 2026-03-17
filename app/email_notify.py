import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from . import config

logger = logging.getLogger(__name__)


def _smtp_host_for(email: str) -> str:
    domain = email.split("@")[-1].lower() if "@" in email else ""
    mapping = {
        "gmail.com":      "smtp.gmail.com",
        "googlemail.com": "smtp.gmail.com",
        "icloud.com":     "smtp.mail.me.com",
        "me.com":         "smtp.mail.me.com",
        "mac.com":        "smtp.mail.me.com",
        "outlook.com":    "smtp.office365.com",
        "hotmail.com":    "smtp.office365.com",
        "live.com":       "smtp.office365.com",
        "yahoo.com":      "smtp.mail.yahoo.com",
    }
    return mapping.get(domain, config.SMTP_HOST or "smtp.gmail.com")


def send(subject: str, body: str):
    if not config.SMTP_USER or not config.NOTIFY_EMAIL:
        logger.warning("Email not configured, skipping notification.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = config.SMTP_USER
    msg["To"]      = config.NOTIFY_EMAIL
    msg.attach(MIMEText(body, "plain"))

    try:
        host = _smtp_host_for(config.SMTP_USER)
        port = int(config.SMTP_PORT or 587)
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_USER, config.NOTIFY_EMAIL, msg.as_string())
        logger.info("Email sent: %s", subject)
    except Exception as e:
        logger.error("Failed to send email: %s", e)


def send_success(tx_count: int):
    send(
        subject=f"Klartion: sync complete",
        body=f"Sync completed successfully. {tx_count} transaction(s) written to Notion."
    )


def send_failure(error: str):
    send(
        subject="Klartion: sync failed",
        body=f"Sync failed with the following error:\n\n{error}\n\nOpen Klartion at {config.KLARTION_URL} to check your configuration."
    )


def send_token_expiry_warning(bank_name: str, days_left: int):
    send(
        subject=f"Klartion: your {bank_name} connection expires in {days_left} days",
        body=f"Your Enable Banking connection to {bank_name} will expire in {days_left} days.\n\nOpen Klartion at {config.KLARTION_URL} and go to the Status page to re-authorise."
    )
