"""ADR-019 step 3b: the reconciler's capacity pass."""

import logging
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from runnerforge import reconcile
from runnerforge.admission import ALLOWLISTED, Admission
from runnerforge.github_client import JobNotFound
from runnerforge.models import GitHubJob, RunnerForgeVmLabels, VmInfo
from runnerforge.reconcile import run_reconcile
from runnerforge.registry import BETA_DEFAULTS, Limits, RegistryEntry

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
IID = 7
REPO = "octo/repo"
LIMITED = Admission(admitted=True, reason="approved", limits=BETA_DEFAULTS, record=True)
UNLIMITED = Admission(admitted=True, reason="approved", record=True)


def _key(job_id: int) -> str:
    return f"100-{job_id}-1"


def _record(job_id: int, minutes_ago: int = 30, iid: int = IID, **facts) -> dict:
    return {
        "installation_id": iid,
        "repository_id": 9,
        "repo": REPO,
        "run_id": 100,
        "job_id": job_id,
        "run_attempt": 1,
        "labels": ["self-hosted", "runnerforge", "small"],
        "queued_at": NOW - timedelta(minutes=minutes_ago),
        "open": True,
        **facts,
    }


def _deferred(job_id: int, **kw) -> tuple[str, dict]:
    return _key(job_id), _record(
        job_id, decision={"kind": "deferred", "reason": "limit_concurrency"}, **kw
    )


def _submitted(job_id: int, at: datetime | None = None, **decision) -> tuple:
    return _key(job_id), _record(
        job_id,
        decision={
            "kind": "vm_submitted",
            "at": at or NOW - timedelta(minutes=10),
            **decision,
        },
    )


def _vm(
    name: str,
    status: str = "RUNNING",
    age: timedelta = timedelta(minutes=5),
    iid: int = IID,
    repo: str = REPO,
) -> VmInfo:
    return VmInfo(
        name=name,
        creation_timestamp=NOW - age,
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id="1",
            run_id="100",
            run_attempt="1",
            installation_id=str(iid),
        ),
        repo_full_name=repo,
        runner_labels=["runnerforge", "small"],
        status=status,
    )


class FakeRegistry:
    enabled = True

    def __init__(self):
        self.rows = {IID: RegistryEntry(IID, "approved", "active", NOW)}

    async def list_all(self):
        return self.rows


@pytest.fixture
def world(monkeypatch, fake_job_store):
    class World:
        def __init__(self) -> None:
            self.store = fake_job_store
            self.vms: list[VmInfo] = []
            self.registry = FakeRegistry()
            self.admission = {IID: LIMITED}
            self.active: set[str] = set()
            self.job = AsyncMock(
                return_value=GitHubJob(id=1, status="queued", conclusion=None)
            )
            self.submit = AsyncMock(return_value=object())
            self.preempted = AsyncMock(return_value=False)

    world = World()
    monkeypatch.setattr(reconcile, "ADMISSION", "registry")
    monkeypatch.setattr(reconcile, "ALLOWED_INSTALLATION_IDS", None)
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

    async def decide(iid):
        return world.admission[iid]

    monkeypatch.setattr(reconcile, "decide_admission", decide)
    monkeypatch.setattr(
        reconcile, "active_vm_names", AsyncMock(side_effect=lambda iid: world.active)
    )
    monkeypatch.setattr(
        reconcile, "get_installation_token", AsyncMock(return_value="ghs")
    )
    monkeypatch.setattr(reconcile, "get_job", world.job)
    monkeypatch.setattr(reconcile, "submit_runner_vm", world.submit)
    monkeypatch.setattr(reconcile, "preemption_status", world.preempted)
    return world


def _spec(world, call: int = 0):
    return world.submit.await_args_list[call].args[0]


# --- when it runs ---


async def test_pass_runs_only_in_registry_mode(world, monkeypatch):
    monkeypatch.setattr(reconcile, "ADMISSION", "allowlist")
    world.store.by_installation[IID] = [_deferred(1)]

    result = await run_reconcile(NOW)

    assert result.started == 0
    assert world.store.queried == []


