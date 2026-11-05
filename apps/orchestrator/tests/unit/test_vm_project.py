"""VMs in a project of their own (ADR-020 step 4): every VM call goes to the
VM project, which defaults to the control project."""

import asyncio
import logging
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from google.api_core.exceptions import GoogleAPIError
from google.cloud import compute_v1

from runnerforge import compute_client

POOL = "runnerforge-pool"


@pytest.fixture
def client(monkeypatch) -> MagicMock:
    mock = MagicMock()
    mock.insert.return_value.name = "op-1"
    mock.delete.return_value.name = "op-2"
    mock.list.return_value = []
    monkeypatch.setattr(compute_client, "_compute_client", mock)
    return mock


@pytest.fixture
def pool(monkeypatch) -> None:
    monkeypatch.setattr(compute_client, "RUNNER_VM_PROJECT_ID", POOL)


def _create(**kwargs):
    return asyncio.run(
        compute_client.create_vm(
            instance_name="runnerforge-1-2-1",
            machine_type="e2-medium",
            labels={"runner": "runnerforge"},
            **kwargs,
        )
    )


def test_create_vm_goes_to_the_vm_project(client, pool):
    handle = _create()
    assert client.insert.call_args.kwargs["request"].project == POOL
    # The watcher polls the operation where the VM was made, never "None".
    assert handle is not None
    assert handle.project == POOL


def test_an_explicit_project_wins(client, pool):
    handle = _create(project_id="elsewhere")
    assert client.insert.call_args.kwargs["request"].project == "elsewhere"
    assert handle is not None
    assert handle.project == "elsewhere"


@pytest.mark.parametrize(
    ("call", "method"),
    [
        (lambda: compute_client.get_vm_labels_by_name("runnerforge-1-2-1"), "get"),
        (lambda: compute_client.get_vm_by_name("runnerforge-1-2-1"), "get"),
        (lambda: compute_client.delete_vm("runnerforge-1-2-1", "webhook"), "delete"),
    ],
    ids=["labels-by-name", "by-name", "delete"],
)
def test_get_and_delete_go_to_the_vm_project(client, pool, call, method):
    asyncio.run(call())
    assert getattr(client, method).call_args.kwargs["project"] == POOL


@pytest.mark.parametrize(
    "call",
    [
        lambda: compute_client.get_vm_labels_by_job("1", "2", "3"),
        lambda: compute_client.find_vms_by_job_id("1", "2", "3"),
        lambda: compute_client.list_runnerforge_vms(),
        lambda: compute_client.active_vm_names(7),
    ],
    ids=["labels-by-job", "find", "list", "active"],
)
def test_lists_go_to_the_vm_project(client, pool, call):
    asyncio.run(call())
    assert client.list.call_args.kwargs["request"].project == POOL


def test_the_vm_list_scan_says_which_project(client, pool, caplog):
    with caplog.at_level(logging.INFO):
        asyncio.run(compute_client.list_runnerforge_vms())
    [scan] = [r for r in caplog.records if r.message == "VM list scan"]
    assert scan.project_id == POOL


def test_machine_types_come_from_the_vm_project(monkeypatch, pool):
    types = MagicMock()
    types.list.return_value = [compute_v1.MachineType(name="e2-medium", guest_cpus=2)]
    monkeypatch.setattr(compute_client.compute_v1, "MachineTypesClient", lambda: types)
    # Restored after the test, which overwrites it.
    monkeypatch.setattr(compute_client, "_KNOWN_MACHINE_TYPES", None)
    compute_client.init_machine_types_cache()
    assert types.list.call_args.kwargs["request"].project == POOL


def test_a_subnetwork_replaces_the_default_network(client, monkeypatch):
    subnet = "projects/runnerforge-pool/regions/europe-west4/subnetworks/pool"
    monkeypatch.setattr(compute_client, "RUNNER_VM_SUBNETWORK", subnet)
    _create()
    [nic] = client.insert.call_args.kwargs[
        "request"
    ].instance_resource.network_interfaces
    assert nic.subnetwork == subnet
    # GCE takes the network from the subnetwork.
    assert not nic.network
    assert not nic.access_configs


def test_no_account_means_none_attached(client, monkeypatch):
    monkeypatch.setattr(compute_client, "RUNNER_VM_SA_EMAIL", "")
    _create()
    instance = client.insert.call_args.kwargs["request"].instance_resource
    assert not instance.service_accounts


