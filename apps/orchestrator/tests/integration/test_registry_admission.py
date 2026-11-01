"""ADR-019 step 3a: ADMISSION=registry end to end through /webhook."""

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

from runnerforge import admission, registry
from runnerforge.compute_client import MAX_VM_RUN_DURATION, OperationHandle
from runnerforge.main import app
from runnerforge.registry import (
    BETA_DEFAULTS,
    UNLIMITED,
    CachedRegistry,
    Limits,
    RegistryEntry,
    RegistryUnavailable,
)

SECRET = b"test-secret"
client = TestClient(app)
# queued_job_payload.json: run 26389905440, job 77678867086, attempt 3.
KEY = "26389905440-77678867086-3"
INSTALLATION_ID = 135399152
APPROVED_AT = datetime(2026, 11, 1, tzinfo=UTC)


class FakeRegistry:
    enabled = True

    def __init__(self):
        self.rows: dict[int, RegistryEntry] = {}
        self.unavailable = False

    async def get(self, installation_id):
        if self.unavailable:
            raise RegistryUnavailable("down")
        return self.rows.get(installation_id)


@pytest.fixture
def fake_registry(monkeypatch) -> FakeRegistry:
    fake = FakeRegistry()
    monkeypatch.setattr(registry, "_registry", fake)
    monkeypatch.setattr(registry, "_cached", CachedRegistry(fake))
    monkeypatch.setattr("runnerforge.main.ADMISSION", "registry")
    # The tenant isn't allow-listed: it goes through the registry.
    monkeypatch.setattr(admission, "ALLOWED_INSTALLATION_IDS", frozenset({1}))
    return fake


@pytest.fixture
def vm(monkeypatch) -> dict[str, AsyncMock]:
    handle = OperationHandle(
        name="op-1",
        project="test-project",
        zone="europe-west4-a",
        spot=True,
        machine_type="n2-standard-4",
        image_version="0.2.0",
    )
    mocks = {
        "create": AsyncMock(return_value=handle),
        "count": AsyncMock(return_value=set()),
    }
    monkeypatch.setattr("runnerforge.handlers.create_vm", mocks["create"])
    monkeypatch.setattr("runnerforge.handlers.active_vm_names", mocks["count"])
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", AsyncMock())
    return mocks


def _approve(fake: FakeRegistry, limits: Limits | None) -> None:
    fake.rows[INSTALLATION_ID] = RegistryEntry(
        INSTALLATION_ID,
        "approved",
        "active",
        APPROVED_AT,
        None if limits is None else limits.to_dict(),
    )


def _post(fixtures_dir, action: str = "queued", labels: list[str] | None = None):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = action
    if labels is not None:
        payload["workflow_job"]["labels"] = labels
    if action != "queued":
        payload["workflow_job"]["runner_name"] = "runnerforge-1-2-1"
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": "workflow_job"}
    return client.post("/webhook", content=body, headers=headers)


def _mock_github_tokens() -> None:
    respx.post(
        f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_installation_token",
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
            200,
            json={
                "token": "ghs_registration_token",
                "expires_at": "2026-05-30T11:00:00Z",
            },
        )
    )


def _outcome(caplog) -> tuple[str, str | None]:
    [record] = [r for r in caplog.records if r.message == "Queued webhook outcome"]
    return record.outcome, getattr(record, "reason", None)


def _messages(caplog) -> list[str]:
    return [r.message for r in caplog.records]


# --- rejections ---


@pytest.mark.parametrize(
    ("row", "unavailable", "reason"),
    [
        (None, False, "not_registered"),
        (RegistryEntry(INSTALLATION_ID, "pending", "active"), False, "pending"),
        (None, True, "registry_unavailable"),
    ],
)
def test_never_approved_installations_are_rejected_without_a_record(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog, row, unavailable, reason
):
    if row is not None:
        fake_registry.rows[INSTALLATION_ID] = row
    fake_registry.unavailable = unavailable

    with caplog.at_level(logging.INFO):
        response = _post(fixtures_dir)

    assert response.status_code == 200
    assert _outcome(caplog) == ("rejected", reason)
    # After the lost-webhook anchor, so the ratio sees a decision.
    messages = _messages(caplog)
    assert messages.index("Webhook event received") < messages.index(
        "Queued webhook outcome"
    )
    decision = next(r for r in caplog.records if r.message == "Admission decision")
    assert (decision.admitted, decision.reason, decision.limited) == (
        False,
        reason,
        False,
    )
    assert "Installation not allowed" not in messages
    assert fake_job_store.created == []
    assert fake_job_store.facts == []
    vm["create"].assert_not_awaited()


def test_a_suspended_tenant_gets_its_rejection_recorded(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog
):
    fake_registry.rows[INSTALLATION_ID] = RegistryEntry(
        INSTALLATION_ID, "suspended", "active", APPROVED_AT
    )

    with caplog.at_level(logging.INFO):
        _post(fixtures_dir)

    assert _outcome(caplog) == ("rejected", "suspended")
    assert fake_job_store.created[0][0] == KEY
    assert fake_job_store.fact("decision")["fact"] == {
        "kind": "rejected",
        "reason": "suspended",
    }
    vm["create"].assert_not_awaited()


