import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from runnerforge import reconcile
from runnerforge.github_client import JobNotFound
from runnerforge.job_store import CreateResult, NoopJobStore
from runnerforge.models import GitHubJob, RunnerForgeVmLabels, VmInfo
from runnerforge.reconcile import run_reconcile

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
KEY = "100-200-1"
OWN_VM = f"runnerforge-{KEY}"
REPO = "octo/repo"


def _record(**facts) -> dict:
    return {
        "installation_id": 7,
        "repo": REPO,
        "job_id": 200,
        "labels": ["runnerforge", "small"],
        "open": True,
        **facts,
    }


def _vm(
    name: str,
    repo: str | None = REPO,
    runner_labels: list[str] | None = None,
    **label_overrides,
) -> VmInfo:
    labels = {
        "runner": "runnerforge",
        "job_id": "200",
        "run_id": "100",
        "run_attempt": "1",
        "installation_id": "7",
        "repository_id": "9",
        "queued_received_at": str(int(NOW.timestamp())),
        **label_overrides,
    }
    return VmInfo(
        name=name,
        creation_timestamp=NOW - timedelta(minutes=10),
        labels=RunnerForgeVmLabels(**labels),
        repo_full_name=repo,
        runner_labels=["runnerforge", "small"]
        if runner_labels is None
        else runner_labels,
    )


def _github(status: str, conclusion: str | None = None, runner: str | None = None):
    return GitHubJob(id=200, status=status, conclusion=conclusion, runner_name=runner)


@pytest.fixture
def world(monkeypatch, fake_job_store):
    """The reconciler's outside world: GCE VMs, GitHub, the audit log."""

    class World:
        def __init__(self) -> None:
            self.store = fake_job_store
            self.vms: list[VmInfo] = []
            self.token = AsyncMock(return_value="ghs_token")
            self.job = AsyncMock(return_value=_github("queued"))
            self.preempted = AsyncMock(return_value=False)

    world = World()
    monkeypatch.setattr(
        "runnerforge.reconcile.list_runnerforge_vms",
        AsyncMock(side_effect=lambda: world.vms),
    )
    monkeypatch.setattr("runnerforge.reconcile.get_installation_token", world.token)
    monkeypatch.setattr("runnerforge.reconcile.get_job", world.job)
    monkeypatch.setattr("runnerforge.reconcile.preemption_status", world.preempted)
    return world


def _patch(world, key: str = KEY) -> dict:
    [fields] = [f for k, f in world.store.patches if k == key]
    return fields


# --- store disabled ---


async def test_disabled_store_does_nothing(monkeypatch, caplog):
    monkeypatch.setattr("runnerforge.job_store._store", NoopJobStore())
    vms = AsyncMock()
    monkeypatch.setattr("runnerforge.reconcile.list_runnerforge_vms", vms)

    with caplog.at_level(logging.INFO):
        result = await run_reconcile(NOW)

    assert result.checked == 0
    vms.assert_not_awaited()
    assert any(
        r.message == "Reconcile skipped: job store disabled" for r in caplog.records
    )


# --- closing records ---


async def test_record_already_completed_is_closed_without_github(world):
    world.store.due = [(KEY, _record(completed={"conclusion": "success"}))]

    result = await run_reconcile(NOW)

    assert world.store.patches == [(KEY, {"open": False})]
    world.job.assert_not_awaited()
    assert (result.checked, result.closed, result.facts) == (1, 1, 0)


async def test_completed_on_github_records_conclusion(world):
    world.store.due = [(KEY, _record())]
    world.job.return_value = _github("completed", "success", "runnerforge-9-9-1")

    result = await run_reconcile(NOW)

    [fact] = world.store.facts
    assert (fact["name"], fact["fact"], fact["source"]) == (
        "completed",
        {"conclusion": "success", "runner_name": "runnerforge-9-9-1"},
        "reconcile",
    )
    world.job.assert_awaited_once_with("ghs_token", REPO, 200)
    world.token.assert_awaited_once_with(7)
    assert result.closed == 1
    assert world.store.patches == []  # closed records aren't rescheduled


