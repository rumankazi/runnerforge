import logging
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from runnerforge.main import app
from runnerforge.models import ReconcileResult

client = TestClient(app)


def test_reconcile_endpoint_returns_result_and_logs_counts(monkeypatch, caplog):
    result = ReconcileResult(
        checked=4,
        closed=2,
        facts=3,
        stuck=1,
        backfilled=1,
        more_due=False,
        errors=["100-200-1: RuntimeError"],
    )
    run_mock = AsyncMock(return_value=result)
    monkeypatch.setattr("runnerforge.main.run_reconcile", run_mock)
    # Same caller as /sweep: the scheduler SA from conftest.
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token",
        MagicMock(
            return_value={
                "email": "test-scheduler@example.iam.gserviceaccount.com",
                "email_verified": True,
            }
        ),
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/reconcile", headers={"Authorization": "Bearer fake.jwt.token"}
        )

    assert response.status_code == 200
    assert response.json() == result.model_dump()
    run_mock.assert_awaited_once()
    log = next(r for r in caplog.records if r.message == "Reconcile completed")
    assert (log.checked, log.closed, log.facts, log.stuck, log.backfilled) == (
        4,
        2,
        3,
        1,
        1,
    )
    assert log.more_due is False
    assert log.error_count == 1


def test_reconcile_endpoint_rejects_invalid_token(monkeypatch):
    run_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.run_reconcile", run_mock)
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token",
        MagicMock(side_effect=ValueError("Bad signature")),
    )

    response = client.post(
        "/reconcile", headers={"Authorization": "Bearer bad.jwt.token"}
    )

    assert response.status_code == 401
    run_mock.assert_not_awaited()