async def test_installations_from_ever_approved_rows_and_the_allow_list(
    world, monkeypatch
):
    world.registry.rows = {
        IID: RegistryEntry(IID, "approved", "active", NOW),
        8: RegistryEntry(8, "pending", "active"),  # never approved: no records
        9: RegistryEntry(9, "suspended", "deleted", NOW),  # uninstalled
        10: RegistryEntry(10, "suspended", "active", NOW),
    }
    monkeypatch.setattr(reconcile, "ALLOWED_INSTALLATION_IDS", frozenset({11}))
    world.admission.update({10: Admission(False, "suspended", record=True)})
    world.admission[11] = ALLOWLISTED

    await run_reconcile(NOW)

    assert world.store.queried == [IID, 10, 11]


async def test_registry_listing_failure_still_uses_the_allow_list(world, monkeypatch):
    world.registry.rows = None
    monkeypatch.setattr(reconcile, "ALLOWED_INSTALLATION_IDS", frozenset({IID}))
    world.admission[IID] = ALLOWLISTED

    await run_reconcile(NOW)

    assert world.store.queried == [IID]


# --- deferred jobs ---


async def test_a_deferred_job_starts_when_a_slot_is_free(world, caplog):
    world.store.by_installation[IID] = [_deferred(1)]
    world.active = {"runnerforge-100-9-1"}  # 1 of 2

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(NOW)

    assert result.started == 1
    [(key, expected, decision, next_check)] = world.store.claims
    assert (key, expected) == (_key(1), 0)
    assert decision == {
        "kind": "vm_submitted",
        "source": "reconcile",
        "start": 1,
        "vm_name": "runnerforge-100-1-1",  # never had a VM: the base name
        "machine_type": "n2-standard-2",
        "spot": True,  # spot_only
        "capacity_retries": 0,
        "at": NOW,
    }
    assert next_check == NOW + reconcile.BACKOFF[0]
    spec = _spec(world)
    assert spec.vm_name == "runnerforge-100-1-1"
    assert (spec.installation_id, spec.repo_full_name, spec.repository_id) == (
        IID,
        REPO,
        9,
    )
    assert (spec.run_id, spec.job_id, spec.run_attempt) == (100, 1, 1)
    assert spec.spot is True
    assert spec.max_run_duration == timedelta(minutes=75)
    # Admitted now, on our clock: readiness runs from this start.
    assert abs(spec.queued_received_at - time.time()) < 5
    started = next(r for r in caplog.records if r.message == "Reconcile started job")
    assert (started.job_key, started.start, started.installation_id) == (
        _key(1),
        1,
        IID,
    )


async def test_no_free_slot_no_lookup(world):
    world.store.by_installation[IID] = [_deferred(1)]
    world.active = {"a", "b"}

    result = await run_reconcile(NOW)

    assert result.started == 0
    world.job.assert_not_awaited()


async def test_starts_fill_the_free_slots_oldest_first(world):
    world.store.by_installation[IID] = [
        _deferred(1, minutes_ago=50),
        _deferred(2, minutes_ago=40),
        _deferred(3, minutes_ago=30),
    ]

    result = await run_reconcile(NOW)

    assert result.started == 2
    assert [c[0] for c in world.store.claims] == [_key(1), _key(2)]


async def test_an_unlimited_installation_starts_its_deferred_jobs(world, monkeypatch):
    """Limits lifted (or allow-listed) while jobs were deferred."""
    world.admission[IID] = UNLIMITED
    world.store.by_installation[IID] = [_deferred(1), _deferred(2), _deferred(3)]
    monkeypatch.setattr(reconcile, "MAX_STARTS_PER_RUN", 2)

    result = await run_reconcile(NOW)

    assert result.started == 2
    reconcile.active_vm_names.assert_not_awaited()
    assert (
        _spec(world).max_run_duration
        == reconcile.vm_settings(reconcile.resolve_labels(["small"]), None)[1]
    )