async def test_job_gone_on_github_closes_record(world):
    world.store.due = [(KEY, _record())]
    world.job.side_effect = JobNotFound("gone")

    result = await run_reconcile(NOW)

    assert world.store.fact("completed")["fact"] == {"conclusion": "gone"}
    assert result.closed == 1


async def test_uninstalled_app_closes_records_with_one_token_request(world):
    request = httpx.Request("POST", "https://api.github.com/x")
    world.token.side_effect = httpx.HTTPStatusError(
        "404", request=request, response=httpx.Response(404, request=request)
    )
    world.store.due = [(KEY, _record()), ("100-201-1", _record(job_id=201))]

    result = await run_reconcile(NOW)

    assert result.closed == 2
    assert world.token.await_count == 1  # the failure is cached per run
    world.job.assert_not_awaited()


async def test_token_error_is_recorded_and_record_rescheduled(world, caplog):
    request = httpx.Request("POST", "https://api.github.com/x")
    world.token.side_effect = httpx.HTTPStatusError(
        "500", request=request, response=httpx.Response(500, request=request)
    )
    world.store.due = [(KEY, _record()), ("100-201-1", _record(job_id=201))]

    with caplog.at_level(logging.ERROR):
        result = await run_reconcile(NOW)

    assert result.errors == [f"{KEY}: HTTPStatusError", "100-201-1: HTTPStatusError"]
    assert world.token.await_count == 1
    assert world.store.facts == []
    assert _patch(world)["next_check_at"] == NOW + timedelta(minutes=5)
    assert any(r.message == "Reconcile failed for record" for r in caplog.records)


async def test_record_without_identity_is_skipped(world, caplog):
    world.store.due = [(KEY, {"open": True})]

    with caplog.at_level(logging.WARNING):
        await run_reconcile(NOW)

    world.job.assert_not_awaited()
    # Slow start: it can't be resolved soon.
    assert _patch(world) == {"next_check_at": NOW + timedelta(hours=1), "checks": 1}
    assert any(
        r.message == "Reconcile skipped record without identity" for r in caplog.records
    )


# --- running ---


async def test_running_job_gets_in_progress_fact_once(world):
    world.job.return_value = _github("in_progress", runner="runnerforge-9-9-1")
    world.store.due = [
        (KEY, _record()),
        ("100-201-1", _record(job_id=201, in_progress={"runner_name": "x"})),
    ]

    await run_reconcile(NOW)

    [fact] = world.store.facts
    assert fact["key"] == KEY
    assert fact["fact"] == {"runner_name": "runnerforge-9-9-1"}
    assert _patch(world)["next_check_at"] == NOW + timedelta(hours=1)


# --- still queued ---


@pytest.mark.parametrize(
    "facts",
    [
        {"decision": {"kind": "policy_rejected"}},
        {"errored": {"stage": "token", "at": NOW}},
        {
            "decision": {"kind": "vm_submitted", "at": NOW - timedelta(minutes=1)},
            "errored": {"stage": "token", "at": NOW},
        },
    ],
    ids=["rejected", "errored", "errored-after-decision"],
)
async def test_queued_job_that_failed_to_start_gets_no_vm_fact(world, facts):
    world.store.due = [(KEY, _record(**facts))]

    await run_reconcile(NOW)

    assert world.store.facts == []
    world.preempted.assert_not_awaited()
    assert _patch(world)["next_check_at"] == NOW + timedelta(hours=1)


async def test_redelivery_after_error_is_judged_by_its_vm(world):
    """A decision newer than the error means the redelivery got a VM."""
    world.store.due = [
        (
            KEY,
            _record(
                errored={"stage": "token", "at": NOW - timedelta(minutes=2)},
                decision={"kind": "vm_submitted", "at": NOW - timedelta(minutes=1)},
            ),
        )
    ]
    world.vms = [_vm(OWN_VM)]

    await run_reconcile(NOW)

    assert world.store.fact("vm")["fact"]["outcome"] == "success"


