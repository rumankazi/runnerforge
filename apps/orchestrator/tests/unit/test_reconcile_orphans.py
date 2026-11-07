"""The reconciler's orphan cleanup: VMs whose runner won't take a job."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, call

import httpx
import pytest

from runnerforge import reconcile
from runnerforge.admission import Admission
from runnerforge.github_client import RepoNotFound
from runnerforge.models import GitHubJob, Runner, RunnerForgeVmLabels, VmInfo
from runnerforge.reconcile import run_reconcile
from runnerforge.registry import BETA_DEFAULTS, RegistryEntry

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
IID = 7
REPO = "octo/repo"
LIMITED = Admission(admitted=True, reason="approved", limits=BETA_DEFAULTS, record=True)


def _vm(
    name: str,
    age: timedelta = timedelta(minutes=30),
    status: str = "RUNNING",
    iid: str = str(IID),
    repo: str | None = REPO,
    labels: list[str] | None = None,
    job_id: str = "1",
) -> VmInfo:
    return VmInfo(
        name=name,
        creation_timestamp=NOW - age,
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id=job_id,
            run_id="100",
            run_attempt="1",
            installation_id=iid,
        ),
        repo_full_name=repo,
        runner_labels=["runnerforge", "small"] if labels is None else labels,
        status=status,
    )


def _record(job_id: int, iid: int = IID, repo: str = REPO, **facts) -> tuple:
    return f"100-{job_id}-1", {
        "installation_id": iid,
        "repo": repo,
        "run_id": 100,
        "job_id": job_id,
        "run_attempt": 1,
        "labels": ["self-hosted", "runnerforge", "small"],
        "queued_at": NOW - timedelta(minutes=30),
        "open": True,
        **facts,
    }


def _runner(name: str, busy: bool = False, status: str = "online") -> Runner:
    return Runner(id=42, name=name, status=status, busy=busy)


class FakeRegistry:
    enabled = True

    def __init__(self):
        self.rows = {IID: RegistryEntry(IID, "approved", "active", NOW)}

    async def list_all(self):
        return self.rows


@pytest.fixture
def world(monkeypatch, fake_job_store):
    """Allowlist mode, installation IID allow-listed: no capacity pass."""

    class World:
        def __init__(self) -> None:
            self.store = fake_job_store
            self.vms: list[VmInfo] = []
            self.runners: dict[str, Runner | None | Exception] = {}
            self.registry = FakeRegistry()
            self.admission = {IID: LIMITED}
            self.active: set[str] = set()
            self.calls: list[tuple] = []
            self.delete_runner = AsyncMock(
                side_effect=lambda token, repo, rid: self.calls.append(
                    ("delete_runner", rid)
                )
            )
            self.delete_vm = AsyncMock(side_effect=self._delete_vm)
            self.submit = AsyncMock(return_value=None)
            self.job = AsyncMock(
                return_value=GitHubJob(id=1, status="queued", conclusion=None)
            )
            self.preempted = AsyncMock(return_value=False)

        def _delete_vm(self, instance_name, reason):
            self.calls.append(("delete_vm", instance_name, reason))
            return "operation-1"

    world = World()

    async def get_runner(token, repo, name):
        found = world.runners.get(name)
        if isinstance(found, Exception):
            raise found
        return found

    async def decide(iid):
        return world.admission[iid]

    monkeypatch.setattr(reconcile, "ADMISSION", "allowlist")
    monkeypatch.setattr(reconcile, "ALLOWED_INSTALLATION_IDS", [IID])
    monkeypatch.setattr(
        reconcile, "list_runnerforge_vms", AsyncMock(side_effect=lambda: world.vms)
    )
    monkeypatch.setattr(reconcile, "get_registry", lambda: world.registry)
    # The cut-off cleanup is tested on its own; here its GitHub check fails,
    # so it acts on nothing.
    monkeypatch.setattr(
        reconcile,
        "get_app_installation",
        AsyncMock(side_effect=RuntimeError("not under test")),
    )
    monkeypatch.setattr(reconcile, "decide_admission", decide)
    monkeypatch.setattr(
        reconcile, "active_vm_names", AsyncMock(side_effect=lambda iid: world.active)
    )
    monkeypatch.setattr(
        reconcile, "get_installation_token", AsyncMock(return_value="ghs")
    )
    monkeypatch.setattr(reconcile, "get_runner", get_runner)
    monkeypatch.setattr(reconcile, "delete_runner", world.delete_runner)
    monkeypatch.setattr(reconcile, "delete_vm", world.delete_vm)
    monkeypatch.setattr(reconcile, "get_job", world.job)
    monkeypatch.setattr(reconcile, "submit_runner_vm", world.submit)
    monkeypatch.setattr(reconcile, "preemption_status", world.preempted)
    return world


def _registry_mode(world, monkeypatch) -> None:
    monkeypatch.setattr(reconcile, "ADMISSION", "registry")
    monkeypatch.setattr(reconcile, "ALLOWED_INSTALLATION_IDS", None)


# --- what GitHub says ---


async def test_runner_gone_deletes_the_vm_without_deregistering(world, caplog):
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(now=NOW)

    assert world.calls == [("delete_vm", "runnerforge-100-1-1", "reconcile")]
    assert result.orphans_deleted == 1
    assert result.errors == []
    log = next(r for r in caplog.records if r.message == "Orphan VM deleted")
    assert (log.vm_name, log.installation_id, log.reason, log.age_minutes) == (
        "runnerforge-100-1-1",
        IID,
        "runner_gone",
        30,
    )


async def test_runner_gone_wins_over_a_stale_busy_record(world):
    """A lost `completed` webhook leaves `in_progress` on the record; the
    ephemeral runner leaving GitHub's list says the job ended."""
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}
    world.store.open = [_record(5, in_progress={"runner_name": "runnerforge-100-1-1"})]

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1


