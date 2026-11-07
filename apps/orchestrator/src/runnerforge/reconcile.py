"""Reconciler (ADR-018 R3).

Brings open job records in line with GitHub and GCE for what the webhooks and
the background tasks missed: lost webhooks, watchers that never finished, and
Spot VMs preempted before they picked up a job. Cloud Scheduler calls it every
5 minutes through POST /reconcile, so it runs inside a request and its CPU
isn't throttled the way background tasks are.

Every fact it writes has `source: reconcile`. VM labels stay the authority for
which VMs exist.

Each run first cleans up after installations GitHub cut off (suspended or
uninstalled, ADR-019 step 4), then deletes orphan VMs: runners GitHub no
longer lists, or lists as idle with no job they could take. With
ADMISSION=registry, it then starts VMs for jobs waiting under an
installation's limit (ADR-019, the capacity pass). Last, it checks due
records.
"""

import asyncio
import logging
import time
import zlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx

from runnerforge.admission import (
    Admission,
    decide_admission,
    exceeds_size,
    registry_backfill_due,
    sync_registry,
    vm_settings,
)
from runnerforge.compute_client import (
    ACTIVE_STATUSES,
    ACTIVE_VMS_TIMEOUT_SECONDS,
    OperationHandle,
    active_vm_names,
    delete_vm,
    list_runnerforge_vms,
    preemption_status,
    turn_off_serial_logging,
)
from runnerforge.config import ADMISSION, ALLOWED_INSTALLATION_IDS
from runnerforge.github_client import (
    JobNotFound,
    RepoNotFound,
    delete_runner,
    get_app_installation,
    get_installation_token,
    get_job,
    get_runner,
)
from runnerforge.handlers import RunnerVmSpec, submit_runner_vm
from runnerforge.job_store import (
    FIRST_CHECK_DELAY,
    RETENTION,
    SCHEMA_VERSION,
    CreateResult,
    JobStore,
    get_job_store,
    job_key,
)
from runnerforge.machine_policy import MachinePolicyError, resolve_labels
from runnerforge.models import GitHubJob, ReconcileResult, VmInfo
from runnerforge.registry import DELETED_RETENTION, RegistryEntry, get_registry

logger = logging.getLogger(__name__)

BATCH_SIZE = 25
# Delay before a record's next check, indexed by how many checks it has had.
BACKOFF = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
)
# Records that can't close soon (rejected, failed to start, running) skip
# straight to the 1 h step, so they don't crowd out new jobs.
SLOW_START = 2
# Every open record at beta scale; the stuck rule reads their runners.
OPEN_LIMIT = 500
# A run stops starting new records after this long and reports more_due, so
# slow GitHub or Logging calls can't make runs overlap (the Scheduler job's
# attempt deadline sits above it). Each record also gets its own bound.
RUN_BUDGET_SECONDS = 120.0
RECORD_TIMEOUT_SECONDS = 30.0
# GitHub adds these to every runner we boot (see machine_policy).
_DEFAULT_RUNNER_LABELS = frozenset({"self-hosted", "linux", "x64"})

# Capacity pass (ADR-019). It runs first, in its own slice of the run budget.
PASS_BUDGET_SECONDS = 40.0
START_TIMEOUT_SECONDS = 15.0
MAX_STARTS_PER_RUN = 10
# Reconciler starts per job; past this, the batch may mark it stuck.
MAX_STARTS_PER_JOB = 2
# Open records read per installation, oldest first. Records that can't start
# (rejected, out of starts) stay open until GitHub cancels them, so the
# window is wide enough that they don't hide the ones that can.
PASS_RECORD_LIMIT = 100
# GitHub job lookups per free slot, so skippable jobs can't use up a run.
LOOKUPS_PER_SLOT = 3
# A VM this young may still register and take a waiting job; an older idle
# one probably never will (its runner died or never registered).
SPARE_VM_MAX_AGE = timedelta(minutes=15)
# A decision this recent may have a VM the run's listing doesn't show yet.
START_GRACE = timedelta(minutes=3)
# GitHub cancels a job queued for 24 h; a VM started this late would idle.
STALE_QUEUED = timedelta(hours=23, minutes=30)
# GCE errors on the create operation that say the zone or quota is full. A
# start that failed this way isn't counted against the job, and the pass
# pauses for a while: more starts would fail the same way.
CAPACITY_ERRORS = frozenset(
    {"QUOTA_EXCEEDED", "ZONE_RESOURCE_POOL_EXHAUSTED", "RESOURCE_POOL_EXHAUSTED"}
)
CAPACITY_PAUSE = timedelta(minutes=10)
# Uncounted retries per job after capacity failures, so a machine type that
# stays stocked out can't keep a job (and its pause) going until GitHub
# cancels it.
MAX_CAPACITY_RETRIES = 3

# Orphan cleanup. Past the spare window, GitHub has had its chance to hand an
# idle runner a job. Its own slice of the run budget, before the pass.
ORPHAN_MIN_AGE = SPARE_VM_MAX_AGE
ORPHAN_BUDGET_SECONDS = 30.0
ORPHAN_CHECK_TIMEOUT_SECONDS = 15.0
MAX_ORPHAN_CHECKS = 10

# Cut-off cleanup (ADR-019 step 4): installations GitHub suspended or
# uninstalled. Each is confirmed with GitHub first: the row's status comes
# from whichever event arrived last. It runs first, in its own slice.
CUTOFF_BUDGET_SECONDS = 30.0
CUTOFF_CALL_TIMEOUT_SECONDS = 10.0
MAX_CUTOFF_CONFIRMS = 10
MAX_CUTOFF_DELETES = 10
# A suspended installation's VM that is on a job gets this long, which
# absorbs a quick unsuspend; an idle one goes at once (it could take jobs
# nobody would tell us about). An uninstalled installation's go at once.
CUTOFF_BUSY_GRACE = timedelta(minutes=15)
# A suspended installation's records aren't checked (GitHub refuses the
# calls); they're looked at again this often, so they resume soon after an
# unsuspend.
CUTOFF_PAUSE = timedelta(minutes=15)
# Job records deleted per uninstalled installation per run.
PURGE_LIMIT = 200

