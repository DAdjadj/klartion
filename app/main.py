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

def main():
    logger.info("Klartion starting up...")

    # Initialise database first (config depends on it)
    db.init()
    logger.info("Database initialised.")

    # Import config after db is ready
    from . import config

    if config.is_configured():
        # Validate licence
        from . import licence
        result = licence.validate()
        if not result["valid"] and not result.get("offline"):
            logger.warning("Licence invalid: %s — web UI will prompt for setup.", result["error"])
        else:
            # Start scheduler
            start_scheduler()
    else:
        logger.info("Not fully configured yet — skipping scheduler. Complete setup at http://localhost:3001")

    logger.info("Web UI available at http://localhost:3001")
    start_web(host="0.0.0.0", port=3000)

if __name__ == "__main__":
    main()
