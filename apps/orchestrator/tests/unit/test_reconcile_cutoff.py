"""ADR-019 step 4: the reconciler cleans up after installations GitHub
suspended or uninstalled."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from runnerforge import reconcile
from runnerforge.admission import Admission
from runnerforge.models import (
    AppInstallation,
    GitHubAccount,
    RunnerForgeVmLabels,
    VmInfo,
)
from runnerforge.reconcile import run_reconcile
from runnerforge.registry import BETA_DEFAULTS, RegistryEntry

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
IID = 7
SUSPENDED_AT = NOW - timedelta(minutes=5)


def _vm(
    name: str,
    created: datetime = SUSPENDED_AT - timedelta(minutes=10),
    status: str = "RUNNING",
    iid: str = str(IID),
) -> VmInfo:
    return VmInfo(
        name=name,
        creation_timestamp=created,
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id="1",
            run_id="100",
            run_attempt="1",
            installation_id=iid,
        ),
        repo_full_name="octo/repo",
        runner_labels=["runnerforge", "small"],
        status=status,
    )


def _row(status: str, iid: int = IID, **kw) -> RegistryEntry:
    return RegistryEntry(iid, "approved", status, NOW - timedelta(days=1), **kw)


def _github(iid: int = IID, suspended_at: datetime | None = SUSPENDED_AT):
    return AppInstallation(
        id=iid,
        account=GitHubAccount(id=1, login="octo", type="User"),
        suspended_at=suspended_at,
    )


def _record(job_id: int, iid: int = IID, **facts) -> tuple[str, dict]:
    return f"100-{job_id}-1", {
        "installation_id": iid,
        "repo": "octo/repo",
        "run_id": 100,
        "job_id": job_id,
        "run_attempt": 1,
        "labels": ["self-hosted", "runnerforge", "small"],
        "queued_at": NOW - timedelta(minutes=30),
        "open": True,
        **facts,
    }


class FakeRegistry:
    enabled = True

    def __init__(self):
        self.rows: dict[int, RegistryEntry] | None = {}
        self.statuses: list[tuple[int, str]] = []
        self.purged: list[int] = []

    async def list_all(self):
        return self.rows

    async def record_github_status(self, installation_id, status, account=None):
        self.statuses.append((installation_id, status))

    async def mark_records_purged(self, installation_id):
        self.purged.append(installation_id)


@pytest.fixture
def world(monkeypatch, fake_job_store):
    class World:
        def __init__(self) -> None:
            self.store = fake_job_store
            self.vms: list[VmInfo] = []
            self.registry = FakeRegistry()
            self.github: dict[int, AppInstallation | None | Exception] = {}
            self.checked: list[int] = []
            self.deleted: list[tuple[str, str]] = []
            self.delete_result: str | None = "operation-1"
            self.delete_error: Exception | None = None
            self.job = AsyncMock()
            self.get_runner = AsyncMock(return_value=None)

        async def get_app_installation(self, iid):
            self.checked.append(iid)
            found = self.github[iid]
            if isinstance(found, Exception):
                raise found
            return found

        async def delete_vm(self, instance_name, reason):
            if self.delete_error is not None:
                raise self.delete_error
            self.deleted.append((instance_name, reason))
            return self.delete_result

    world = World()
    monkeypatch.setattr(reconcile, "ADMISSION", "allowlist")
    monkeypatch.setattr(reconcile, "ALLOWED_INSTALLATION_IDS", [IID])
    monkeypatch.setattr(reconcile, "registry_backfill_due", lambda now: False)
    monkeypatch.setattr(
        reconcile, "list_runnerforge_vms", AsyncMock(side_effect=lambda: world.vms)
    )
    monkeypatch.setattr(reconcile, "get_registry", lambda: world.registry)
    monkeypatch.setattr(reconcile, "get_app_installation", world.get_app_installation)
    monkeypatch.setattr(reconcile, "delete_vm", world.delete_vm)
    monkeypatch.setattr(
        reconcile, "get_installation_token", AsyncMock(return_value="ghs")
    )
    monkeypatch.setattr(reconcile, "get_runner", world.get_runner)
    monkeypatch.setattr(reconcile, "get_job", world.job)
    monkeypatch.setattr(reconcile, "preemption_status", AsyncMock(return_value=None))
    return world


def _suspended(world) -> None:
    world.registry.rows = {IID: _row("suspended")}
    world.github = {IID: _github()}


def _deleted(world, **kw) -> None:
    world.registry.rows = {IID: _row("deleted", **kw)}
    world.github = {IID: None}


# --- confirming with GitHub ---


async def test_registry_read_failure_skips_the_cleanup(world, caplog):
    world.registry.rows = None
    world.vms = [_vm("runnerforge-100-1-1")]

    with caplog.at_level(logging.WARNING):
        await run_reconcile(now=NOW)

    assert world.deleted == []
    log = next(r for r in caplog.records if r.message == "Cut-off cleanup skipped")
    assert log.reason == "registry_unavailable"


async def test_active_rows_are_not_checked(world):
    world.registry.rows = {IID: _row("active")}
    world.vms = [_vm("runnerforge-100-1-1")]

    await run_reconcile(now=NOW)

    assert world.checked == []  # GitHub never asked
    assert world.deleted == []


async def test_a_row_github_contradicts_is_repaired_and_nothing_deleted(world, caplog):
    """A lost or reordered `unsuspend` must not delete an active
    installation's VMs, the allow-listed owner's included."""
    world.registry.rows = {IID: _row("suspended")}
    world.github = {IID: _github(suspended_at=None)}
    world.vms = [_vm("runnerforge-100-1-1")]

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(now=NOW)

    assert world.deleted == []
    assert world.registry.statuses == [(IID, "active")]
    assert result.registry_changes == 1
    log = next(r for r in caplog.records if r.message == "Registry status repaired")
    assert (log.was, log.now) == ("suspended", "active")


async def test_a_failed_check_skips_the_installation(world, caplog):
    world.registry.rows = {IID: _row("suspended")}
    world.github = {IID: RuntimeError("github down")}
    world.vms = [_vm("runnerforge-100-1-1")]

    with caplog.at_level(logging.WARNING):
        await run_reconcile(now=NOW)

    assert world.deleted == []
    log = next(r for r in caplog.records if r.message == "Cut-off check failed")
    assert (log.installation_id, log.error_class) == (IID, "RuntimeError")


async def test_a_suspended_row_github_no_longer_has_counts_as_deleted(world):
    world.registry.rows = {IID: _row("suspended")}
    world.github = {IID: None}
    world.vms = [_vm("runnerforge-100-1-1", created=NOW - timedelta(minutes=1))]

    await run_reconcile(now=NOW)

    assert world.registry.statuses == [(IID, "deleted")]
    # Gone just now: every VM made before this run goes.
    assert world.deleted == [("runnerforge-100-1-1", "cutoff")]


async def test_checks_stop_at_the_cap_and_say_so(world, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "MAX_CUTOFF_CONFIRMS", 1)
    world.registry.rows = {IID: _row("suspended"), 8: _row("suspended", iid=8)}
    world.github = {IID: _github(suspended_at=None), 8: _github(8, suspended_at=None)}

    with caplog.at_level(logging.WARNING):
        await run_reconcile(now=NOW)

    assert len(world.checked) == 1
    log = next(r for r in caplog.records if r.message == "Cut-off cleanup incomplete")
    assert log.unchecked == 1


async def test_the_capped_order_changes_between_runs(world, monkeypatch):
    """The cap can't always pass over the same installations."""
    monkeypatch.setattr(reconcile, "MAX_CUTOFF_CONFIRMS", 1)
    world.registry.rows = {i: _row("suspended", iid=i) for i in range(10, 20)}
    world.github = {i: _github(i, suspended_at=None) for i in range(10, 20)}

    for run in range(6):
        await run_reconcile(now=NOW + timedelta(minutes=5 * run))

    assert len(set(world.checked)) > 1


