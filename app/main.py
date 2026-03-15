import logging
import threading
import sys
from . import config, db, licence
from .scheduler import start as start_scheduler
from .web.server import start as start_web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

def main():
    logger.info("Klartion starting up...")

    # Initialise database
    db.init()
    logger.info("Database initialised at %s", config.DB_PATH)

    # Validate environment
    missing = config.validate()
    if missing:
        logger.warning("Missing config vars: %s — web UI will prompt for setup.", missing)
    else:
        # Validate licence on startup
        result = licence.validate()
        if not result["valid"] and not result.get("offline"):
            logger.error("Licence validation failed: %s", result["error"])
            logger.error("Please check your LICENCE_KEY in .env and restart.")
            sys.exit(1)
        elif result.get("offline"):
            logger.warning("Could not reach Lemon Squeezy — proceeding offline.")
        else:
            logger.info("Licence valid.")

        # Start scheduler in background thread
        start_scheduler()

    # Start Flask web server (blocking, main thread)
    logger.info("Web UI available at http://localhost:3000")
    start_web(host="0.0.0.0", port=3000)

if __name__ == "__main__":
    main()
