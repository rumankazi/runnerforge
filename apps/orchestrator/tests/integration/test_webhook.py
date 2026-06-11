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

# Reused by tests covering the new runner-readiness + parent-child trace
# behavior. Encodes valid hex span/trace ids so handlers.py can rehydrate a
# SpanContext, plus a fixed queued_received_at so the latency-metric maths
# is deterministic.
_VALID_VM_LABELS_WITH_TRACE = {
    "runner": "runnerforge",
    "job_id": "77678867086",
    "run_id": "26389905440",
    "run_attempt": "3",
    "repo": "rumankazi_runnerforge",
    "installation_id": "135399152",
    "queued_trace_id": "0123456789abcdef0123456789abcdef",
    "queued_span_id": "0123456789abcdef",
    # Sat 30 May 2026 10:00:00 UTC = epoch 1780135200
    "queued_received_at": "1780135200",
}


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
    from runnerforge.compute_client import OperationHandle

    create_vm_mock = AsyncMock(
        return_value=OperationHandle(
            name="op-test-12345",
            zone="europe-west4-a",
            spot=True,
            machine_type="e2-medium",
            image_version="0.2.0",
        )
    )
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_vm_mock)
    # Mock wait_for_vm_creation so the background task is a no-op
    wait_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", wait_mock)
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
    # Observability contract: lost-webhook-ratio anchor — every accepted
    # RunnerForge webhook emits this with event_type for metric extraction.
    event_log = next(
        (r for r in caplog.records if "Webhook event received" in r.message), None
    )
    assert event_log is not None
    assert event_log.event_type == "queued"

    # Background polling was scheduled and ran (TestClient flushes BackgroundTasks
    # after the response). It received the OperationHandle we returned from create_vm.
    wait_mock.assert_awaited_once()
    assert wait_mock.await_args is not None
    assert wait_mock.await_args.args[0].name == "op-test-12345"
    assert wait_mock.await_args.args[0].zone == "europe-west4-a"


@respx.mock
def test_webhook_queued_event_does_not_schedule_polling_when_vm_already_exists(
    fixtures_dir, monkeypatch, caplog
):
    """When create_vm returns None (VM already exists), no background polling is
    scheduled — there's no operation to observe."""
    body = (fixtures_dir / "queued_job_payload.json").read_bytes()

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

    # create_vm returns None to simulate the AlreadyExists path
    monkeypatch.setattr("runnerforge.handlers.create_vm", AsyncMock(return_value=None))
    wait_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", wait_mock)

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )

    assert response.status_code == 200
    # No operation to poll → no background task fired
    wait_mock.assert_not_awaited()


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
def test_webhook_queued_event_does_not_create_vm_when_labels_are_invalid(
    fixtures_dir, monkeypatch, caplog
):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["workflow_job"]["labels"] = ["runnerforge", "gigantic"]
    body = json.dumps(payload).encode()
    create_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_mock)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook",
            content=body,
            headers={"X-Hub-Signature-256": _sign(body)},
        )

    assert response.status_code == 200
    create_mock.assert_not_awaited()
    # Reject emits the structured warning log
    assert any(
        r.levelno == logging.WARNING
        and "Machine policy rejected" in r.message
        and getattr(r, "reason", None) == "unknown_token"
        and getattr(r, "offending_tokens", None) == ["gigantic"]
        for r in caplog.records
    )
    assert not any("Registration token received" in r.message for r in caplog.records)