async def test_rows_with_work_left_come_before_purged_ones(world, monkeypatch):
    monkeypatch.setattr(reconcile, "MAX_CUTOFF_CONFIRMS", 1)
    monkeypatch.setattr(reconcile, "registry_backfill_due", lambda now: True)
    monkeypatch.setattr(reconcile, "sync_registry", AsyncMock(return_value=0))
    world.registry.rows = {
        i: _row("deleted", iid=i, records_purged_at=NOW) for i in range(10, 20)
    } | {IID: _row("suspended")}
    world.github = {i: None for i in range(10, 20)} | {IID: _github()}

    for run in range(4):
        await run_reconcile(now=NOW + timedelta(minutes=5 * run))

    assert set(world.checked) == {IID}


async def test_checks_stop_at_the_deadline(world, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "CUTOFF_BUDGET_SECONDS", -1.0)
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1")]

    with caplog.at_level(logging.WARNING):
        await run_reconcile(now=NOW)

    assert world.checked == []
    assert world.deleted == []
    log = next(r for r in caplog.records if r.message == "Cut-off cleanup incomplete")
    assert log.installation_id == IID


async def test_a_purged_row_is_rechecked_only_in_the_backfill_slot(world, monkeypatch):
    _deleted(world, records_purged_at=NOW - timedelta(hours=1))

    await run_reconcile(now=NOW)
    assert world.store.purged == []

    monkeypatch.setattr(reconcile, "registry_backfill_due", lambda now: True)
    monkeypatch.setattr(reconcile, "sync_registry", AsyncMock(return_value=0))
    await run_reconcile(now=NOW)
    assert world.store.purged == [(IID, reconcile.PURGE_LIMIT)]
    # Already marked: not marked again.
    assert world.registry.purged == []