# Serial logging backstop (ADR-020): VMs the in_progress webhook didn't turn
# off. Its own slice of the run budget, after the orphan cleanup.
SERIAL_BUDGET_SECONDS = 15.0
MAX_SERIAL_OFFS = 10
# The webhook turns it off within seconds of writing the in_progress fact; a
# younger fact may still have that webhook in flight.
SERIAL_BUSY_GRACE = timedelta(minutes=2)
# Past boot and registration. A VM that never gets a job (a spare, a
# cancelled job's) gets no in_progress, but stops logging too.
SERIAL_BOOT_WINDOW = timedelta(minutes=10)
# Asking GitHub whether such a VM's runner is on a job after all: its
# in_progress webhook was lost before a record check wrote the fact, or the
# open records couldn't all be read. One timeout for all of a run's checks.
SERIAL_RUNNER_CHECK_TIMEOUT_SECONDS = 5.0


class _InstallationGone(Exception):
    """The App installation is gone (token request returned 404)."""


@dataclass(frozen=True)
class _Cutoff:
    """An installation GitHub confirmed as cut off this run."""

    status: str  # "suspended" | "deleted"
    since: datetime  # when it was cut off: only VMs made before it are touched


async def run_reconcile(
    now: datetime | None = None,
    on_started: Callable[[OperationHandle], None] | None = None,
) -> ReconcileResult:
    """`on_started` gets the operation of each VM the capacity pass starts,
    so the caller can watch it like the webhook path does."""
    store = get_job_store()
    if not store.enabled:
        logger.info("Reconcile skipped: job store disabled")
        return ReconcileResult(
            checked=0,
            closed=0,
            facts=0,
            stuck=0,
            backfilled=0,
            more_due=False,
            errors=[],
        )
    now = now or datetime.now(UTC)
    counts: Counter[str] = Counter()
    started = time.monotonic()
    # ADR-019: keep the tenant registry in step with GitHub, once an hour.
    registry_changes = await sync_registry() if registry_backfill_due(now) else 0
    errors: list[str] = []

    # One extra record tells us whether more were due than a batch holds.
    due = await store.list_due(now, BATCH_SIZE + 1)
    more_due = len(due) > BATCH_SIZE
    due = due[:BATCH_SIZE]
    vms = await list_runnerforge_vms()
    listed = await store.list_open(OPEN_LIMIT)
    open_records = listed or []
    # Runners that are on a job right now. GitHub gives a queued job to any
    # idle runner with matching labels, so a VM's name says which job caused
    # it, not which job it is running.
    busy_runners = {
        rec["in_progress"].get("runner_name")
        for _, rec in open_records
        if isinstance(rec.get("in_progress"), dict)
    }

    # Read in both modes for the cut-off cleanup; None is a failed read.
    all_rows = await get_registry().list_all()
    cutoff_deadline = time.monotonic() + CUTOFF_BUDGET_SECONDS
    cutoffs = await _confirm_cutoffs(now, all_rows, counts, cutoff_deadline)

    # A cut-off installation's VMs get no records back: it sends no
    # webhooks, and an uninstalled one's records are being deleted.
    counts["backfilled"] = await _backfill(
        store,
        [vm for vm in vms if _installation(vm) not in cutoffs],
        {k for k, _ in open_records},
    )

    tokens: dict[int, str | Exception] = {}
    rows = (all_rows or {}) if ADMISSION == "registry" else {}
    # Before the pass and the batch, so both see the freed slots.
    deleted = await _delete_cutoff_vms(
        now,
        vms,
        cutoffs,
        # Every VM counts as busy unless all open records were read.
        busy_runners if listed is not None and len(listed) < OPEN_LIMIT else None,
        counts,
        errors,
        cutoff_deadline,
    )
    await _purge_cutoff_records(
        store, vms, cutoffs, all_rows or {}, counts, cutoff_deadline
    )
    deleted |= await _delete_orphans(
        now,
        [vm for vm in vms if vm.name not in deleted],
        listed,
        busy_runners,
        tokens,
        counts,
        errors,
        rows,
    )
    vms = [vm for vm in vms if vm.name not in deleted]
    await _serial_logging_pass(now, vms, open_records, tokens, errors)
    # Before the batch, so a slow batch can't starve it.
    managed: set[int] = set()
    started_keys: set[str] = set()
    if ADMISSION == "registry":
        managed, started_keys = await _capacity_pass(
            store,
            now,
            vms,
            busy_runners,
            tokens,
            counts,
            errors,
            on_started,
            rows,
            deleted,
        )
    # A started job's record changed; the batch's copy is stale.
    due = [(key, rec) for key, rec in due if key not in started_keys]
    checked = 0
    for key, rec in due:
        if time.monotonic() - started > RUN_BUDGET_SECONDS:
            more_due = True
            break
        if _record_installation(rec) in cutoffs:
            # Suspended: GitHub refuses the calls, and the suspension may be
            # lifted, so no verdict. Uninstalled: the record is being
            # deleted. Either way, look again later without counting it, so
            # these records can't hold the head of every batch.
            await store.patch(key, {"next_check_at": now + CUTOFF_PAUSE})
            continue
        checked += 1
        try:
            async with asyncio.timeout(RECORD_TIMEOUT_SECONDS):
                await _reconcile_one(
                    store, key, rec, now, vms, busy_runners, tokens, counts, managed
                )
        # Deliberately broad, like the sweep: one record that fails must not
        # stop the run. It is still pushed back, or a record that always fails
        # would sit at the head of every batch.
        except Exception as e:  # noqa: BLE001
            errors.append(f"{key}: {type(e).__name__}")
            logger.error(
                "Reconcile failed for record", extra={"job_key": key, "error": str(e)}
            )
            await _reschedule(store, key, rec, now)

    return ReconcileResult(
        checked=checked,
        closed=counts["closed"],
        facts=counts["facts"],
        stuck=counts["stuck"],
        backfilled=counts["backfilled"],
        more_due=more_due,
        errors=errors,
        registry_changes=registry_changes + counts["registry_repairs"],
        started=counts["started"],
        orphans_deleted=counts["orphans_deleted"],
        cutoff_vms_deleted=counts["cutoff_vms_deleted"],
        cutoff_records_deleted=counts["cutoff_records_deleted"],
    )


async def _backfill(store: JobStore, vms: list[VmInfo], open_keys: set[str]) -> int:
    """A VM whose job has no open record (its `queued` write failed) gets one,
    so the job is reconciled like any other. VMs whose record is closed are
    left alone: create_if_absent finds them and creates nothing."""
    created = 0
    for vm in vms:
        base = _base_from_vm(vm)
        if base is None:
            continue
        key = job_key(base["run_id"], base["job_id"], base["run_attempt"])
        if key in open_keys:
            continue
        if await store.create_if_absent(key, base) is CreateResult.CREATED:
            created += 1
    return created


