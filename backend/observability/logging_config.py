"""Structured JSON logging — one JSON object per line, trivially
parsed by any log shipper/aggregator, suitable for production
debugging.

Opt-in: importing this module (or anything else in `observability`)
does not change any existing logging configuration. A caller (a
script's `main()`, or a future API entry point) calls
`configure_json_logging()` explicitly. No existing test calls it, so
none of this project's existing console output is affected.

`Tracer` (see tracer.py) already logs through the standard `logging`
module for every span/event and every fail-safe warning — once this
formatter is installed, all of that becomes structured JSON for free,
with the same redaction (`sanitize_metadata`) applied to any `extra`
fields a log call supplies.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from observability.redaction import sanitize_metadata

_RESERVED_LOG_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys())


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extra = {key: value for key, value in vars(record).items() if key not in _RESERVED_LOG_RECORD_ATTRS}
        if extra:
            payload.update(sanitize_metadata(extra))

        try:
            return json.dumps(payload, default=str)
        except Exception:
            return json.dumps({"timestamp": payload["timestamp"], "level": payload["level"], "message": "<unserializable log record>"})


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
