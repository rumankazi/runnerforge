"""ADR-020 step 5: TERMINATED cleanup and slot repair in the reconciler."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from runnerforge import reconcile
from runnerforge.compute_client import MAX_VM_RUN_DURATION
from runnerforge.models import RunnerForgeVmLabels, VmInfo
from runnerforge.reconcile import run_reconcile

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _vm(name: str, status: str, age: timedelta = timedelta(minutes=20)) -> VmInfo:
    return VmInfo(
        name=name,
        creation_timestamp=NOW - age,
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id="1",
            run_id="100",
            run_attempt="1",
            installation_id="7",
        ),
        status=status,
        # Not a serial-logging candidate: that pass is tested on its own.
        serial_logging=False,
    )


@pytest.fixture
def world(monkeypatch, fake_job_store, fake_slot_ledger):
    class World:
        def __init__(self) -> None:
            self.vms: list[VmInfo] = []
            self.delete = AsyncMock(return_value="op-delete")
            self.ledger = fake_slot_ledger
            self.listed_after: datetime | None = None

    world = World()

    async def listing():
        listed = list(world.vms)
        world.listed_after = datetime.now(UTC)
        return listed

    monkeypatch.setattr(reconcile, "list_runnerforge_vms", listing)
    monkeypatch.setattr(reconcile, "delete_vm", world.delete)
    return world


async def test_old_terminated_vms_are_deleted_then_repair_runs(world, caplog):
    world.vms = [
        _vm("old", "TERMINATED"),
        _vm("young", "TERMINATED", age=timedelta(minutes=5)),
        _vm("running", "RUNNING"),
    ]
    before = datetime.now(UTC)

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(NOW)

    world.delete.assert_awaited_once_with(instance_name="old", reason="terminated")
    assert result.terminated_deleted == 1
    [repair] = world.ledger.repairs
    assert repair["vms"] == ["young", "running"]
    assert repair["deleted"] == {"old"}
    # Taken before the listing, so a release during it counts as later.
    assert world.listed_after is not None
    assert before <= repair["listed_at"] <= world.listed_after
    assert repair["vm_lifetime"] == MAX_VM_RUN_DURATION
    assert repair["vcpus_of"]("n2-standard-8") == 8
    [record] = [r for r in caplog.records if r.message == "Terminated VM deleted"]
    assert (record.vm_name, record.installation_id, record.age_minutes) == (
        "old",
        "7",
        20,
    )


async def test_a_terminated_vm_already_gone_is_not_counted(world):
    world.vms = [_vm("old", "TERMINATED")]
    world.delete.return_value = None

    result = await run_reconcile(NOW)

    assert result.terminated_deleted == 0
    assert world.ledger.repairs[0]["deleted"] == {"old"}


async def test_a_failed_delete_keeps_the_vm_and_the_run_goes_on(world, caplog):
    world.vms = [_vm("a", "TERMINATED"), _vm("b", "TERMINATED")]
    world.delete.side_effect = [RuntimeError("down"), "op"]

    result = await run_reconcile(NOW)

    assert result.terminated_deleted == 1
    assert result.errors == ["a: RuntimeError"]
    assert world.ledger.repairs[0]["deleted"] == {"b"}
    assert world.ledger.repairs[0]["vms"] == ["a"]
    [record] = [r for r in caplog.records if r.message == "Terminated VM delete failed"]
    assert record.error_class == "RuntimeError"


async def test_deletes_are_capped_per_run(world, caplog):
    world.vms = [_vm(f"vm-{n:02}", "TERMINATED") for n in range(12)]

    result = await run_reconcile(NOW)

    assert result.terminated_deleted == reconcile.MAX_TERMINATED_DELETES == 10
    [record] = [
        r for r in caplog.records if r.message == "Terminated cleanup incomplete"
    ]
    assert record.left == 2


async def test_deletes_stop_at_the_budget(world, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "TERMINATED_BUDGET_SECONDS", -1.0)
    world.vms = [_vm("a", "TERMINATED")]

    result = await run_reconcile(NOW)

    world.delete.assert_not_awaited()
    assert result.terminated_deleted == 0
    [record] = [
        r for r in caplog.records if r.message == "Terminated cleanup incomplete"
    ]
    assert record.left == 1


async def test_a_run_already_late_skips_the_cleanup(world, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "TERMINATED_LATEST_START_SECONDS", -1.0)
    world.vms = [_vm("a", "TERMINATED")]

    result = await run_reconcile(NOW)

    world.delete.assert_not_awaited()
    assert result.terminated_deleted == 0
    [record] = [r for r in caplog.records if r.message == "Terminated cleanup skipped"]
    assert record.reason == "run_budget"
    # Repair still runs: the VM still counts.
    assert world.ledger.repairs[0]["vms"] == ["a"]


async def test_vms_the_orphan_cleanup_deleted_reach_repair_as_deleted(
    world, monkeypatch
):
    monkeypatch.setattr(
        reconcile, "_delete_orphans", AsyncMock(return_value={"orphan"})
    )
    world.vms = [_vm("orphan", "RUNNING"), _vm("kept", "RUNNING")]

    await run_reconcile(NOW)

    [repair] = world.ledger.repairs
    assert (repair["vms"], repair["deleted"]) == (["kept"], {"orphan"})
