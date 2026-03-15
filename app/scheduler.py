import schedule
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from . import config, db, sync

logger = logging.getLogger(__name__)

def _should_catchup() -> bool:
    """
    Returns True if the last successful sync was more than 20 hours ago.
    Handles the case where the container was down at the scheduled time.
    """
    last = db.get_last_sync()
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        return (datetime.now(timezone.utc) - last_dt.replace(tzinfo=timezone.utc)) > timedelta(hours=20)
    except Exception:
        return False

def _run_sync():
    logger.info("Scheduled sync triggered at %s", datetime.now().isoformat())
    sync.run()

def start():
    """
    Start the scheduler in a background thread.
    Registers the daily job and runs a catch-up sync on startup if needed.
    """
    logger.info("Scheduler starting. Daily sync at %s", config.SYNC_TIME)

    schedule.every().day.at(config.SYNC_TIME).do(_run_sync)

    if _should_catchup():
        logger.info("Last sync was >20 hours ago or never ran. Running catch-up sync.")
        threading.Thread(target=sync.run, daemon=True).start()

    def loop():
        while True:
            schedule.run_pending()
            time.sleep(60)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    logger.info("Scheduler running.")