def test_security_keys_win_over_the_callers_metadata(client):
    _create(
        metadata={
            "serial-port-logging-enable": "FALSE",
            "enable-oslogin": "FALSE",
            "startup-script": "#!/bin/bash",
        }
    )
    instance = client.insert.call_args.kwargs["request"].instance_resource
    assert {item.key: item.value for item in instance.metadata.items} == {
        "serial-port-logging-enable": "TRUE",
        "enable-oslogin": "TRUE",
        "block-project-ssh-keys": "TRUE",
        "startup-script": "#!/bin/bash",
    }


def test_security_keys_without_caller_metadata(client):
    _create(max_run_duration=timedelta(hours=1))
    instance = client.insert.call_args.kwargs["request"].instance_resource
    assert {item.key for item in instance.metadata.items} == set(
        compute_client.SECURITY_METADATA
    )


# --- the preemption read, per project ---


def _logging(monkeypatch, answers: dict[str, list | Exception]) -> MagicMock:
    """A logging client that answers per project."""

    def list_entries(resource_names, filter_, max_results):
        [name] = resource_names
        answer = answers[name.removeprefix("projects/")]
        if isinstance(answer, Exception):
            raise answer
        return iter(answer)

    mock = MagicMock()
    mock.list_entries.side_effect = list_entries
    monkeypatch.setattr(compute_client, "_logging_client", mock)
    return mock


def test_preemption_is_read_once_when_the_projects_match(monkeypatch):
    mock = _logging(monkeypatch, {"test-project": []})
    assert asyncio.run(compute_client.preemption_status("vm-1")) is False
    mock.list_entries.assert_called_once()
    filter_ = mock.list_entries.call_args.kwargs["filter_"]
    # Only GCE's own log: no entry a VM wrote elsewhere can pass for one.
    assert filter_.startswith('LOG_ID("cloudaudit.googleapis.com/system_event") AND ')


@pytest.mark.parametrize(
    ("control", "vm", "expected"),
    [
        ([], [], False),
        ([MagicMock()], [], True),
        ([], [MagicMock()], True),
        (GoogleAPIError("down"), [MagicMock()], True),
        ([MagicMock()], GoogleAPIError("down"), True),
        (GoogleAPIError("down"), [], None),
        ([], GoogleAPIError("down"), None),
    ],
    ids=[
        "neither",
        "control",
        "vm-project",
        "control-down-vm-yes",
        "vm-down-control-yes",
        "control-down",
        "vm-down",
    ],
)
def test_preemption_is_read_in_both_projects(monkeypatch, pool, control, vm, expected):
    mock = _logging(monkeypatch, {"test-project": control, POOL: vm})
    assert asyncio.run(compute_client.preemption_status("vm-1")) is expected
    assert mock.list_entries.call_count == 2


def test_a_failed_preemption_read_names_its_project(monkeypatch, pool, caplog):
    _logging(monkeypatch, {"test-project": [], POOL: GoogleAPIError("down")})
    with caplog.at_level(logging.WARNING):
        asyncio.run(compute_client.preemption_status("vm-1"))
    [failed] = [r for r in caplog.records if "preemption check" in r.message]
    assert failed.project_id == POOL


# --- serial logging state, as listed ---


def _vm(items: dict[str, str], effective: bool | None = None) -> compute_v1.Instance:
    instance = compute_v1.Instance(
        name="runnerforge-1-2-1",
        creation_timestamp="2026-11-05T10:00:00Z",
        status="RUNNING",
        labels={
            "runner": "runnerforge",
            "job_id": "1",
            "run_id": "2",
            "run_attempt": "1",
            "installation_id": "7",
        },
        metadata=compute_v1.Metadata(
            items=[compute_v1.Items(key=k, value=v) for k, v in items.items()]
        ),
    )
    if effective is not None:
        instance.resource_status = compute_v1.ResourceStatus(
            effective_instance_metadata=compute_v1.ResourceStatusEffectiveInstanceMetadata(
                serial_port_logging_enable_metadata_value=effective
            )
        )
    return instance


@pytest.mark.parametrize(
    ("items", "effective", "expected"),
    [
        # GCE's consolidated value wins over the item.
        ({"serial-port-logging-enable": "TRUE"}, False, False),
        ({}, True, True),
        ({"serial-port-logging-enable": "TRUE"}, None, True),
        ({"serial-port-logging-enable": "true"}, None, True),
        ({"serial-port-logging-enable": " FALSE "}, None, False),
        # Absent: it may come from the project.
        ({}, None, None),
        ({"serial-port-logging-enable": "maybe"}, None, None),
    ],
    ids=["effective-off", "effective-on", "TRUE", "true", "FALSE", "absent", "junk"],
)
def test_listed_vms_carry_their_serial_logging(client, items, effective, expected):
    client.list.return_value = [_vm(items, effective)]
    [vm] = asyncio.run(compute_client.list_runnerforge_vms())
    assert vm.serial_logging is expected
