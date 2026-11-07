"""ADR-019 step 3a: registry-mode admission decisions and limit checks."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.cloud import compute_v1

from runnerforge import admission, compute_client, handlers, main, registry
from runnerforge.admission import ALLOWLISTED, decide_admission, exceeds_size
from runnerforge.config import parse_admission
from runnerforge.machine_policy import MachinePolicy, resolve_labels
from runnerforge.registry import (
    BETA_DEFAULTS,
    CachedRegistry,
    Limits,
    NoopRegistry,
    RegistryEntry,
    RegistryUnavailable,
)

APPROVED_AT = datetime(2026, 11, 1, tzinfo=UTC)


class FakeRegistry:
    enabled = True

    def __init__(self):
        self.rows: dict[int, RegistryEntry] = {}
        self.unavailable = False

    async def get(self, installation_id):
        if self.unavailable:
            raise RegistryUnavailable("down")
        return self.rows.get(installation_id)


@pytest.fixture
def fake_registry(monkeypatch) -> FakeRegistry:
    fake = FakeRegistry()
    monkeypatch.setattr(registry, "_registry", fake)
    monkeypatch.setattr(registry, "_cached", CachedRegistry(fake))
    monkeypatch.setattr(admission, "ALLOWED_INSTALLATION_IDS", frozenset({1}))
    return fake


# --- config ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [("allowlist", "allowlist"), (" Registry ", "registry")],
)
def test_parse_admission(value, expected):
    assert parse_admission(value) == expected


def test_parse_admission_rejects_a_typo():
    with pytest.raises(ValueError, match="ADMISSION"):
        parse_admission("registy")


@pytest.mark.parametrize(("enabled", "warns"), [(True, False), (False, True)])
def test_log_admission_mode_in_registry_mode(monkeypatch, caplog, enabled, warns):
    monkeypatch.setattr(main, "ADMISSION", "registry")
    monkeypatch.setattr(main, "ALLOWED_INSTALLATION_IDS", None)
    fake = FakeRegistry() if enabled else NoopRegistry()
    monkeypatch.setattr(registry, "_registry", fake)

    with caplog.at_level(logging.INFO):
        main.log_admission_mode()

    log = next(r for r in caplog.records if r.message == "Admission by tenant registry")
    assert log.allowed_installation_count == 0
    assert any("without a registry" in r.message for r in caplog.records) is warns


# --- decide_admission ---


async def test_allowlisted_is_unlimited_without_a_read(fake_registry):
    fake_registry.unavailable = True
    assert await decide_admission(1) is ALLOWLISTED


async def test_unset_allow_list_admits_nobody_without_a_row(fake_registry, monkeypatch):
    monkeypatch.setattr(admission, "ALLOWED_INSTALLATION_IDS", None)
    decision = await decide_admission(1)
    assert (decision.admitted, decision.reason) == (False, "not_registered")


@pytest.mark.parametrize(
    ("row", "reason", "record"),
    [
        (RegistryEntry(7, "pending", "active"), "pending", False),
        (RegistryEntry(7, "approved", "suspended"), "github_inactive", False),
        (
            RegistryEntry(7, "approved", "deleted", approved_at=APPROVED_AT),
            "github_inactive",
            True,
        ),
        (
            RegistryEntry(7, "suspended", "active", approved_at=APPROVED_AT),
            "suspended",
            True,
        ),
        # A hand-edited state is no approval.
        (RegistryEntry(7, "Approved", "active"), "pending", False),
    ],
)
async def test_rows_that_are_not_admitted(fake_registry, row, reason, record):
    fake_registry.rows = {7: row}
    decision = await decide_admission(7)
    assert (decision.admitted, decision.reason, decision.record) == (
        False,
        reason,
        record,
    )
    assert decision.limits is None


async def test_unreadable_registry_rejects(fake_registry):
    fake_registry.unavailable = True
    decision = await decide_admission(7)
    assert (decision.admitted, decision.reason, decision.record) == (
        False,
        "registry_unavailable",
        False,
    )


LIMITED = Limits(4, "large", False, 120, True)


@pytest.mark.parametrize(
    ("stored", "limits"),
    [
        ({"unlimited": True}, None),
        (LIMITED.to_dict(), LIMITED),
        (None, BETA_DEFAULTS),
        ({"max_size": "small"}, BETA_DEFAULTS),
        ({**LIMITED.to_dict(), "max_vcpus": 8}, LIMITED),
    ],
    ids=["unlimited", "limited", "no-limits", "invalid", "newer"],
)
async def test_approved_rows_carry_their_limits(fake_registry, caplog, stored, limits):
    fake_registry.rows = {
        7: RegistryEntry(7, "approved", "active", APPROVED_AT, stored)
    }
    with caplog.at_level(logging.WARNING):
        decision = await decide_admission(7)
    assert (decision.admitted, decision.reason, decision.record) == (
        True,
        "approved",
        True,
    )
    assert decision.limits == limits
    invalid = [r for r in caplog.records if "limits invalid" in r.message]
    assert len(invalid) == (1 if stored == {"max_size": "small"} else 0)
    unknown = [
        r for r in caplog.records if r.message == "Limits fields unknown, ignored"
    ]
    assert [(r.installation_id, r.fields) for r in unknown] == (
        [(7, ["max_vcpus"])] if stored and "max_vcpus" in stored else []
    )


# --- exceeds_size ---


@pytest.mark.parametrize(
    ("labels", "max_size", "exceeds"),
    [
        (["runnerforge"], "medium", False),  # no size label = medium
        (["runnerforge"], "small", True),
        (["runnerforge", "small"], "small", False),
        (["runnerforge", "large"], "medium", True),
        (["runnerforge", "xlarge"], "xlarge", False),
    ],
)
def test_size_labels_are_capped_by_max_size(labels, max_size, exceeds):
    limits = Limits(max_size=max_size)
    assert exceeds_size(resolve_labels(labels), limits) is exceeds


def test_literal_types_need_permission(monkeypatch):
    monkeypatch.setattr(compute_client, "_KNOWN_MACHINE_TYPES", {"e2-small": 2})
    literal = MachinePolicy("e2-small", False, literal=True)
    assert exceeds_size(literal, Limits(literal_machine_types=False)) is True


@pytest.mark.parametrize(
    ("machine_type", "exceeds"),
    [("n2-standard-4", False), ("e2-highcpu-8", True), ("unknown-type", True)],
)
def test_allowed_literals_are_capped_by_vcpus(monkeypatch, machine_type, exceeds):
    monkeypatch.setattr(
        compute_client,
        "_KNOWN_MACHINE_TYPES",
        {"n2-standard-4": 4, "e2-highcpu-8": 8},
    )
    literal = MachinePolicy(machine_type, False, literal=True)
    limits = Limits(max_size="medium", literal_machine_types=True)
    assert exceeds_size(literal, limits) is exceeds


def test_literals_are_refused_when_the_cache_is_degraded(monkeypatch):
    monkeypatch.setattr(compute_client, "_KNOWN_MACHINE_TYPES", None)
    literal = MachinePolicy("e2-small", False, literal=True)
    assert exceeds_size(literal, Limits(literal_machine_types=True)) is True


# --- compute helpers ---


def test_machine_type_vcpus(monkeypatch):
    monkeypatch.setattr(compute_client, "_KNOWN_MACHINE_TYPES", {"e2-small": 2})
    assert compute_client.machine_type_vcpus("e2-small") == 2
    assert compute_client.machine_type_vcpus("e2-medium") is None
    monkeypatch.setattr(compute_client, "_KNOWN_MACHINE_TYPES", None)
    assert compute_client.machine_type_vcpus("e2-small") is None


def test_active_vm_names_lists_only_slot_holders(monkeypatch):
    client = MagicMock()
    client.list.return_value = [
        SimpleNamespace(name=f"vm-{status}", status=status)
        for status in ("PROVISIONING", "STAGING", "RUNNING", "STOPPING", "TERMINATED")
    ]
    monkeypatch.setattr(compute_client, "_compute_client", client)

    assert asyncio.run(compute_client.active_vm_names(42)) == {
        "vm-PROVISIONING",
        "vm-STAGING",
        "vm-RUNNING",
    }

    kwargs = client.list.call_args.kwargs
    assert kwargs["request"].filter == (
        "labels.runner=runnerforge AND labels.installation_id=42"
    )
    assert kwargs["timeout"] == compute_client.ACTIVE_VMS_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "name", ["runnerforge-Dev_Box", "runnerforge-" + "a" * 60, "runnerforge-x-"]
)
def test_labels_by_name_skips_names_gce_cant_hold(monkeypatch, name):
    client = MagicMock()
    monkeypatch.setattr(compute_client, "_compute_client", client)
    assert asyncio.run(compute_client.get_vm_labels_by_name(name)) is None
    client.get.assert_not_called()


# --- runner VM for readiness ---


def _in_progress(runner_name: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_job=SimpleNamespace(
            runner_name=runner_name, id=2, run_id=1, run_attempt=1
        ),
        installation=SimpleNamespace(id=42),
    )


async def test_runner_vm_without_a_runner_name_falls_back_to_the_job(monkeypatch):
    by_job = AsyncMock(return_value={"queued_received_at": "1"})
    monkeypatch.setattr(handlers, "get_vm_labels_by_job", by_job)
    # Labels for readiness, but no VM: a guess by job is never changed.
    assert await handlers._runner_vm(_in_progress(None)) == (
        {"queued_received_at": "1"},
        None,
    )
    by_job.assert_awaited_once_with(job_id="2", run_id="1", run_attempt="1")


async def test_runner_vm_of_a_foreign_runner_is_not_looked_up(monkeypatch):
    by_name = AsyncMock()
    monkeypatch.setattr(handlers, "get_vm_by_name", by_name)
    assert await handlers._runner_vm(_in_progress("my-own-runner")) == (None, None)
    by_name.assert_not_awaited()


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (None, None),
        ({"runner": "other", "installation_id": "42"}, None),
        ({"runner": "runnerforge", "installation_id": "7"}, None),
        (
            {"runner": "runnerforge", "installation_id": "42", "job_id": "9"},
            {"runner": "runnerforge", "installation_id": "42", "job_id": "9"},
        ),
    ],
    ids=["gone", "not-ours", "other-installation", "ours"],
)
async def test_runner_vm_is_trusted_only_by_its_labels(monkeypatch, labels, expected):
    vm = None if labels is None else compute_v1.Instance(labels=labels)
    by_name = AsyncMock(return_value=vm)
    monkeypatch.setattr(handlers, "get_vm_by_name", by_name)
    result = await handlers._runner_vm(_in_progress("runnerforge-1-9-1"))
    assert result == (expected, None if expected is None else vm)
    by_name.assert_awaited_once_with("runnerforge-1-9-1")


# --- readiness and GitHub's clock ---


def _readiness_event(started_at: float, created_at: float | None, runner_name="r"):
    def at(ts):
        return None if ts is None else datetime.fromtimestamp(ts, UTC)

    return SimpleNamespace(
        workflow_job=SimpleNamespace(
            id=9,
            started_at=at(started_at),
            created_at=at(created_at),
            runner_name=runner_name,
        ),
        repository=SimpleNamespace(full_name="o/r"),
    )


def _readiness_logs(caplog):
    by_message = {r.message: r for r in caplog.records}
    return by_message.get("Runner registration outcome"), by_message.get(
        "Runner readiness unknown"
    )


OWN = {"job_id": "9", "queued_received_at": "1000000"}
OTHER = {"job_id": "8", "queued_received_at": "1000000"}


@pytest.mark.parametrize(
    ("labels", "started_at", "created_at", "received_at", "expected"),
    [
        # started_at up to 5 s after our receipt is clock noise.
        (OWN, 1_000_104, None, 1_000_100, 104.0),
        (OWN, 1_000_106, None, 1_000_100, "started_at_ahead"),
        # Up to 5 s before the VM's admission is noise; more is GitHub behind.
        (OWN, 999_996, None, 1_000_100, 0.0),
        (OWN, 999_994, None, 1_000_100, "started_at_before_admission"),
        # Another VM: created_at bounded by our receipt, not only the start.
        (OTHER, 1_000_103, 1_000_104, 1_000_100, 0.0),
        (OTHER, 1_000_103, 1_000_106, 1_000_100, "created_at_unusable"),
        (OTHER, 1_000_050, None, 1_000_100, "created_at_unusable"),
    ],
)
def test_readiness_tolerances(
    caplog, labels, started_at, created_at, received_at, expected
):
    with caplog.at_level(logging.INFO):
        handlers._log_readiness(
            _readiness_event(started_at, created_at), labels, received_at
        )
    outcome, unknown = _readiness_logs(caplog)
    if isinstance(expected, str):
        assert outcome is None
        assert unknown.reason == expected
    else:
        assert unknown is None
        assert outcome.runner_readiness_seconds == expected


def test_an_early_start_without_a_runner_name_is_measured(caplog):
    """Without a runner_name the VM is the job's own by its labels, not the
    runner GitHub chose, so an earlier start can be real: floor, don't skip."""
    with caplog.at_level(logging.INFO):
        handlers._log_readiness(
            _readiness_event(999_000, None, runner_name=None), OWN, 1_000_100
        )
    outcome, unknown = _readiness_logs(caplog)
    assert unknown is None
    assert outcome.runner_readiness_seconds == 0.0