@pytest.mark.parametrize("reason", ["suspended", "github_inactive", "pending"])
async def test_deferred_jobs_of_an_installation_no_longer_admitted_are_rejected(
    world, reason
):
    world.admission[IID] = Admission(False, reason, record=True)
    world.store.by_installation[IID] = [_deferred(1)]

    result = await run_reconcile(NOW)

    assert result.started == 0
    [(key, fields)] = world.store.patches
    assert key == _key(1)
    assert fields["decision"] == {
        "kind": "rejected",
        "reason": reason,
        "source": "reconcile",
        "at": NOW,
    }
    world.job.assert_not_awaited()


async def test_a_job_now_above_max_size_is_rejected(world):
    world.admission[IID] = Admission(
        True, "approved", limits=Limits(max_size="small"), record=True
    )
    key, rec = _deferred(1)
    rec["labels"] = ["runnerforge", "large"]
    world.store.by_installation[IID] = [(key, rec)]

    await run_reconcile(NOW)

    [(_, fields)] = world.store.patches
    assert fields["decision"]["kind"] == "rejected"
    assert fields["decision"]["reason"] == "limit_size"
    assert fields["decision"]["machine_type"] == "n2-standard-8"
    world.submit.assert_not_awaited()


async def test_labels_the_policy_rejects_start_nothing(world):
    key, rec = _deferred(1)
    rec["labels"] = ["runnerforge", "windows"]
    world.store.by_installation[IID] = [(key, rec)]

    await run_reconcile(NOW)

    assert world.store.claims == []


# --- what GitHub says ---


@pytest.mark.parametrize(
    ("job", "fact", "expected"),
    [
        (
            GitHubJob(id=1, status="completed", conclusion="cancelled"),
            "completed",
            {"runner_name": None, "conclusion": "cancelled"},
        ),
        (
            GitHubJob(id=1, status="in_progress", runner_name="r"),
            "in_progress",
            {"runner_name": "r"},
        ),
    ],
)
async def test_a_job_that_moved_on_is_recorded_not_started(world, job, fact, expected):
    world.job.return_value = job
    world.store.by_installation[IID] = [_deferred(1)]

    await run_reconcile(NOW)

    assert world.store.claims == []
    written = world.store.fact(fact)
    assert (written["key"], written["fact"], written["source"]) == (
        _key(1),
        expected,
        "reconcile",
    )


async def test_a_job_github_no_longer_knows_is_closed(world):
    world.job.side_effect = JobNotFound("gone")
    world.store.by_installation[IID] = [_deferred(1)]

    await run_reconcile(NOW)

    assert world.store.fact("completed")["fact"]["conclusion"] == "gone"
    assert world.store.claims == []


async def test_a_github_failure_is_an_error_not_a_start(world, monkeypatch):
    response = httpx.Response(500, request=httpx.Request("POST", "https://x"))
    monkeypatch.setattr(
        reconcile,
        "get_installation_token",
        AsyncMock(
            side_effect=httpx.HTTPStatusError("x", request=None, response=response)
        ),
    )
    world.store.by_installation[IID] = [_deferred(1)]

    result = await run_reconcile(NOW)

    assert result.started == 0
    assert result.errors == [f"{_key(1)}: HTTPStatusError"]


# --- spare VMs ---


async def test_a_young_idle_vm_excuses_one_waiting_job(world):
    world.store.by_installation[IID] = [_deferred(1), _deferred(2)]
    world.vms = [_vm("runnerforge-100-5-1", age=timedelta(minutes=2))]

    result = await run_reconcile(NOW)

    # One job is left for the spare; the other gets a VM.
    assert result.started == 1
    assert [c[0] for c in world.store.claims] == [_key(2)]


