import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.api_core.exceptions import AlreadyExists, GoogleAPIError
from google.auth.exceptions import DefaultCredentialsError
from runnerforge import compute_client
from runnerforge.compute_client import (
    create_vm,
    delete_vm,
    find_vms_by_job_id,
    get_vm_labels_by_job,
    list_runnerforge_vms,
)
from runnerforge.config import GCP_PROJECT_ID, RUNNER_VM_SA_EMAIL
from runnerforge.models import RunnerForgeVmLabels, VmInfo


def test_create_vm_builds_correct_instance(monkeypatch, caplog):
    mock_client = MagicMock()
    mock_client.insert.return_value.name = "op-abc-123"
    monkeypatch.setattr(
        "runnerforge.compute_client._compute_client",
        mock_client,
    )
    with caplog.at_level(logging.INFO):
        op_handle = asyncio.run(
            create_vm(
                instance_name="test-create-vm",
                machine_type="e2-micro",
                labels={"runner": "runnerforge", "job_id": "42"},
                metadata={
                    "registration-token": "reg-token",
                    "repo-url": "https://github.com/foo/bar",
                    "runner-labels": "runnerforge, medium",
                },
            )
        )

    assert op_handle is not None
    assert op_handle.name == "op-abc-123"
    assert op_handle.zone == "europe-west4-a"
    mock_client.insert.assert_called_once()
    request = mock_client.insert.call_args.kwargs["request"]
    assert request.project == "test-project"  # from conftest env
    assert request.zone == "europe-west4-a"
    assert request.instance_resource.name == "test-create-vm"
    assert request.instance_resource.labels == {"runner": "runnerforge", "job_id": "42"}
    assert len(request.instance_resource.disks) == 2  # boot and data disks
    assert request.instance_resource.disks[0].boot is True
    assert request.instance_resource.disks[1].boot is False
    metadata_items = {
        item.key: item.value for item in request.instance_resource.metadata.items
    }
    assert metadata_items == {
        "registration-token": "reg-token",
        "repo-url": "https://github.com/foo/bar",
        "runner-labels": "runnerforge, medium",
    }

    # Observability contract: structured log on success
    log = next(
        (r for r in caplog.records if "Submitted VM creation" in r.message), None
    )
    assert log is not None
    assert log.vm_name == "test-create-vm"
    assert log.zone == "europe-west4-a"
    assert log.operation_id == "op-abc-123"


def test_delete_vm_deletes_instance(monkeypatch, caplog):
    mock_client = MagicMock()
    mock_client.delete.return_value.name = "op-abc-123"
    monkeypatch.setattr("runnerforge.compute_client._compute_client", mock_client)

    with caplog.at_level(logging.INFO):
        op_id = asyncio.run(
            delete_vm(
                instance_name="test-delete-vm",
            )
        )

    assert op_id == "op-abc-123"
    mock_client.delete.assert_called_once()
    kwargs = mock_client.delete.call_args.kwargs
    assert kwargs["project"] == "test-project"  # from conftest env
    assert kwargs["zone"] == "europe-west4-a"  # from conftest env
    assert kwargs["instance"] == "test-delete-vm"

    # Observability contract: structured log on success
    log = next(
        (r for r in caplog.records if "Submitted VM deletion" in r.message), None
    )
    assert log is not None
    assert log.vm_name == "test-delete-vm"
    assert log.zone == "europe-west4-a"
    assert log.operation_id == "op-abc-123"


def test_find_vms_by_job_id(monkeypatch, caplog):
    mock_client = MagicMock()
    mock_vm = MagicMock()
    mock_vm.name = "runnerforge-12"
    mock_client.list.return_value = [mock_vm]
    monkeypatch.setattr("runnerforge.compute_client._compute_client", mock_client)
    with caplog.at_level(logging.INFO):
        instances = asyncio.run(
            find_vms_by_job_id(job_id="12", run_id="23", run_attempt="1")
        )
    mock_client.list.assert_called_once()
    request = mock_client.list.call_args.kwargs["request"]
    assert request.zone == "europe-west4-a"
    assert request.project == "test-project"
    assert (
        request.filter
        == "labels.job_id=12 AND labels.run_id=23 AND labels.run_attempt=1"
    )
    assert instances == ["runnerforge-12"]

    # Observability contract: lookup logs job_id + match_count
    log = next((r for r in caplog.records if "VM lookup by job_id" in r.message), None)
    assert log is not None
    assert log.job_id == "12"
    assert log.run_id == "23"
    assert log.run_attempt == "1"
    assert log.match_count == 1