def _base_from_vm(vm: VmInfo) -> dict[str, Any] | None:
    """Base fields from a VM's labels and metadata, or None if they can't
    identify a job GitHub can be asked about."""
    labels = vm.labels
    if vm.repo_full_name is None or not (
        labels.run_id.isdigit()
        and labels.job_id.isdigit()
        and labels.run_attempt.isdigit()
        and labels.installation_id.isdigit()
    ):
        return None
    received = labels.queued_received_at
    queued_at = (
        datetime.fromtimestamp(int(received), UTC)
        if received.isdigit()
        else vm.creation_timestamp
    )
    return {
        "installation_id": int(labels.installation_id),
        "repository_id": int(labels.repository_id)
        if labels.repository_id.isdigit()
        else None,
        "repo": vm.repo_full_name,
        "run_id": int(labels.run_id),
        "job_id": int(labels.job_id),
        "run_attempt": int(labels.run_attempt),
        "labels": vm.runner_labels,
        "queued_at": queued_at,
        "expire_at": queued_at + RETENTION,
        "next_check_at": queued_at + FIRST_CHECK_DELAY,
        "schema_version": SCHEMA_VERSION,
    }


async def _reconcile_one(
    store: JobStore,
    key: str,
    rec: dict[str, Any],
    now: datetime,
    vms: list[VmInfo],
    busy_runners: set[str | None],
    tokens: dict[int, str | Exception],
    counts: Counter[str],
    managed: set[int],
) -> None:
    async def write(name: str, fact: dict[str, Any]) -> None:
        # The record exists, so no base fields are needed.
        await store.record_fact(key, {}, name, fact, source="reconcile")
        counts["facts"] += 1

    if isinstance(rec.get("completed"), dict):
        # Reopened by a late in_progress: close again, no need to ask GitHub.
        await store.patch(key, {"open": False})
        counts["closed"] += 1
        return

    repo, job_id = rec.get("repo"), rec.get("job_id")
    installation_id = rec.get("installation_id")
    if not repo or job_id is None or installation_id is None:
        logger.warning(
            "Reconcile skipped record without identity", extra={"job_key": key}
        )
        await _reschedule(store, key, rec, now, slow=True)
        return

    try:
        token = await _token(int(installation_id), tokens)
        job = await get_job(token, repo, int(job_id))
    except JobNotFound, _InstallationGone:
        await write("completed", {"conclusion": "gone"})
        counts["closed"] += 1
        return

    if job.status == "completed":
        await write(
            "completed", {"conclusion": job.conclusion, "runner_name": job.runner_name}
        )
        counts["closed"] += 1
        return
    if job.status == "in_progress":
        if not isinstance(rec.get("in_progress"), dict):
            await write("in_progress", {"runner_name": job.runner_name})
        await _reschedule(store, key, rec, now, slow=True)
        return

    # Still waiting for a runner.
    if _failed_to_start(rec):
        # No VM was made for it; the record already says why.
        await _reschedule(store, key, rec, now, slow=True)
        return
    if _decision_kind(rec) == "deferred":
        # Waiting for a free slot under its installation's limit (ADR-019),
        # not stuck: no VM was made for it on purpose.
        await _reschedule(store, key, rec, now)
        return
    own_vm = _current_vm_name(key, rec)
    if any(vm.name == own_vm for vm in vms) and own_vm not in busy_runners:
        if not isinstance(rec.get("vm"), dict):
            # The watcher never recorded it (often: CPU throttled after the
            # response), but the VM exists.
            await write("vm", {"outcome": "success", "error_code": None})
        await _reschedule(store, key, rec, now)
        return
    # Its own VM is gone, or GitHub gave it another job (ephemeral runners
    # take one job each), so only another VM can still take this one.
    if int(installation_id) in managed and _pass_may_restart(rec, now):
        # The capacity pass can still start it under its installation's
        # limit (ADR-019): waiting, not stuck.
        await _reschedule(store, key, rec, now)
        return
    if not _already_stuck(rec) and not _spare_runner(rec, repo, vms, busy_runners):
        # Nothing can take the job. The preemption check looks at its own VM
        # only, so `missing` also covers "the VM that would have taken it was
        # preempted" after a cross-assignment.
        preempted = await preemption_status(own_vm)
        if preempted is True:
            await write("preempted", {"phase": "before_pickup"})
            counts["stuck"] += 1
        elif preempted is False:
            await write("vm", {"outcome": "missing", "error_code": None})
            counts["stuck"] += 1
        # None: the audit log couldn't answer. A verdict here would be
        # permanent, so ask again next time.
    await _reschedule(store, key, rec, now)


def _decision_kind(rec: dict[str, Any]) -> str | None:
    return _decision(rec).get("kind")


def _decision(rec: dict[str, Any]) -> dict[str, Any]:
    decision = rec.get("decision")
    return decision if isinstance(decision, dict) else {}


def _starts(rec: dict[str, Any]) -> int:
    """Reconciler starts this job has had (ADR-019)."""
    return int(_decision(rec).get("start") or 0)


def _pass_may_restart(rec: dict[str, Any], now: datetime) -> bool:
    """Whether the capacity pass may still start this submitted job: the
    batch leaves its stuck verdict to the pass. Other kinds (a duplicate's
    `already_exists`, no decision) the pass never starts."""
    queued_at = rec.get("queued_at")
    return (
        _decision_kind(rec) == "vm_submitted"
        and _starts_left(rec)
        and not (isinstance(queued_at, datetime) and now - queued_at > STALE_QUEUED)
    )


def _starts_left(rec: dict[str, Any]) -> bool:
    # A refund reuses the start's number and makes no extra VM, so a
    # capacity failure on the last start may still be retried.
    return _starts(rec) < MAX_STARTS_PER_JOB or _refundable(rec)


def _capacity_failure(rec: dict[str, Any]) -> str | None:
    """The error code if the job's latest start failed because the zone or
    quota was full. Only the fact about the latest start's VM counts: a late
    watcher of an earlier start doesn't."""
    vm = rec.get("vm") if isinstance(rec.get("vm"), dict) else {}
    if vm.get("outcome") != "failure" or vm.get("error_code") not in CAPACITY_ERRORS:
        return None
    current = _decision(rec).get("vm_name")
    if current is None or vm.get("vm_name") != current:
        return None
    return vm["error_code"]


def _refundable(rec: dict[str, Any]) -> bool:
    return (
        _capacity_failure(rec) is not None
        and int(_decision(rec).get("capacity_retries") or 0) < MAX_CAPACITY_RETRIES
    )


def _current_vm_name(key: str, rec: dict[str, Any]) -> str:
    """The VM last made for the job: a reconciler start names it in the
    decision (`-rN` after the first)."""
    return _decision(rec).get("vm_name") or f"runnerforge-{key}"


