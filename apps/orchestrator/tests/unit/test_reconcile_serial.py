"""The reconciler's serial logging backstop (ADR-020): running VMs whose
serial logging the in_progress webhook didn't turn off."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, call

import pytest

from runnerforge import reconcile
from runnerforge.models import RunnerForgeVmLabels, VmInfo

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
IID = 7


def _vm(
    name: str = "runnerforge-1-1-1",
    age: timedelta = timedelta(minutes=5),
    status: str = "RUNNING",
    serial: bool | None = True,
    iid: str = str(IID),
) -> VmInfo:
    return VmInfo(
        name=name,
        creation_timestamp=NOW - age,
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id="1",
            run_id="1",
            run_attempt="1",
            installation_id=iid,
        ),
        status=status,
        serial_logging=serial,
    )


def _busy(runner: str, since: timedelta | None, iid: int = IID) -> tuple:
    fact = {"runner_name": runner}
    if since is not None:
        fact["at"] = NOW - since
    return f"1-{runner}-1", {"installation_id": iid, "in_progress": fact}


async def _pass(vms, records=(), errors=None):
    errors = [] if errors is None else errors
    await reconcile._serial_logging_pass(NOW, vms, list(records), errors)
    return errors


def _offs(serial_off) -> list[tuple[str, str]]:
    return [(c.args[0], c.args[2]) for c in serial_off.await_args_list]


async def test_a_busy_vm_the_webhook_missed_is_turned_off(serial_off):
    errors = await _pass([_vm()], [_busy("runnerforge-1-1-1", timedelta(minutes=3))])
    assert serial_off.await_args_list == [
        call("runnerforge-1-1-1", "reconcile", "busy", installation_id=IID)
    ]
    assert errors == []


async def test_a_job_that_just_started_is_left_to_its_webhook(serial_off):
    await _pass(
        [_vm(age=timedelta(minutes=30))],
        [_busy("runnerforge-1-1-1", timedelta(seconds=30))],
    )
    serial_off.assert_not_awaited()


async def test_a_busy_fact_without_a_time_counts_as_old(serial_off):
    await _pass([_vm()], [_busy("runnerforge-1-1-1", None)])
    assert _offs(serial_off) == [("runnerforge-1-1-1", "busy")]


async def test_another_installations_record_doesnt_make_a_vm_busy(serial_off):
    """It can't steer the pass at a VM that isn't its own."""
    await _pass([_vm()], [_busy("runnerforge-1-1-1", timedelta(minutes=3), iid=8)])
    serial_off.assert_not_awaited()


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(minutes=5), []),
        (timedelta(minutes=11), [("runnerforge-1-1-1", "aged")]),
    ],
    ids=["booting", "past-boot"],
)
async def test_an_idle_vm_is_turned_off_once_past_boot(serial_off, age, expected):
    await _pass([_vm(age=age)])
    assert _offs(serial_off) == expected


@pytest.mark.parametrize(
    "vm",
    [
        _vm(age=timedelta(hours=1), status="STOPPING"),
        _vm(age=timedelta(hours=1), serial=False),
    ],
    ids=["not-running", "already-off"],
)
async def test_vms_not_logging_are_skipped(serial_off, vm):
    await _pass([vm])
    serial_off.assert_not_awaited()


async def test_an_unknown_state_is_turned_off(serial_off):
    await _pass([_vm(age=timedelta(hours=1), serial=None)])
    assert _offs(serial_off) == [("runnerforge-1-1-1", "aged")]


async def test_a_vm_without_an_installation_is_still_turned_off(serial_off):
    await _pass([_vm(age=timedelta(hours=1), iid="")])
    assert serial_off.await_args.kwargs == {"installation_id": None}


async def test_the_pass_stops_at_its_cap(serial_off, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "MAX_SERIAL_OFFS", 2)
    vms = [_vm(name=f"runnerforge-{i}-1-1", age=timedelta(hours=1)) for i in range(3)]
    with caplog.at_level(logging.WARNING):
        await _pass(vms)
    assert serial_off.await_count == 2
    [incomplete] = [
        r for r in caplog.records if r.message == "Serial logging pass incomplete"
    ]
    assert incomplete.unchecked == 1


async def test_the_pass_stops_at_its_budget(serial_off, monkeypatch):
    monkeypatch.setattr(reconcile, "SERIAL_BUDGET_SECONDS", -1.0)
    await _pass([_vm(age=timedelta(hours=1))])
    serial_off.assert_not_awaited()


async def test_failures_are_reported_and_the_pass_goes_on(serial_off, caplog):
    serial_off.side_effect = [RuntimeError("boom"), "failed", "conflict", "vm_gone"]
    vms = [_vm(name=f"runnerforge-{i}-1-1", age=timedelta(hours=1)) for i in range(4)]
    with caplog.at_level(logging.ERROR):
        errors = await _pass(vms)
    assert errors == [
        "runnerforge-0-1-1: serial logging RuntimeError",
        "runnerforge-1-1-1: serial logging failed",
        "runnerforge-2-1-1: serial logging conflict",
    ]
    [failed] = [r for r in caplog.records if r.message == "Serial logging off failed"]
    assert failed.error_class == "RuntimeError"
    assert failed.exc_info is None


async def test_the_run_turns_off_the_vms_it_kept(
    monkeypatch, fake_job_store, serial_off
):
    """Wired into run_reconcile, after the orphan cleanup."""
    vms = [_vm(age=timedelta(hours=1))]
    monkeypatch.setattr(
        reconcile, "list_runnerforge_vms", AsyncMock(side_effect=lambda: vms)
    )
    monkeypatch.setattr(reconcile, "_confirm_cutoffs", AsyncMock(return_value={}))
    monkeypatch.setattr(reconcile, "_delete_orphans", AsyncMock(return_value=set()))
    monkeypatch.setattr(reconcile, "_backfill", AsyncMock(return_value=0))

    class Registry:
        async def list_all(self):
            return {}

    monkeypatch.setattr(reconcile, "get_registry", lambda: Registry())
    monkeypatch.setattr(reconcile, "ADMISSION", "allowlist")
    result = await reconcile.run_reconcile(now=NOW)
    assert _offs(serial_off) == [("runnerforge-1-1-1", "aged")]
    assert result.errors == []
