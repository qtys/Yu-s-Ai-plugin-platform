import os
import logging
import re
import traceback
import copy
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .database import DATA_DIR
from .plugins import registry

LOG_DIR = Path(os.environ.get("YUS_AI_LOG_DIR", DATA_DIR / "logs"))
LOG_FILE = LOG_DIR / "yus-ai.log"
PLUGIN_CONTEXT = ContextVar("plugin_log_context", default=None)


def plugin_for_path(path: str) -> str | None:
    if path.startswith("/api/plugins/"):
        candidate = path[len("/api/plugins/"):].split("/")[0]
        return candidate if registry.get(candidate) else None
    for prefix, plugin_id in (("/api/images", "image_generation"), ("/api/speech", "speech"),
                              ("/api/translation", "translation")):
        if path == prefix or path.startswith(prefix + "/"):
            return plugin_id
    return None


def get_plugin_logger(plugin_id: str) -> logging.Logger:
    if not registry.get(plugin_id):
        raise ValueError("Unknown plugin ID")
    return logging.getLogger(f"yus_ai.plugins.{plugin_id}")


class PluginFilter(logging.Filter):
    def __init__(self, plugin_id: str):
        super().__init__()
        self.plugin_id = plugin_id

    def filter(self, record):
        aliases = {"yus_ai.images": "image_generation", "yus_ai.speech": "speech",
                   "yus_ai.translation": "translation", "yus_ai.instruction_review": "instruction_review"}
        plugin_id = aliases.get(record.name)
        if record.name.startswith("yus_ai.plugins."):
            plugin_id = record.name.split(".")[2]
        if not plugin_id and record.name == "yus_ai.api":
            message = str(record.msg)
            plugin_id = next((value for value in ("proactive", "translation", "instruction_review")
                              if message.startswith(value + "_") or value == "instruction_review" and "instruction_review" in message), None)
        return (plugin_id or PLUGIN_CONTEXT.get()) == self.plugin_id


class SafeFormatter(logging.Formatter):
    def formatException(self, exc_info):
        # Exception strings can contain credentials, prompts or signed URLs.
        frames = traceback.extract_tb(exc_info[2])
        return "exception_type=" + exc_info[0].__name__ + "\n" + "\n".join(
            f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in frames)

    def format(self, record):
        record = copy.copy(record)
        record.exc_text = None
        result = super().format(record)
        # Never persist signed download queries, URL credentials, or bearer tokens.
        result = re.sub(r"(https?://)([^\s/]+@)", r"\1", result)
        result = re.sub(r"(https?://[^\s?'\"]+)\?[^\s'\"]+", r"\1?<redacted>", result)
        return re.sub(r"(?i)Bearer\s+[^\s'\"]+", "Bearer <redacted>", result)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = SafeFormatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    targets = [(LOG_FILE, None)] + [(LOG_DIR / "plugins" / f"{item.id}.log", item.id) for item in registry.list()]
    for path, plugin_id in targets:
        if any(isinstance(item, RotatingFileHandler) and Path(item.baseFilename) == path.resolve() for item in root.handlers):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024,
                                      backupCount=2 if plugin_id else 5, encoding="utf-8", delay=True)
        handler.setFormatter(formatter)
        if plugin_id:
            handler.addFilter(PluginFilter(plugin_id))
        root.addHandler(handler)
