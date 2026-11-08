"""The slot ledger on the webhook path (ADR-020 step 5, release 1: shadow)."""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from google.api_core.exceptions import Forbidden

from runnerforge import handlers
from runnerforge.admission import Admission
from runnerforge.compute_client import MAX_VM_RUN_DURATION, OperationHandle
from runnerforge.job_store import CreateResult
from runnerforge.main import app
from runnerforge.models import WorkflowJobEvent
from runnerforge.registry import BETA_DEFAULTS
from runnerforge.slots import EXPIRY_SLACK, NO_CAPS

SECRET = b"test-secret"
client = TestClient(app)
failing_client = TestClient(app, raise_server_exceptions=False)
# queued_job_payload.json: run 26389905440, job 77678867086, attempt 3.
KEY = "26389905440-77678867086-3"
VM_NAME = f"runnerforge-{KEY}"
INSTALLATION_ID = 135399152


def _headers(body: bytes) -> dict[str, str]:
    signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": signature, "X-GitHub-Event": "workflow_job"}


def _payload(fixtures_dir, action: str = "queued", **job_fields) -> bytes:
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = action
    payload["workflow_job"].update(job_fields)
    return json.dumps(payload).encode()


def _mock_tokens(status: int = 200) -> None:
    respx.post(
        f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
    ).mock(
        return_value=httpx.Response(
            status,
            json={
                "token": "ghs_installation",
                "expires_at": "2026-05-30T11:00:00Z",
                "permissions": {
                    "actions": "read",
                    "metadata": "read",
                    "administration": "write",
                },
                "repository_selection": "selected",
            },
        )
    )
    respx.post(
        "https://api.github.com/repos/rumankazi/runnerforge/actions/runners/registration-token"
    ).mock(
        return_value=httpx.Response(
            200, json={"token": "ghs_reg", "expires_at": "2026-05-30T11:00:00Z"}
        )
    )


def _handle() -> OperationHandle:
    return OperationHandle(
        name="op-1",
        project="test-project",
        zone="europe-west4-a",
        spot=False,
        machine_type="n2-standard-2",
        image_version="0.2.0",
    )


@pytest.fixture
def created(monkeypatch) -> AsyncMock:
    create = AsyncMock(return_value=_handle())
    monkeypatch.setattr("runnerforge.handlers.create_vm", create)
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", AsyncMock())
    return create


# --- queued ---


@respx.mock
def test_queued_takes_a_slot_before_the_insert(
    fixtures_dir, fake_job_store, fake_slot_ledger, created
):
    _mock_tokens()
    body = _payload(fixtures_dir, labels=["runnerforge", "small"])

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    [(req, caps)] = fake_slot_ledger.acquired
    assert (req.vm_name, req.installation_id, req.job_key) == (
        VM_NAME,
        INSTALLATION_ID,
        KEY,
    )
    # n2-standard-2, sized from its name while the cache is empty.
    assert (req.vcpus, req.per_installation, req.source) == (2, None, "webhook")
    expected = datetime.now(UTC) + MAX_VM_RUN_DURATION + EXPIRY_SLACK
    assert abs((req.expires_at - expected).total_seconds()) < 5
    assert caps == NO_CAPS
    created.assert_awaited_once()
    assert fake_slot_ledger.released == []


@respx.mock
@pytest.mark.parametrize(
    "outcome", ["installation_full", "pool_full", "unavailable", "skipped"]
)
def test_queued_slot_outcomes_are_not_enforced_yet(
    fixtures_dir, fake_job_store, fake_slot_ledger, created, outcome
):
    fake_slot_ledger.outcome = outcome
    _mock_tokens()
    body = _payload(fixtures_dir)

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    created.assert_awaited_once()
    assert fake_job_store.fact("decision")["fact"]["kind"] == "vm_submitted"


@respx.mock
def test_queued_skips_the_ledger_when_the_store_just_failed(
    fixtures_dir, fake_job_store, fake_slot_ledger, created, caplog
):
    fake_job_store.create_result = CreateResult.UNKNOWN
    _mock_tokens()
    body = _payload(fixtures_dir)

    with caplog.at_level(logging.INFO):
        response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    assert fake_slot_ledger.acquired == []
    created.assert_awaited_once()
    [record] = [r for r in caplog.records if r.message == "Slot decision"]
    assert (record.outcome, record.reason, record.vm_name) == (
        "skipped",
        "store_unavailable",
        VM_NAME,
    )


