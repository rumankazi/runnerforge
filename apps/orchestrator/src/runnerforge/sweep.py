import logging
from datetime import UTC, datetime, timedelta

from runnerforge.compute_client import (
    MAX_VM_RUN_DURATION,
    delete_vm,
    list_runnerforge_vms,
)
from runnerforge.github_client import get_installation_token, get_job_status
from runnerforge.models import SweepResult

MAX_LIFETIME_HOURS = 1
# Same cap GCE enforces via max_run_duration; the sweep is the backstop for
# VMs created before that shipped (or if GCE's termination lags).
HARD_LIMIT = MAX_VM_RUN_DURATION

logger = logging.getLogger(__name__)


async def run_sweep() -> SweepResult:
    now = datetime.now(tz=UTC)
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

        try:
            installation_id = int(vm.labels.installation_id)
            if age < timedelta(hours=MAX_LIFETIME_HOURS):
                # potentially active, webhook should handle it
                skipped += 1
                logger.info(
                    "Sweep decision",
                    extra={**log_extra, "decision": "skip", "reason": "too_young"},
                )
                continue

            if age > HARD_LIMIT:
                # Job probably stuck or zombie - kill regardless of status

                await delete_vm(instance_name=vm.name, reason="sweep")
                deleted += 1
                logger.info(
                    "Sweep decision",
                    extra={**log_extra, "decision": "delete", "reason": "hard_limit"},
                )
                continue

            # Age is between MAX_LIFETIME and HARD_LIMIT — check job status
            if vm.repo_full_name is None:
                # Without the exact repo, GitHub would 404 and a 404 reads as
                # "orphan" — never delete on a guess. The hard limit still applies.
                skipped += 1
                logger.warning(
                    "Sweep decision",
                    extra={**log_extra, "decision": "skip", "reason": "unknown_repo"},
                )
                continue
            if installation_id not in installation_token_cache:
                installation_token_cache[
                    installation_id
                ] = await get_installation_token(installation_id)
            token = installation_token_cache[installation_id]

            job_status = await get_job_status(
                installation_token=token,
                repo_full_name=vm.repo_full_name,
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
        # Deliberately broad: one VM that fails to sweep must not abort the
        # sweep for every other VM. The failure is recorded and reported.
        except Exception as e:  # noqa: BLE001
            errors.append(f"{vm.name}: {e}")
            logger.error(
                "Sweep failed for VM", extra={"vm_name": vm.name, "error": str(e)}
            )
            continue
    return SweepResult(
        checked=len(vms), deleted=deleted, skipped=skipped, errors=errors
    )