def test_a_job_above_max_size_is_rejected(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog
):
    _approve(fake_registry, BETA_DEFAULTS)

    with caplog.at_level(logging.INFO):
        _post(fixtures_dir, labels=["self-hosted", "runnerforge", "large"])

    assert _outcome(caplog) == ("rejected", "limit_size")
    assert fake_job_store.fact("decision")["fact"] == {
        "kind": "rejected",
        "reason": "limit_size",
        "machine_type": "n2-standard-8",
    }
    vm["count"].assert_not_awaited()
    vm["create"].assert_not_awaited()


# --- admitted ---


@respx.mock
@pytest.mark.parametrize("allowlisted", [True, False])
def test_unlimited_tenants_skip_the_limits(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog, monkeypatch, allowlisted
):
    if allowlisted:
        monkeypatch.setattr(
            admission, "ALLOWED_INSTALLATION_IDS", frozenset({INSTALLATION_ID})
        )
        fake_registry.unavailable = True  # never read
    else:
        _approve(fake_registry, UNLIMITED)
    _mock_github_tokens()

    with caplog.at_level(logging.INFO):
        _post(fixtures_dir, labels=["runnerforge", "xlarge"])

    assert _outcome(caplog) == ("vm_submitted", None)
    kwargs = vm["create"].await_args.kwargs
    assert kwargs["machine_type"] == "n2-standard-16"
    assert kwargs["spot"] is False
    assert kwargs["max_run_duration"] == MAX_VM_RUN_DURATION
    vm["count"].assert_not_awaited()


@respx.mock
def test_limits_force_spot_and_cap_the_runtime(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog
):
    _approve(fake_registry, Limits(max_concurrent_vms=2, max_runtime_minutes=30))
    vm["count"].return_value = {"runnerforge-9-9-1"}
    _mock_github_tokens()

    with caplog.at_level(logging.INFO):
        _post(fixtures_dir)

    assert _outcome(caplog) == ("vm_submitted", None)
    kwargs = vm["create"].await_args.kwargs
    assert kwargs["spot"] is True
    assert kwargs["max_run_duration"] == timedelta(minutes=45)
    vm["count"].assert_awaited_once_with(INSTALLATION_ID)
    assert fake_job_store.fact("decision")["fact"] == {
        "kind": "vm_submitted",
        "machine_type": "n2-standard-4",
        "spot": True,
    }


# --- concurrency ---


def test_a_job_at_the_limit_is_deferred(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog
):
    _approve(fake_registry, BETA_DEFAULTS)
    vm["count"].return_value = {"runnerforge-9-9-1", "runnerforge-9-8-1"}

    with caplog.at_level(logging.INFO):
        response = _post(fixtures_dir)

    assert response.status_code == 200
    assert _outcome(caplog) == ("deferred", "limit_concurrency")
    assert fake_job_store.fact("decision")["fact"] == {
        "kind": "deferred",
        "reason": "limit_concurrency",
        "active_vms": 2,
        "machine_type": "n2-standard-4",
        "spot": True,
    }
    vm["create"].assert_not_awaited()


@respx.mock
def test_a_deferral_that_cant_be_stored_is_admitted(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog
):
    _approve(fake_registry, BETA_DEFAULTS)
    vm["count"].return_value = {"a", "b", "c"}
    fake_job_store.record_result = False
    _mock_github_tokens()

    with caplog.at_level(logging.INFO):
        _post(fixtures_dir)

    assert _outcome(caplog) == ("vm_submitted", "deferral_unrecorded")
    assert "Deferral not recorded, admitting" in _messages(caplog)
    decisions = [f["fact"] for f in fake_job_store.facts if f["name"] == "decision"]
    assert decisions[-1]["deferral_unrecorded"] is True
    vm["create"].assert_awaited_once()


@respx.mock
def test_a_failed_concurrency_check_admits(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog
):
    _approve(fake_registry, BETA_DEFAULTS)
    vm["count"].side_effect = RuntimeError("gce down")
    _mock_github_tokens()

    with caplog.at_level(logging.INFO):
        _post(fixtures_dir)

    assert _outcome(caplog) == ("vm_submitted", None)
    failed = next(
        r for r in caplog.records if r.message == "Concurrency check failed, admitting"
    )
    assert failed.error_class == "RuntimeError"


@respx.mock
def test_a_redelivered_job_at_the_limit_is_not_deferred(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog
):
    _approve(fake_registry, BETA_DEFAULTS)
    # Its own VM exists, and with another one the tenant is at its limit.
    vm["count"].return_value = {f"runnerforge-{KEY}", "runnerforge-9-9-1"}
    vm["create"].return_value = None  # GCE: AlreadyExists
    _mock_github_tokens()

    with caplog.at_level(logging.INFO):
        _post(fixtures_dir)

    assert _outcome(caplog) == ("already_exists", None)
    assert fake_job_store.fact("decision")["fact"]["kind"] == "already_exists"


