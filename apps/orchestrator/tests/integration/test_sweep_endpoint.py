import logging
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from runnerforge.main import app
from runnerforge.models import SweepResult

client = TestClient(app)

SWEEP_TOKEN = "test-sweep-auth-token"  # matches conftest


def test_sweep_endpoint_returns_result(monkeypatch, caplog):
    run_sweep_mock = AsyncMock(
        return_value=SweepResult(checked=3, deleted=1, skipped=2, errors=[])
    )
    monkeypatch.setattr("runnerforge.main.run_sweep", run_sweep_mock)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/sweep",
            headers={"Authorization": f"Bearer {SWEEP_TOKEN}"},
        )

    assert response.status_code == 200
    assert response.json() == {"checked": 3, "deleted": 1, "skipped": 2, "errors": []}
    run_sweep_mock.assert_awaited_once()
    assert any("Sweep completed" in r.message for r in caplog.records)


def test_sweep_endpoint_rejects_wrong_token(monkeypatch):
    run_sweep_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.run_sweep", run_sweep_mock)

    response = client.post(
        "/sweep",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
    run_sweep_mock.assert_not_awaited()


def test_sweep_endpoint_rejects_missing_bearer_prefix(monkeypatch):
    run_sweep_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.run_sweep", run_sweep_mock)

    response = client.post(
        "/sweep",
        headers={"Authorization": SWEEP_TOKEN},  # missing "Bearer "
    )

    assert response.status_code == 401
    run_sweep_mock.assert_not_awaited()