def test_create_vm_propagates_gce_errors(monkeypatch):
    mock_client = MagicMock()
    mock_client.insert.side_effect = GoogleAPIError("simulated quota exceeded")
    monkeypatch.setattr(
        "runnerforge.compute_client._compute_client",
        mock_client,
    )

    with pytest.raises(GoogleAPIError):
        asyncio.run(
            create_vm(
                instance_name="x",
                machine_type="e2-micro",
                labels={"runner": "runnerforge"},
            )
        )


def test_find_vms_by_job_id_returns_empty_when_no_match(monkeypatch, caplog):
    mock_client = MagicMock()
    mock_client.list.return_value = []
    monkeypatch.setattr("runnerforge.compute_client._compute_client", mock_client)

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            find_vms_by_job_id(job_id="nonexistent", run_id="NA", run_attempt="NA")
        )

    assert result == []
    mock_client.list.assert_called_once()

    # Observability contract: empty-result case still logs (with match_count=0)
    log = next((r for r in caplog.records if "VM lookup by job_id" in r.message), None)
    assert log is not None
    assert log.job_id == "nonexistent"
    assert log.match_count == 0


def test_get_vm_labels_by_job_returns_empty_when_no_match(monkeypatch, caplog):
    mock_client = MagicMock()
    mock_client.list.return_value = []
    monkeypatch.setattr("runnerforge.compute_client._compute_client", mock_client)

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            get_vm_labels_by_job(job_id="nonexistent", run_id="NA", run_attempt="NA")
        )

    assert result is None
    mock_client.list.assert_called_once()

    # Observability contract: empty-result case still logs (with match_count=0)
    log = next((r for r in caplog.records if "VM lookup by job_id" in r.message), None)
    assert log is None


def test_get_vm_labels_by_job_returns_labels_when_matched(monkeypatch, caplog):
    mock_client = MagicMock()

    mock_vm = MagicMock()
    mock_vm.name = "runnerforge-12"
    mock_vm.labels = {
        "runner": "runnerforge",
        "repo": "rumankazi/runnerforge",
        "job_id": "1",
        "run_id": "2",
        "run_attempt": "2",
        "installation_id": "123",
        "queued_trace_id": "12da",
        "queued_span_id": "129asd",
    }
    mock_client.list.return_value = [mock_vm]
    monkeypatch.setattr("runnerforge.compute_client._compute_client", mock_client)

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            get_vm_labels_by_job(job_id="1", run_id="2", run_attempt="2")
        )

    assert result is not None
    mock_client.list.assert_called_once()

    # Observability contract: empty-result case still logs (with match_count=0)
    log = next((r for r in caplog.records if "VM lookup by job_id" in r.message), None)
    assert log is not None
    assert log.job_id == "1"
    assert log.run_id == "2"


