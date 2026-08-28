import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .database import DATA_DIR

LOG_DIR = Path(os.environ.get("YUS_AI_LOG_DIR", DATA_DIR / "logs"))
LOG_FILE = LOG_DIR / "yus-ai.log"


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, RotatingFileHandler) for item in root.handlers):
        root.addHandler(handler)
