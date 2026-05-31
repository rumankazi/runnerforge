import asyncio
from unittest.mock import MagicMock

import pytest
from google.api_core.exceptions import GoogleAPIError
from runnerforge.compute_client import create_vm, delete_vm, find_vms_by_job_id


def test_create_vm_builds_correct_instance(monkeypatch):
    mock_client = MagicMock()
    mock_client.insert.return_value.name = "op-abc-123"
    monkeypatch.setattr(
        "runnerforge.compute_client.compute_v1.InstancesClient",
        lambda: mock_client,
    )
    op_id = asyncio.run(
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

    assert op_id == "op-abc-123"
    mock_client.insert.assert_called_once()
    request = mock_client.insert.call_args.kwargs["request"]
    assert request.project == "test-project"  # from conftest env
    assert request.zone == "europe-west4-a"
    assert request.instance_resource.name == "test-create-vm"
    assert request.instance_resource.labels == {"runner": "runnerforge", "job_id": "42"}
    assert len(request.instance_resource.disks) == 1
    assert request.instance_resource.disks[0].boot is True
    metadata_items = {
        item.key: item.value for item in request.instance_resource.metadata.items
    }
    assert metadata_items == {
        "registration-token": "reg-token",
        "repo-url": "https://github.com/foo/bar",
        "runner-labels": "runnerforge, medium",
    }


def test_delete_vm_deletes_instance(monkeypatch):
    mock_client = MagicMock()
    mock_client.delete.return_value.name = "op-abc-123"
    monkeypatch.setattr(
        "runnerforge.compute_client.compute_v1.InstancesClient", lambda: mock_client
    )

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


def test_find_vms_by_job_id(monkeypatch):
    mock_client = MagicMock()
    mock_vm = MagicMock()
    mock_vm.name = "runnerforge-12"
    mock_client.list.return_value = [mock_vm]
    monkeypatch.setattr(
        "runnerforge.compute_client.compute_v1.InstancesClient", lambda: mock_client
    )
    instances = asyncio.run(find_vms_by_job_id(job_id="12"))
    mock_client.list.assert_called_once()
    request = mock_client.list.call_args.kwargs["request"]
    assert request.zone == "europe-west4-a"
    assert request.project == "test-project"
    assert request.filter == "labels.job_id=12"
    assert instances == ["runnerforge-12"]


def test_create_vm_propagates_gce_errors(monkeypatch):
    mock_client = MagicMock()
    mock_client.insert.side_effect = GoogleAPIError("simulated quota exceeded")
    monkeypatch.setattr(
        "runnerforge.compute_client.compute_v1.InstancesClient",
        lambda: mock_client,
    )

    with pytest.raises(GoogleAPIError):
        asyncio.run(
            create_vm(
                instance_name="x",
                machine_type="e2-micro",
                labels={"runner": "runnerforge"},
            )
        )


def test_find_vms_by_job_id_returns_empty_when_no_match(monkeypatch):
    mock_client = MagicMock()
    mock_client.list.return_value = []
    monkeypatch.setattr(
        "runnerforge.compute_client.compute_v1.InstancesClient", lambda: mock_client
    )

    result = asyncio.run(find_vms_by_job_id(job_id="nonexistent"))

    assert result == []
    mock_client.list.assert_called_once()