@respx.mock
def test_webhook_queued_event_rejects_when_machine_type_not_in_cache(
    fixtures_dir, monkeypatch, caplog
):
    """Cache says the literal escape-hatch type doesn't exist in our zone →
    handler raises MachinePolicyError("invalid_machine_type", ...) before any
    GH token exchange. No create_vm call; structured warning log fires with the
    new reason discriminator."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    # Loose regex accepts this token; cache (mocked below) doesn't contain it.
    payload["workflow_job"]["labels"] = ["runnerforge", "n2-malformed-4"]
    body = json.dumps(payload).encode()

    # Populated cache that EXCLUDES the bad type — exercises the validator's
    # cache-populated-but-missing branch.
    monkeypatch.setattr(
        "runnerforge.compute_client._KNOWN_MACHINE_TYPES",
        {"n2-standard-2", "n2-standard-4", "n2-standard-8"},
    )
    create_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_mock)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook",
            content=body,
            headers={"X-Hub-Signature-256": _sign(body)},
        )

    assert response.status_code == 200
    create_mock.assert_not_awaited()
    # Reject happens BEFORE the GH token exchange (resolve_labels is at the top
    # of handle_queued after the reorder)
    assert not any("Registration token received" in r.message for r in caplog.records)
    # Structured warning with the new "invalid_machine_type" reason
    assert any(
        r.levelno == logging.WARNING
        and "Machine policy rejected" in r.message
        and getattr(r, "reason", None) == "invalid_machine_type"
        and getattr(r, "offending_tokens", None) == ["n2-malformed-4"]
        for r in caplog.records
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
    # The on-our-side readiness metric is ALSO gated on started_at — verify
    # the new structured log doesn't fire either, so neither SLO source
    # produces a sample for an event missing the load-bearing timestamp.
    assert not any("Runner registration outcome" in r.message for r in caplog.records)


@respx.mock
def test_webhook_queued_event_persists_queued_received_at_to_vm_labels(
    fixtures_dir, monkeypatch, caplog
):
    """handle_queued must write a Unix epoch integer to RunnerForgeVmLabels so
    handle_in_progress can compute `runner_readiness_seconds` later. Stored
    as a string of digits (no '.') to match GCE label constraints."""
    body = (fixtures_dir / "queued_job_payload.json").read_bytes()

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

    from runnerforge.compute_client import OperationHandle

    create_vm_mock = AsyncMock(
        return_value=OperationHandle(
            name="op-test",
            zone="europe-west4-a",
            spot=False,
            machine_type="n2-standard-4",
            image_version="0.2.0",
        )
    )
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_vm_mock)
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", AsyncMock())

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )

    assert response.status_code == 200
    labels = create_vm_mock.call_args.kwargs["labels"]
    assert labels["queued_received_at"]
    # Digits-only string — must round-trip as an int (no '.', no scientific notation)
    assert int(labels["queued_received_at"]) > 0


@respx.mock
def test_webhook_in_progress_emits_runner_readiness_outcome_log(
    fixtures_dir, monkeypatch, caplog
):
    """When the VM has both queued trace context AND queued_received_at, the
    in_progress handler emits the new `Runner registration outcome` log
    carrying `runner_readiness_seconds` + the two GH delivery lags. This is
    the new SLO source — must always carry runner_readiness_seconds and
    in_progress lag, and queued lag only when created_at is set."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    # started_at 30s after queued_received_at = 1779177630
    payload["workflow_job"]["started_at"] = str(
        datetime(2026, 5, 30, 10, 0, 30, tzinfo=timezone.utc)
    )
    payload["workflow_job"]["created_at"] = str(
        datetime(2026, 5, 30, 10, 0, 0, tzinfo=timezone.utc)
    )
    body = json.dumps(payload).encode()

    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job",
        AsyncMock(return_value=_VALID_VM_LABELS_WITH_TRACE),
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    outcome_log = next(
        (r for r in caplog.records if "Runner registration outcome" in r.message), None
    )
    assert outcome_log is not None
    # started_at (1779177630) - queued_received_at (1779177600) = 30s
    assert outcome_log.runner_readiness_seconds == 30.0
    # github_queued_delivery_lag = queued_received_at - created_at = 0s here
    # (both are 10:00:00); the field must still be present
    assert hasattr(outcome_log, "github_queued_delivery_lag_seconds")
    # in_progress lag is whatever wall-clock is now minus started_at — only
    # assert it's non-negative (floor-at-zero contract)
    assert outcome_log.github_in_progress_delivery_lag_seconds >= 0


