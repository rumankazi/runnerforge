import logging

from runnerforge.compute_client import create_vm, delete_vm
from runnerforge.config import STARTUP_SCRIPT
from runnerforge.github_client import get_installation_token, get_registration_token
from runnerforge.models import RunnerForgeVmLabels, WorkflowJobEvent

logger = logging.getLogger(__name__)


# TODO: move this later to proper location
def machine_type_for_labels(labels: list[str]) -> str:
    # later add the actual logic for handling and processing this
    return "e2-medium"


async def handle_queued(event: WorkflowJobEvent):

    installation_id = event.installation.id
    installation_token = await get_installation_token(installation_id)
    registration_token = await get_registration_token(
        installation_token=installation_token,
        repo_full_name=event.repository.full_name,
    )
    logger.info(
        "Registration token received",
        extra={"job_id": event.workflow_job.id, "run_id": event.workflow_job.run_id},
    )

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
        extra={"job_id": event.workflow_job.id, "labels": event.workflow_job.labels},
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
    logger.info("Job picked up by a runner", extra={"job_id": event.workflow_job.id})


async def handle_completed(runner_name: str | None):
    if not runner_name:
        logger.info(
            "No VM found for completed job (already cleaned up or never created)",
            extra={"runner_name": runner_name},
        )
        return

    operation_id = await delete_vm(runner_name)
    logger.info(
        "VM deletion submitted",
        extra={"runner_name": runner_name, "operation_id": operation_id},
    )
