from unittest.mock import MagicMock

from fastapi import FastAPI

from runnerforge.tracing import setup_tracing


def test_setup_tracing_no_op_outside_cloud_run(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    app = FastAPI()
    # should not raise
    setup_tracing(app)


def test_setup_tracing_wires_provider_in_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "runnerforge")

    monkeypatch.setattr(
        "runnerforge.tracing.CloudTraceSpanExporter",
        lambda **kwargs: MagicMock(),
    )

    app = FastAPI()
    setup_tracing(app)