@pytest.mark.parametrize(
    "vm",
    [
        _vm("old", age=timedelta(minutes=20)),
        _vm("stopping", status="STOPPING"),
        _vm("other-tenant", iid=8),
        _vm("other-repo", repo="octo/other"),
    ],
    ids=["too-old", "not-active", "other-installation", "other-repo"],
)
async def test_vms_that_dont_count_as_spare(world, vm):
    world.store.by_installation[IID] = [_deferred(1)]
    world.vms = [vm]

    result = await run_reconcile(NOW)

    assert result.started == 1


async def test_a_busy_vm_is_not_spare(world):
    busy = _vm("runnerforge-100-5-1")
    world.vms = [busy]
    world.store.by_installation[IID] = [
        (_key(5), _record(5, in_progress={"runner_name": busy.name})),
        _deferred(1),
    ]

    result = await run_reconcile(NOW)

    assert result.started == 1


async def test_a_vm_with_other_runner_labels_is_not_spare(world):
    vm = _vm("runnerforge-100-5-1")
    vm.runner_labels = ["runnerforge", "large"]
    world.vms = [vm]
    world.store.by_installation[IID] = [_deferred(1)]

    result = await run_reconcile(NOW)

    assert result.started == 1


# --- jobs that lost their VM (limited installations) ---


async def test_a_job_whose_vm_was_taken_gets_a_new_one(world):
    own = _vm("runnerforge-100-1-1")
    world.vms = [own]
    world.store.by_installation[IID] = [
        _submitted(1),
        # Another job is running on job 1's VM.
        (_key(2), _record(2, in_progress={"runner_name": own.name})),
    ]

    result = await run_reconcile(NOW)

    assert result.started == 1
    [(key, expected, decision, _)] = world.store.claims
    assert (key, expected) == (_key(1), 0)
    assert decision["vm_name"] == "runnerforge-100-1-1-r1"
    assert decision["start"] == 1


@pytest.mark.parametrize(
    "vms", [[], [_vm("runnerforge-100-1-1", status="TERMINATED")]], ids=["gone", "dead"]
)
async def test_a_job_whose_vm_is_gone_gets_a_new_one(world, vms):
    world.vms = vms
    world.store.by_installation[IID] = [_submitted(1)]

    result = await run_reconcile(NOW)

    assert result.started == 1


async def test_a_restart_names_the_next_vm(world):
    world.store.by_installation[IID] = [
        _submitted(1, start=1, vm_name="runnerforge-100-1-1", source="reconcile")
    ]

    await run_reconcile(NOW)

    [(_, expected, decision, _)] = world.store.claims
    assert expected == 1
    assert (decision["start"], decision["vm_name"]) == (2, "runnerforge-100-1-1-r2")


@pytest.mark.parametrize(
    "rec",
    [
        # Its own VM is up and free: it will take the job.
        _submitted(1),
        # Out of starts: the batch may mark it stuck.
        _submitted(1, start=2, vm_name="runnerforge-100-1-1-r2"),
        _deferred(1, minutes_ago=24 * 60),  # about to be cancelled by GitHub
        (_key(1), _record(1, decision={"kind": "rejected"})),
        (_key(1), _record(1, completed={"conclusion": "success"})),
    ],
    ids=["own-vm-free", "start-cap", "stale", "rejected", "completed"],
)
async def test_jobs_that_are_not_waiting(world, rec):
    world.vms = [_vm("runnerforge-100-1-1")]
    world.store.by_installation[IID] = [rec]

    result = await run_reconcile(NOW)

    assert result.started == 0
    world.job.assert_not_awaited()


async def test_an_unlimited_installations_lost_vm_is_left_to_the_batch(world):
    """Unlimited tenants keep 'mark stuck' (owner decision, 3b scope)."""
    world.admission[IID] = UNLIMITED
    world.store.by_installation[IID] = [_submitted(1)]

    result = await run_reconcile(NOW)

    assert result.started == 0


# --- claims, failures, budgets ---