def test_list_runnerforge_vms(monkeypatch, caplog):
    mock_client = MagicMock()
    mock_vm_1 = MagicMock()
    mock_vm_1.name = "runnerforge-1"
    mock_vm_1.creation_timestamp = "2026-05-31T10:00:00Z"
    mock_vm_1.labels = {
        "runner": "runnerforge",
        "repo": "rumankazi/runnerforge",
        "job_id": "1",
        "run_id": "2",
        "run_attempt": "2",
        "installation_id": "123",
    }

    mock_vm_2 = MagicMock()
    mock_vm_2.name = "runnerforge-2"
    mock_vm_2.creation_timestamp = "2026-05-31T12:00:00Z"
    mock_vm_2.labels = {
        "runner": "runnerforge",
        "repo": "rumankazi/runnerforge",
        "job_id": "2",
        "run_id": "3",
        "run_attempt": "3",
        "installation_id": "124",
    }
    mock_client.list.return_value = [mock_vm_1, mock_vm_2]
    monkeypatch.setattr("runnerforge.compute_client._compute_client", mock_client)
    with caplog.at_level(logging.INFO):
        vms = asyncio.run(
            list_runnerforge_vms(zone="europe-west4-a", project_id=GCP_PROJECT_ID)
        )
    mock_client.list.assert_called_once()
    request = mock_client.list.call_args.kwargs["request"]
    assert request.zone == "europe-west4-a"
    assert request.project == "test-project"
    assert request.filter == "labels.runner=runnerforge"
    assert vms == [
        VmInfo(
            name="runnerforge-1",
            creation_timestamp=datetime(2026, 5, 31, 10, 0, 0, tzinfo=timezone.utc),
            labels=RunnerForgeVmLabels(
                runner="runnerforge",
                repo="rumankazi/runnerforge",
                job_id="1",
                run_id="2",
                run_attempt="2",
                installation_id="123",
            ),
        ),
        VmInfo(
            name="runnerforge-2",
            creation_timestamp=datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc),
            labels=RunnerForgeVmLabels(
                runner="runnerforge",
                repo="rumankazi/runnerforge",
                job_id="2",
                run_id="3",
                run_attempt="3",
                installation_id="124",
            ),
        ),
    ]

    # Observability contract: lookup logs job_id + match_count
    log = next((r for r in caplog.records if "VM list scan" in r.message), None)
    assert log is not None
    assert log.runnerforge_vm_count == 2


def test_list_runnerforge_vms_with_malformed_labels(monkeypatch, caplog):
    mock_client = MagicMock()
    mock_vm_1 = MagicMock()
    mock_vm_1.name = "runnerforge-1"
    mock_vm_1.creation_timestamp = "2026-05-31T10:00:00Z"
    mock_vm_1.labels = {
        "runner": "runnerforge",
        "repo": "rumankazi/runnerforge",
        "job_id": "1",
        "run_id": "2",
        "run_attempt": "3",
    }

    mock_vm_2 = MagicMock()
    mock_vm_2.name = "runnerforge-2"
    mock_vm_2.creation_timestamp = "2026-05-31T12:00:00Z"
    mock_vm_2.labels = {
        "runner": "runnerforge",
        "repo": "rumankazi/runnerforge",
        "job_id": "2",
        "run_id": "3",
        "run_attempt": "4",
        "installation_id": "124",
    }
    mock_client.list.return_value = [mock_vm_1, mock_vm_2]
    monkeypatch.setattr("runnerforge.compute_client._compute_client", mock_client)
    with caplog.at_level(logging.INFO):
        vms = asyncio.run(
            list_runnerforge_vms(zone="europe-west4-a", project_id=GCP_PROJECT_ID)
        )

    assert len(vms) == 1
    assert vms[0].name == "runnerforge-2"
    # Warning emitted for the malformed VM
    warning_log = next(
        (
            r
            for r in caplog.records
            if "Skipping VM with unexpected label shape" in r.message
        ),
        None,
    )
    assert warning_log is not None
    assert warning_log.vm_name == "runnerforge-1"

    # Summary log shows the count breakdown
    summary_log = next((r for r in caplog.records if "VM list scan" in r.message), None)
    assert summary_log is not None
    assert summary_log.runnerforge_vm_count == 1
    assert summary_log.skipped_malformed_count == 1


def test_delete_vm_returns_none_when_vm_already_gone(monkeypatch, caplog):
    from google.api_core.exceptions import NotFound

    mock_client = MagicMock()
    mock_client.delete.side_effect = NotFound("not found")
    monkeypatch.setattr("runnerforge.compute_client._compute_client", mock_client)
    with caplog.at_level(logging.INFO):
        result = asyncio.run(delete_vm(instance_name="ghost-vm"))
    assert result is None
    assert any("VM already deleted" in r.message for r in caplog.records)


