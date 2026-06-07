import logging

from opentelemetry import trace

from runnerforge.compute_client import create_vm, delete_vm
from runnerforge.config import STARTUP_SCRIPT
from runnerforge.github_client import get_installation_token, get_registration_token
from runnerforge.models import RunnerForgeVmLabels, WorkflowJobEvent

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


# TODO: move this later to proper location
def machine_type_for_labels(labels: list[str]) -> str:
    # later add the actual logic for handling and processing this
    return "e2-medium"


async def handle_queued(event: WorkflowJobEvent):
    with tracer.start_as_current_span("webhook.handle_queued") as span:
        span.set_attribute("repo", event.repository.full_name)
        span.set_attribute("sender", event.sender.login)
        span.set_attribute("job_id", event.workflow_job.id)
        span.set_attribute("installation_id", event.installation.id)
        span.set_attribute("labels", event.workflow_job.labels)
        span.set_attribute("run_id", event.workflow_job.run_id)
        span.set_attribute("run_attempt", event.workflow_job.run_attempt)

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
        )
        await create_vm(
            instance_name=vm_name,
            machine_type=machine_type_for_labels(event.workflow_job.labels),
            labels=labels.model_dump(),
            metadata=metadata,
        )


def handle_in_progress(event: WorkflowJobEvent):
    with tracer.start_as_current_span("webhook.handle_in_progress") as span:
        span.set_attribute("repo", event.repository.full_name)
        span.set_attribute("sender", event.sender.login)
        span.set_attribute("job_id", event.workflow_job.id)
        span.set_attribute("installation_id", event.installation.id)
        span.set_attribute("labels", event.workflow_job.labels)
        span.set_attribute("run_id", event.workflow_job.run_id)
        span.set_attribute("run_attempt", event.workflow_job.run_attempt)

        logger.info(
            "Job picked up by a runner", extra={"job_id": event.workflow_job.id}
        )


async def handle_completed(event: WorkflowJobEvent):
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
            return
        span.set_attribute("runner_name", runner_name)

        operation_id = await delete_vm(runner_name)
        logger.info(
            "VM deletion submitted",
            extra={"runner_name": runner_name, "operation_id": operation_id},
        )