def _failed_to_start(rec: dict[str, Any]) -> bool:
    decision = rec.get("decision") if isinstance(rec.get("decision"), dict) else None
    errored = rec.get("errored") if isinstance(rec.get("errored"), dict) else None
    # Label policy, or a tenant's limit or admission (ADR-019).
    if _decision_kind(rec) in ("policy_rejected", "rejected"):
        return True
    if errored is None:
        return False
    # A redelivery after a 500 can add a decision later; the newer fact wins.
    return decision is None or errored.get("at") > decision.get("at")


def _already_stuck(rec: dict[str, Any]) -> bool:
    vm = rec.get("vm") if isinstance(rec.get("vm"), dict) else {}
    return isinstance(rec.get("preempted"), dict) or vm.get("outcome") in (
        "failure",
        "missing",
    )


def _spare_runner(
    rec: dict[str, Any], repo: str, vms: list[VmInfo], busy_runners: set[str | None]
) -> bool:
    """Is there a RunnerForge VM that could still pick this job up: same
    repository, not running another job, and registered with labels that
    cover the job's? A VM whose labels are unknown counts, so the reconciler
    errs towards waiting rather than calling a job stuck."""
    wanted = {str(label).lower() for label in rec.get("labels") or []}
    for vm in vms:
        if vm.repo_full_name != repo or vm.name in busy_runners:
            continue
        offered = {label.lower() for label in vm.runner_labels}
        if not offered or wanted <= offered | _DEFAULT_RUNNER_LABELS:
            return True
    return False


async def _token(installation_id: int, tokens: dict[int, str | Exception]) -> str:
    """One token per installation per run. A failure is cached too, so a
    broken installation costs one request, not one per record."""
    if installation_id not in tokens:
        try:
            tokens[installation_id] = await get_installation_token(installation_id)
        except httpx.HTTPStatusError as e:
            tokens[installation_id] = (
                _InstallationGone(installation_id)
                if e.response.status_code == 404
                else e
            )
    cached = tokens[installation_id]
    if isinstance(cached, Exception):
        raise cached
    return cached


async def _reschedule(
    store: JobStore, key: str, rec: dict[str, Any], now: datetime, slow: bool = False
) -> None:
    checks = int(rec.get("checks") or 0) + 1
    step = max(checks - 1, SLOW_START if slow else 0)
    delay = BACKOFF[min(step, len(BACKOFF) - 1)]
    await store.patch(key, {"next_check_at": now + delay, "checks": checks})


# --- capacity pass (ADR-019) ---


@dataclass
class _Queue:
    """One installation's waiting jobs in this run."""

    installation_id: int
    admission: Admission
    waiting: list[tuple[str, dict[str, Any]]]
    free: int
    lookups: int
    busy: set[str]


@dataclass
class _Pass:
    store: JobStore
    now: datetime
    vms: list[VmInfo]
    tokens: dict[int, str | Exception]
    counts: Counter[str]
    errors: list[str]
    deadline: float
    busy_runners: set[str | None]
    on_started: Callable[[OperationHandle], None] | None
    # VMs the orphan cleanup deleted this run; a live listing may still show them.
    deleted: set[str] = field(default_factory=set)
    # Installations whose row says GitHub suspended them.
    suspended: set[int] = field(default_factory=set)
    started_keys: set[str] = field(default_factory=set)
    attempts: int = 0
    stocked_out: set[str | None] = field(default_factory=set)
    spares_used: set[str] = field(default_factory=set)
    stopped: bool = False


async def _capacity_pass(
    store: JobStore,
    now: datetime,
    vms: list[VmInfo],
    busy_runners: set[str | None],
    tokens: dict[int, str | Exception],
    counts: Counter[str],
    errors: list[str],
    on_started: Callable[[OperationHandle], None] | None,
    rows: dict[int, RegistryEntry],
    deleted: set[str],
) -> tuple[set[int], set[str]]:
    """Start VMs for jobs waiting under an installation's limit: deferred
    jobs, and a limited installation's jobs whose VM another job took or
    GCE never made. GitHub gives a queued job to any idle runner with
    matching labels, so this works from free slots, not decision kinds.

    Returns the admitted, limited installations (the batch leaves their
    stuck verdicts to this pass) and the records it started."""
    run = _Pass(
        store,
        now,
        vms,
        tokens,
        counts,
        errors,
        time.monotonic() + PASS_BUDGET_SECONDS,
        busy_runners,
        on_started,
        deleted,
        {iid for iid, row in rows.items() if row.github_status == "suspended"},
    )
    managed: set[int] = set()
    queues: list[_Queue] = []
    for iid in sorted(_record_installations(rows)):
        admission = await decide_admission(iid)
        if admission.reason == "registry_unavailable":
            # Unknown this run, not rejected: try again next run, and hold
            # back stuck verdicts meanwhile.
            managed.add(iid)
            continue
        if admission.admitted and admission.limits is not None:
            managed.add(iid)
        queue = await _queue(run, iid, admission)
        if queue is not None:
            queues.append(queue)
    # Recent capacity failures: a quota is project-wide, so it pauses every
    # start; a stockout is per machine type, so it pauses only that type.
    recent = [
        (_capacity_failure(rec), _decision(rec).get("machine_type"))
        for queue in queues
        for _, rec in queue.waiting
        if _capacity_failure(rec)
        and isinstance(rec["vm"].get("at"), datetime)
        and now - rec["vm"]["at"] < CAPACITY_PAUSE
    ]
    if any(code == "QUOTA_EXCEEDED" for code, _ in recent):
        logger.warning("Capacity pass paused: quota full")
        return managed, run.started_keys
    run.stocked_out = {machine_type for _, machine_type in recent}
    if run.stocked_out:
        logger.warning(
            "Capacity pass paused for stocked-out machine types",
            extra={"machine_types": sorted(str(t) for t in run.stocked_out)},
        )
    # Round-robin, one start or skip per installation per round, so one
    # tenant's backlog can't use up another's turn.
    while queues and not run.stopped:
        for queue in list(queues):
            if (
                run.stopped
                or run.attempts >= MAX_STARTS_PER_RUN
                or time.monotonic() > run.deadline
            ):
                return managed, run.started_keys
            if queue.free <= 0 or queue.lookups <= 0 or not queue.waiting:
                queues.remove(queue)
                continue
            queue.lookups -= 1
            key, rec = queue.waiting.pop(0)
            try:
                async with asyncio.timeout(START_TIMEOUT_SECONDS):
                    await _consider(run, queue, key, rec)
            except Exception as e:  # noqa: BLE001
                run.errors.append(f"{key}: {type(e).__name__}")
                logger.error(
                    "Reconcile start failed",
                    extra={"job_key": key, "error_class": type(e).__name__},
                )
                # This installation is done for the run: a token or GCE
                # outage would fail its next start the same way.
                queue.free = 0
                if _out_of_capacity(e):
                    # Quota or a zone stockout: every start would fail.
                    run.stopped = True
    return managed, run.started_keys


