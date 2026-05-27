
import json
import logging
import os
import sys
from datetime import datetime, timezone

_STANDARD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName"
}

_IGNORED_EXTRAS = {
    "color_message" # for avoiding the uvicorn log noise
}

class ColorFormatter(logging.Formatter):
    _COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[1;31m", # bold red
    }
    _RESET = "\033[0m"

    def __init__(self):
        super().__init__()
        # Skip ANSI when piped/redirected, otherwise the file gets escape garbage
        self._use_color = sys.stderr.isatty()

    def format(self, record):
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS and k not in _IGNORED_EXTRAS
        }
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = f"{record.levelname:<8}"
        if self._use_color:
            color = self._COLORS.get(record.levelname, "")
            level = f"{color}{level}{self._RESET}"
        line = f"{level} {timestamp} {record.name}: {record.getMessage()}"
        if extras:
            line += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):

    def format(self, record):
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS and k not in _IGNORED_EXTRAS
        }
        formatted_record = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "logger": record.name,
            "logging.googleapis.com/sourceLocation": {
                "file": record.pathname,
                "line": str(record.lineno),  # Cloud Logging spec requires a string
                "function": record.funcName,
            },
        }
        formatted_record.update(extras)
        if record.exc_info:
            formatted_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(formatted_record, default=str)

def setup_logging():
    # K_SERVICE is set automatically on Cloud Run; absence means local dev
    if os.environ.get("K_SERVICE"):
        formatter = JsonFormatter()
    else:
        formatter = ColorFormatter()

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # Our access middleware emits richer per-request logs; silence uvicorn's
    # duplicate INFO access log but keep WARNING+ in case something goes wrong.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
