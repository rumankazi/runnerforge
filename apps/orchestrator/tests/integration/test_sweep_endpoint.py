import logging
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
from runnerforge.main import app
from runnerforge.models import SweepResult

client = TestClient(app)


def test_sweep_endpoint_returns_result(monkeypatch, caplog):
    run_sweep_mock = AsyncMock(
        return_value=SweepResult(checked=3, deleted=1, skipped=2, errors=[])
    )
    monkeypatch.setattr("runnerforge.main.run_sweep", run_sweep_mock)

    verify_mock = MagicMock(
        return_value={
            "email": "test-scheduler@example.iam.gserviceaccount.com",  # matches conftest
            "email_verified": True,
        }
    )
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token", verify_mock
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/sweep",
            headers={"Authorization": "Bearer fake.jwt.token"},
        )

    assert response.status_code == 200
    assert response.json() == {"checked": 3, "deleted": 1, "skipped": 2, "errors": []}
    run_sweep_mock.assert_awaited_once()
    log = next((r for r in caplog.records if "Sweep completed" in r.message), None)
    assert log is not None
    assert log.checked == 3
    assert log.deleted == 1
    assert log.skipped == 2
    assert log.error_count == 0


def test_sweep_endpoint_rejects_invalid_token(monkeypatch):
    run_sweep_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.run_sweep", run_sweep_mock)

    verify_mock = MagicMock(side_effect=ValueError("Bad signature"))
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token", verify_mock
    )

    response = client.post(
        "/sweep",
        headers={"Authorization": "Bearer bad.jwt.token"},
    )

    assert response.status_code == 401
    run_sweep_mock.assert_not_awaited()


def test_sweep_endpoint_rejects_missing_bearer_prefix(monkeypatch):
    run_sweep_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.run_sweep", run_sweep_mock)

    response = client.post(
        "/sweep",
        headers={"Authorization": "fake.jwt.token"},  # missing "Bearer "
    )

    assert response.status_code == 401
    run_sweep_mock.assert_not_awaited()