async def _queue(run: _Pass, iid: int, admission: Admission) -> _Queue | None:
    recs = await run.store.list_open_by_installation(iid, PASS_RECORD_LIMIT)
    if not recs:
        return None
    busy = {
        str(rec["in_progress"].get("runner_name"))
        for _, rec in recs
        if isinstance(rec.get("in_progress"), dict)
    } | {str(name) for name in run.busy_runners if name}
    by_name = {vm.name: vm for vm in run.vms}
    waiting = [
        (key, rec)
        for key, rec in recs
        if _waiting(key, rec, admission, run.now, by_name, busy)
    ]
    if not waiting:
        return None
    if not admission.admitted:
        if iid in run.suspended:
            # Suspended on GitHub, which can be lifted: its deferred jobs
            # keep waiting, and start again once it is.
            return None
        # Uninstalled, or suspended or no longer approved by the owner: its
        # deferred jobs won't start, and the record says why.
        for key, _ in waiting:
            await run.store.patch(
                key,
                {
                    "decision": {
                        "kind": "rejected",
                        "reason": admission.reason,
                        "source": "reconcile",
                        "at": run.now,
                    }
                },
            )
            run.counts["facts"] += 1
        return None
    if admission.limits is None:
        free = MAX_STARTS_PER_RUN
    else:
        try:
            # Live, not the run's listing: the webhook path keeps creating.
            async with asyncio.timeout(ACTIVE_VMS_TIMEOUT_SECONDS + 1):
                active = await active_vm_names(iid)
        except Exception as e:  # noqa: BLE001
            # Fail closed: unlike a webhook, the next run asks again.
            logger.warning(
                "Capacity check failed",
                extra={"installation_id": iid, "error_class": type(e).__name__},
            )
            return None
        # By name: a deleted VM may or may not still be listed.
        free = admission.limits.max_concurrent_vms - len(active - run.deleted)
    return _Queue(iid, admission, waiting, free, max(free, 0) * LOOKUPS_PER_SLOT, busy)


def _waiting(
    key: str,
    rec: dict[str, Any],
    admission: Admission,
    now: datetime,
    vms: dict[str, VmInfo],
    busy: set[str],
) -> bool:
    """A job the pass may start: deferred; or, for a limited installation,
    one whose VM another job took or GCE never made."""
    if isinstance(rec.get("in_progress"), dict) or isinstance(
        rec.get("completed"), dict
    ):
        return False
    if not _starts_left(rec):
        return False
    queued_at = rec.get("queued_at")
    if isinstance(queued_at, datetime) and now - queued_at > STALE_QUEUED:
        return False
    decision = _decision(rec)
    if decision.get("kind") == "deferred":
        return True
    if decision.get("kind") != "vm_submitted" or admission.limits is None:
        return False
    at = decision.get("at")
    if isinstance(at, datetime) and now - at < START_GRACE:
        return False
    vm = vms.get(_current_vm_name(key, rec))
    # Its own VM, up and not on another job, will take it.
    return vm is None or vm.status not in ACTIVE_STATUSES or vm.name in busy


async def _consider(run: _Pass, queue: _Queue, key: str, rec: dict[str, Any]) -> None:
    """Ask GitHub about one waiting job, then start a VM for it if nothing
    else can take it."""
    iid = queue.installation_id
    repo, job_id = rec.get("repo"), rec.get("job_id")
    if not repo or job_id is None:
        return
    try:
        token = await _token(iid, run.tokens)
        job = await get_job(token, repo, int(job_id))
    except JobNotFound, _InstallationGone:
        job = GitHubJob(id=int(job_id), status="completed", conclusion="gone")
    if job.status != "queued":
        # Record it, so the job leaves the waiting set.
        name = "completed" if job.status == "completed" else "in_progress"
        fact: dict[str, Any] = {"runner_name": job.runner_name}
        if name == "completed":
            fact["conclusion"] = job.conclusion
        await run.store.record_fact(key, {}, name, fact, source="reconcile")
        run.counts["facts"] += 1
        return
    if _take_spare(run, queue, rec, repo):
        return
    try:
        policy = resolve_labels([str(label) for label in rec.get("labels") or []])
    except MachinePolicyError:
        return  # admitted once with these labels; nothing to start
    limits = queue.admission.limits
    if limits is not None and exceeds_size(policy, limits):
        # Its limits changed since it was deferred.
        await run.store.patch(
            key,
            {
                "decision": {
                    "kind": "rejected",
                    "reason": "limit_size",
                    "machine_type": policy.machine_type,
                    "source": "reconcile",
                    "at": run.now,
                }
            },
        )
        run.counts["facts"] += 1
        return
    if policy.machine_type in run.stocked_out:
        return  # the zone is out of this type; try again after the pause
    spot, max_run_duration = vm_settings(policy, limits)
    start = _starts(rec)
    base = f"runnerforge-{key}"
    retries = 0
    if _refundable(rec):
        # GCE never made that VM, so its name is free and the attempt
        # doesn't count against the job.
        n, vm_name = start, _current_vm_name(key, rec)
        retries = int(_decision(rec).get("capacity_retries") or 0) + 1
    else:
        n = start + 1
        # Never reuse a name: GitHub may still hold an offline runner of it.
        vm_name = (
            base if _decision_kind(rec) == "deferred" and start == 0 else f"{base}-r{n}"
        )
    if not await run.store.claim_start(
        key,
        start,
        {
            "kind": "vm_submitted",
            "source": "reconcile",
            "start": n,
            "vm_name": vm_name,
            "machine_type": policy.machine_type,
            "spot": spot,
            "capacity_retries": retries,
            "at": run.now,
        },
        run.now + BACKOFF[0],
    ):
        return  # moved on, or another run took it
    # Counted from the claim: a start cancelled mid-insert may still have
    # made its VM.
    queue.free -= 1
    run.attempts += 1

    async def record_errored(stage, exc: Exception) -> None:
        await run.store.record_fact(
            key,
            {},
            "errored",
            {"stage": stage, "error_class": type(exc).__name__},
            source="reconcile",
        )

    run.started_keys.add(key)
    handle = await submit_runner_vm(
        RunnerVmSpec(
            vm_name=vm_name,
            installation_id=iid,
            repo_full_name=repo,
            repository_id=rec.get("repository_id"),
            run_id=int(rec["run_id"]),
            job_id=int(job_id),
            run_attempt=int(rec["run_attempt"]),
            job_labels=[str(label) for label in rec.get("labels") or []],
            machine_type=policy.machine_type,
            spot=spot,
            max_run_duration=max_run_duration,
            # Readiness runs from this start (ADR-019).
            queued_received_at=time.time(),
            installation_token=token,
        ),
        on_error=record_errored,
    )
    run.counts["started"] += 1
    if handle is not None and run.on_started is not None:
        # The record exists, so the watcher's write needs no base fields.
        run.on_started(replace(handle, job_key=key, job_base={}))
    logger.info(
        "Reconcile started job",
        extra={"job_key": key, "vm_name": vm_name, "start": n, "installation_id": iid},
    )