async def test_a_lost_claim_starts_nothing(world):
    world.store.claim_result = False
    world.store.by_installation[IID] = [_deferred(1)]

    result = await run_reconcile(NOW)

    assert result.started == 0
    world.submit.assert_not_awaited()


async def test_a_failed_start_records_its_stage(world, caplog):
    async def fail(spec, on_error):
        exc = RuntimeError("token down")
        await on_error("token", exc)
        raise exc

    world.submit.side_effect = fail
    world.store.by_installation[IID] = [_deferred(1), _deferred(2)]

    with caplog.at_level(logging.ERROR):
        result = await run_reconcile(NOW)

    assert result.started == 0
    errored = [f for f in world.store.facts if f["name"] == "errored"]
    assert [f["fact"] for f in errored] == [
        {"stage": "token", "error_class": "RuntimeError"}
    ]
    # The installation is done for the run: a token outage would burn the
    # next job's start the same way.
    assert world.submit.await_count == 1
    assert result.errors == [f"{_key(1)}: RuntimeError"]


@pytest.mark.parametrize(
    "message",
    ["QUOTA_EXCEEDED: N2_CPUS", "ZONE_RESOURCE_POOL_EXHAUSTED: europe-west4-a"],
)
async def test_a_capacity_error_stops_every_installation(world, message):
    world.registry.rows[8] = RegistryEntry(8, "approved", "active", NOW)
    world.admission[8] = LIMITED
    world.submit.side_effect = RuntimeError(message)
    world.store.by_installation[IID] = [_deferred(1)]
    world.store.by_installation[8] = [
        (_key(3), _record(3, iid=8, decision={"kind": "deferred"}))
    ]

    result = await run_reconcile(NOW)

    assert world.submit.await_count == 1
    assert result.errors == [f"{_key(1)}: RuntimeError"]


async def test_installations_take_turns(world):
    world.registry.rows[8] = RegistryEntry(8, "approved", "active", NOW)
    world.admission[8] = LIMITED
    world.store.by_installation[IID] = [_deferred(1), _deferred(2)]
    world.store.by_installation[8] = [
        (_key(3), _record(3, iid=8, decision={"kind": "deferred"}))
    ]

    await run_reconcile(NOW)

    assert [c[0] for c in world.store.claims] == [_key(1), _key(3), _key(2)]


async def test_the_run_cap_bounds_starts(world, monkeypatch):
    monkeypatch.setattr(reconcile, "MAX_STARTS_PER_RUN", 1)
    world.store.by_installation[IID] = [_deferred(1), _deferred(2)]

    result = await run_reconcile(NOW)

    assert result.started == 1


async def test_the_pass_budget_bounds_starts(world, monkeypatch):
    monkeypatch.setattr(reconcile, "PASS_BUDGET_SECONDS", -1.0)
    world.store.by_installation[IID] = [_deferred(1)]

    result = await run_reconcile(NOW)

    assert result.started == 0


async def test_lookups_are_bounded_per_free_slot(world, monkeypatch):
    monkeypatch.setattr(reconcile, "LOOKUPS_PER_SLOT", 1)
    world.job.return_value = GitHubJob(id=1, status="completed", conclusion="success")
    world.store.by_installation[IID] = [_deferred(1), _deferred(2), _deferred(3)]

    await run_reconcile(NOW)

    # 2 free slots x 1 lookup each.
    assert world.job.await_count == 2


async def test_a_failed_capacity_check_skips_the_installation(world, caplog):
    reconcile.active_vm_names.side_effect = RuntimeError("gce down")
    world.store.by_installation[IID] = [_deferred(1)]

    with caplog.at_level(logging.WARNING):
        result = await run_reconcile(NOW)

    assert result.started == 0
    failed = next(r for r in caplog.records if r.message == "Capacity check failed")
    assert failed.error_class == "RuntimeError"


