"""Write points for the per-job record (ADR-018), end to end through /webhook."""

import hashlib
import hmac
import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from google.api_core.exceptions import Forbidden

from runnerforge.compute_client import OperationHandle
from runnerforge.job_store import CreateResult
from runnerforge.machine_policy import resolve_labels
from runnerforge.main import app

SECRET = b"test-secret"
client = TestClient(app)
# queued_job_payload.json: run 26389905440, job 77678867086, attempt 3.
KEY = "26389905440-77678867086-3"
INSTALLATION_ID = 135399152


def _headers(body: bytes) -> dict[str, str]:
    signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": signature, "X-GitHub-Event": "workflow_job"}


def _payload(fixtures_dir, action: str, **job_fields) -> bytes:
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = action
    payload["workflow_job"].update(job_fields)
    return json.dumps(payload).encode()


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


def _handle() -> OperationHandle:
    return OperationHandle(
        name="op-1",
        project="test-project",
        zone="europe-west4-a",
        spot=False,
        machine_type="e2-medium",
        image_version="0.2.0",
    )


def _queued_outcomes(caplog) -> list[str]:
    return [r.outcome for r in caplog.records if r.message == "Queued webhook outcome"]


# --- queued ---


@respx.mock
@pytest.mark.parametrize(
    "create_result", [CreateResult.CREATED, CreateResult.EXISTS_OPEN]
)
def test_queued_creates_record_and_writes_vm_submitted_decision(
    fixtures_dir, monkeypatch, fake_job_store, create_result
):
    """EXISTS_OPEN is a redelivery after an earlier failure: it must still
    get a VM, which is what the runbooks rely on."""
    fake_job_store.create_result = create_result
    _mock_github_tokens()
    monkeypatch.setattr(
        "runnerforge.handlers.create_vm", AsyncMock(return_value=_handle())
    )
    wait_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", wait_mock)
    body = _payload(fixtures_dir, "queued")

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    [(created_key, created_base)] = fake_job_store.created
    assert created_key == KEY
    assert created_base["installation_id"] == INSTALLATION_ID
    assert created_base["repo"] == "rumankazi/runnerforge"
    decision = fake_job_store.fact("decision")
    assert decision["key"] == KEY
    policy = resolve_labels(["self-hosted", "runnerforge"])
    assert decision["fact"] == {
        "kind": "vm_submitted",
        "machine_type": policy.machine_type,
        "spot": policy.spot,
    }
    # The watcher gets the record's key and full base fields for the vm fact.
    (handle,) = wait_mock.await_args.args
    assert handle.job_key == KEY
    assert handle.job_base == created_base


@respx.mock
def test_queued_skips_other_writes_when_store_failed(
    fixtures_dir, monkeypatch, fake_job_store
):
    """A failed or timed-out create: the VM still comes, but this request
    makes no more store calls, so a degraded store costs one bound."""
    fake_job_store.create_result = CreateResult.UNKNOWN
    _mock_github_tokens()
    create_vm_mock = AsyncMock(return_value=_handle())
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_vm_mock)
    wait_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", wait_mock)
    body = _payload(fixtures_dir, "queued")

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    create_vm_mock.assert_awaited_once()
    assert fake_job_store.facts == []
    # The watcher writes later, off the webhook path, with its own bound.
    (handle,) = wait_mock.await_args.args
    assert handle.job_key == KEY


@respx.mock
def test_queued_skips_vm_when_record_already_closed(
    fixtures_dir, monkeypatch, fake_job_store, caplog
):
    """`completed` arrived first: a VM now would only sit idle."""
    fake_job_store.create_result = CreateResult.EXISTS_CLOSED
    create_vm_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_vm_mock)
    token_route = respx.post(
        f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
    )
    body = _payload(fixtures_dir, "queued")

    with caplog.at_level(logging.INFO):
        response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    create_vm_mock.assert_not_awaited()
    assert not token_route.called
    assert _queued_outcomes(caplog) == ["already_completed"]
    # A decision write would reopen the closed record.
    assert fake_job_store.facts == []


@respx.mock
def test_queued_writes_already_exists_decision_without_watcher(
    fixtures_dir, monkeypatch, fake_job_store
):
    _mock_github_tokens()
    monkeypatch.setattr("runnerforge.handlers.create_vm", AsyncMock(return_value=None))
    wait_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", wait_mock)
    body = _payload(fixtures_dir, "queued")

    client.post("/webhook", content=body, headers=_headers(body))

    assert fake_job_store.fact("decision")["fact"]["kind"] == "already_exists"
    wait_mock.assert_not_awaited()