def _take_spare(run: _Pass, queue: _Queue, rec: dict[str, Any], repo: str) -> bool:
    """Is there a young, idle VM of the installation that can still take
    this job? Each excuses one waiting job only."""
    wanted = {str(label).lower() for label in rec.get("labels") or []}
    for vm in run.vms:
        if (
            vm.name in run.spares_used
            or vm.name in queue.busy
            or vm.status not in ACTIVE_STATUSES
            or vm.labels.installation_id != str(queue.installation_id)
            or vm.repo_full_name != repo
            or run.now - vm.creation_timestamp > SPARE_VM_MAX_AGE
        ):
            continue
        offered = {label.lower() for label in vm.runner_labels}
        if offered and not wanted <= offered | _DEFAULT_RUNNER_LABELS:
            continue
        run.spares_used.add(vm.name)
        return True
    return False


def _out_of_capacity(exc: Exception) -> bool:
    text = str(exc).upper()
    return "QUOTA" in text or "RESOURCE_POOL_EXHAUSTED" in text


def _record_installations(rows: dict[int, RegistryEntry]) -> set[int]:
    """Installations that get job records (the record gate): allow-listed,
    and, from the registry, ones approved at least once."""
    return {
        iid
        for iid, row in rows.items()
        if row.approved_at is not None and row.github_status != "deleted"
    } | set(ALLOWED_INSTALLATION_IDS or ())


# --- orphan cleanup ---


async def _delete_orphans(
    now: datetime,
    vms: list[VmInfo],
    open_records: list[tuple[str, dict[str, Any]]] | None,
    busy_runners: set[str | None],
    tokens: dict[int, str | Exception],
    counts: Counter[str],
    errors: list[str],
    rows: dict[int, RegistryEntry],
) -> set[str]:
    """Delete VMs whose runner won't take a job: GitHub no longer lists it
    (it finished and its `completed` webhook was lost, or it never
    registered), or lists it idle with no job it could take. Only for
    installations that get records: the records say which runners are busy.

    Returns the VMs deleted. Their jobs' records are left to the batch, which
    asks GitHub before any verdict: a VM whose runner is gone usually ran its
    job, even when the webhooks that would say so were lost."""
    deleted: set[str] = set()
    if open_records is None or len(open_records) >= OPEN_LIMIT:
        # Fail closed: without every open record, a busy runner may look idle.
        logger.warning(
            "Orphan cleanup skipped",
            extra={
                "reason": "store_unavailable"
                if open_records is None
                else "too_many_open"
            },
        )
        return deleted
    # An unset allow-list admits, and records, everyone in allowlist mode.
    everyone = ADMISSION == "allowlist" and ALLOWED_INSTALLATION_IDS is None
    # GitHub refuses the calls of an installation it suspended; ADR-019's
    # cleanup of cut-off installations handles their VMs. An owner-suspended
    # one is checked: its idle runners could still take new jobs.
    ids = _record_installations(rows) - {
        iid for iid, row in rows.items() if row.github_status == "suspended"
    }
    candidates = [
        vm
        for vm in vms
        if vm.status in ACTIVE_STATUSES
        and vm.labels.installation_id.isdigit()
        and (everyone or int(vm.labels.installation_id) in ids)
        and vm.repo_full_name is not None
        and now - vm.creation_timestamp > ORPHAN_MIN_AGE
    ]
    # A different order each run, so VMs that are kept every run (long jobs)
    # can't always take the checks.
    candidates.sort(key=lambda vm: zlib.crc32(f"{vm.name}/{now:%Y%m%d%H%M}".encode()))
    deadline = time.monotonic() + ORPHAN_BUDGET_SECONDS
    for checked, vm in enumerate(candidates):
        if checked >= MAX_ORPHAN_CHECKS or time.monotonic() > deadline:
            logger.warning(
                "Orphan cleanup incomplete",
                extra={"unchecked": len(candidates) - checked},
            )
            break
        iid = int(vm.labels.installation_id)
        try:
            async with asyncio.timeout(ORPHAN_CHECK_TIMEOUT_SECONDS):
                reason = await _delete_if_orphan(
                    vm, iid, open_records, busy_runners, tokens
                )
        # Deliberately broad: a VM whose check fails is kept, and the run goes on.
        except Exception as e:  # noqa: BLE001
            errors.append(f"{vm.name}: {type(e).__name__}")
            logger.error(
                "Orphan check failed",
                extra={"vm_name": vm.name, "error_class": type(e).__name__},
            )
            continue
        if reason is None:
            continue
        # Gone from the rest of the run either way.
        deleted.add(vm.name)
        if reason == "already_deleted":
            continue  # its `completed` webhook got there first
        counts["orphans_deleted"] += 1
        logger.info(
            "Orphan VM deleted",
            extra={
                "vm_name": vm.name,
                "installation_id": iid,
                "reason": reason,
                "age_minutes": int((now - vm.creation_timestamp).total_seconds() / 60),
            },
        )
    return deleted


