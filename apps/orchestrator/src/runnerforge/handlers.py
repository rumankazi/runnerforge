import logging

from opentelemetry import trace

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


async def handle_queued(event: WorkflowJobEvent) -> OperationHandle | None:
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
        )

        operation = await create_vm(
            instance_name=vm_name,
            machine_type=policy.machine_type,
            spot=policy.spot,
            labels=labels.model_dump(),
            metadata=metadata,
        )
        return operation


async def handle_in_progress(event: WorkflowJobEvent):
    with tracer.start_as_current_span("webhook.handle_in_progress") as span:
        span.set_attribute("repo", event.repository.full_name)
        span.set_attribute("sender", event.sender.login)
        span.set_attribute("job_id", event.workflow_job.id)
        span.set_attribute("installation_id", event.installation.id)
        span.set_attribute("labels", event.workflow_job.labels)
        span.set_attribute("run_id", event.workflow_job.run_id)
        span.set_attribute("run_attempt", event.workflow_job.run_attempt)
        vm_labels = await get_vm_labels_by_job(
            job_id=str(event.workflow_job.id),
            run_id=str(event.workflow_job.run_id),
            run_attempt=str(event.workflow_job.run_attempt),
        )
        if vm_labels and vm_labels.get("queued_trace_id"):
            from opentelemetry.trace import SpanContext, TraceFlags

            queued_ctx = SpanContext(
                trace_id=int(vm_labels["queued_trace_id"], 16),
                span_id=int(vm_labels["queued_span_id"], 16),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            span.add_link(queued_ctx)
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


async def handle_completed(event: WorkflowJobEvent) -> PreemptionCheckRequest | None:
    """Process a `workflow_job.completed` webhook: deletes the runner VM and
    returns a `PreemptionCheckRequest` when the job ended in failure or
    cancellation — the caller (main.py) schedules the audit-log cross-check
    in a BackgroundTask. Symmetric with `handle_queued` returning an
    `OperationHandle` for `wait_for_vm_creation` to consume.
    """
    with tracer.start_as_current_span("webhook.handle_completed") as span:
        span.set_attribute("repo", event.repository.full_name)
        span.set_attribute("sender", event.sender.login)
        span.set_attribute("job_id", event.workflow_job.id)
        span.set_attribute("installation_id", event.installation.id)
        span.set_attribute("labels", event.workflow_job.labels)
        span.set_attribute("run_id", event.workflow_job.run_id)
        span.set_attribute("run_attempt", event.workflow_job.run_attempt)
        runner_name = event.workflow_job.runner_name
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