# --- deleting VMs ---


async def test_suspended_idle_vm_goes_at_once(world, caplog):
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1")]

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(now=NOW)

    assert world.deleted == [("runnerforge-100-1-1", "cutoff")]
    assert result.cutoff_vms_deleted == 1
    log = next(r for r in caplog.records if r.message == "Cut-off VM deleted")
    assert (log.vm_name, log.installation_id, log.github_status, log.busy) == (
        "runnerforge-100-1-1",
        IID,
        "suspended",
        False,
    )
    assert log.age_minutes == 15


async def test_suspended_busy_vm_waits_out_the_grace(world):
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1")]
    world.store.open = [_record(5, in_progress={"runner_name": "runnerforge-100-1-1"})]

    await run_reconcile(now=NOW)
    assert world.deleted == []

    await run_reconcile(now=SUSPENDED_AT + reconcile.CUTOFF_BUSY_GRACE)
    assert world.deleted == [("runnerforge-100-1-1", "cutoff")]


async def test_every_vm_counts_as_busy_when_records_cant_be_read(world):
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1")]
    world.store.open = None

    await run_reconcile(now=NOW)

    assert world.deleted == []


async def test_every_vm_counts_as_busy_when_the_read_was_cut_short(world, monkeypatch):
    """At OPEN_LIMIT the read may have left out the record of a busy VM."""
    monkeypatch.setattr(reconcile, "OPEN_LIMIT", 1)
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1")]
    world.store.open = [_record(5, iid=8)]

    await run_reconcile(now=NOW)

    assert world.deleted == []


async def test_uninstalled_installations_vms_go_at_once_busy_or_not(world):
    _deleted(world, github_status_at=SUSPENDED_AT)
    world.vms = [_vm("runnerforge-100-1-1")]
    world.store.open = [_record(5, in_progress={"runner_name": "runnerforge-100-1-1"})]

    await run_reconcile(now=NOW)

    assert world.deleted == [("runnerforge-100-1-1", "cutoff")]


