import io
import json
import logging

import pytest
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
    # force reload of logger module so GCP_PROJECT_ID is re-read
    monkeypatch.setattr("runnerforge.logger.GCP_PROJECT_ID", "my-project")
    update_context(trace_id="abc123")

    output = _capture(JsonFormatter())
    parsed = json.loads(output)
    assert parsed["logging.googleapis.com/trace"] == "projects/my-project/traces/abc123"


def test_json_formatter_omits_trace_fields_when_project_id_missing(monkeypatch):
    monkeypatch.setattr("runnerforge.logger.GCP_PROJECT_ID", None)
    update_context(trace_id="abc123")
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
        ("trace_id", "trace-abc"),
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
    output = _capture(ColorFormatter(), {"job_id": 31, "random": "foo-bar"})
    assert "INFO" in output
    assert "job_id=31" in output
    assert "random=foo-bar" in output
    assert "app_version=dev" in output
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
