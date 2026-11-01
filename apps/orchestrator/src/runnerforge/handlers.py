import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)

from runnerforge.admission import ALLOWLISTED, Admission, exceeds_size, vm_settings
from runnerforge.compute_client import (
    ACTIVE_VMS_TIMEOUT_SECONDS,
    GITHUB_REPO_URL_PREFIX,
    OperationHandle,
    PreemptionCheckRequest,
    active_vm_names,
    create_vm,
    delete_vm,
    get_vm_labels_by_job,
    get_vm_labels_by_name,
    is_valid_machine_type,
)
from runnerforge.config import STARTUP_SCRIPT
from runnerforge.github_client import get_installation_token, get_registration_token
from runnerforge.job_store import (
    CreateResult,
    JobStore,
    NoopJobStore,
    base_fields,
    get_job_store,
    job_key,
)
from runnerforge.machine_policy import MachinePolicyError, resolve_labels
from runnerforge.models import RunnerForgeVmLabels, WorkflowJobEvent

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def _webhook_span_context(
    webhook_trace_id: str, webhook_span_id: str
) -> SpanContext | None:
    """Reconstruct an OTel SpanContext from the hex strings persisted in VM
    labels. Returns None if either is empty (VM created before this field
    shipped, or webhook span context was never written). Marked as
    `is_remote=True` so the tracer treats it as cross-process — same trace
    id continuum but originating from a different worker.
    """
    if not webhook_trace_id or not webhook_span_id:
        return None
    return SpanContext(
        trace_id=int(webhook_trace_id, 16),
        span_id=int(webhook_span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )


# GitHub's timestamps and ours may disagree by this much (NTP, one-second
# resolution) before a readiness sample is treated as skewed.
CLOCK_SKEW_TOLERANCE_SECONDS = 5.0


def _log_queued_outcome(
    outcome: Literal[
        "vm_submitted",
        "already_exists",
        "policy_rejected",
        "already_completed",
        "rejected",
        "deferred",
    ],
    reason: str | None = None,
) -> None:
    """One line per queued event that reached a deliberate decision. The
    lost_webhook_ratio alert divides these by queued "Webhook event received"
    lines, so only a queued event with NO outcome (an exception, a crash)
    counts as lost. Rejections, duplicates and late deliveries are decisions,
    not losses."""
    extra: dict[str, str] = {"outcome": outcome}
    if reason is not None:
        extra["reason"] = reason
    logger.info("Queued webhook outcome", extra=extra)


async def _record_errored(
    store: JobStore,
    key: str,
    base: dict[str, Any],
    stage: Literal["token", "gce_insert"],
    exc: Exception,
) -> None:
    await store.record_fact(
        key, base, "errored", {"stage": stage, "error_class": type(exc).__name__}
    )


@dataclass(frozen=True, slots=True)
class RunnerVmSpec:
    """Everything a runner VM for one job needs, from a `queued` webhook or
    from a job record (the reconciler's capacity starts, ADR-019)."""

    vm_name: str
    installation_id: int
    repo_full_name: str
    repository_id: int | None
    run_id: int
    job_id: int
    run_attempt: int
    job_labels: list[str]
    machine_type: str
    spot: bool
    max_run_duration: timedelta
    # When this VM was admitted: Time-to-Register runs from here.
    queued_received_at: float
    webhook_trace_id: str = ""
    webhook_span_id: str = ""
    # A caller that already holds one (the reconciler caches one per run).
    installation_token: str | None = None


async def submit_runner_vm(
    spec: RunnerVmSpec,
    on_error: Callable[[Literal["token", "gce_insert"], Exception], Awaitable[None]],
) -> OperationHandle | None:
    """Fetch a registration token and submit the VM. Returns None when the
    VM already exists. A failure is passed to `on_error` with its stage,
    then raised."""
    try:
        installation_token = spec.installation_token or (
            await get_installation_token(spec.installation_id)
        )
        registration_token = await get_registration_token(
            installation_token=installation_token,
            repo_full_name=spec.repo_full_name,
        )
    except Exception as exc:
        await on_error("token", exc)
        raise
    logger.info("Registration token received")

    # Create the VM with the registration token metadata
    metadata = {
        "registration-token": registration_token,
        "repo-url": f"{GITHUB_REPO_URL_PREFIX}{spec.repo_full_name}",
        "runner-labels": ",".join(spec.job_labels),
        "startup-script": STARTUP_SCRIPT,
    }
    logger.info("Creating VM", extra={"labels": spec.job_labels})
    labels = RunnerForgeVmLabels(
        runner="runnerforge",
        job_id=str(spec.job_id),
        run_id=str(spec.run_id),
        run_attempt=str(spec.run_attempt),
        repository_id="" if spec.repository_id is None else str(spec.repository_id),
        installation_id=str(spec.installation_id),
        webhook_trace_id=spec.webhook_trace_id,
        webhook_span_id=spec.webhook_span_id,
        # GCE label values can't contain '.' — store as integer epoch
        # seconds. Sub-second precision isn't needed for the SLO
        # (300s threshold, NTP skew is sub-second).
        queued_received_at=str(int(spec.queued_received_at)),
    )
    try:
        return await create_vm(
            instance_name=spec.vm_name,
            machine_type=spec.machine_type,
            spot=spec.spot,
            labels=labels.model_dump(),
            metadata=metadata,
            max_run_duration=spec.max_run_duration,
        )
    except Exception as exc:
        await on_error("gce_insert", exc)
        raise


async def handle_queued(
    event: WorkflowJobEvent, received_at: float, admission: Admission = ALLOWLISTED
) -> OperationHandle | None:
    # Anchor on the outer (auto-instrumented `POST /webhook`) server span —
    # captured BEFORE we enter our own span. handle_in_progress later parents
    # under this same anchor, so it renders as a sibling of handle_queued
    # (not nested inside it) when the two webhooks merge into one trace.
    outer_ctx = trace.get_current_span().get_span_context()
    webhook_trace_id = format(outer_ctx.trace_id, "032x")
    webhook_span_id = format(outer_ctx.span_id, "016x")

    with tracer.start_as_current_span("webhook.handle_queued") as span:
        span.set_attribute("repo", event.repository.full_name)
        span.set_attribute("sender", event.sender.login)
        span.set_attribute("job_id", event.workflow_job.id)
        span.set_attribute("installation_id", event.installation.id)
        span.set_attribute("labels", event.workflow_job.labels)
        span.set_attribute("run_id", event.workflow_job.run_id)
        span.set_attribute("run_attempt", event.workflow_job.run_attempt)

        # Job record (ADR-018). Every store call is bounded and fails open.
        # Installations never approved get none (ADR-019).
        store = get_job_store() if admission.record else NoopJobStore()
        key = job_key(
            event.workflow_job.run_id,
            event.workflow_job.id,
            event.workflow_job.run_attempt,
        )
        base = base_fields(event, received_at)
        # A closed record means `completed` arrived before this `queued`
        # (reordered or redelivered): a VM now would only sit idle. No decision
        # is written, because that would reopen the record.
        created = await store.create_if_absent(key, base)
        if created is CreateResult.EXISTS_CLOSED:
            logger.info("Skipping VM: job already completed")
            _log_queued_outcome("already_completed")
            return None
        if created is CreateResult.UNKNOWN:
            # The store just failed or timed out (or is off): skip this
            # request's other writes, so a degraded store costs one 2 s bound
            # per webhook, not several. The reconciler fills what it can.
            store = NoopJobStore()

        if not admission.admitted:
            logger.warning(
                "Installation not admitted", extra={"reason": admission.reason}
            )
            await store.record_fact(
                key, base, "decision", {"kind": "rejected", "reason": admission.reason}
            )
            _log_queued_outcome("rejected", admission.reason)
            return None

        # Get requested machine type from labels
        try:
            policy = resolve_labels(event.workflow_job.labels)
            if not is_valid_machine_type(policy.machine_type):
                raise MachinePolicyError("invalid_machine_type", [policy.machine_type])
        except MachinePolicyError as e:
            logger.warning(
                "Machine policy rejected",
                extra={"reason": e.reason, "offending_tokens": e.offending_tokens},
            )
            await store.record_fact(
                key,
                base,
                "decision",
                {
                    "kind": "policy_rejected",
                    "reason": e.reason,
                    "offending_tokens": e.offending_tokens,
                },
            )
            _log_queued_outcome("policy_rejected")
            return None
        span.set_attribute("policy.machine_type", policy.machine_type)
        installation_id = event.installation.id
        vm_name = f"runnerforge-{event.workflow_job.run_id}-{event.workflow_job.id}-{event.workflow_job.run_attempt}"
        spot, max_run_duration = vm_settings(policy, admission.limits)
        deferral_unrecorded = False
        limits = admission.limits
        if limits is not None:
            if exceeds_size(policy, limits):
                logger.warning(
                    "Limit rejected",
                    extra={
                        "reason": "limit_size",
                        "machine_type": policy.machine_type,
                        "max_size": limits.max_size,
                    },
                )
                await store.record_fact(
                    key,
                    base,
                    "decision",
                    {
                        "kind": "rejected",
                        "reason": "limit_size",
                        "machine_type": policy.machine_type,
                    },
                )
                _log_queued_outcome("rejected", "limit_size")
                return None
            try:
                # to_thread can't be cancelled, so the list has its own
                # timeout too; this bounds the wait either way.
                async with asyncio.timeout(ACTIVE_VMS_TIMEOUT_SECONDS + 1):
                    names = await active_vm_names(installation_id)
            except Exception as exc:  # noqa: BLE001
                # Limits are soft and GCE quotas are the backstop: admit.
                logger.warning(
                    "Concurrency check failed, admitting",
                    extra={"error_class": type(exc).__name__},
                )
                names = set()
            # A redelivered `queued` finds its own VM: that's a duplicate for
            # create_vm to report, not a reason to defer.
            active = len(names - {vm_name})
            if vm_name not in names and active >= limits.max_concurrent_vms:
                deferred = await store.record_fact(
                    key,
                    base,
                    "decision",
                    {
                        "kind": "deferred",
                        "reason": "limit_concurrency",
                        "active_vms": active,
                        "machine_type": policy.machine_type,
                        "spot": spot,
                    },
                )
                if deferred:
                    # The reconciler starts it when a slot frees (ADR-019).
                    logger.info("Job deferred", extra={"active_vms": active})
                    _log_queued_outcome("deferred", "limit_concurrency")
                    return None
                # A deferral nobody recorded would be lost silently.
                logger.warning(
                    "Deferral not recorded, admitting", extra={"active_vms": active}
                )
                deferral_unrecorded = True
        span.set_attribute("policy.spot", spot)

        async def record_errored(stage: Literal["token", "gce_insert"], exc) -> None:
            # Still a 500 with no outcome line; the record says why.
            await _record_errored(store, key, base, stage, exc)

        operation = await submit_runner_vm(
            RunnerVmSpec(
                vm_name=vm_name,
                installation_id=installation_id,
                repo_full_name=event.repository.full_name,
                repository_id=event.repository.id,
                run_id=event.workflow_job.run_id,
                job_id=event.workflow_job.id,
                run_attempt=event.workflow_job.run_attempt,
                job_labels=event.workflow_job.labels,
                machine_type=policy.machine_type,
                spot=spot,
                max_run_duration=max_run_duration,
                queued_received_at=received_at,
                webhook_trace_id=webhook_trace_id,
                webhook_span_id=webhook_span_id,
            ),
            on_error=record_errored,
        )
        # create_vm returns None when the VM already exists (duplicate delivery).
        kind = "vm_submitted" if operation else "already_exists"
        decision: dict[str, Any] = {
            "kind": kind,
            "machine_type": policy.machine_type,
            "spot": spot,
        }
        if deferral_unrecorded:
            decision["deferral_unrecorded"] = True
        await store.record_fact(key, base, "decision", decision)
        _log_queued_outcome(
            kind, "deferral_unrecorded" if deferral_unrecorded else None
        )
        if operation is None:
            return None
        # The watcher records the `vm` fact with these.
        return replace(operation, job_key=key, job_base=base)


async def _runner_vm_labels(event: WorkflowJobEvent) -> dict[str, str] | None:
    """Labels of the VM running the job. GitHub gives a queued job to any
    idle runner with matching labels, so that can be the VM made for another
    job; readiness then runs from that VM's admission, whichever job it was
    made for (ADR-019), and neither a swap nor a deferral reads as slow
    registration."""
    runner_name = event.workflow_job.runner_name
    if not runner_name:
        return await get_vm_labels_by_job(
            job_id=str(event.workflow_job.id),
            run_id=str(event.workflow_job.run_id),
            run_attempt=str(event.workflow_job.run_attempt),
        )
    if not runner_name.startswith("runnerforge-"):
        return None
    labels = await get_vm_labels_by_name(runner_name)
    # Whoever registers a runner chooses its name: trust the VM only if its
    # own labels say we made it for this installation.
    if (
        labels is None
        or labels.get("runner") != "runnerforge"
        or labels.get("installation_id") != str(event.installation.id)
    ):
        return None
    return labels


async def handle_in_progress(event: WorkflowJobEvent, received_at: float):
    """Handle a `workflow_job.in_progress` webhook.

    Two trace-structure jobs in one function:

    1. Parent the in_progress span under the queued request's outer
       `POST /webhook` server span (anchor persisted in VM labels by
       handle_queued). Effect: queued + in_progress render as one trace
       tree in Cloud Trace, with `webhook.handle_in_progress` as a sibling
       of `webhook.handle_queued` — operator opens one trace and sees the
       entire runner-registration lifecycle.

    2. Emit the on-our-side latency metric — `runner_readiness_seconds` —
       which strips GitHub's queued-webhook delivery delay from the SLO
       signal. Also emits two observation-only metrics for GH delivery lag
       so we can correlate "our SLO got worse" with "GitHub got slower at
       firing webhooks." All deltas floor at zero — NTP skew can otherwise
       drive small negatives that are noise, not signal.
    """
    # Labels lookup BEFORE starting our own span so we can parent it under
    # the queued request when the VM exists.
    vm_labels = await _runner_vm_labels(event)
    queued_ctx = (
        _webhook_span_context(
            vm_labels.get("webhook_trace_id", ""), vm_labels.get("webhook_span_id", "")
        )
        if vm_labels
        else None
    )
    parent_context = (
        set_span_in_context(NonRecordingSpan(queued_ctx)) if queued_ctx else None
    )
    queued_received_at_str = (
        vm_labels.get("queued_received_at", "") if vm_labels else ""
    )

    with tracer.start_as_current_span(
        "webhook.handle_in_progress", context=parent_context
    ) as span:
        span.set_attribute("repo", event.repository.full_name)
        span.set_attribute("sender", event.sender.login)
        span.set_attribute("job_id", event.workflow_job.id)
        span.set_attribute("installation_id", event.installation.id)
        span.set_attribute("labels", event.workflow_job.labels)
        span.set_attribute("run_id", event.workflow_job.run_id)
        span.set_attribute("run_attempt", event.workflow_job.run_attempt)

        # End-to-end view (kept for backwards compat — was the SLO source
        # pre-PR; now demoted to a dashboard tracking metric). Includes
        # GitHub's webhook delivery delay so degradation here doesn't
        # cleanly attribute to our service.
        if event.workflow_job.started_at and event.workflow_job.created_at:
            delta_seconds = (
                event.workflow_job.started_at - event.workflow_job.created_at
            ).total_seconds()
        else:
            delta_seconds = None
        # A negative value means GitHub's two timestamps disagree (see
        # _log_readiness); it would chart as instant.
        if delta_seconds is not None and delta_seconds >= -CLOCK_SKEW_TOLERANCE_SECONDS:
            logger.info(
                "Time for runner to be picked up by github",
                extra={
                    "job_id": event.workflow_job.id,
                    "repo": event.repository.full_name,
                    "time_to_runner_seconds": delta_seconds,
                    "labels": event.workflow_job.labels,
                },
            )

        # On-our-side runner readiness — the new SLO source. Strips queued
        # delivery lag by anchoring on the moment we received the queued
        # webhook (persisted in VM labels) rather than GH's `created_at`.
        # Two GitHub-delivery-lag observations emitted on the same line so
        # dashboards can correlate "our latency moved" with "GH delivery
        # lag moved." All deltas floored at zero — negative deltas would
        # be clock skew, not real signal.
        if event.workflow_job.started_at and queued_received_at_str:
            _log_readiness(event, vm_labels or {}, received_at)


def _log_readiness(
    event: WorkflowJobEvent, vm_labels: dict[str, str], received_at: float
) -> None:
    """runner_readiness_seconds, the Time-to-Register SLO's source.

    It mixes clocks: GitHub's (`started_at`, `created_at`) and ours (the VM's
    `queued_received_at`, this request's receipt). On 2026-11-01 GitHub's
    `created_at` ran an hour ahead of its `started_at`. A sample whose
    timestamps contradict our clock is skipped and logged instead: any
    number from them, 0 s or hours, would be wrong for the SLO."""
    job = event.workflow_job
    assert job.started_at is not None
    queued_received_at = float(vm_labels["queued_received_at"])
    started_at = job.started_at.timestamp()
    created_at = job.created_at.timestamp() if job.created_at else None
    own_vm = vm_labels.get("job_id") == str(job.id)
    # A job is created before it starts, and starts before we hear of it.
    created_ok = created_at is not None and created_at <= (
        min(started_at, received_at) + CLOCK_SKEW_TOLERANCE_SECONDS
    )
    if started_at > received_at + CLOCK_SKEW_TOLERANCE_SECONDS:
        skew = "started_at_ahead"
    elif (
        job.runner_name
        and started_at < queued_received_at - CLOCK_SKEW_TOLERANCE_SECONDS
    ):
        # The runner registered after its VM booted, which was after the VM's
        # admission, so this can't be a real start: GitHub's clock is behind.
        # (Without a runner_name the VM is a guess and an earlier start can
        # be real.)
        skew = "started_at_before_admission"
    elif not own_vm and not created_ok:
        # Another job's VM: only the job's creation says how long the VM
        # had been waiting for it.
        skew = "created_at_unusable"
    else:
        skew = None
    if skew is not None:
        logger.warning(
            "Runner readiness unknown",
            extra={
                "reason": skew,
                "started_at_offset_seconds": started_at - received_at,
                "created_at_offset_seconds": (
                    None if created_at is None else created_at - started_at
                ),
            },
        )
        return

    # Its own VM was admitted for this job, so from that admission. Another
    # job's VM (a swap, or an idle VM left by a cancelled job) from the later
    # of its admission and this job's creation: one already waiting is ready
    # at once.
    ready_from = queued_received_at
    if not own_vm:
        assert created_at is not None
        ready_from = max(ready_from, created_at)
    extras: dict[str, float | str | int] = {
        "job_id": job.id,
        "repo": event.repository.full_name,
        # Floored at zero: small negatives are clock skew, not signal.
        "runner_readiness_seconds": max(0.0, started_at - ready_from),
        "github_in_progress_delivery_lag_seconds": max(0.0, received_at - started_at),
    }
    # The queued delivery lag is this job's only if the VM was made for it.
    if own_vm and created_ok:
        assert created_at is not None
        extras["github_queued_delivery_lag_seconds"] = max(
            0.0, queued_received_at - created_at
        )
    logger.info("Runner registration outcome", extra=extras)


async def handle_completed(event: WorkflowJobEvent) -> PreemptionCheckRequest | None:
    """Process a `workflow_job.completed` webhook: deletes the runner VM and
    returns a `PreemptionCheckRequest` when the job ended in failure or
    cancellation — the caller (main.py) schedules the audit-log cross-check
    in a BackgroundTask. Symmetric with `handle_queued` returning an
    `OperationHandle` for `wait_for_vm_creation` to consume.

    Completion is intentionally a separate trace from queued+in_progress —
    the wall-clock gap (the job running on the VM, can be hours) would
    dominate any merged trace's timeline. Cross-trace navigation is
    preserved via `span.add_link(queued_ctx)` so an operator debugging a
    completion can jump to the matching registration trace.
    """
    # Fetch labels BEFORE delete_vm — while the VM still exists (it may not:
    # GCE deletes preempted Spot VMs itself), so we can retrieve the queued
    # trace context for the cross-trace link.
    runner_name = event.workflow_job.runner_name
    vm_labels = None
    if runner_name:
        vm_labels = await get_vm_labels_by_job(
            job_id=str(event.workflow_job.id),
            run_id=str(event.workflow_job.run_id),
            run_attempt=str(event.workflow_job.run_attempt),
        )

    with tracer.start_as_current_span("webhook.handle_completed") as span:
        span.set_attribute("repo", event.repository.full_name)
        span.set_attribute("sender", event.sender.login)
        span.set_attribute("job_id", event.workflow_job.id)
        span.set_attribute("installation_id", event.installation.id)
        span.set_attribute("labels", event.workflow_job.labels)
        span.set_attribute("run_id", event.workflow_job.run_id)
        span.set_attribute("run_attempt", event.workflow_job.run_attempt)
        if vm_labels:
            queued_ctx = _webhook_span_context(
                vm_labels.get("webhook_trace_id", ""),
                vm_labels.get("webhook_span_id", ""),
            )
            if queued_ctx is not None:
                span.add_link(queued_ctx)
        if not runner_name:
            logger.info(
                "No VM found for completed job (already cleaned up or never created)",
                extra={"runner_name": runner_name},
            )
            return None
        span.set_attribute("runner_name", runner_name)
        # Users may run their own (non-RunnerForge) runners alongside ours;
        # their completed events are normal traffic, not a threat.
        if not runner_name.startswith("runnerforge-"):
            logger.info(
                "Skipping delete: runner is not a RunnerForge VM",
                extra={"runner_name": runner_name},
            )
            return None
        # runner_name is chosen by whoever registered the runner, so the
        # prefix alone proves nothing. Only delete a VM whose own labels say
        # we created it for the installation that sent this event.
        target_labels = await get_vm_labels_by_name(runner_name)
        installation_id = str(event.installation.id)
        if target_labels is None:
            # Not an early return: a preempted Spot VM is deleted by GCE
            # (termination action DELETE) before this event arrives, and that
            # is exactly the case the preemption cross-check below exists for.
            logger.info(
                "VM already deleted",
                extra={"vm_name": runner_name, "reason": "webhook"},
            )
        elif (
            target_labels.get("runner") != "runnerforge"
            or target_labels.get("installation_id") != installation_id
        ):
            # runner_name + installation_id are already on the record via the
            # request log context (main.py update_context).
            logger.warning(
                "Refusing to delete VM not owned by this installation",
                extra={"vm_installation_id": target_labels.get("installation_id")},
            )
            return None
        else:
            operation_id = await delete_vm(runner_name, reason="webhook")
            logger.info(
                "VM deletion submitted",
                extra={"runner_name": runner_name, "operation_id": operation_id},
            )

        # On failure/cancellation, surface a PreemptionCheckRequest so main.py
        # can schedule the audit-log cross-check in a BackgroundTask. Keeps the
        # webhook response off the Cloud Logging round-trip (~100-500ms) and
        # ensures request_id/job_id/repo correlation survives the post-response
        # hop (main.py wraps the task with `with_log_context`).
        conclusion = event.workflow_job.conclusion
        if conclusion in ("failure", "cancelled"):
            return PreemptionCheckRequest(
                vm_name=runner_name,
                job_id=event.workflow_job.id,
                conclusion=conclusion,
            )
        return None