async def _delete_if_orphan(
    vm: VmInfo,
    iid: int,
    open_records: list[tuple[str, dict[str, Any]]],
    busy_runners: set[str | None],
    tokens: dict[int, str | Exception],
) -> str | None:
    """Delete the VM if it is an orphan; returns why (`already_deleted` if
    GCE had no such VM), or None if it's kept."""
    repo = str(vm.repo_full_name)
    token = await _token(iid, tokens)
    try:
        # VM name == runner name.
        runner = await get_runner(token, repo, vm.name)
    except RepoNotFound:
        return None  # no evidence either way; its runtime cap still removes it
    if runner is None:
        # Ephemeral runners leave GitHub's list after their one job, so this
        # holds even if a record still says the VM is busy.
        reason = "runner_gone"
    elif runner.busy or vm.name in busy_runners:
        # GitHub's busy flag can read false mid-job; the records back it up.
        return None
    elif runner.status == "online" and _may_take_waiting_job(vm, iid, open_records):
        return None  # GitHub would hand it that job
    else:
        reason = "runner_idle"
        # First, so GitHub can't hand it a job while the VM shuts down.
        await delete_runner(token, repo, runner.id)
    if await delete_vm(instance_name=vm.name, reason="reconcile") is None:
        return "already_deleted"
    return reason


def _may_take_waiting_job(
    vm: VmInfo, iid: int, open_records: list[tuple[str, dict[str, Any]]]
) -> bool:
    """Is a job of the installation waiting for a runner in the VM's repo,
    with labels its runner covers? A VM whose labels are unknown covers
    every job, so the cleanup errs towards keeping it."""
    offered = {label.lower() for label in vm.runner_labels}
    for _, rec in open_records:
        if (
            rec.get("installation_id") != iid
            or rec.get("repo") != vm.repo_full_name
            or _on_github_runner(rec)
        ):
            continue
        wanted = {str(label).lower() for label in rec.get("labels") or []}
        if not offered or wanted <= offered | _DEFAULT_RUNNER_LABELS:
            return True
    return False


async def _serial_logging_pass(
    now: datetime,
    vms: list[VmInfo],
    open_records: list[tuple[str, dict[str, Any]]],
    tokens: dict[int, str | Exception],
    errors: list[str],
) -> None:
    """Turn serial logging off on running VMs that still log it: those on a
    job whose in_progress webhook didn't (`busy`), and those past boot
    (`aged`, or `runner_busy` when GitHub says the runner is on a job no
    record shows). Fails open, like the webhook: a VM that keeps logging is
    alerted on, not deleted."""
    # When each VM's job started, keyed by VM and installation: a record of
    # another installation can't say a VM is busy.
    started_at: dict[tuple[str, int | None], Any] = {}
    for _, rec in open_records:
        fact = rec.get("in_progress")
        if isinstance(fact, dict) and fact.get("runner_name"):
            started_at[(fact["runner_name"], _record_installation(rec))] = fact.get(
                "at"
            )
    candidates: list[tuple[VmInfo, Literal["busy", "aged", "runner_busy"]]] = []
    for vm in vms:
        if vm.status != "RUNNING" or vm.serial_logging is False:
            continue
        key = (vm.name, _installation(vm))
        if key in started_at:
            at = started_at[key]
            # A fact without a time can't be the webhook's in-flight one.
            if not isinstance(at, datetime) or now - at > SERIAL_BUSY_GRACE:
                candidates.append((vm, "busy"))
        elif now - vm.creation_timestamp > SERIAL_BOOT_WINDOW:
            candidates.append((vm, "aged"))
    # Known jobs first: aged VMs are usually spares.
    candidates.sort(key=lambda c: c[1] != "busy")
    deadline = time.monotonic() + SERIAL_BUDGET_SECONDS
    busy_on_github = await _runners_busy(
        [vm for vm, reason in candidates[:MAX_SERIAL_OFFS] if reason == "aged"], tokens
    )
    candidates = [
        (vm, "runner_busy" if vm.name in busy_on_github else reason)
        for vm, reason in candidates
    ]
    for done, (vm, reason) in enumerate(candidates):
        if done >= MAX_SERIAL_OFFS or time.monotonic() > deadline:
            logger.warning(
                "Serial logging pass incomplete",
                extra={"unchecked": len(candidates) - done},
            )
            break
        try:
            outcome = await turn_off_serial_logging(
                vm.name, "reconcile", reason, installation_id=_installation(vm)
            )
        # Deliberately broad: one VM must not stop the pass. Only the class is
        # logged, since the VM's metadata holds its registration token.
        except Exception as e:  # noqa: BLE001
            outcome = type(e).__name__
            logger.error(
                "Serial logging off failed",
                extra={"vm_name": vm.name, "error_class": outcome},
            )
        if outcome not in ("submitted", "already_off", "vm_gone", "not_running"):
            errors.append(f"{vm.name}: serial logging {outcome}")