@respx.mock
def test_a_slow_concurrency_check_admits(
    fixtures_dir, fake_registry, fake_job_store, vm, caplog, monkeypatch
):
    _approve(fake_registry, BETA_DEFAULTS)
    vm["count"].side_effect = TimeoutError
    _mock_github_tokens()

    with caplog.at_level(logging.INFO):
        _post(fixtures_dir)

    assert _outcome(caplog) == ("vm_submitted", None)
    failed = next(
        r for r in caplog.records if r.message == "Concurrency check failed, admitting"
    )
    assert failed.error_class == "TimeoutError"


# --- readiness from the runner's VM ---


def _in_progress(fixtures_dir, created_at: int, started_at: int):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    job = payload["workflow_job"]
    job["runner_name"] = "runnerforge-1-9-1"
    job["created_at"] = datetime.fromtimestamp(created_at, UTC).isoformat()
    job["started_at"] = datetime.fromtimestamp(started_at, UTC).isoformat()
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": "workflow_job"}
    return client.post("/webhook", content=body, headers=headers)


def _runner_vm(monkeypatch, labels: dict[str, str] | None) -> AsyncMock:
    by_name = AsyncMock(return_value=labels)
    by_job = AsyncMock(return_value=None)
    monkeypatch.setattr("runnerforge.handlers.get_vm_labels_by_name", by_name)
    monkeypatch.setattr("runnerforge.handlers.get_vm_labels_by_job", by_job)
    return by_job


def _readiness(caplog):
    return next(
        (r for r in caplog.records if r.message == "Runner registration outcome"),
        None,
    )


@pytest.mark.parametrize(
    ("vm_job", "admitted_at", "readiness"),
    [
        # Swapped VM admitted after the job was created: from its admission.
        ("1", 1_000_100, 50.0),
        # Idle leftover VM admitted long before: from the job's creation.
        ("1", 990_000, 150.0),
        # Its own VM: unchanged, and with the queued delivery lag.
        ("77678867086", 1_000_100, 50.0),
    ],
    ids=["swapped", "idle-leftover", "own"],
)
def test_readiness_runs_from_the_runner_vm(
    fixtures_dir, monkeypatch, fake_job_store, caplog, vm_job, admitted_at, readiness
):
    by_job = _runner_vm(
        monkeypatch,
        {
            "runner": "runnerforge",
            "installation_id": str(INSTALLATION_ID),
            "job_id": vm_job,
            "queued_received_at": str(admitted_at),
        },
    )
    with caplog.at_level(logging.INFO):
        response = _in_progress(
            fixtures_dir, created_at=1_000_000, started_at=1_000_150
        )

    assert response.status_code == 200
    record = _readiness(caplog)
    assert record.runner_readiness_seconds == readiness
    own = vm_job == "77678867086"
    assert hasattr(record, "github_queued_delivery_lag_seconds") is own
    by_job.assert_not_awaited()


def test_no_readiness_from_another_installations_vm(
    fixtures_dir, monkeypatch, fake_job_store, caplog
):
    _runner_vm(
        monkeypatch,
        {"runner": "runnerforge", "installation_id": "7", "queued_received_at": "1"},
    )
    with caplog.at_level(logging.INFO):
        response = _in_progress(
            fixtures_dir, created_at=1_000_000, started_at=1_000_150
        )

    assert response.status_code == 200
    assert _readiness(caplog) is None


def test_an_invalid_runner_name_is_not_a_500(fixtures_dir, fake_job_store, monkeypatch):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["runner_name"] = "runnerforge-Dev_Box"
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": "workflow_job"}
    assert client.post("/webhook", content=body, headers=headers).status_code == 200


# --- records for later events ---


@pytest.mark.parametrize(
    ("row", "recorded"),
    [
        (None, False),
        (RegistryEntry(INSTALLATION_ID, "pending", "active"), False),
        (RegistryEntry(INSTALLATION_ID, "suspended", "active", APPROVED_AT), True),
    ],
)
@pytest.mark.parametrize("action", ["in_progress", "completed"])
def test_later_events_are_recorded_only_for_tenants_ever_approved(
    fixtures_dir,
    fake_registry,
    fake_job_store,
    monkeypatch,
    caplog,
    row,
    recorded,
    action,
):
    if row is not None:
        fake_registry.rows[INSTALLATION_ID] = row
    handler = AsyncMock(return_value=None)
    monkeypatch.setattr(f"runnerforge.main.handle_{action}", handler)

    with caplog.at_level(logging.INFO):
        response = _post(fixtures_dir, action=action)

    assert response.status_code == 200
    handler.assert_awaited_once()
    assert fake_job_store.names() == ([action] if recorded else [])
    assert "Admission decision" not in _messages(caplog)