# --- the shared VM start (ADR-019 step 3b) ---


async def test_submit_runner_vm_labels_a_record_without_a_repository_id(monkeypatch):
    monkeypatch.setattr(handlers, "get_installation_token", AsyncMock(return_value="i"))
    monkeypatch.setattr(handlers, "get_registration_token", AsyncMock(return_value="r"))
    create = AsyncMock(return_value=None)
    monkeypatch.setattr(handlers, "create_vm", create)
    spec = handlers.RunnerVmSpec(
        vm_name="runnerforge-1-2-1-r1",
        installation_id=7,
        repo_full_name="o/r",
        repository_id=None,
        run_id=1,
        job_id=2,
        run_attempt=1,
        job_labels=["runnerforge"],
        machine_type="n2-standard-2",
        spot=True,
        max_run_duration=timedelta(minutes=75),
        queued_received_at=1_000_000.9,
    )

    assert await handlers.submit_runner_vm(spec, on_error=AsyncMock()) is None

    kwargs = create.await_args.kwargs
    assert kwargs["instance_name"] == "runnerforge-1-2-1-r1"
    assert kwargs["labels"]["repository_id"] == ""
    assert kwargs["labels"]["queued_received_at"] == "1000000"
    assert kwargs["labels"]["webhook_trace_id"] == ""
    assert kwargs["metadata"]["repo-url"] == "https://github.com/o/r"
    assert kwargs["max_run_duration"] == timedelta(minutes=75)


async def test_submit_runner_vm_uses_a_token_it_is_given(monkeypatch):
    minted = AsyncMock()
    monkeypatch.setattr(handlers, "get_installation_token", minted)
    registration = AsyncMock(return_value="r")
    monkeypatch.setattr(handlers, "get_registration_token", registration)
    monkeypatch.setattr(handlers, "create_vm", AsyncMock(return_value=None))
    spec = handlers.RunnerVmSpec(
        vm_name="v",
        installation_id=7,
        repo_full_name="o/r",
        repository_id=1,
        run_id=1,
        job_id=2,
        run_attempt=1,
        job_labels=[],
        machine_type="n2-standard-2",
        spot=True,
        max_run_duration=timedelta(minutes=75),
        queued_received_at=1.0,
        installation_token="cached",
    )

    await handlers.submit_runner_vm(spec, on_error=AsyncMock())

    minted.assert_not_awaited()
    assert registration.await_args.kwargs["installation_token"] == "cached"
