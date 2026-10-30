"""Reconciler (ADR-018 R3).

Brings open job records in line with GitHub and GCE for what the webhooks and
the background tasks missed: lost webhooks, watchers that never finished, and
Spot VMs preempted before they picked up a job. Cloud Scheduler calls it every
5 minutes through POST /reconcile, so it runs inside a request and its CPU
isn't throttled the way background tasks are.

Every fact it writes has `source: reconcile`. VM labels stay the authority for
which VMs exist; the reconciler only reads them.
"""

import asyncio
import logging
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from runnerforge.compute_client import list_runnerforge_vms, preemption_status
from runnerforge.github_client import JobNotFound, get_installation_token, get_job
from runnerforge.job_store import (
    FIRST_CHECK_DELAY,
    RETENTION,
    SCHEMA_VERSION,
    CreateResult,
    JobStore,
    get_job_store,
    job_key,
)
from runnerforge.models import ReconcileResult, VmInfo

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


class _InstallationGone(Exception):
    """The App installation is gone (token request returned 404)."""


async def run_reconcile(now: datetime | None = None) -> ReconcileResult:
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
    errors: list[str] = []

    # One extra record tells us whether more were due than a batch holds.
    due = await store.list_due(now, BATCH_SIZE + 1)
    more_due = len(due) > BATCH_SIZE
    due = due[:BATCH_SIZE]
    vms = await list_runnerforge_vms()
    open_records = await store.list_open(OPEN_LIMIT)
    # Runners that are on a job right now. GitHub gives a queued job to any
    # idle runner with matching labels, so a VM's name says which job caused
    # it, not which job it is running.
    busy_runners = {
        rec["in_progress"].get("runner_name")
        for _, rec in open_records
        if isinstance(rec.get("in_progress"), dict)
    }

    counts["backfilled"] = await _backfill(store, vms, {k for k, _ in open_records})

    tokens: dict[int, str | Exception] = {}
    started = time.monotonic()
    checked = 0
    for key, rec in due:
        if time.monotonic() - started > RUN_BUDGET_SECONDS:
            more_due = True
            break
        checked += 1
        try:
            async with asyncio.timeout(RECORD_TIMEOUT_SECONDS):
                await _reconcile_one(
                    store, key, rec, now, vms, busy_runners, tokens, counts
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
    own_vm = f"runnerforge-{key}"
    if any(vm.name == own_vm for vm in vms) and own_vm not in busy_runners:
        if not isinstance(rec.get("vm"), dict):
            # The watcher never recorded it (often: CPU throttled after the
            # response), but the VM exists.
            await write("vm", {"outcome": "success", "error_code": None})
        await _reschedule(store, key, rec, now)
        return
    # Its own VM is gone, or GitHub gave it another job (ephemeral runners
    # take one job each), so only another VM can still take this one.
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


def _failed_to_start(rec: dict[str, Any]) -> bool:
    decision = rec.get("decision") if isinstance(rec.get("decision"), dict) else None
    errored = rec.get("errored") if isinstance(rec.get("errored"), dict) else None
    if decision is not None and decision.get("kind") == "policy_rejected":
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
