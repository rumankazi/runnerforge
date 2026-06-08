import json
import logging
import os
import sys
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType

from opentelemetry import trace

from runnerforge.config import GCP_PROJECT_ID


def _resolve_app_version() -> str:
    """Read version from installed package metadata, fall back to 'dev'."""
    try:
        return version("runnerforge")
    except PackageNotFoundError:
        return "dev"


_APP_VERSION = _resolve_app_version()
_GIT_SHA = os.environ.get("GIT_SHA", "unknown")[:7]  # short SHA

# -----LogFormatter----#
_STANDARD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "taskName",
}

_IGNORED_EXTRAS = {
    "color_message"  # for avoiding the uvicorn log noise
}


def _extract_extras(record):
    return {
        k: v
        for k, v in record.__dict__.items()
        if k not in _STANDARD_ATTRS and k not in _IGNORED_EXTRAS
    }


class ColorFormatter(logging.Formatter):
    _COLORS = MappingProxyType(
        {
            "DEBUG": "\033[36m",  # cyan
            "INFO": "\033[32m",  # green
            "WARNING": "\033[33m",  # yellow
            "ERROR": "\033[31m",  # red
            "CRITICAL": "\033[1;31m",  # bold red
        }
    )
    _RESET = "\033[0m"

    def __init__(self):
        super().__init__()
        # Skip ANSI when piped/redirected, otherwise the file gets escape garbage
        self._use_color = sys.stderr.isatty()

    def format(self, record):
        extras = _extract_extras(record)
        timestamp = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .astimezone()
            .strftime("%H:%M:%S")
        )
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
        extras = _extract_extras(record)
        formatted_record = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
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

        trace_id = getattr(record, "trace_id", None)
        if trace_id and GCP_PROJECT_ID:
            formatted_record["logging.googleapis.com/trace"] = (
                f"projects/{GCP_PROJECT_ID}/traces/{trace_id}"
            )
        span_id = getattr(record, "span_id", None)
        if span_id and GCP_PROJECT_ID:
            formatted_record["logging.googleapis.com/spanId"] = span_id

        return json.dumps(formatted_record, default=str)


# -----LogContext-------#
# Sets up a set of context vars at startup to be attached to logs
# This would help us distinguish different users making the request
@dataclass(frozen=True)
class LogContext:
    request_id: str | None = None
    repo: str | None = None
    owner: str | None = None
    sender: str | None = None
    job_id: int | None = None
    run_id: int | None = None
    run_attempt: int | None = None
    runner_name: str | None = None
    runner_id: int | None = None
    installation_id: int | None = None


_context_var: ContextVar[LogContext] = ContextVar("ctx", default=LogContext())  # noqa: B039


def update_context(**kwargs):
    _context_var.set(replace(_context_var.get(), **kwargs))


class ContextFilter(logging.Filter):
    def filter(self, record):
        for name, value in asdict(_context_var.get()).items():
            if value is not None:
                setattr(record, name, value)

        # OTel
        current_span = trace.get_current_span()
        span_context = current_span.get_span_context()
        if span_context.is_valid:
            record.trace_id = format(span_context.trace_id, "032x")
            record.span_id = format(span_context.span_id, "016x")

        record.app_version = _APP_VERSION
        record.git_sha = _GIT_SHA
        return True


# ------Setup, Wiring-----#
def setup_logging():
    # K_SERVICE is set automatically on Cloud Run; absence means local dev
    if os.environ.get("K_SERVICE"):
        formatter = JsonFormatter()
    else:
        formatter = ColorFormatter()

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()  # To ensure idempotency
    root.setLevel(
        logging.INFO
    )  # setting root logger level, defaults to WARNING otherwise
    root.addHandler(handler)  # Adding updated handler with formatter and filter

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # Our access middleware emits richer per-request logs; silence uvicorn's
    # duplicate INFO access log but keep WARNING+ in case something goes wrong.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
