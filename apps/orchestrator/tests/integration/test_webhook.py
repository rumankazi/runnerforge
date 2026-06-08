import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
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
def test_webhook_in_progress_event_does_not_call_github(
    fixtures_dir, monkeypatch, caplog
):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    body = json.dumps(payload).encode()

    # in_progress now looks up the VM to retrieve the queued trace context;
    # mock that so we don't hit GCE in this test.
    get_labels_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("runnerforge.handlers.get_vm_labels_by_job", get_labels_mock)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    assert any(
        "Time for runner to be picked up by github" in r.message for r in caplog.records
    )


@respx.mock
def test_webhook_in_progress_event_adds_span_link_when_queued_trace_present(
    fixtures_dir, monkeypatch, caplog
):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["started_at"] = str(
        datetime(2026, 5, 31, 10, 0, 30, tzinfo=timezone.utc)
    )
    payload["workflow_job"]["created_at"] = str(
        datetime(2026, 5, 31, 10, 0, 00, tzinfo=timezone.utc)
    )
    body = json.dumps(payload).encode()

    # Return labels with valid trace context — exercises the SpanContext + add_link path
    get_labels_mock = AsyncMock(
        return_value={
            "runner": "runnerforge",
            "job_id": "77678867086",
            "run_id": "26389905440",
            "run_attempt": "3",
            "repo": "rumankazi_runnerforge",
            "installation_id": "135399152",
            "queued_trace_id": "0123456789abcdef0123456789abcdef",  # 32 hex chars
            "queued_span_id": "0123456789abcdef",  # 16 hex chars
        }
    )
    monkeypatch.setattr("runnerforge.handlers.get_vm_labels_by_job", get_labels_mock)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    get_labels_mock.assert_awaited_once()
    # The time-to-runner metric still fires regardless of span-link presence
    log = next(
        (
            r
            for r in caplog.records
            if "Time for runner to be picked up by github" in r.message
        ),
        None,
    )
    assert log is not None
    assert log.time_to_runner_seconds == 30.0


@respx.mock
def test_webhook_in_progress_skips_time_log_when_started_at_missing(
    fixtures_dir, monkeypatch, caplog
):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["started_at"] = None  # in_progress without started_at
    body = json.dumps(payload).encode()

    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job", AsyncMock(return_value=None)
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    assert not any(
        "Time for runner to be picked up by github" in r.message for r in caplog.records
    )


@respx.mock
def test_webhook_completed_event_triggers_vm_deletion(
    fixtures_dir, monkeypatch, caplog
):
    # Use a copy of queued payload with action="completed" + conclusion="success"
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["runner_name"] = "test-runner-name"
    payload["workflow_job"]["runner_id"] = 1234
    body = json.dumps(payload).encode()
    # Mock delete_vm, to we don't hit real GCP

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

    delete_mock = AsyncMock()
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


@respx.mock
async def test_concurrent_webhooks_keep_per_request_context_isolated(
    fixtures_dir, monkeypatch, caplog
):
    monkeypatch.setenv("K_SERVICE", "runnerforge")
    # Two payloads with distinct job_id + repo
    payload_a = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload_a["workflow_job"]["id"] = 1001
    payload_a["repository"]["full_name"] = "alice/repo-a"
    body_a = json.dumps(payload_a).encode()

    payload_b = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload_b["workflow_job"]["id"] = 1002
    payload_b["repository"]["full_name"] = "bob/repo-b"
    body_b = json.dumps(payload_b).encode()

    # respx mocks: one access_tokens route, two registration-token routes (per repo)
    # See test_webhook_queued_event_triggers_github_auth_chain for the pattern
    # TODO — set up respx.post(...).mock(...) for the three URLs
    respx.post("https://api.github.com/app/installations/135399152/access_tokens").mock(
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
        "https://api.github.com/repos/alice/repo-a/actions/runners/registration-token"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_registration_token_a",
                "expires_at": "2026-05-30T11:00:00Z",
            },
        )
    )
    respx.post(
        "https://api.github.com/repos/bob/repo-b/actions/runners/registration-token"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_registration_token_b",
                "expires_at": "2026-05-30T11:00:00Z",
            },
        )
    )
    # Stop short of real GCP
    create_vm_mock = AsyncMock(return_value="op-test")
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_vm_mock)

    # Fire concurrently against an in-memory ASGI transport
    transport = httpx.ASGITransport(app=app)
    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response_a, response_b = await asyncio.gather(
                client.post(
                    "/webhook",
                    content=body_a,
                    headers={"X-Hub-Signature-256": _sign(body_a)},
                ),
                client.post(
                    "/webhook",
                    content=body_b,
                    headers={"X-Hub-Signature-256": _sign(body_b)},
                ),
            )

    assert response_a.status_code == 200
    assert response_b.status_code == 200

    # Isolation assertions
    records_a = [r for r in caplog.records if getattr(r, "job_id", None) == 1001]
    records_b = [r for r in caplog.records if getattr(r, "job_id", None) == 1002]

    assert records_a, "no log records tagged with A's job_id — handler may not have run"
    assert records_b
    assert all(getattr(r, "repo", None) == "alice/repo-a" for r in records_a)
    assert all(getattr(r, "repo", None) == "bob/repo-b" for r in records_b)