def test_vm_already_exists_returns_instance_name(monkeypatch, caplog):
    mock_client = MagicMock()
    mock_client.insert.side_effect = AlreadyExists("Instance already exists")
    monkeypatch.setattr(
        "runnerforge.compute_client._compute_client",
        mock_client,
    )

    with caplog.at_level(logging.INFO):
        response = asyncio.run(
            create_vm(
                instance_name="x",
                machine_type="e2-micro",
                labels={"runner": "runnerforge"},
            )
        )

    assert response is None
    assert any("VM already exists" in r.message for r in caplog.records)
    assert all(record.levelno == logging.WARNING for record in caplog.records)


def test_create_vm_requests_receives_runner_vm_sa(monkeypatch):
    mock_client = MagicMock()
    mock_client.insert.return_value = MagicMock(name="op-test")
    monkeypatch.setattr(
        "runnerforge.compute_client._compute_client",
        mock_client,
    )

    asyncio.run(
        create_vm(
            instance_name="x",
            machine_type="e2-micro",
            labels={"runner": "runnerforge"},
        )
    )

    captured_request = mock_client.insert.call_args.kwargs["request"]
    service_account = captured_request.instance_resource.service_accounts[0]
    assert service_account.email == RUNNER_VM_SA_EMAIL
    assert service_account.scopes == ["https://www.googleapis.com/auth/cloud-platform"]


def test_init_compute_client_logs_warning_when_no_credentials(monkeypatch, caplog):
    # Make the constructor raise the credentials error
    def _raise():
        raise DefaultCredentialsError("no creds")

    monkeypatch.setattr(
        "runnerforge.compute_client.compute_v1.InstancesClient",
        _raise,
    )
    monkeypatch.setattr(compute_client, "_compute_client", None)

    # Should NOT raise — we catch and log instead
    with caplog.at_level(logging.WARNING):
        compute_client.init_compute_client()

    # Module global stays None — init failed gracefully
    assert compute_client._compute_client is None

    # Warning was logged with the expected message
    assert any(
        r.levelno == logging.WARNING and "compute client init skipped" in r.message
        for r in caplog.records
    )


# -----------------------------------------------------------------------------
# wait_for_vm_creation
# -----------------------------------------------------------------------------


def _done_op(error_code: str | None = None, error_message: str | None = None):
    """Build a MagicMock GCE Operation in DONE state, optionally with an error."""
    from google.cloud import compute_v1

    op = MagicMock()
    op.status = compute_v1.Operation.Status.DONE
    if error_code is None:
        op.error = None
    else:
        err = MagicMock()
        err.code = error_code
        err.message = error_message or ""
        op.error.errors = [err]
    return op


def _running_op():
    """Build a MagicMock GCE Operation in RUNNING state."""
    from google.cloud import compute_v1

    op = MagicMock()
    op.status = compute_v1.Operation.Status.RUNNING
    op.error = None
    return op


def test_wait_for_vm_creation_returns_success_when_op_done_no_error(
    monkeypatch, caplog
):
    from runnerforge.compute_client import OperationHandle, wait_for_vm_creation

    zone_ops_mock = MagicMock()
    zone_ops_mock.get.return_value = _done_op()
    monkeypatch.setattr("runnerforge.compute_client._zone_ops_client", zone_ops_mock)

    handle = OperationHandle(name="op-123", zone="europe-west4-a")

    with caplog.at_level(logging.INFO):
        outcome = asyncio.run(wait_for_vm_creation(handle, timeout=10.0))

    assert outcome.outcome == "success"
    assert outcome.op_name == "op-123"
    assert outcome.zone == "europe-west4-a"
    assert outcome.error_code is None
    assert outcome.error_message is None
    assert outcome.duration_ms >= 0

    log = next((r for r in caplog.records if r.message == "vm.create.outcome"), None)
    assert log is not None
    assert log.levelno == logging.INFO
    assert log.outcome == "success"


