import logging
import sys
from .config import settings


class JSONFormatter(logging.Formatter):
    """Minimal structured log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        import json, datetime

        log_obj = {
            "time": datetime.datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra fields
        for key, val in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "id", "levelname", "levelno",
                "lineno", "module", "msecs", "message", "msg",
                "name", "pathname", "process", "processName",
                "relativeCreated", "stack_info", "thread", "threadName",
            ):
                log_obj[key] = val

        if record.exc_info:
            log_obj["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


def setup_logging():
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JSONFormatter() if settings.ENVIRONMENT == "production" else
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)
    # Quieten noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
