from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_"):
                try:
                    json.dumps(value)
                    extra_fields[key] = value
                except (TypeError, ValueError):
                    extra_fields[key] = repr(value)
        if extra_fields:
            payload["fields"] = extra_fields
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging(level: str, log_format: str = "plain") -> None:
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    formatter: logging.Formatter = (
        JsonLogFormatter() if log_format.lower() == "json" else logging.Formatter(_LOG_FORMAT)
    )
    if not root_logger.handlers:
        logging.basicConfig(level=resolved_level, format=_LOG_FORMAT)
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
        return
    root_logger.setLevel(resolved_level)
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)
