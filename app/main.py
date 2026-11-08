import logging
import threading
import sys
from . import db
from .scheduler import start as start_scheduler
from .web.server import start as start_web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

def _start_scheduler():
    # A scheduler problem must never keep the web UI from starting: it's where it gets fixed.
    try:
        start_scheduler()
    except Exception:
        logger.exception("Could not start the scheduler. Check the Sync page.")

def main():
    logger.info("Klartion starting up...")

    # Initialise database first (config depends on it)
    db.init()
    logger.info("Database initialized.")

    # Import config after db is ready
    from . import config

    if config.is_configured():
        # Validate licence
        from . import licence
        result = licence.validate()
        if result.get("offline_expired"):
            # Keep the schedule: each sync checks again and carries on once the server is back.
            logger.warning(result["error"])
            _start_scheduler()
        elif not result["valid"] and not result.get("offline"):
            logger.warning("License invalid: %s — web UI will prompt for setup.", result["error"])
        else:
            _start_scheduler()
    else:
        logger.info("Not fully configured yet — skipping scheduler. Complete setup at http://localhost:3001")

    logger.info("Web UI available at http://localhost:3001")
    start_web(host="0.0.0.0", port=3000)

if __name__ == "__main__":
    main()
