import io
import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_tracer_provider

from runnerforge.logger import (
    ColorFormatter,
    ContextFilter,
    JsonFormatter,
    update_context,
)


@pytest.fixture(autouse=True)
def reset_log_context():
    yield
    from runnerforge.logger import LogContext, _context_var

    _context_var.set(LogContext())


def _capture(formatter, record_kwargs=None, exc=False):
    """Run a log record through a formatter and return the output string."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    handler.addFilter(ContextFilter())
    logger = logging.getLogger("test")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    if exc:
        try:
            raise ValueError("boom!")
        except ValueError:
            logger.exception("oops!", extra=record_kwargs or {})
    else:
        logger.info("hello", extra=record_kwargs or {})
    return stream.getvalue()


################## JsonFormatter ##################
def test_json_formatter_emits_valid_json_with_cloud_logging_fields():
    output = _capture(JsonFormatter(), {"job_id": 31, "random": "foo-bar"})
    parsed = json.loads(output)
    assert parsed["severity"] == "INFO"
    assert parsed["message"] == "hello"
    assert parsed["job_id"] == 31
    assert parsed["random"] == "foo-bar"
    assert "logging.googleapis.com/sourceLocation" in parsed
    assert "timestamp" in parsed


def test_json_formatter_emits_trace_field_when_both_present(monkeypatch):
    monkeypatch.setattr("runnerforge.logger.GCP_PROJECT_ID", "my-project")
    set_tracer_provider(TracerProvider())
    tracer = trace.get_tracer("test")

    # Start a real span so ContextFilter picks up trace_id/span_id from OTel
    with tracer.start_as_current_span("test-span"):
        output = _capture(JsonFormatter())

    parsed = json.loads(output)
    assert "logging.googleapis.com/trace" in parsed
    assert parsed["logging.googleapis.com/trace"].startswith(
        "projects/my-project/traces/"
    )
    assert "logging.googleapis.com/spanId" in parsed


def test_json_formatter_omits_trace_fields_when_project_id_missing(monkeypatch):
    monkeypatch.setattr("runnerforge.logger.GCP_PROJECT_ID", None)
    set_tracer_provider(TracerProvider())
    tracer = trace.get_tracer("test")

    with tracer.start_as_current_span("test-span"):
        output = _capture(JsonFormatter())
    parsed = json.loads(output)

    assert "logging.googleapis.com/trace" not in parsed


def test_context_filter_injects_request_id():
    update_context(request_id="req-123")
    output = _capture(JsonFormatter())
    parsed = json.loads(output)
    assert parsed["request_id"] == "req-123"


def test_setup_logging_picks_json_in_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "runnerforge")

    from runnerforge.logger import setup_logging

    setup_logging()

    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)


def test_json_formatter_renders_exception():
    output = _capture(JsonFormatter(), exc=True)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert "ValueError: boom" in parsed["exception"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "req-123"),
        ("repo", "owner/repo"),
        ("owner", "owner"),
        ("sender", "sender-user"),
        ("run_id", 42),
        ("installation_id", 99),
    ],
)
def test_context_filter_injects_field(field, value):
    update_context(**{field: value})
    output = _capture(JsonFormatter())
    parsed = json.loads(output)
    assert parsed[field] == value


def test_resolve_app_version_falls_back_to_dev_when_package_not_installed(monkeypatch):
    from importlib.metadata import PackageNotFoundError

    from runnerforge.logger import _resolve_app_version

    def raise_not_found(*args, **kwargs):
        raise PackageNotFoundError

    monkeypatch.setattr("runnerforge.logger.version", raise_not_found)
    assert _resolve_app_version() == "dev"


def test_context_filter_always_injects_version_and_sha():
    output = _capture(JsonFormatter())
    parsed = json.loads(output)
    assert "app_version" in parsed
    assert "git_sha" in parsed


################## ColorFormatter ##################
def test_color_formatter_is_human_readable():
    output = _capture(ColorFormatter())
    assert "INFO" in output
    assert "hello" in output
    assert "test:" in output  # logger name


def test_color_formatter_emits_valid_extra_fields():
    from importlib.metadata import version

    output = _capture(ColorFormatter(), {"job_id": 31, "random": "foo-bar"})
    assert "INFO" in output
    assert "job_id=31" in output
    assert "random=foo-bar" in output
    # app_version is sourced from pyproject.toml via importlib.metadata;
    # don't hardcode a string that changes on every release.
    assert f"app_version={version('runnerforge')}" in output
    assert "git_sha=unknown" in output


def test_color_formatter_omits_extras_block_when_none():
    formatter = ColorFormatter()
    record = logging.LogRecord(
        name="runnerforge.test",
        level=logging.INFO,
        pathname="/x/y.py",
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    out = formatter.format(record)
    # The message text is at the end; nothing after it
    assert out.endswith("runnerforge.test: hello")
    assert " request_id=" not in out  # sanity: no context leaked in


def test_setup_logging_picks_color_locally(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    from runnerforge.logger import setup_logging

    setup_logging()

    root = logging.getLogger()
    assert any(isinstance(h.formatter, ColorFormatter) for h in root.handlers)


def test_color_formatter_renders_exception():
    output = _capture(ColorFormatter(), exc=True)
    assert "ValueError: boom" in output
    assert "Traceback" in output


def test_color_formatter_emits_ansi_when_color_enabled():
    fmt = ColorFormatter()
    fmt._use_color = True  # bypass tty detection
    output = _capture(fmt)
    assert "\033[" in output  # INFO is green
    assert "INFO" in output  # plain text should still be there


def test_color_formatter_emits_ansi_when_color_disabled():
    fmt = ColorFormatter()
    fmt._use_color = False  # bypass tty detection
    output = _capture(fmt)
    assert "\033[" not in output  # INFO is green
    assert "INFO" in output  # plain text should still be there


# -----------------------------------------------------------------------------
# get_context / with_log_context — BackgroundTasks propagation helpers
# -----------------------------------------------------------------------------


def test_get_context_returns_current_log_context_snapshot():
    from runnerforge.logger import LogContext, get_context

    update_context(request_id="req-snap-1", job_id=42, repo="owner/repo")

    snapshot = get_context()

    assert isinstance(snapshot, LogContext)
    assert snapshot.request_id == "req-snap-1"
    assert snapshot.job_id == 42
    assert snapshot.repo == "owner/repo"


async def test_with_log_context_installs_snapshot_before_awaiting():
    """A coroutine launched via with_log_context should observe the snapshotted
    LogContext, even if the current context has since been overwritten."""
    from runnerforge.logger import LogContext, _context_var, with_log_context

    snapshot = LogContext(request_id="req-orig", job_id=1, repo="orig/repo")

    # Mutate the live context to a different value — the snapshot should win
    _context_var.set(LogContext(request_id="req-stale", job_id=999, repo="stale/repo"))

    captured = {}

    async def record_context():
        live = _context_var.get()
        captured["request_id"] = live.request_id
        captured["job_id"] = live.job_id
        captured["repo"] = live.repo

    await with_log_context(snapshot, record_context)

    assert captured == {
        "request_id": "req-orig",
        "job_id": 1,
        "repo": "orig/repo",
    }


async def test_with_log_context_forwards_args_and_kwargs_and_returns_result():
    """The helper should pass through positional + keyword arguments and bubble
    the awaited function's return value back to the caller."""
    from runnerforge.logger import LogContext, with_log_context

    async def echo(a, b, *, c):
        return (a, b, c)

    result = await with_log_context(LogContext(), echo, 1, 2, c=3)

    assert result == (1, 2, 3)