async def test_a_record_without_a_repo_is_skipped(world):
    key, rec = _deferred(1)
    del rec["repo"]
    world.store.by_installation[IID] = [(key, rec)]

    result = await run_reconcile(NOW)

    assert result.started == 0
    world.job.assert_not_awaited()


# --- the batch, alongside the pass ---


async def test_the_batch_leaves_a_managed_jobs_verdict_to_the_pass(world):
    """A limited tenant's job with no VM and starts left is waiting, not
    stuck, even when the pass couldn't start it this run (no free slot)."""
    world.active = {"a", "b"}
    key, rec = _submitted(1)
    world.store.due = [(key, rec)]
    world.store.by_installation[IID] = [(key, rec)]

    result = await run_reconcile(NOW)

    assert result.stuck == 0
    world.preempted.assert_not_awaited()
    assert [f["name"] for f in world.store.facts] == []


async def test_the_batch_marks_a_job_out_of_starts_stuck_on_its_current_vm(world):
    key, rec = _submitted(1, start=2, vm_name="runnerforge-100-1-1-r2")
    world.store.due = [(key, rec)]

    result = await run_reconcile(NOW)

    assert result.stuck == 1
    world.preempted.assert_awaited_once_with("runnerforge-100-1-1-r2")


async def test_the_batch_sees_a_started_vm_by_its_name(world):
    key, rec = _submitted(1, start=1, vm_name="runnerforge-100-1-1-r1")
    world.vms = [_vm("runnerforge-100-1-1-r1")]
    world.store.due = [(key, rec)]

    await run_reconcile(NOW)

    assert world.store.fact("vm")["fact"]["outcome"] == "success"


async def test_the_batch_skips_records_the_pass_just_started(world):
    key, rec = _deferred(1)
    world.store.due = [(key, rec)]
    world.store.by_installation[IID] = [(key, rec)]

    result = await run_reconcile(NOW)

    assert (result.started, result.checked) == (1, 0)


async def test_a_job_decided_moments_ago_is_given_time(world):
    """The run's VM listing may not show a VM created seconds ago."""
    world.store.by_installation[IID] = [_submitted(1, at=NOW - timedelta(minutes=1))]

    result = await run_reconcile(NOW)

    assert result.started == 0
    world.job.assert_not_awaited()


async def test_a_start_reuses_the_runs_installation_token(world):
    world.store.by_installation[IID] = [_deferred(1)]

    await run_reconcile(NOW)

    assert _spec(world).installation_token == "ghs"


async def test_started_vms_are_handed_to_a_watcher(world):
    handle = reconcile.OperationHandle(
        name="op-1",
        project="p",
        zone="z",
        machine_type="n2-standard-2",
        spot=True,
        image_version="0.2.0",
    )
    world.submit.return_value = handle
    world.store.by_installation[IID] = [_deferred(1)]
    watched = []

    await run_reconcile(NOW, on_started=watched.append)

    [watch] = watched
    assert (watch.name, watch.job_key, watch.job_base) == ("op-1", _key(1), {})


async def test_an_existing_vm_is_not_watched(world):
    world.submit.return_value = None  # AlreadyExists: an earlier attempt made it
    world.store.by_installation[IID] = [_deferred(1)]
    watched = []

    result = await run_reconcile(NOW, on_started=watched.append)

    assert (result.started, watched) == (1, [])


# --- registry and capacity trouble ---


async def test_an_unreadable_registry_rejects_nothing(world):
    world.admission[IID] = Admission(False, "registry_unavailable")
    key, rec = _submitted(1)
    world.store.by_installation[IID] = [_deferred(2), (key, rec)]
    world.store.due = [(key, rec)]

    result = await run_reconcile(NOW)

    # No rejection: the only patch is the batch's reschedule.
    assert not any("decision" in fields for _, fields in world.store.patches)
    assert world.store.queried == []
    # Its verdicts wait too.
    assert result.stuck == 0


