import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import respx
from fastapi.testclient import TestClient

from runnerforge.main import app
from runnerforge.models import ReconcileResult, RunnerForgeVmLabels, VmInfo

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
        started=2,
        orphans_deleted=1,
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
    assert log.started == 2
    assert log.orphans_deleted == 1


def test_reconcile_endpoint_watches_started_vms(monkeypatch):
    """Like the webhook path, each VM the capacity pass starts gets a
    watcher that records its `vm` fact (ADR-019)."""
    handle = object()

    async def run(on_started):
        on_started(handle)
        return ReconcileResult(
            checked=0,
            closed=0,
            facts=0,
            stuck=0,
            backfilled=0,
            more_due=False,
            errors=[],
        )

    monkeypatch.setattr("runnerforge.main.run_reconcile", run)
    watcher = AsyncMock()
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", watcher)
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token",
        MagicMock(
            return_value={
                "email": "test-scheduler@example.iam.gserviceaccount.com",
                "email_verified": True,
            }
        ),
    )

    response = client.post(
        "/reconcile", headers={"Authorization": "Bearer fake.jwt.token"}
    )

    assert response.status_code == 200
    watcher.assert_awaited_once_with(handle)


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


@respx.mock
def test_reconcile_deregisters_and_deletes_an_idle_orphan(
    monkeypatch, caplog, fake_job_store
):
    """Through the endpoint with the real reconciler: GitHub lists the VM's
    runner idle, and no job of the installation is waiting."""
    vm = VmInfo(
        name="runnerforge-100-1-1",
        creation_timestamp=datetime.now(UTC) - timedelta(minutes=30),
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id="1",
            run_id="100",
            run_attempt="1",
            installation_id="135399152",
        ),
        repo_full_name="rumankazi/runnerforge",
        runner_labels=["runnerforge", "small"],
        status="RUNNING",
    )
    # The hourly registry backfill would call GitHub too.
    monkeypatch.setattr(
        "runnerforge.reconcile.registry_backfill_due", lambda now: False
    )
    monkeypatch.setattr("runnerforge.reconcile.ADMISSION", "allowlist")
    monkeypatch.setattr("runnerforge.reconcile.ALLOWED_INSTALLATION_IDS", [135399152])
    monkeypatch.setattr(
        "runnerforge.reconcile.list_runnerforge_vms", AsyncMock(return_value=[vm])
    )
    delete_vm = AsyncMock(return_value="op-1")
    monkeypatch.setattr("runnerforge.reconcile.delete_vm", delete_vm)
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token",
        MagicMock(
            return_value={
                "email": "test-scheduler@example.iam.gserviceaccount.com",
                "email_verified": True,
            }
        ),
    )
    respx.post("https://api.github.com/app/installations/135399152/access_tokens").mock(
        return_value=httpx.Response(
            201,
            json={
                "token": "ghs_test",
                "expires_at": "2026-11-01T13:00:00Z",
                "permissions": {
                    "actions": "read",
                    "metadata": "read",
                    "administration": "write",
                },
                "repository_selection": "selected",
            },
        )
    )
    respx.get(
        "https://api.github.com/repos/rumankazi/runnerforge/actions/runners"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "runners": [
                    {
                        "id": 42,
                        "name": "runnerforge-100-1-1",
                        "os": "Linux",
                        "status": "online",
                        "busy": False,
                    }
                ],
            },
        )
    )
    deregister = respx.delete(
        "https://api.github.com/repos/rumankazi/runnerforge/actions/runners/42"
    ).mock(return_value=httpx.Response(204))

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/reconcile", headers={"Authorization": "Bearer fake.jwt.token"}
        )

    assert response.status_code == 200
    assert response.json()["orphans_deleted"] == 1
    assert response.json()["errors"] == []
    assert deregister.call_count == 1
    delete_vm.assert_awaited_once_with(
        instance_name="runnerforge-100-1-1", reason="reconcile"
    )
    log = next(r for r in caplog.records if r.message == "Reconcile completed")
    assert log.orphans_deleted == 1