@pytest.mark.parametrize("status", ["online", "offline"])
async def test_idle_runner_is_deregistered_then_deleted(world, caplog, status):
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {
        "runnerforge-100-1-1": _runner("runnerforge-100-1-1", status=status)
    }

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(now=NOW)

    # Deregistered first, so GitHub can't hand it a job while it shuts down.
    assert world.calls == [
        ("delete_runner", 42),
        ("delete_vm", "runnerforge-100-1-1", "reconcile"),
    ]
    assert result.orphans_deleted == 1
    log = next(r for r in caplog.records if r.message == "Orphan VM deleted")
    assert log.reason == "runner_idle"


async def test_busy_runner_is_kept(world):
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1", busy=True)}

    result = await run_reconcile(now=NOW)

    assert world.calls == []
    assert result.orphans_deleted == 0


async def test_idle_runner_on_a_job_per_the_records_is_kept(world):
    """GitHub's busy flag can read false mid-job."""
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}
    world.store.open = [_record(5, in_progress={"runner_name": "runnerforge-100-1-1"})]

    await run_reconcile(now=NOW)

    assert world.calls == []


async def test_repo_not_found_keeps_the_vm(world):
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": RepoNotFound(REPO)}

    result = await run_reconcile(now=NOW)

    assert world.calls == []
    assert result.errors == []


# --- a waiting job the runner could take ---


async def test_online_idle_runner_that_could_take_a_waiting_job_is_kept(world):
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}
    world.store.open = [_record(5, decision={"kind": "deferred"})]

    await run_reconcile(now=NOW)

    assert world.calls == []


async def test_vm_with_unknown_labels_could_take_any_waiting_job(world):
    world.vms = [_vm("runnerforge-100-1-1", labels=[])]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}
    world.store.open = [_record(5, labels=["runnerforge", "xlarge"])]

    await run_reconcile(now=NOW)

    assert world.calls == []


@pytest.mark.parametrize(
    "waiting",
    [
        _record(5, iid=8),  # another installation
        _record(5, repo="octo/other"),  # another repo
        _record(5, in_progress={"runner_name": "elsewhere"}),  # taken
        _record(5, completed={"conclusion": "success"}),  # ended
        _record(5, labels=["runnerforge", "xlarge"]),  # labels not covered
    ],
)
async def test_online_idle_runner_no_waiting_job_can_use_is_deleted(world, waiting):
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}
    world.store.open = [waiting]

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1


async def test_offline_idle_runner_is_deleted_even_with_a_waiting_job(world):
    """An offline runner takes nothing."""
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {
        "runnerforge-100-1-1": _runner("runnerforge-100-1-1", status="offline")
    }
    world.store.open = [_record(5, decision={"kind": "deferred"})]

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1


# --- which VMs are checked ---


@pytest.mark.parametrize(
    "vm",
    [
        _vm("young", age=timedelta(minutes=10)),
        _vm("stopping", status="STOPPING"),
        _vm("other", iid="8"),
        _vm("unlabelled", iid=""),
        _vm("no-repo", repo=None),
    ],
)
async def test_vm_that_isnt_a_candidate_is_not_checked(world, vm):
    world.vms = [vm]
    world.runners = {vm.name: None}

    await run_reconcile(now=NOW)

    assert world.calls == []


async def test_allowlist_mode_ignores_registry_approvals(world):
    """The registry is read in both modes (for cut-off installations), but
    in allowlist mode only the allow-list gets records, so an approved
    row's VMs aren't orphan candidates."""
    world.registry.rows = {8: RegistryEntry(8, "approved", "active", NOW)}
    world.vms = [_vm("runnerforge-100-1-1", iid="8")]
    world.runners = {"runnerforge-100-1-1": None}

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 0
    assert world.calls == []


