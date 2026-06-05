import hashlib
import hmac
import json
import logging
from unittest.mock import AsyncMock

import httpx
import respx
from fastapi.testclient import TestClient
from runnerforge.main import app

SECRET = b"test-secret"
client = TestClient(app)


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


@respx.mock
def test_webhook_queued_event_triggers_github_auth_chain(
    fixtures_dir, monkeypatch, caplog
):
    body = (fixtures_dir / "queued_job_payload.json").read_bytes()

    install_route = respx.post(
        "https://api.github.com/app/installations/135399152/access_tokens"
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
    reg_route = respx.post(
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

    # Mock create_vm so we don't hit real GCP
    create_vm_mock = AsyncMock(return_value="op-test-12345")
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_vm_mock)
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    assert install_route.called
    assert reg_route.called
    # Bearer to the install endpoint is a JWT; bearer to reg is the install token
    assert (
        reg_route.calls.last.request.headers["Authorization"]
        == "Bearer ghs_installation_token"
    )
    call_kwargs = create_vm_mock.call_args.kwargs
    assert "startup-script" in call_kwargs["metadata"]
    assert "#!/bin/bash" in call_kwargs["metadata"]["startup-script"]  # smoke check

    # Observability contract: handler logs key progress events
    assert any("Registration token received" in r.message for r in caplog.records)
    assert any("Creating VM" in r.message for r in caplog.records)


@respx.mock
def test_webhook_rejects_invalid_signature_without_calling_github(fixtures_dir, caplog):
    body = (fixtures_dir / "queued_job_payload.json").read_bytes()

    install_route = respx.post(
        "https://api.github.com/app/installations/135399152/access_tokens"
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
    reg_route = respx.post(
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

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": "wrong-signature"}
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid signature provided"
    assert not install_route.called
    assert not reg_route.called
    assert any(
        record.levelno == logging.WARNING
        and "signature validation failed" in record.message
        for record in caplog.records
    )


@respx.mock
def test_webhook_in_progress_event_does_not_call_github(fixtures_dir, caplog):
    # Use a copy of queued payload with action="completed" + conclusion="success"
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    body = json.dumps(payload).encode()
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    assert any("picked up by a runner" in r.message for r in caplog.records)


@respx.mock
def test_webhook_completed_event_triggers_vm_deletion(
    fixtures_dir, monkeypatch, caplog
):
    # Use a copy of queued payload with action="completed" + conclusion="success"
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "success"
    body = json.dumps(payload).encode()
    # Mock delete_vm, to we don't hit real GCP
    find_mock = AsyncMock(return_value=["runnerforge-77678867086"])
    monkeypatch.setattr("runnerforge.handlers.find_vms_by_job_id", find_mock)

    delete_mock = AsyncMock(return_value="op-test-delete")
    monkeypatch.setattr("runnerforge.handlers.delete_vm", delete_mock)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    assert any("VM deletion submitted" in r.message for r in caplog.records)


@respx.mock
def test_webhook_completed_event_logs_when_no_vm_found(
    fixtures_dir, monkeypatch, caplog
):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "success"
    body = json.dumps(payload).encode()

    find_mock = AsyncMock(return_value=[])  # ← no VMs found
    delete_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.handlers.find_vms_by_job_id", find_mock)
    monkeypatch.setattr("runnerforge.handlers.delete_vm", delete_mock)

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )

    assert response.status_code == 200
    delete_mock.assert_not_awaited()
    assert any(
        r.levelno == logging.INFO and "No VM found" in r.message for r in caplog.records
    )


@respx.mock
def test_webhook_unknown_action_logs_warning(fixtures_dir, caplog):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "deleted"  # not in our match arms
    body = json.dumps(payload).encode()
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )
    assert response.status_code == 200
    assert any(
        r.levelno == logging.WARNING and "ignoring unknown action" in r.message.lower()
        for r in caplog.records
    )


@respx.mock
def test_webhook_without_runnerforge_label_skips_processing(fixtures_dir, caplog):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    workflow_job = payload["workflow_job"]
    workflow_job["labels"] = ["not-runnerforge-label"]  # not in our match arms
    body = json.dumps(payload).encode()
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )
    assert response.status_code == 200
    assert any(
        r.levelno == logging.INFO
        and "skipping! not a runnerforge request" in r.message.lower()
        for r in caplog.records
    )


def test_webhook_with_malformed_json_body():
    # malformed json body
    body = b"not a valid json"
    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Failed to load request body"
