import logging

from runnerforge.compute_client import create_vm, delete_vm, find_vms_by_job_id
from runnerforge.config import STARTUP_SCRIPT
from runnerforge.github_client import get_installation_token, get_registration_token
from runnerforge.models import WorkflowJobEvent

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
        extra={"job_id": event.workflow_job.id},
    )

    # Create the VM with the registration token metadata
    vm_name = f"runnerforge-{event.workflow_job.id}"
    labels = {
        "runner": "runnerforge",
        "job_id": str(event.workflow_job.id),
        "repo": event.repository.full_name.replace("/", "_"),
        "installation_id": str(event.installation.id),
    }

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
    await create_vm(
        instance_name=vm_name,
        machine_type=machine_type_for_labels(event.workflow_job.labels),
        labels=labels,
        metadata=metadata,
    )


def handle_in_progress(event: WorkflowJobEvent):
    logger.info("Job picked up by a runner", extra={"job_id": event.workflow_job.id})


async def handle_completed(event: WorkflowJobEvent):
    job_id = str(event.workflow_job.id)
    vms = await find_vms_by_job_id(job_id)

    if not vms:
        logger.warning(
            "No VM found for completed job (already cleaned up or never created)",
            extra={
                "job_id": event.workflow_job.id,
                "conclusion": event.workflow_job.conclusion,
            },
        )
        return

    for vm_name in vms:
        operation_id = await delete_vm(vm_name)
        logger.info(
            "VM deletion submitted",
            extra={"job_id": job_id, "vm_name": vm_name, "operation_id": operation_id},
        )