def _capacity_failed(
    job_id: int,
    minutes_ago: int,
    start: int = 1,
    code: str = "ZONE_RESOURCE_POOL_EXHAUSTED",
    retries: int = 0,
    watched: str | None = None,
) -> tuple:
    vm_name = f"runnerforge-100-{job_id}-1-r{start}"
    key, rec = _submitted(
        job_id,
        start=start,
        vm_name=vm_name,
        machine_type="n2-standard-2",
        capacity_retries=retries,
    )
    rec["vm"] = {
        "outcome": "failure",
        "error_code": code,
        "vm_name": watched or vm_name,
        "at": NOW - timedelta(minutes=minutes_ago),
    }
    return key, rec


async def test_a_recent_quota_failure_pauses_every_start(world, caplog):
    world.store.by_installation[IID] = [
        _capacity_failed(1, 2, code="QUOTA_EXCEEDED"),
        _deferred(2),
    ]

    with caplog.at_level(logging.WARNING):
        result = await run_reconcile(NOW)

    assert result.started == 0
    assert world.store.claims == []
    assert "Capacity pass paused: quota full" in [r.message for r in caplog.records]


async def test_a_recent_stockout_pauses_only_its_machine_type(world, caplog):
    other = _deferred(3)
    other[1]["labels"] = ["runnerforge", "medium"]
    world.store.by_installation[IID] = [_capacity_failed(1, 2), _deferred(2), other]

    with caplog.at_level(logging.WARNING):
        await run_reconcile(NOW)

    # small (n2-standard-2) is stocked out; medium still starts.
    assert [c[0] for c in world.store.claims] == [_key(3)]
    paused = next(
        r
        for r in caplog.records
        if r.message == "Capacity pass paused for stocked-out machine types"
    )
    assert paused.machine_types == ["n2-standard-2"]


@pytest.mark.parametrize("start", [1, 2], ids=["first", "last"])
async def test_a_start_that_failed_for_capacity_is_retried_without_counting(
    world, start
):
    world.store.by_installation[IID] = [_capacity_failed(1, 20, start=start)]

    await run_reconcile(NOW)

    [(_, expected, decision, _)] = world.store.claims
    assert expected == start
    # Same attempt again: GCE never made that VM.
    assert (decision["start"], decision["vm_name"], decision["capacity_retries"]) == (
        start,
        f"runnerforge-100-1-1-r{start}",
        1,
    )


async def test_capacity_retries_are_capped(world):
    world.store.by_installation[IID] = [
        _capacity_failed(1, 20, start=2, retries=reconcile.MAX_CAPACITY_RETRIES)
    ]

    result = await run_reconcile(NOW)

    assert result.started == 0


async def test_a_late_watcher_of_an_earlier_start_is_not_a_refund(world):
    """The failure names the VM of start 1; the record is on start 2."""
    world.store.by_installation[IID] = [
        _capacity_failed(1, 20, start=2, watched="runnerforge-100-1-1-r1")
    ]

    result = await run_reconcile(NOW)

    # Start 2 was the last; its own VM is gone and nothing refunds it.
    assert result.started == 0


# --- the batch keeps marking what the pass won't start ---


@pytest.mark.parametrize(
    ("admission", "decision"),
    [
        (UNLIMITED, {"kind": "vm_submitted"}),
        (Admission(False, "suspended", record=True), {"kind": "vm_submitted"}),
        (LIMITED, {"kind": "already_exists"}),
        (LIMITED, None),
    ],
    ids=["unlimited", "not-admitted", "already-exists", "no-decision"],
)
async def test_the_batch_marks_jobs_the_pass_wont_start_stuck(
    world, admission, decision
):
    world.admission[IID] = admission
    key = _key(1)
    rec = _record(1)
    if decision is not None:
        rec["decision"] = {**decision, "at": NOW - timedelta(minutes=10)}
    world.store.due = [(key, rec)]

    result = await run_reconcile(NOW)

    assert result.stuck == 1
    world.preempted.assert_awaited_once_with("runnerforge-100-1-1")