async def _runners_busy(
    vms: list[VmInfo], tokens: dict[int, str | Exception]
) -> set[str]:
    """The VMs whose runner GitHub says is on a job. Only a yes counts: its
    busy flag can read false mid-job, and a VM without one stays `aged`,
    which is still turned off. Every check runs at once under one timeout,
    so a slow GitHub can't eat the pass's turn-offs."""
    by_installation: dict[int, list[VmInfo]] = {}
    for vm in vms:
        iid = _installation(vm)
        if iid is not None and vm.repo_full_name is not None:
            by_installation.setdefault(iid, []).append(vm)
    if not by_installation:
        return set()
    checks = {
        asyncio.ensure_future(_installation_runners_busy(iid, its, tokens)): its
        for iid, its in by_installation.items()
    }
    done, pending = await asyncio.wait(
        checks, timeout=SERIAL_RUNNER_CHECK_TIMEOUT_SECONDS
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    busy: set[str] = set()
    for task, its in checks.items():
        if task in done and task.exception() is None:
            busy |= task.result()
            continue
        error_class = (
            type(task.exception()).__name__ if task in done else "TimeoutError"
        )
        for vm in its:
            _log_runner_check_failed(vm, error_class)
    return busy


async def _installation_runners_busy(
    iid: int, vms: list[VmInfo], tokens: dict[int, str | Exception]
) -> set[str]:
    """One installation's share: one token, then its VMs' runners at once."""
    token = await _token(iid, tokens)
    # VM name == runner name.
    answers = await asyncio.gather(
        *(get_runner(token, str(vm.repo_full_name), vm.name) for vm in vms),
        return_exceptions=True,
    )
    busy: set[str] = set()
    for vm, answer in zip(vms, answers, strict=True):
        if isinstance(answer, BaseException):
            _log_runner_check_failed(vm, type(answer).__name__)
        elif answer is not None and answer.busy:
            busy.add(vm.name)
    return busy


def _log_runner_check_failed(vm: VmInfo, error_class: str) -> None:
    logger.warning(
        "Serial logging runner check failed",
        extra={"vm_name": vm.name, "error_class": error_class},
    )


def _on_github_runner(rec: dict[str, Any]) -> bool:
    """A runner took the job (or it ended): it's no longer waiting."""
    return isinstance(rec.get("in_progress"), dict) or isinstance(
        rec.get("completed"), dict
    )


# --- cut-off installations (ADR-019 step 4) ---


def _installation(vm: VmInfo) -> int | None:
    iid = vm.labels.installation_id
    return int(iid) if iid.isdigit() else None


def _record_installation(rec: dict[str, Any]) -> int | None:
    iid = rec.get("installation_id")
    return iid if isinstance(iid, int) else None


async def _confirm_cutoffs(
    now: datetime,
    rows: dict[int, RegistryEntry] | None,
    counts: Counter[str],
    deadline: float,
) -> dict[int, _Cutoff]:
    """Installations whose row says GitHub suspended or uninstalled them, as
    GitHub confirms it now (GET /app/installations/{id}, with the App's own
    JWT, which works while an installation is suspended). The row comes from
    whichever event arrived last, so a lost or reordered `unsuspend` must not
    delete an active installation's VMs. A row GitHub contradicts is put
    right; a check that fails skips the installation for the run."""
    if rows is None:
        logger.warning(
            "Cut-off cleanup skipped", extra={"reason": "registry_unavailable"}
        )
        return {}
    candidates = sorted(
        (
            row
            for row in rows.values()
            if row.github_status in ("suspended", "deleted")
            # Once its records are gone, an uninstalled installation is only
            # looked at again hourly, for stragglers, until its row expires.
            and not (
                row.records_purged_at is not None and not registry_backfill_due(now)
            )
        ),
        # Work left first (purged rows only look for stragglers), then a
        # different order each run, so the cap can't always pass over the
        # same installations.
        key=lambda row: (
            row.records_purged_at is not None,
            zlib.crc32(f"{row.installation_id}/{now:%Y%m%d%H%M}".encode()),
        ),
    )
    if len(candidates) > MAX_CUTOFF_CONFIRMS:
        logger.warning(
            "Cut-off cleanup incomplete",
            extra={"unchecked": len(candidates) - MAX_CUTOFF_CONFIRMS},
        )
    cutoffs: dict[int, _Cutoff] = {}
    for row in candidates[:MAX_CUTOFF_CONFIRMS]:
        iid = row.installation_id
        if time.monotonic() > deadline:
            logger.warning("Cut-off cleanup incomplete", extra={"installation_id": iid})
            break
        try:
            async with asyncio.timeout(CUTOFF_CALL_TIMEOUT_SECONDS):
                found = await get_app_installation(iid)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Cut-off check failed",
                extra={"installation_id": iid, "error_class": type(e).__name__},
            )
            continue
        if found is None:
            status = "deleted"
        elif found.suspended_at is not None:
            status = "suspended"
        else:
            status = "active"
        if status != row.github_status:
            await get_registry().record_github_status(iid, status)
            counts["registry_repairs"] += 1
            logger.info(
                "Registry status repaired",
                extra={"installation_id": iid, "was": row.github_status, "now": status},
            )
        if status == "active":
            continue
        if status == "suspended":
            since = found.suspended_at  # GitHub's own time
        elif row.github_status == "deleted":
            since = row.github_status_at or (
                row.expire_at - DELETED_RETENTION if row.expire_at else now
            )
        else:
            since = now  # just learned it's gone
        cutoffs[iid] = _Cutoff(status, since)
    return cutoffs


async def _delete_cutoff_vms(
    now: datetime,
    vms: list[VmInfo],
    cutoffs: dict[int, _Cutoff],
    busy_runners: set[str | None] | None,
    counts: Counter[str],
    errors: list[str],
    deadline: float,
) -> set[str]:
    """Delete the VMs of cut-off installations. No runner check is possible
    (GitHub refuses the installation's calls) and its runner can't be
    deregistered, so GitHub keeps an offline entry for it. Only VMs made
    before the cut-off: a newer one means GitHub was still sending webhooks.
    `busy_runners` is None when the records couldn't be read: every VM then
    counts as busy. Returns the VMs deleted."""
    deleted: set[str] = set()
    for vm in vms:
        cut = cutoffs.get(_installation(vm))
        if cut is None or vm.status not in ACTIVE_STATUSES:
            continue
        if vm.creation_timestamp >= cut.since:
            continue
        busy = busy_runners is None or vm.name in busy_runners
        if cut.status == "suspended" and busy and now - cut.since < CUTOFF_BUSY_GRACE:
            continue
        if len(deleted) >= MAX_CUTOFF_DELETES or time.monotonic() > deadline:
            logger.warning("Cut-off cleanup incomplete", extra={"vm_name": vm.name})
            break
        try:
            async with asyncio.timeout(CUTOFF_CALL_TIMEOUT_SECONDS):
                operation = await delete_vm(instance_name=vm.name, reason="cutoff")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{vm.name}: {type(e).__name__}")
            logger.error(
                "Cut-off VM delete failed",
                extra={"vm_name": vm.name, "error_class": type(e).__name__},
            )
            continue
        deleted.add(vm.name)
        if operation is None:
            continue  # GCE had no such VM any more
        counts["cutoff_vms_deleted"] += 1
        logger.info(
            "Cut-off VM deleted",
            extra={
                "vm_name": vm.name,
                "installation_id": _installation(vm),
                "github_status": cut.status,
                "busy": busy,
                "age_minutes": int((now - vm.creation_timestamp).total_seconds() / 60),
            },
        )
    return deleted


async def _purge_cutoff_records(
    store: JobStore,
    vms: list[VmInfo],
    cutoffs: dict[int, _Cutoff],
    rows: dict[int, RegistryEntry],
    counts: Counter[str],
    deadline: float,
) -> None:
    """Delete an uninstalled installation's job records (ADR-018, ADR-019),
    once the run's listing shows none of its VMs in any status: a VM left
    over would give its job a record back. Bounded per run; the next run
    goes on where this one stopped."""
    present = {_installation(vm) for vm in vms}
    for iid, cut in sorted(cutoffs.items()):
        if cut.status != "deleted" or iid in present:
            continue
        if time.monotonic() > deadline:
            logger.warning("Cut-off cleanup incomplete", extra={"installation_id": iid})
            break
        count = await store.delete_by_installation(iid, PURGE_LIMIT)
        if count is None:
            continue  # logged by the store; the next run tries again
        counts["cutoff_records_deleted"] += count
        more = count >= PURGE_LIMIT
        if count:
            logger.info(
                "Cut-off records deleted",
                extra={"installation_id": iid, "count": count, "more": more},
            )
        row = rows.get(iid)
        if not more and row is not None and row.records_purged_at is None:
            await get_registry().mark_records_purged(iid)