async def test_queued_job_with_own_vm_gets_vm_success_when_watcher_missed_it(world):
    """The watcher often never finishes (CPU throttled after the response)."""
    world.store.due = [(KEY, _record()), ("100-201-1", _record(job_id=201, vm={}))]
    world.vms = [_vm(OWN_VM), _vm("runnerforge-100-201-1", job_id="201")]

    await run_reconcile(NOW)

    [fact] = world.store.facts
    assert (fact["key"], fact["name"], fact["fact"]) == (
        KEY,
        "vm",
        {"outcome": "success", "error_code": None},
    )
    assert _patch(world)["next_check_at"] == NOW + timedelta(minutes=5)


@pytest.mark.parametrize(
    ("preempted", "name", "fact"),
    [
        (True, "preempted", {"phase": "before_pickup"}),
        (False, "vm", {"outcome": "missing", "error_code": None}),
    ],
)
async def test_queued_job_without_any_runner_is_stuck(world, preempted, name, fact):
    world.store.due = [(KEY, _record(decision={"kind": "vm_submitted"}))]
    world.preempted.return_value = preempted
    # Not spare: another repo, a busy runner, and labels that don't cover it.
    busy = "runnerforge-100-300-1"
    world.vms = [
        _vm("runnerforge-1-2-1", repo="octo/other"),
        _vm(busy, job_id="300"),
        _vm(
            "runnerforge-100-400-1",
            job_id="400",
            runner_labels=["runnerforge", "large"],
        ),
    ]
    world.store.open = [
        ("100-500-1", _record(job_id=500, in_progress={"runner_name": busy}))
    ]

    result = await run_reconcile(NOW)

    world.preempted.assert_awaited_once_with(OWN_VM)
    recorded = world.store.fact(name)
    assert recorded["fact"] == fact
    assert recorded["source"] == "reconcile"
    assert result.stuck == 1


@pytest.mark.parametrize(
    ("job_labels", "spare_labels"),
    [
        (["runnerforge", "small"], ["runnerforge", "small", "self-hosted"]),
        (["runnerforge", "small"], []),
        # GitHub adds self-hosted/linux/x64 to every runner we boot.
        (["self-hosted", "Linux", "runnerforge", "small"], ["runnerforge", "small"]),
        (["RunnerForge", "small"], ["runnerforge", "SMALL"]),
    ],
    ids=["labels-cover-the-job", "labels-unknown", "default-labels", "case"],
)
async def test_queued_job_waits_while_another_runner_can_take_it(
    world, job_labels, spare_labels
):
    """GitHub may hand the job to another idle VM with matching labels."""
    world.store.due = [(KEY, _record(labels=job_labels))]
    world.vms = [_vm("runnerforge-100-300-1", job_id="300", runner_labels=spare_labels)]
    world.store.open = [("100-300-1", _record(job_id=300))]  # open, not running

    result = await run_reconcile(NOW)

    assert world.store.facts == []
    assert result.stuck == 0
    world.preempted.assert_not_awaited()


@pytest.mark.parametrize(
    "facts",
    [
        {"preempted": {"phase": "before_pickup"}},
        {"vm": {"outcome": "missing"}},
        {"vm": {"outcome": "failure"}},
    ],
)
async def test_already_stuck_job_is_not_marked_again(world, facts):
    world.store.due = [(KEY, _record(**facts))]

    result = await run_reconcile(NOW)

    assert world.store.facts == []
    assert result.stuck == 0


# --- backfill ---


async def test_vm_without_open_record_gets_one(world, fake_job_store):
    world.vms = [_vm(OWN_VM)]

    result = await run_reconcile(NOW)

    [(key, base)] = fake_job_store.created
    assert key == KEY
    assert base["installation_id"] == 7
    assert base["repository_id"] == 9
    assert base["repo"] == REPO
    assert base["labels"] == ["runnerforge", "small"]
    assert base["queued_at"] == NOW
    assert base["expire_at"] == NOW + timedelta(days=90)
    assert base["next_check_at"] == NOW + timedelta(minutes=5)
    assert result.backfilled == 1


async def test_backfill_uses_vm_creation_when_received_time_missing(
    world, fake_job_store
):
    world.vms = [_vm(OWN_VM, queued_received_at="", repository_id="")]
    fake_job_store.create_result = CreateResult.EXISTS_CLOSED

    result = await run_reconcile(NOW)

    [(_, base)] = fake_job_store.created
    assert base["queued_at"] == NOW - timedelta(minutes=10)
    assert base["repository_id"] is None
    assert result.backfilled == 0  # it existed already


