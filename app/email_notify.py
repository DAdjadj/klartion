import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from . import config

logger = logging.getLogger(__name__)

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
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_USER, config.NOTIFY_EMAIL, msg.as_string())
        logger.info("Email sent: %s", subject)
    except Exception as e:
        logger.error("Failed to send email: %s", e)

def send_success(tx_count: int):
    send(
        subject=f"Klartion: synced {tx_count} transaction{'s' if tx_count != 1 else ''}",
        body=(
            f"Your daily Klartion sync completed successfully.\n\n"
            f"Transactions written to Notion: {tx_count}\n\n"
            f"-- Klartion"
        ),
    )

def send_failure(error: str):
    send(
        subject="Klartion: sync failed",
        body=(
            f"Your daily Klartion sync failed with the following error:\n\n"
            f"{error}\n\n"
            f"Please check your configuration at {config.KLARTION_URL}\n\n"
            f"-- Klartion"
        ),
    )

def send_token_expiry_warning(bank_name: str, days_left: int):
    send(
        subject=f"Klartion: your {bank_name} connection expires in {days_left} days",
        body=(
            f"Your Enable Banking connection to {bank_name} will expire in {days_left} days.\n\n"
            f"Please reconnect at {config.KLARTION_URL} to avoid sync interruptions.\n\n"
            f"-- Klartion"
        ),
    )