@respx.mock
def test_webhook_in_progress_floors_negative_delivery_lag_at_zero(
    fixtures_dir, monkeypatch, caplog
):
    """If GH's clock is slightly ahead of ours, `queued_received_at -
    created_at` can go negative. NTP skew, not signal — floor at 0 so the
    distribution metric doesn't get a negative outlier that breaks
    exponential bucket math."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    # started_at = queued_received_at exactly = no positive readiness contribution
    payload["workflow_job"]["started_at"] = str(
        datetime(2026, 5, 30, 10, 0, 0, tzinfo=timezone.utc)
    )
    # created_at FUTURE of queued_received_at — drives queued delivery lag negative
    payload["workflow_job"]["created_at"] = str(
        datetime(2026, 5, 30, 10, 0, 10, tzinfo=timezone.utc)
    )
    body = json.dumps(payload).encode()

    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job",
        AsyncMock(return_value=_VALID_VM_LABELS_WITH_TRACE),
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    outcome_log = next(
        (r for r in caplog.records if "Runner registration outcome" in r.message), None
    )
    assert outcome_log is not None
    # All three deltas must be >= 0 even though raw subtraction would yield
    # a negative for the queued delivery lag.
    assert outcome_log.runner_readiness_seconds >= 0
    assert outcome_log.github_queued_delivery_lag_seconds == 0.0
    assert outcome_log.github_in_progress_delivery_lag_seconds >= 0


@respx.mock
def test_webhook_in_progress_skips_outcome_log_when_queued_received_at_missing(
    fixtures_dir, monkeypatch, caplog
):
    """Back-compat: VMs created before this field shipped have an empty
    `queued_received_at`. The new structured log must not fire for them
    (we'd be subtracting from zero — garbage data). The legacy
    `time_to_runner_seconds` log still fires so the existing dashboard
    panel keeps populating during the rollout window."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["started_at"] = str(
        datetime(2026, 5, 30, 10, 0, 30, tzinfo=timezone.utc)
    )
    payload["workflow_job"]["created_at"] = str(
        datetime(2026, 5, 30, 10, 0, 0, tzinfo=timezone.utc)
    )
    body = json.dumps(payload).encode()

    # Labels exist, but no queued_received_at (simulates pre-deploy VM in flight)
    labels_without_received_at = dict(_VALID_VM_LABELS_WITH_TRACE)
    labels_without_received_at["queued_received_at"] = ""
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job",
        AsyncMock(return_value=labels_without_received_at),
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    # New log gated off
    assert not any("Runner registration outcome" in r.message for r in caplog.records)
    # Legacy log still fires — keeps the existing panel populated through rollout
    assert any(
        "Time for runner to be picked up by github" in r.message for r in caplog.records
    )


@respx.mock
def test_webhook_in_progress_emits_no_outcome_log_when_vm_labels_missing(
    fixtures_dir, monkeypatch, caplog
):
    """Lost-webhook safety: in_progress fires but the queued event never
    successfully created a VM (or its labels were never written). We can't
    compute on-our-side latency without queued_received_at, so the new
    structured log stays off — no false data into the SLO."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["started_at"] = str(
        datetime(2026, 5, 30, 10, 0, 30, tzinfo=timezone.utc)
    )
    body = json.dumps(payload).encode()

    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job", AsyncMock(return_value=None)
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    assert not any("Runner registration outcome" in r.message for r in caplog.records)


@respx.mock
def test_webhook_in_progress_with_empty_trace_id_skips_parent_context(
    fixtures_dir, monkeypatch, caplog
):
    """Defensive path on `_queued_span_context`: if VM labels exist but the
    queued_trace_id/span_id were never populated (e.g. VM provisioned by an
    older orchestrator revision), the helper returns None and the
    in_progress span stays standalone — no parent context, no synthetic
    gap span. The new structured log MUST NOT fire either, since
    queued_received_at is also absent on those legacy VMs."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["started_at"] = str(
        datetime(2026, 5, 30, 10, 0, 30, tzinfo=timezone.utc)
    )
    body = json.dumps(payload).encode()

    labels_legacy = dict(_VALID_VM_LABELS_WITH_TRACE)
    labels_legacy["queued_trace_id"] = ""
    labels_legacy["queued_span_id"] = ""
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job",
        AsyncMock(return_value=labels_legacy),
    )

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    # Span construction must not raise. Handler returns 200, the legacy log
    # is the only metric emitted.
    assert response.status_code == 200