def test_queued_policy_rejection_is_recorded(fixtures_dir, fake_job_store):
    body = _payload(
        fixtures_dir, "queued", labels=["self-hosted", "runnerforge", "gigantic"]
    )

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    assert fake_job_store.fact("decision")["fact"] == {
        "kind": "policy_rejected",
        "reason": "unknown_token",
        "offending_tokens": ["gigantic"],
    }


@respx.mock
def test_queued_token_failure_records_errored_and_still_fails(
    fixtures_dir, fake_job_store, caplog
):
    respx.post(
        f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
    ).mock(return_value=httpx.Response(500, json={"message": "boom"}))
    failing_client = TestClient(app, raise_server_exceptions=False)
    body = _payload(fixtures_dir, "queued")

    with caplog.at_level(logging.INFO):
        response = failing_client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 500
    assert fake_job_store.fact("errored")["fact"] == {
        "stage": "token",
        "error_class": "HTTPStatusError",
    }
    assert "decision" not in fake_job_store.names()
    # Still no outcome line: the lost-webhook alert must keep counting it.
    assert _queued_outcomes(caplog) == []


@respx.mock
def test_queued_gce_insert_failure_records_errored_and_still_fails(
    fixtures_dir, monkeypatch, fake_job_store, caplog
):
    _mock_github_tokens()
    monkeypatch.setattr(
        "runnerforge.handlers.create_vm",
        AsyncMock(side_effect=Forbidden("QUOTA_EXCEEDED")),
    )
    failing_client = TestClient(app, raise_server_exceptions=False)
    body = _payload(fixtures_dir, "queued")

    with caplog.at_level(logging.INFO):
        response = failing_client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 500
    assert fake_job_store.fact("errored")["fact"] == {
        "stage": "gce_insert",
        "error_class": "Forbidden",
    }
    assert "decision" not in fake_job_store.names()
    assert _queued_outcomes(caplog) == []


# --- in_progress / completed ---


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("in_progress", {"runner_name": "runnerforge-1-2-1"}),
        ("completed", {"conclusion": "success", "runner_name": "runnerforge-1-2-1"}),
    ],
)
def test_fact_is_written_before_handler(
    fixtures_dir, monkeypatch, fake_job_store, action, expected
):
    """A failing GCE lookup in the handler must not lose the fact."""
    handler = AsyncMock(side_effect=RuntimeError("GCE lookup failed"))
    monkeypatch.setattr(f"runnerforge.main.handle_{action}", handler)
    failing_client = TestClient(app, raise_server_exceptions=False)
    body = _payload(
        fixtures_dir, action, runner_name="runnerforge-1-2-1", conclusion="success"
    )

    response = failing_client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 500
    fact = fake_job_store.fact(action)
    assert fact["key"] == KEY
    assert fact["fact"] == expected
    assert fact["base"]["queued_at"].isoformat() == "2026-05-25T08:10:58+00:00"


def test_completed_fact_closes_record_and_tags_preemption_check(
    fixtures_dir, monkeypatch, fake_job_store
):
    from runnerforge.compute_client import PreemptionCheckRequest

    monkeypatch.setattr(
        "runnerforge.main.handle_completed",
        AsyncMock(
            return_value=PreemptionCheckRequest(
                vm_name="runnerforge-1-2-1", job_id=77678867086, conclusion="failure"
            )
        ),
    )
    cross_check = AsyncMock()
    monkeypatch.setattr("runnerforge.main.cross_check_preemption", cross_check)
    body = _payload(
        fixtures_dir, "completed", runner_name="runnerforge-1-2-1", conclusion="failure"
    )

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    assert fake_job_store.fact("completed")["fact"] == {
        "conclusion": "failure",
        "runner_name": "runnerforge-1-2-1",
    }
    (request,) = cross_check.await_args.args
    assert request.job_key == KEY
    assert request.job_base["installation_id"] == INSTALLATION_ID


@pytest.mark.parametrize("action", ["in_progress", "completed"])
def test_installation_not_allowed_gets_no_record(
    fixtures_dir, monkeypatch, fake_job_store, action
):
    """Once the App is public, unapproved installations must not grow the
    store; their in_progress/completed events still reach the handlers."""
    monkeypatch.setattr("runnerforge.main.ALLOWED_INSTALLATION_IDS", frozenset())
    handler = AsyncMock(return_value=None)
    monkeypatch.setattr(f"runnerforge.main.handle_{action}", handler)
    body = _payload(fixtures_dir, action, runner_name="runnerforge-1-2-1")

    response = client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 200
    handler.assert_awaited_once()
    assert fake_job_store.facts == []
