import logging

from runnerforge.github_client import get_installation_token, get_registration_token

logger = logging.getLogger(__name__)


async def handle_queued(event):
    logger.info(
        "Creating VM",
        extra={"job_id": event.workflow_job.id, "labels": event.workflow_job.labels},
    )
    installation_id = event.installation.id
    installation_token = await get_installation_token(installation_id)
    await get_registration_token(
        installation_token=installation_token,
        repo_full_name=event.repository.full_name,
    )
    logger.info(
        "Registration token received",
        extra={"job_id": event.workflow_job.id},
    )


def handle_in_progress(event):
    logger.info("Job picked up by a runner", extra={"job_id": event.workflow_job.id})


def handle_completed(event):
    logger.info(
        "Would delete VM",
        extra={
            "job_id": event.workflow_job.id,
            "conclusion": event.workflow_job.conclusion,
        },
    )