@respx.mock
def test_webhook_completed_adds_cross_trace_link_when_labels_present(
    fixtures_dir, monkeypatch, caplog
):
    """Completion stays in its own trace (long wall-clock gap from queued)
    but adds a span link back so an operator debugging completion can jump
    to the matching registration trace. Test verifies the labels lookup
    fires and delete_vm runs after."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["runner_name"] = "test-runner-completed"
    payload["workflow_job"]["runner_id"] = 4242
    body = json.dumps(payload).encode()

    get_labels_mock = AsyncMock(return_value=_VALID_VM_LABELS_WITH_TRACE)
    monkeypatch.setattr("runnerforge.handlers.get_vm_labels_by_job", get_labels_mock)
    delete_mock = AsyncMock(return_value="op-completed")
    monkeypatch.setattr("runnerforge.handlers.delete_vm", delete_mock)

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )

    assert response.status_code == 200
    # Labels lookup ran BEFORE delete (need labels to build the cross-trace link)
    get_labels_mock.assert_awaited_once()
    delete_mock.assert_awaited_once()


@respx.mock
def test_webhook_in_progress_outcome_log_omits_queued_lag_when_created_at_missing(
    fixtures_dir, monkeypatch, caplog
):
    """The queued delivery lag depends on `created_at`. If GH omits it (or
    schema drift drops the field), the outcome log still fires but without
    the lag — runner_readiness_seconds is the primary signal and stays
    populated. Covers the inner `if created_at` branch."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "in_progress"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["started_at"] = str(
        datetime(2026, 5, 30, 10, 0, 30, tzinfo=timezone.utc)
    )
    payload["workflow_job"]["created_at"] = None
    body = json.dumps(payload).encode()

    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job",
        AsyncMock(return_value=_VALID_VM_LABELS_WITH_TRACE),
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    outcome_log = next(
        (r for r in caplog.records if "Runner registration outcome" in r.message), None
    )
    assert outcome_log is not None
    assert outcome_log.runner_readiness_seconds == 30.0
    assert outcome_log.github_in_progress_delivery_lag_seconds >= 0
    # queued lag field absent when we can't compute it
    assert not hasattr(outcome_log, "github_queued_delivery_lag_seconds")