@pytest.mark.parametrize(
    "vm",
    [
        # Made after the cut-off: GitHub was still sending webhooks.
        _vm("runnerforge-100-1-1", created=SUSPENDED_AT + timedelta(seconds=1)),
        _vm("runnerforge-100-1-1", status="STOPPING"),
        _vm("runnerforge-100-1-1", iid="8"),
        _vm("runnerforge-100-1-1", iid=""),
    ],
)
async def test_vm_that_isnt_the_cut_off_installations_is_kept(world, vm):
    _suspended(world)
    world.vms = [vm]

    await run_reconcile(now=NOW)

    assert world.deleted == []


@pytest.mark.parametrize(
    ("kw", "kept_until"),
    [
        ({"github_status_at": SUSPENDED_AT}, SUSPENDED_AT),
        # Rows uninstalled before github_status_at existed.
        ({"expire_at": SUSPENDED_AT + timedelta(days=30)}, SUSPENDED_AT),
        ({}, NOW),
    ],
)
async def test_uninstall_time_comes_from_the_row(world, kw, kept_until):
    _deleted(world, **kw)
    world.vms = [
        _vm("runnerforge-100-1-1", created=kept_until - timedelta(seconds=1)),
        _vm("runnerforge-100-2-1", created=kept_until),
    ]

    await run_reconcile(now=NOW)

    assert world.deleted == [("runnerforge-100-1-1", "cutoff")]


async def test_deletes_stop_at_the_cap(world, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "MAX_CUTOFF_DELETES", 1)
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1"), _vm("runnerforge-100-2-1")]

    with caplog.at_level(logging.WARNING):
        await run_reconcile(now=NOW)

    assert len(world.deleted) == 1
    assert any(r.message == "Cut-off cleanup incomplete" for r in caplog.records)


async def test_a_failed_delete_is_an_error_and_the_run_goes_on(world, caplog):
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1")]
    world.delete_error = RuntimeError("gce down")

    with caplog.at_level(logging.ERROR):
        result = await run_reconcile(now=NOW)

    assert result.errors == ["runnerforge-100-1-1: RuntimeError"]
    log = next(r for r in caplog.records if r.message == "Cut-off VM delete failed")
    assert log.error_class == "RuntimeError"


async def test_a_vm_gce_no_longer_has_is_not_counted(world):
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1")]
    world.delete_result = None

    result = await run_reconcile(now=NOW)

    assert result.cutoff_vms_deleted == 0


async def test_deleted_vms_are_left_out_of_the_orphan_cleanup(world):
    _suspended(world)
    world.vms = [_vm("runnerforge-100-1-1", created=NOW - timedelta(hours=1))]

    await run_reconcile(now=NOW)

    assert world.deleted == [("runnerforge-100-1-1", "cutoff")]
    world.get_runner.assert_not_awaited()


# --- an uninstalled installation's records ---


async def test_records_are_deleted_once_no_vm_is_left(world, caplog):
    _deleted(world, github_status_at=SUSPENDED_AT)
    world.store.purge_result = 3

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(now=NOW)

    assert world.store.purged == [(IID, reconcile.PURGE_LIMIT)]
    assert result.cutoff_records_deleted == 3
    assert world.registry.purged == [IID]
    log = next(r for r in caplog.records if r.message == "Cut-off records deleted")
    assert (log.installation_id, log.count, log.more) == (IID, 3, False)


async def test_a_full_page_means_more_and_no_mark(world):
    _deleted(world, github_status_at=SUSPENDED_AT)
    world.store.purge_result = reconcile.PURGE_LIMIT

    await run_reconcile(now=NOW)

    assert world.registry.purged == []


async def test_nothing_left_still_marks_the_row(world, caplog):
    _deleted(world, github_status_at=SUSPENDED_AT)

    with caplog.at_level(logging.INFO):
        await run_reconcile(now=NOW)

    assert world.registry.purged == [IID]
    assert not any(r.message == "Cut-off records deleted" for r in caplog.records)