def test_wait_for_vm_creation_returns_failure_on_op_error(monkeypatch, caplog):
    from runnerforge.compute_client import OperationHandle, wait_for_vm_creation

    zone_ops_mock = MagicMock()
    zone_ops_mock.get.return_value = _done_op(
        error_code="QUOTA_EXCEEDED", error_message="SSD_TOTAL_GB cap hit"
    )
    monkeypatch.setattr("runnerforge.compute_client._zone_ops_client", zone_ops_mock)

    handle = OperationHandle(name="op-456", zone="europe-west4-a")

    with caplog.at_level(logging.WARNING):
        outcome = asyncio.run(wait_for_vm_creation(handle, timeout=10.0))

    assert outcome.outcome == "failure"
    assert outcome.error_code == "QUOTA_EXCEEDED"
    assert outcome.error_message == "SSD_TOTAL_GB cap hit"

    log = next((r for r in caplog.records if r.message == "vm.create.outcome"), None)
    assert log is not None
    assert log.levelno == logging.WARNING
    assert log.outcome == "failure"
    assert log.error_code == "QUOTA_EXCEEDED"


def test_wait_for_vm_creation_polls_until_done(monkeypatch):
    from runnerforge.compute_client import OperationHandle, wait_for_vm_creation

    # Two RUNNING, then DONE
    zone_ops_mock = MagicMock()
    zone_ops_mock.get.side_effect = [_running_op(), _running_op(), _done_op()]
    monkeypatch.setattr("runnerforge.compute_client._zone_ops_client", zone_ops_mock)
    # Zero out the inter-poll sleep so tests run instantly
    monkeypatch.setattr("runnerforge.compute_client.asyncio.sleep", AsyncMock())

    handle = OperationHandle(name="op-789", zone="europe-west4-a")

    outcome = asyncio.run(wait_for_vm_creation(handle, timeout=10.0))

    assert outcome.outcome == "success"
    assert zone_ops_mock.get.call_count == 3


def test_wait_for_vm_creation_times_out_when_op_stays_running(monkeypatch, caplog):
    from runnerforge.compute_client import OperationHandle, wait_for_vm_creation

    zone_ops_mock = MagicMock()
    zone_ops_mock.get.return_value = _running_op()
    monkeypatch.setattr("runnerforge.compute_client._zone_ops_client", zone_ops_mock)
    monkeypatch.setattr("runnerforge.compute_client.asyncio.sleep", AsyncMock())

    handle = OperationHandle(name="op-timeout", zone="europe-west4-a")

    # Tiny timeout — the monotonic clock will pass the deadline immediately
    with caplog.at_level(logging.WARNING):
        outcome = asyncio.run(wait_for_vm_creation(handle, timeout=0.0001))

    assert outcome.outcome == "timeout"
    assert outcome.error_code is None

    log = next((r for r in caplog.records if r.message == "vm.create.outcome"), None)
    assert log is not None
    assert log.levelno == logging.WARNING
    assert log.outcome == "timeout"


def test_wait_for_vm_creation_keeps_polling_after_transient_get_error(
    monkeypatch, caplog
):
    from runnerforge.compute_client import OperationHandle, wait_for_vm_creation

    # First call raises, second returns DONE-success
    zone_ops_mock = MagicMock()
    zone_ops_mock.get.side_effect = [
        GoogleAPIError("transient network glitch"),
        _done_op(),
    ]
    monkeypatch.setattr("runnerforge.compute_client._zone_ops_client", zone_ops_mock)
    monkeypatch.setattr("runnerforge.compute_client.asyncio.sleep", AsyncMock())

    handle = OperationHandle(name="op-flake", zone="europe-west4-a")

    with caplog.at_level(logging.WARNING):
        outcome = asyncio.run(wait_for_vm_creation(handle, timeout=10.0))

    assert outcome.outcome == "success"
    assert zone_ops_mock.get.call_count == 2

    # Warning logged for the transient error
    warn = next(
        (r for r in caplog.records if "Transient error" in r.message), None
    )
    assert warn is not None
