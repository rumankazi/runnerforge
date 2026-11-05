"""Serial logging off when a job starts (ADR-020): turn_off_serial_logging."""

import asyncio
import logging
import time
from unittest.mock import create_autospec

import pytest
from google.api_core.exceptions import (
    InternalServerError,
    NotFound,
    PreconditionFailed,
)
from google.auth.exceptions import RefreshError
from google.cloud import compute_v1
from google.cloud.compute_v1.services.instances import InstancesClient

from runnerforge import compute_client
from runnerforge.compute_client import turn_off_serial_logging

NAME = "runnerforge-1-2-1"
TOKEN = "AABBCCDDTOKEN"


def _vm(
    serial: str | None = "TRUE",
    status: str = "RUNNING",
    labels: dict[str, str] | None = None,
    fingerprint: str = "fp-1",
) -> compute_v1.Instance:
    items = {"registration-token": TOKEN, "startup-script": "#!/bin/bash"}
    if serial is not None:
        items["serial-port-logging-enable"] = serial
    return compute_v1.Instance(
        name=NAME,
        status=status,
        labels={"runner": "runnerforge", "installation_id": "7"}
        if labels is None
        else labels,
        metadata=compute_v1.Metadata(
            fingerprint=fingerprint,
            items=[compute_v1.Items(key=k, value=v) for k, v in items.items()],
        ),
    )


@pytest.fixture
def client(monkeypatch):
    # Autospec, so a keyword the real client doesn't take fails here. From
    # its own module: conftest replaces compute_v1.InstancesClient.
    mock = create_autospec(InstancesClient, instance=True)
    mock.get.return_value = _vm()
    mock.set_metadata.return_value.name = "op-serial"
    monkeypatch.setattr(compute_client, "_compute_client", mock)
    return mock


def _off(**kwargs) -> str:
    kwargs.setdefault("installation_id", 7)
    return asyncio.run(
        turn_off_serial_logging(NAME, "in_progress", "webhook", **kwargs)
    )


def _sent(client, call: int = -1) -> compute_v1.SetMetadataInstanceRequest:
    return client.set_metadata.call_args_list[call].kwargs["request"]


def _items(request) -> dict[str, str]:
    return {i.key: i.value for i in request.metadata_resource.items}


def test_it_sets_false_and_keeps_every_other_item(client, caplog):
    with caplog.at_level(logging.INFO):
        assert _off() == "submitted"
    request = _sent(client)
    assert (request.project, request.zone, request.instance) == (
        "test-project",
        "europe-west4-a",
        NAME,
    )
    # setMetadata replaces every item: the rest must go back unchanged.
    assert _items(request) == {
        "registration-token": TOKEN,
        "startup-script": "#!/bin/bash",
        "serial-port-logging-enable": "FALSE",
    }
    assert request.metadata_resource.fingerprint == "fp-1"
    assert request.request_id
    # No client retry: its 600 s would outlive our timeout in the thread.
    assert client.get.call_args.kwargs["retry"] is None
    [line] = [r for r in caplog.records if r.message == "Serial logging outcome"]
    assert line.levelno == logging.INFO
    assert (line.vm_name, line.project_id, line.trigger, line.reason) == (
        NAME,
        "test-project",
        "in_progress",
        "webhook",
    )
    assert (line.outcome, line.operation_id, line.error_class) == (
        "submitted",
        "op-serial",
        None,
    )


def test_a_vm_without_the_item_gets_it(client):
    """It may inherit TRUE from its project, so absent isn't off."""
    client.get.return_value = _vm(serial=None)
    assert _off() == "submitted"
    assert _items(_sent(client))["serial-port-logging-enable"] == "FALSE"


def test_a_vm_the_caller_already_read_isnt_read_again(client):
    assert _off(instance=_vm(fingerprint="fp-given")) == "submitted"
    client.get.assert_not_called()
    assert _sent(client).metadata_resource.fingerprint == "fp-given"


def test_it_goes_to_the_vm_project(client, monkeypatch):
    monkeypatch.setattr(compute_client, "RUNNER_VM_PROJECT_ID", "runnerforge-pool")
    assert _off() == "submitted"
    assert client.get.call_args.kwargs["project"] == "runnerforge-pool"
    assert _sent(client).project == "runnerforge-pool"


