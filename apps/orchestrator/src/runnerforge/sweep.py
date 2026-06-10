import logging
from datetime import datetime, timedelta, timezone

from runnerforge.compute_client import delete_vm, list_runnerforge_vms
from runnerforge.github_client import get_installation_token, get_job_status
from runnerforge.models import SweepResult

MAX_LIFETIME_HOURS = 1
HARD_LIMIT_HOURS = 6

logger = logging.getLogger(__name__)


async def run_sweep() -> SweepResult:
    now = datetime.now(tz=timezone.utc)
    installation_token_cache: dict[int, str] = {}
    errors: list[str] = []
    deleted: int = 0
    skipped: int = 0
    # Fetch all candidate VMs
    vms = await list_runnerforge_vms()
    # For each VM
    for vm in vms:
        age = now - vm.creation_timestamp
        age_minutes = int(age.total_seconds() / 60)
        log_extra = {"vm_name": vm.name, "age_minutes": age_minutes}
        installation_id = int(vm.labels.installation_id)

        try:
            if age < timedelta(hours=MAX_LIFETIME_HOURS):
                # potentially active, webhook should handle it
                skipped += 1
                logger.info(
                    "Sweep decision",
                    extra={**log_extra, "decision": "skip", "reason": "too_young"},
                )
                continue

            if age > timedelta(hours=HARD_LIMIT_HOURS):
                # Job probably stuck or zombie - kill regardless of status

                await delete_vm(instance_name=vm.name, reason="sweep")
                deleted += 1
                logger.info(
                    "Sweep decision",
                    extra={**log_extra, "decision": "delete", "reason": "hard_limit"},
                )
                continue

            # Age is between MAX_LIFETIME and HARD_LIMIT — check job status
            if installation_id not in installation_token_cache:
                installation_token_cache[
                    installation_id
                ] = await get_installation_token(installation_id)
            token = installation_token_cache[installation_id]

            job_status = await get_job_status(
                installation_token=token,
                repo_full_name=vm.labels.repo,
                job_id=vm.labels.job_id,
            )

            if job_status is None or job_status.status == "completed":
                await delete_vm(instance_name=vm.name, reason="sweep")
                deleted += 1
                logger.info(
                    "Sweep decision",
                    extra={
                        **log_extra,
                        "decision": "delete",
                        "reason": "job_completed",
                    },
                )
            else:
                skipped += 1
                logger.info(
                    "Sweep decision",
                    extra={**log_extra, "decision": "skip", "reason": "job_running"},
                )
        except Exception as e:
            errors.append(f"{vm.name}: {e}")
            logger.error(
                "Sweep failed for VM", extra={"vm_name": vm.name, "error": str(e)}
            )
            continue
    return SweepResult(
        checked=len(vms), deleted=deleted, skipped=skipped, errors=errors
    )