@respx.mock
@pytest.mark.parametrize(("outcome", "released"), [("granted", True), ("held", False)])
def test_a_token_failure_releases_only_a_slot_this_call_took(
    fixtures_dir, fake_job_store, fake_slot_ledger, outcome, released
):
    """A `held` slot belongs to an earlier delivery of the same job, whose VM
    may exist: releasing it would free a slot still in use."""
    fake_slot_ledger.outcome = outcome
    _mock_tokens(status=500)
    body = _payload(fixtures_dir)

    response = failing_client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 500
    expected = [(VM_NAME, INSTALLATION_ID, True)] if released else []
    assert fake_slot_ledger.released == expected


@respx.mock
def test_an_insert_failure_keeps_the_slot(
    fixtures_dir, monkeypatch, fake_job_store, fake_slot_ledger
):
    """GCE may have made the VM anyway; repair drops the slot if it didn't."""
    _mock_tokens()
    monkeypatch.setattr(
        "runnerforge.handlers.create_vm",
        AsyncMock(side_effect=Forbidden("QUOTA_EXCEEDED")),
    )
    body = _payload(fixtures_dir)

    response = failing_client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 500
    assert fake_job_store.fact("errored")["fact"]["stage"] == "gce_insert"
    assert fake_slot_ledger.released == []


@respx.mock
async def test_a_limited_tenant_asks_for_its_limit(
    fixtures_dir, monkeypatch, fake_job_store, fake_slot_ledger, created
):
    _mock_tokens()
    monkeypatch.setattr(handlers, "active_vm_names", AsyncMock(return_value=set()))
    event = WorkflowJobEvent.model_validate_json(_payload(fixtures_dir))
    admission = Admission(
        admitted=True, reason="approved", limits=BETA_DEFAULTS, record=True
    )

    await handlers.handle_queued(event, received_at=0.0, admission=admission)

    [(req, _)] = fake_slot_ledger.acquired
    assert req.per_installation == BETA_DEFAULTS.max_concurrent_vms
    runtime = timedelta(minutes=BETA_DEFAULTS.max_runtime_minutes)
    assert req.expires_at - datetime.now(UTC) > runtime


@respx.mock
def test_queued_without_a_ledger_logs_no_slot_decision(
    fixtures_dir, fake_job_store, created, caplog
):
    """JOB_STORE=none: no ledger, and no line on every job about it."""
    fake_job_store.create_result = CreateResult.UNKNOWN
    _mock_tokens()
    body = _payload(fixtures_dir)

    with caplog.at_level(logging.INFO):
        response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    created.assert_awaited_once()
    assert not [r for r in caplog.records if r.message == "Slot decision"]


# --- completed ---


def _completed(fixtures_dir, runner_name: str = "runnerforge-1-2-1") -> bytes:
    return _payload(
        fixtures_dir, "completed", conclusion="success", runner_name=runner_name
    )


def test_completed_releases_the_vm_it_deletes(
    fixtures_dir, monkeypatch, fake_slot_ledger
):
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_name",
        AsyncMock(
            return_value={
                "runner": "runnerforge",
                "installation_id": str(INSTALLATION_ID),
            }
        ),
    )
    delete = AsyncMock(return_value="op-delete")
    monkeypatch.setattr("runnerforge.handlers.delete_vm", delete)
    body = _completed(fixtures_dir)

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    delete.assert_awaited_once()
    # Its labels were checked: owned.
    assert fake_slot_ledger.released == [("runnerforge-1-2-1", INSTALLATION_ID, True)]


def test_completed_releases_a_vm_already_gone(
    fixtures_dir, monkeypatch, fake_slot_ledger
):
    """A preempted Spot VM: GCE deleted it before the webhook came."""
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_name", AsyncMock(return_value=None)
    )
    body = _completed(fixtures_dir)

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    # Nothing proves the name was ours: only a slot it holds is released.
    assert fake_slot_ledger.released == [("runnerforge-1-2-1", INSTALLATION_ID, False)]


def test_completed_from_an_installation_without_records_releases_nothing(
    fixtures_dir, monkeypatch, fake_slot_ledger
):
    """It can't hold a slot, and anyone can send a `completed` naming a
    runner that doesn't exist."""
    monkeypatch.setattr("runnerforge.main.ALLOWED_INSTALLATION_IDS", {1})
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_name", AsyncMock(return_value=None)
    )
    body = _completed(fixtures_dir, "runnerforge-made-up")

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    assert fake_slot_ledger.released == []


@pytest.mark.parametrize(
    "labels",
    [
        {"runner": "runnerforge", "installation_id": "999"},
        {"runner": "other", "installation_id": str(INSTALLATION_ID)},
    ],
)
def test_completed_releases_nothing_for_a_vm_it_wont_delete(
    fixtures_dir, monkeypatch, fake_slot_ledger, labels
):
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_name", AsyncMock(return_value=labels)
    )
    monkeypatch.setattr("runnerforge.handlers.delete_vm", AsyncMock())
    body = _completed(fixtures_dir)

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    assert fake_slot_ledger.released == []