async def test_registry_mode_checks_approved_installations(world, monkeypatch):
    _registry_mode(world, monkeypatch)
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1


@pytest.mark.parametrize(
    "row",
    [
        RegistryEntry(IID, "approved", "suspended", NOW),  # GitHub refuses calls
        RegistryEntry(IID, "approved", "deleted", NOW),
        RegistryEntry(IID, "pending", "active"),  # never approved: no records
    ],
)
async def test_registry_mode_skips_installations_without_usable_records(
    world, monkeypatch, row
):
    _registry_mode(world, monkeypatch)
    world.registry.rows = {IID: row}
    world.admission = {IID: Admission(admitted=False, reason="pending")}
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}

    await run_reconcile(now=NOW)

    assert world.calls == []


# --- failing closed and staying bounded ---


async def test_failed_open_records_read_skips_the_cleanup(world, caplog):
    world.store.open = None
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}

    with caplog.at_level(logging.WARNING):
        result = await run_reconcile(now=NOW)

    assert world.calls == []
    assert result.orphans_deleted == 0
    log = next(r for r in caplog.records if r.message == "Orphan cleanup skipped")
    assert log.reason == "store_unavailable"


async def test_open_records_at_the_read_limit_skip_the_cleanup(
    world, monkeypatch, caplog
):
    """A truncated read may leave out the record of a busy runner."""
    monkeypatch.setattr(reconcile, "OPEN_LIMIT", 1)
    world.store.open = [_record(5, iid=8)]
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}

    with caplog.at_level(logging.WARNING):
        await run_reconcile(now=NOW)

    assert world.calls == []
    log = next(r for r in caplog.records if r.message == "Orphan cleanup skipped")
    assert log.reason == "too_many_open"


async def test_failed_check_keeps_the_vm_and_the_run_goes_on(world, caplog):
    world.vms = [_vm("runnerforge-100-1-1"), _vm("runnerforge-100-2-1")]
    request = httpx.Request("GET", "https://api.github.com")
    world.runners = {
        "runnerforge-100-1-1": httpx.HTTPStatusError(
            "boom", request=request, response=httpx.Response(502, request=request)
        ),
        "runnerforge-100-2-1": None,
    }

    with caplog.at_level(logging.ERROR):
        result = await run_reconcile(now=NOW)

    assert world.calls == [("delete_vm", "runnerforge-100-2-1", "reconcile")]
    assert result.errors == ["runnerforge-100-1-1: HTTPStatusError"]
    log = next(r for r in caplog.records if r.message == "Orphan check failed")
    assert (log.vm_name, log.error_class) == ("runnerforge-100-1-1", "HTTPStatusError")


async def test_failed_deregistration_keeps_the_vm(world):
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}
    world.delete_runner.side_effect = RuntimeError("422")

    result = await run_reconcile(now=NOW)

    world.delete_vm.assert_not_awaited()
    assert result.orphans_deleted == 0
    assert result.errors == ["runnerforge-100-1-1: RuntimeError"]


async def test_checks_stop_at_the_cap_and_say_so(world, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "MAX_ORPHAN_CHECKS", 1)
    world.vms = [_vm("runnerforge-100-1-1"), _vm("runnerforge-100-2-1")]
    world.runners = {"runnerforge-100-1-1": None, "runnerforge-100-2-1": None}

    with caplog.at_level(logging.WARNING):
        result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1
    log = next(r for r in caplog.records if r.message == "Orphan cleanup incomplete")
    assert log.unchecked == 1


async def test_checks_stop_at_the_deadline(world, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "ORPHAN_BUDGET_SECONDS", -1.0)
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}

    with caplog.at_level(logging.WARNING):
        result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 0
    assert any(r.message == "Orphan cleanup incomplete" for r in caplog.records)


async def test_check_order_changes_between_runs(world, monkeypatch):
    """VMs kept every run (long jobs) can't always take the checks."""
    monkeypatch.setattr(reconcile, "MAX_ORPHAN_CHECKS", 1)
    names = [f"runnerforge-100-{i}-1" for i in range(5)]
    # Serial logging off, so only the orphan checks ask GitHub.
    world.vms = [
        _vm(name).model_copy(update={"serial_logging": False}) for name in names
    ]
    world.runners = {name: _runner(name, busy=True) for name in names}
    checked: list[str] = []

    async def get_runner(token, repo, name):
        checked.append(name)
        return world.runners[name]

    monkeypatch.setattr(reconcile, "get_runner", get_runner)
    for run in range(6):
        await run_reconcile(now=NOW + timedelta(minutes=5 * run))

    assert len(checked) == 6
    assert len(set(checked)) > 1


