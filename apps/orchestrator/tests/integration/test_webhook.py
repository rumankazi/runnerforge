import hashlib
import hmac
import json
import logging

import httpx
import respx
from fastapi.testclient import TestClient
from runnerforge.main import app

SECRET = b"test-secret"
client = TestClient(app)


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


@respx.mock
def test_webhook_queued_event_triggers_github_auth_chain(fixtures_dir, caplog):
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
def test_webhook_completed_event_does_not_call_github(fixtures_dir, caplog):
    # Use a copy of queued payload with action="completed" + conclusion="success"
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "success"
    body = json.dumps(payload).encode()
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    assert any("Would delete VM" in r.message for r in caplog.records)


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