@pytest.mark.parametrize(
    ("vm", "installation_id", "expected"),
    [
        (_vm(serial="FALSE"), 7, "already_off"),
        (_vm(serial="false"), 7, "already_off"),
        (_vm(status="STOPPING"), 7, "not_running"),
        (_vm(labels={"runner": "other", "installation_id": "7"}), 7, "not_owned"),
        (_vm(labels={"runner": "runnerforge", "installation_id": "8"}), 7, "not_owned"),
    ],
    ids=["off", "off-lowercase", "stopping", "not-ours", "other-installation"],
)
def test_it_changes_nothing_it_shouldnt(client, vm, installation_id, expected):
    assert _off(instance=vm, installation_id=installation_id) == expected
    client.set_metadata.assert_not_called()


def test_without_an_installation_only_the_runner_label_counts(client):
    vm = _vm(labels={"runner": "runnerforge", "installation_id": "8"})
    assert _off(instance=vm, installation_id=None) == "submitted"


def test_a_name_gce_cant_hold_is_never_looked_up(client):
    outcome = asyncio.run(
        turn_off_serial_logging(
            "runnerforge-Dev_Box", "in_progress", "webhook", installation_id=7
        )
    )
    assert outcome == "not_owned"
    client.get.assert_not_called()


def test_a_vm_gone_before_the_get(client):
    client.get.side_effect = NotFound("gone")
    assert _off() == "vm_gone"


def test_a_vm_gone_before_the_set(client):
    client.set_metadata.side_effect = NotFound("gone")
    assert _off() == "vm_gone"


def test_a_changed_fingerprint_is_read_again_and_retried(client):
    client.get.side_effect = [_vm(fingerprint="fp-1"), _vm(fingerprint="fp-2")]
    client.set_metadata.side_effect = [
        PreconditionFailed("stale"),
        client.set_metadata.return_value,
    ]
    assert _off() == "submitted"
    first, second = _sent(client, 0), _sent(client, 1)
    assert second.metadata_resource.fingerprint == "fp-2"
    # A repeated request ID would be ignored as the same request.
    assert first.request_id != second.request_id


def test_a_retry_finds_it_already_off(client):
    client.get.side_effect = [_vm(), _vm(serial="FALSE")]
    client.set_metadata.side_effect = PreconditionFailed("stale")
    assert _off() == "already_off"
    assert client.set_metadata.call_count == 1


def test_two_changed_fingerprints_is_a_conflict(client, caplog):
    client.set_metadata.side_effect = PreconditionFailed("stale")
    with caplog.at_level(logging.INFO):
        assert _off() == "conflict"
    assert client.set_metadata.call_count == 2
    [line] = [r for r in caplog.records if r.message == "Serial logging outcome"]
    assert line.levelno == logging.WARNING


def test_an_api_error_fails_open(client, caplog):
    # The message carries the token, as an echoed request could.
    client.set_metadata.side_effect = InternalServerError(f"bad item {TOKEN}")
    with caplog.at_level(logging.DEBUG):
        assert _off() == "failed"
    [line] = [r for r in caplog.records if r.message == "Serial logging outcome"]
    assert line.levelno == logging.WARNING
    assert line.error_class == "InternalServerError"
    assert line.exc_info is None
    # Never the token, from the error or the VM's items.
    assert TOKEN not in caplog.text
    assert all(TOKEN not in str(r.__dict__) for r in caplog.records)


def test_a_slow_call_times_out(client, monkeypatch):
    monkeypatch.setattr(compute_client, "SERIAL_OFF_TIMEOUT_SECONDS", 0.01)

    def slow_get(**kwargs):
        time.sleep(0.2)
        return _vm()

    client.get.side_effect = slow_get
    assert _off() == "failed"


def test_a_failed_token_refresh_fails_open(client, caplog):
    client.get.side_effect = RefreshError("metadata server unreachable")
    with caplog.at_level(logging.INFO):
        assert _off() == "failed"
    [line] = [r for r in caplog.records if r.message == "Serial logging outcome"]
    assert line.error_class == "RefreshError"