async def test_allow_everyone_checks_every_installation(world, monkeypatch):
    """An unset allow-list admits, and records, every installation."""
    monkeypatch.setattr(reconcile, "ALLOWED_INSTALLATION_IDS", None)
    world.vms = [_vm("runnerforge-100-1-1", iid="8")]
    world.runners = {"runnerforge-100-1-1": None}

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1


async def test_unset_allow_list_in_registry_mode_means_nobody(world, monkeypatch):
    _registry_mode(world, monkeypatch)
    world.registry.rows = {}
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}

    await run_reconcile(now=NOW)

    assert world.calls == []


async def test_vm_already_deleted_is_not_counted(world, caplog):
    """Its `completed` webhook deleted it between the listing and the check."""
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}
    world.delete_vm.side_effect = None
    world.delete_vm.return_value = None

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 0
    assert not any(r.message == "Orphan VM deleted" for r in caplog.records)


async def test_owner_suspended_installation_is_checked(world, monkeypatch):
    """Its idle runners could still take new jobs; GitHub still answers."""
    _registry_mode(world, monkeypatch)
    world.registry.rows = {IID: RegistryEntry(IID, "suspended", "active", NOW)}
    world.admission = {IID: Admission(admitted=False, reason="suspended")}
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1


# --- the deleted VM's own job: left to the batch ---


async def test_batch_asks_github_before_calling_the_job_stuck(world):
    """The cleanup writes no verdict; the batch finds the job still queued
    and its VM gone."""
    key, rec = _record(1, decision={"kind": "vm_submitted"})
    world.store.open = [(key, rec)]
    world.store.due = [(key, rec)]
    world.vms = [_vm("runnerforge-100-1-1", labels=["runnerforge", "xlarge"])]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1
    world.job.assert_awaited_once()
    world.preempted.assert_awaited_once_with("runnerforge-100-1-1")
    assert world.store.fact("vm")["fact"] == {"outcome": "missing", "error_code": None}
    assert result.stuck == 1


async def test_job_that_finished_unseen_is_closed_not_stuck(world):
    """A runner gone from GitHub usually ran its job, even when the webhooks
    that would say so were lost."""
    key, rec = _record(1, decision={"kind": "vm_submitted"})
    world.store.open = [(key, rec)]
    world.store.due = [(key, rec)]
    world.vms = [_vm("runnerforge-100-1-1")]
    world.runners = {"runnerforge-100-1-1": None}
    world.job.return_value = GitHubJob(
        id=1,
        status="completed",
        conclusion="success",
        runner_name="runnerforge-100-1-1",
    )

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1
    assert result.closed == 1
    assert result.stuck == 0


# --- registry mode: the freed slot ---


async def test_limited_job_whose_vm_is_deleted_starts_again(world, monkeypatch):
    """The capacity pass sees the VM gone and starts the job on a new name."""
    _registry_mode(world, monkeypatch)
    key, rec = _record(
        1,
        decision={"kind": "vm_submitted", "at": NOW - timedelta(minutes=30)},
    )
    world.store.open = [(key, rec)]
    world.store.by_installation = {IID: [(key, rec)]}
    world.vms = [_vm("runnerforge-100-1-1", labels=["runnerforge", "xlarge"])]
    world.runners = {"runnerforge-100-1-1": _runner("runnerforge-100-1-1")}
    world.active = {"runnerforge-100-1-1"}

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1
    assert result.started == 1
    [(claimed, _, decision, _)] = world.store.claims
    assert (claimed, decision["vm_name"]) == (key, "runnerforge-100-1-1-r1")


async def test_freed_slot_starts_a_deferred_job_in_the_same_run(world, monkeypatch):
    """The live listing may still show the deleted VM; it doesn't count."""
    _registry_mode(world, monkeypatch)
    deferred = _record(9, decision={"kind": "deferred"})
    world.store.open = [deferred]
    world.store.by_installation = {IID: [deferred]}
    world.vms = [_vm("runnerforge-100-1-1"), _vm("runnerforge-100-2-1")]
    world.runners = {
        "runnerforge-100-1-1": None,
        "runnerforge-100-2-1": _runner("runnerforge-100-2-1", busy=True),
    }
    # At its limit of 2 before the cleanup.
    world.active = {"runnerforge-100-1-1", "runnerforge-100-2-1"}

    result = await run_reconcile(now=NOW)

    assert result.orphans_deleted == 1
    assert result.started == 1
    assert world.store.claims[0][0] == "100-9-1"
    assert world.delete_vm.await_args_list == [
        call(instance_name="runnerforge-100-1-1", reason="reconcile")
    ]