async def test_a_failed_purge_tries_again_next_run(world):
    _deleted(world, github_status_at=SUSPENDED_AT)
    world.store.purge_result = None

    await run_reconcile(now=NOW)

    assert world.registry.purged == []


@pytest.mark.parametrize("status", ["RUNNING", "STOPPING", "TERMINATED"])
async def test_records_wait_while_any_of_its_vms_is_listed(world, status):
    """A VM left over would give its job a record back (backfill)."""
    _deleted(world, github_status_at=SUSPENDED_AT)
    world.vms = [_vm("runnerforge-100-1-1", created=NOW, status=status)]

    await run_reconcile(now=NOW)

    assert world.store.purged == []


async def test_purge_stops_at_the_deadline(world, monkeypatch, caplog):
    """The confirmations used up the step's time: no purge this run."""
    clock = {"now": 0.0}

    class FakeTime:
        @staticmethod
        def monotonic():
            return clock["now"]

        @staticmethod
        def time():
            return 0.0

    async def slow_check(iid):
        clock["now"] += reconcile.CUTOFF_BUDGET_SECONDS + 1  # then: uninstalled

    monkeypatch.setattr(reconcile, "time", FakeTime)
    monkeypatch.setattr(reconcile, "get_app_installation", slow_check)
    _deleted(world, github_status_at=SUSPENDED_AT)

    with caplog.at_level(logging.WARNING):
        await run_reconcile(now=NOW)

    assert world.store.purged == []
    assert any(r.message == "Cut-off cleanup incomplete" for r in caplog.records)


# --- records in the batch ---


async def test_a_suspended_installations_records_are_paused(world):
    _suspended(world)
    key, rec = _record(5, decision={"kind": "vm_submitted"})
    world.store.due = [(key, rec)]

    result = await run_reconcile(now=NOW)

    world.job.assert_not_awaited()
    assert result.checked == 0
    assert world.store.patches == [
        (key, {"next_check_at": NOW + reconcile.CUTOFF_PAUSE})
    ]
    assert world.store.facts == []


async def test_an_uninstalled_installations_records_are_pushed_back(world):
    """Not checked, and not left at the head of every batch while the
    purge waits for its VMs."""
    _deleted(world, github_status_at=SUSPENDED_AT)
    world.vms = [_vm("runnerforge-100-9-1", created=NOW)]  # keeps the purge waiting
    key, rec = _record(5)
    world.store.due = [(key, rec)]

    result = await run_reconcile(now=NOW)

    world.job.assert_not_awaited()
    assert result.checked == 0
    assert world.store.patches == [
        (key, {"next_check_at": NOW + reconcile.CUTOFF_PAUSE})
    ]


async def test_backfill_gives_cut_off_vms_no_records(world):
    _suspended(world)
    world.vms = [
        _vm("runnerforge-100-1-1", created=SUSPENDED_AT + timedelta(minutes=1))
    ]

    result = await run_reconcile(now=NOW)

    assert result.backfilled == 0
    assert world.store.created == []


# --- the capacity pass ---


async def test_deferred_jobs_of_a_github_suspended_installation_keep_waiting(
    world, monkeypatch
):
    """A suspension can be lifted; the jobs start again once it is."""
    monkeypatch.setattr(reconcile, "ADMISSION", "registry")
    monkeypatch.setattr(reconcile, "ALLOWED_INSTALLATION_IDS", None)
    world.registry.rows = {IID: _row("suspended", limits=BETA_DEFAULTS.to_dict())}
    world.github = {IID: RuntimeError("not this test")}

    async def decide(iid):
        return Admission(admitted=False, reason="github_inactive", record=True)

    monkeypatch.setattr(reconcile, "decide_admission", decide)
    deferred = _record(5, decision={"kind": "deferred"})
    world.store.by_installation = {IID: [deferred]}

    await run_reconcile(now=NOW)

    assert world.store.patches == []
