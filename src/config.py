"""Project settings: paths, scraping parameters and logging (overridable via environment variables)."""

import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
CLEAN_DIR = DATA_DIR / "clean"
DB_PATH = Path(os.getenv("STEPIK_DB_PATH", DATA_DIR / "output.db"))
SCHEMA_SQL_PATH = ROOT_DIR / "sql" / "schema.sql"

CATALOG_URL = "https://stepik.org/catalog/search"
BASE_URL = "https://stepik.org"
# Catalogue filter value -> label shown on the site. Every filtered listing is capped at 10 pages of 20 courses.
LEVELS = {"easy": "Для начинающих", "normal": "Для продолжающих", "hard": "Для профи"}
PAGES_PER_LEVEL = int(os.getenv("STEPIK_PAGES_PER_LEVEL", "5"))
HEADLESS = os.getenv("STEPIK_HEADLESS", "1") == "1"
WAIT_TIMEOUT_S = 30
SCROLL_STEP_PX = 350
SCROLL_PAUSE_S = 0.7
FILL_ATTEMPTS = 6

MIN_CLEAN_RECORDS = 200
TIMEZONE = ZoneInfo("Asia/Almaty")


def setup_logging(level: int = logging.INFO) -> None:
    """Console logging for CLI runs; inside Airflow the task logger captures the same records."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)-8s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for noisy in ("selenium", "urllib3", "WDM"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
