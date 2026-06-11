import logging

from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)

from runnerforge.compute_client import (
    OperationHandle,
    PreemptionCheckRequest,
    create_vm,
    delete_vm,
    get_vm_labels_by_job,
    is_valid_machine_type,
)
from runnerforge.config import STARTUP_SCRIPT
from runnerforge.github_client import get_installation_token, get_registration_token
from runnerforge.machine_policy import MachinePolicyError, resolve_labels
from runnerforge.models import RunnerForgeVmLabels, WorkflowJobEvent

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def _queued_span_context(
    queued_trace_id: str, queued_span_id: str
) -> SpanContext | None:
    """Reconstruct an OTel SpanContext from the hex strings persisted in VM
    labels. Returns None if either is empty (VM created before this field
    shipped, or queued span context was never written). Marked as
    `is_remote=True` so the tracer treats it as cross-process — same trace
    id continuum but originating from a different worker.
    """
    if not queued_trace_id or not queued_span_id:
        return None
    return SpanContext(
        trace_id=int(queued_trace_id, 16),
        span_id=int(queued_span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )


async def handle_queued(
    event: WorkflowJobEvent, received_at: float
) -> OperationHandle | None:
    with tracer.start_as_current_span("webhook.handle_queued") as span:
        span_context = span.get_span_context()
        queued_trace_id = format(span_context.trace_id, "032x")
        queued_span_id = format(span_context.span_id, "016x")
        span.set_attribute("repo", event.repository.full_name)
        span.set_attribute("sender", event.sender.login)
        span.set_attribute("job_id", event.workflow_job.id)
        span.set_attribute("installation_id", event.installation.id)
        span.set_attribute("labels", event.workflow_job.labels)
        span.set_attribute("run_id", event.workflow_job.run_id)
        span.set_attribute("run_attempt", event.workflow_job.run_attempt)

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
            return None
        span.set_attribute("policy.machine_type", policy.machine_type)
        span.set_attribute("policy.spot", policy.spot)

        installation_id = event.installation.id
        installation_token = await get_installation_token(installation_id)
        registration_token = await get_registration_token(
            installation_token=installation_token,
            repo_full_name=event.repository.full_name,
        )
        logger.info("Registration token received")

        # Create the VM with the registration token metadata
        vm_name = f"runnerforge-{event.workflow_job.run_id}-{event.workflow_job.id}-{event.workflow_job.run_attempt}"

        metadata = {
            "registration-token": registration_token,
            "repo-url": f"https://github.com/{event.repository.full_name}",
            "runner-labels": ",".join(event.workflow_job.labels),
            "startup-script": STARTUP_SCRIPT,
        }

        logger.info(
            "Creating VM",
            extra={
                "labels": event.workflow_job.labels,
            },
        )
        labels = RunnerForgeVmLabels(
            runner="runnerforge",
            job_id=str(event.workflow_job.id),
            run_id=str(event.workflow_job.run_id),
            run_attempt=str(event.workflow_job.run_attempt),
            repo=event.repository.full_name.replace("/", "_"),
            installation_id=str(event.installation.id),
            queued_trace_id=queued_trace_id,
            queued_span_id=queued_span_id,
            # GCE label values can't contain '.' — store as integer epoch
            # seconds. Sub-second precision isn't needed for the SLO
            # (300s threshold, NTP skew is sub-second).
            queued_received_at=str(int(received_at)),
        )

        operation = await create_vm(
            instance_name=vm_name,
            machine_type=policy.machine_type,
            spot=policy.spot,
            labels=labels.model_dump(),
            metadata=metadata,
        )
        return operation


async def handle_in_progress(event: WorkflowJobEvent, received_at: float):
    """Handle a `workflow_job.in_progress` webhook.

    Two trace-structure jobs in one function:

    1. Promote the in_progress span from a sibling-via-link of the queued
       span (old behavior) to a child of it. Effect: queued + in_progress
       render as one trace tree in Cloud Trace, so an operator opens one
       trace and sees the entire runner-registration lifecycle. The gap
       between the two webhook deliveries is filled by an explicitly-named
       `runner.provisioning_wait` span so the empty-timeline gap is
       informative ("this is the wait period; not us") rather than blank.

    2. Emit the on-our-side latency metric — `runner_readiness_seconds` —
       which strips GitHub's queued-webhook delivery delay from the SLO
       signal. Also emits two observation-only metrics for GH delivery lag
       so we can correlate "our SLO got worse" with "GitHub got slower at
       firing webhooks." All deltas floor at zero — NTP skew can otherwise
       drive small negatives that are noise, not signal.
    """
    # Labels lookup BEFORE starting our own span so we can parent it under
    # the queued span when the VM exists.
    vm_labels = await get_vm_labels_by_job(
        job_id=str(event.workflow_job.id),
        run_id=str(event.workflow_job.run_id),
        run_attempt=str(event.workflow_job.run_attempt),
    )
    queued_ctx = (
        _queued_span_context(
            vm_labels.get("queued_trace_id", ""), vm_labels.get("queued_span_id", "")
        )
        if vm_labels
        else None
    )

    # Synthetic gap span: explicitly labels the wall-clock window between the
    # two webhook deliveries so the merged trace timeline shows "what's
    # happening here" instead of empty whitespace. Span starts at
    # queued_received_at and ends now — so it overlaps the queued span's
    # ~1-2s handler duration at the leading edge. That overlap is acceptable
    # cost for not needing to persist queued_span_end_at on the VM labels.
    parent_context = (
        set_span_in_context(NonRecordingSpan(queued_ctx)) if queued_ctx else None
    )
    queued_received_at_str = vm_labels.get("queued_received_at", "") if vm_labels else ""
    if parent_context is not None and queued_received_at_str:
        gap_start_ns = int(queued_received_at_str) * 1_000_000_000
        gap_span = tracer.start_span(
            "runner.provisioning_wait",
            context=parent_context,
            start_time=gap_start_ns,
            attributes={
                "spans": "queued_received_at -> in_progress_received_at",
                "note": (
                    "Wall-clock gap between the two webhooks. Includes VM "
                    "provisioning, runner registration, and GitHub's delay "
                    "firing the in_progress webhook."
                ),
            },
        )
        gap_span.end(end_time=int(received_at * 1_000_000_000))

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
            queued_received_at_float = float(queued_received_at_str)
            started_at_epoch = event.workflow_job.started_at.timestamp()
            runner_readiness_seconds = max(
                0.0, started_at_epoch - queued_received_at_float
            )
            in_progress_delivery_lag = max(
                0.0, received_at - started_at_epoch
            )

            extras: dict[str, float | str | int] = {
                "job_id": event.workflow_job.id,
                "repo": event.repository.full_name,
                "runner_readiness_seconds": runner_readiness_seconds,
                "github_in_progress_delivery_lag_seconds": in_progress_delivery_lag,
            }
            if event.workflow_job.created_at:
                created_at_epoch = event.workflow_job.created_at.timestamp()
                extras["github_queued_delivery_lag_seconds"] = max(
                    0.0, queued_received_at_float - created_at_epoch
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
    # Fetch labels BEFORE delete_vm — the VM still exists at this point, so
    # we can retrieve the queued trace context for the cross-trace link.
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
            queued_ctx = _queued_span_context(
                vm_labels.get("queued_trace_id", ""),
                vm_labels.get("queued_span_id", ""),
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
