import asyncio
from unittest.mock import MagicMock

from runnerforge.compute_client import create_vm


def test_create_vm_builds_correct_instance(monkeypatch):
    mock_client = MagicMock()
    mock_client.insert.return_value.name = "op-abc-123"
    monkeypatch.setattr(
        "runnerforge.compute_client.compute_v1.InstancesClient",
        lambda: mock_client,
    )

    op_id = asyncio.run(
        create_vm(
            instance_name="test-vm",
            machine_type="e2-micro",
            labels={"runner": "runnerforge", "job_id": "42"},
        )
    )

    assert op_id == "op-abc-123"
    mock_client.insert.assert_called_once()
    request = mock_client.insert.call_args.kwargs["request"]
    assert request.project == "test-project"  # from conftest env
    assert request.zone == "europe-west4-a"
    assert request.instance_resource.name == "test-vm"
    assert request.instance_resource.labels == {"runner": "runnerforge", "job_id": "42"}
    assert len(request.instance_resource.disks) == 1
    assert request.instance_resource.disks[0].boot is True