async def test_backfill_skips_vms_it_cannot_identify_or_already_tracks(
    world, fake_job_store
):
    world.vms = [
        _vm(OWN_VM),  # has an open record
        _vm("runnerforge-x", repo=None),  # unknown repo
        _vm("runnerforge-y", job_id="not-a-number"),
    ]
    world.store.open = [(KEY, _record())]

    await run_reconcile(NOW)

    assert fake_job_store.created == []


# --- batching and backoff ---


async def test_batch_is_capped_and_reports_more_due(world):
    world.job.return_value = _github("completed", "success")
    world.store.due = [(f"100-{i}-1", _record(job_id=i)) for i in range(30)]

    result = await run_reconcile(NOW)

    assert result.checked == reconcile.BATCH_SIZE
    assert result.more_due is True
    assert world.job.await_count == reconcile.BATCH_SIZE


@pytest.mark.parametrize(
    ("checks", "delay"),
    [
        (0, timedelta(minutes=5)),
        (1, timedelta(minutes=15)),
        (2, timedelta(hours=1)),
        (3, timedelta(hours=6)),
        (9, timedelta(hours=6)),
    ],
)
async def test_backoff_grows_with_checks(world, checks, delay):
    world.store.due = [(KEY, _record(checks=checks))]
    world.vms = [_vm(OWN_VM)]

    await run_reconcile(NOW)

    assert _patch(world) == {"next_check_at": NOW + delay, "checks": checks + 1}


async def test_one_failing_record_does_not_stop_the_run(world):
    world.job.side_effect = [RuntimeError("boom"), _github("completed", "success")]
    world.store.due = [(KEY, _record()), ("100-201-1", _record(job_id=201))]

    result = await run_reconcile(NOW)

    assert result.errors == [f"{KEY}: RuntimeError"]
    assert result.closed == 1
    assert _patch(world)["checks"] == 1


async def test_defaults_to_current_time(world):
    world.store.due = [(KEY, _record())]
    world.vms = [_vm(OWN_VM)]

    await run_reconcile()

    assert _patch(world)["next_check_at"] > NOW


async def test_own_vm_running_another_job_does_not_count(world):
    """Cross-assignment: GitHub gave this job's VM to another job. Ephemeral
    runners take one job each, so this job is stuck if nothing else can."""
    world.store.due = [(KEY, _record(decision={"kind": "vm_submitted"}))]
    world.vms = [_vm(OWN_VM)]
    world.store.open = [
        ("100-201-1", _record(job_id=201, in_progress={"runner_name": OWN_VM}))
    ]

    result = await run_reconcile(NOW)

    assert world.store.fact("vm")["fact"] == {"outcome": "missing", "error_code": None}
    assert result.stuck == 1


async def test_unknown_preemption_answer_records_nothing(world):
    """A failed audit-log query must not become a permanent `missing`."""
    world.store.due = [(KEY, _record())]
    world.preempted.return_value = None

    result = await run_reconcile(NOW)

    world.preempted.assert_awaited_once_with(OWN_VM)
    assert world.store.facts == []
    assert result.stuck == 0
    assert _patch(world)["checks"] == 1


async def test_run_stops_when_its_budget_is_spent(world, monkeypatch):
    monkeypatch.setattr(reconcile, "RUN_BUDGET_SECONDS", -1.0)
    world.store.due = [(KEY, _record())]

    result = await run_reconcile(NOW)

    assert result.checked == 0
    assert result.more_due is True
    world.job.assert_not_awaited()


async def test_slow_record_times_out_and_is_retried_later(world, monkeypatch):
    async def hang(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(reconcile, "RECORD_TIMEOUT_SECONDS", 0.01)
    world.job.side_effect = hang
    world.store.due = [(KEY, _record())]

    result = await run_reconcile(NOW)

    assert result.errors == [f"{KEY}: TimeoutError"]
    assert _patch(world)["checks"] == 1