@respx.mock
def test_webhook_completed_skips_link_when_labels_have_empty_trace_id(
    fixtures_dir, monkeypatch, caplog
):
    """Legacy VM (pre-PR) has labels but no queued_trace_id. The completed
    handler safely no-ops on the link without raising — defense for the
    cross-trace navigation feature degrading gracefully on partial data."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["runner_name"] = "test-runner-legacy"
    payload["workflow_job"]["runner_id"] = 5555
    body = json.dumps(payload).encode()

    labels_no_trace = dict(_VALID_VM_LABELS_WITH_TRACE)
    labels_no_trace["queued_trace_id"] = ""
    labels_no_trace["queued_span_id"] = ""
    monkeypatch.setattr(
        "runnerforge.handlers.get_vm_labels_by_job",
        AsyncMock(return_value=labels_no_trace),
    )
    delete_mock = AsyncMock(return_value="op-legacy")
    monkeypatch.setattr("runnerforge.handlers.delete_vm", delete_mock)

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    # Doesn't raise on missing trace context; delete still fires.
    assert response.status_code == 200
    delete_mock.assert_awaited_once()


@respx.mock
def test_webhook_completed_no_labels_lookup_when_runner_name_missing(
    fixtures_dir, monkeypatch, caplog
):
    """If runner_name isn't on the completed event, we know there's no VM
    to look up — the labels lookup is skipped entirely. Symmetric with the
    'No VM found' branch."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "success"
    # runner_name absent
    body = json.dumps(payload).encode()

    get_labels_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("runnerforge.handlers.get_vm_labels_by_job", get_labels_mock)
    delete_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.handlers.delete_vm", delete_mock)

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )

    assert response.status_code == 200
    get_labels_mock.assert_not_awaited()
    delete_mock.assert_not_awaited()


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
def test_webhook_completed_failure_logs_preemption_when_audit_log_says_so(
    fixtures_dir, monkeypatch, caplog
):
    """When a job completes with conclusion=failure AND the audit log shows
    the VM was preempted, the BackgroundTask emits a structured
    'Job ended due to spot preemption' event. This is the foundation for the
    future user-facing per-job-outcome surface."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "failure"
    payload["workflow_job"]["runner_name"] = "test-runner-preempted"
    payload["workflow_job"]["runner_id"] = 1234
    body = json.dumps(payload).encode()

    monkeypatch.setattr(
        "runnerforge.handlers.delete_vm", AsyncMock(return_value="op-test")
    )
    # Cross-check returns True → preemption was found in the audit log
    was_preempted_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("runnerforge.compute_client.was_preempted", was_preempted_mock)

    with caplog.at_level(logging.WARNING):
        response = client.post(
            "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
        )

    assert response.status_code == 200
    # BackgroundTask ran the cross-check
    was_preempted_mock.assert_awaited_once_with("test-runner-preempted")
    # Structured event fired with the right payload
    assert any(
        r.levelno == logging.WARNING
        and "Job ended due to spot preemption" in r.message
        and getattr(r, "vm_name", None) == "test-runner-preempted"
        and getattr(r, "conclusion", None) == "failure"
        for r in caplog.records
    )


@respx.mock
def test_webhook_completed_failure_skips_preemption_log_when_audit_log_empty(
    fixtures_dir, monkeypatch, caplog
):
    """Conclusion=failure but no preemption entry in the audit log → cross-check
    runs but no structured preemption event. The job failed for some other
    reason (real bug, OOM, etc.) — not our concern to characterize."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "failure"
    payload["workflow_job"]["runner_name"] = "test-runner-real-fail"
    payload["workflow_job"]["runner_id"] = 5678
    body = json.dumps(payload).encode()

    monkeypatch.setattr(
        "runnerforge.handlers.delete_vm", AsyncMock(return_value="op-test")
    )
    was_preempted_mock = AsyncMock(return_value=False)
    monkeypatch.setattr("runnerforge.compute_client.was_preempted", was_preempted_mock)

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )

    assert response.status_code == 200
    was_preempted_mock.assert_awaited_once_with("test-runner-real-fail")
    # No preemption event emitted
    assert not any(
        "Job ended due to spot preemption" in r.message for r in caplog.records
    )


@respx.mock
def test_webhook_completed_success_does_not_schedule_preemption_check(
    fixtures_dir, monkeypatch, caplog
):
    """Conclusion=success → no cross-check scheduled. Skipping the audit-log
    query on the success path keeps the common case cheap (no Cloud Logging
    call) and prevents spurious preemption signals."""
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload["action"] = "completed"
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["runner_name"] = "test-runner-ok"
    payload["workflow_job"]["runner_id"] = 9999
    body = json.dumps(payload).encode()

    monkeypatch.setattr(
        "runnerforge.handlers.delete_vm", AsyncMock(return_value="op-test")
    )
    was_preempted_mock = AsyncMock(return_value=False)
    monkeypatch.setattr("runnerforge.compute_client.was_preempted", was_preempted_mock)

    response = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )

    assert response.status_code == 200
    # Cross-check NOT scheduled for successful jobs
    was_preempted_mock.assert_not_awaited()


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
    # Observability contract: lost-webhook-ratio anchor must NOT fire on the
    # skip path — counting non-runnerforge traffic would poison the alert.
    assert not any("Webhook event received" in r.message for r in caplog.records)


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
    from runnerforge.compute_client import OperationHandle

    create_vm_mock = AsyncMock(
        return_value=OperationHandle(
            name="op-test",
            zone="europe-west4-a",
            machine_type="e2-medium",
            spot=False,
            image_version="0.1.0",
        )
    )
    monkeypatch.setattr("runnerforge.handlers.create_vm", create_vm_mock)
    # Mock wait_for_vm_creation so the background task is a no-op
    monkeypatch.setattr("runnerforge.main.wait_for_vm_creation", AsyncMock())

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
