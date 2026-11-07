"""Admission by the tenant registry (ADR-019).

With ADMISSION=allowlist (the default) the env allow-list admits, and each
`queued` logs what the registry would have decided next to it (shadow mode),
so a wrong rejection shows up before the registry decides anything. With
ADMISSION=registry, `decide_admission` admits, and its limits apply.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from runnerforge.compute_client import MAX_VM_RUN_DURATION, machine_type_vcpus
from runnerforge.config import ALLOWED_INSTALLATION_IDS
from runnerforge.github_client import get_app_installation, list_app_installations
from runnerforge.machine_policy import SIZE_TABLE, SIZE_VCPUS, MachinePolicy
from runnerforge.models import AppInstallation, InstallationEvent
from runnerforge.registry import (
    BETA_DEFAULTS,
    Account,
    GitHubStatus,
    Limits,
    RegistryUnavailable,
    get_cached_registry,
    get_registry,
    unknown_limit_fields,
)

logger = logging.getLogger(__name__)

Reason = Literal[
    "allowlisted",
    "approved",
    "not_registered",
    "pending",
    "suspended",
    "github_inactive",
    "registry_unavailable",
]


@dataclass(frozen=True, slots=True)
class Admission:
    admitted: bool
    reason: Reason
    # None means unlimited.
    limits: Limits | None = None
    # Job records and the preemption cross-check: allow-listed, or approved
    # at least once (`approved_at`), so strangers can't grow the store.
    record: bool = False


# Added to a limited tenant's runtime cap for the VM's boot.
BOOT_ALLOWANCE = timedelta(minutes=15)

# What ADMISSION=allowlist gives every installation it lets through.
ALLOWLISTED = Admission(admitted=True, reason="allowlisted", record=True)


def _row_limits(installation_id: int, raw: object) -> Limits | None:
    if raw is None:
        # Approved without limits: only a hand edit does that.
        return BETA_DEFAULTS
    try:
        limits = Limits.from_dict(raw)
    except ValueError:
        logger.warning(
            "Registry limits invalid, using the beta defaults",
            extra={"installation_id": installation_id},
        )
        return BETA_DEFAULTS
    if unknown := unknown_limit_fields(raw):
        # Written by a newer version; see `Limits.from_dict`.
        logger.warning(
            "Limits fields unknown, ignored",
            extra={"installation_id": installation_id, "fields": unknown},
        )
    return None if limits.unlimited else limits


async def decide_admission(installation_id: int) -> Admission:
    """Registry-mode admission for one installation: allow-listed IDs are
    always admitted and unlimited; others need an approved row of an
    installation GitHub shows as active."""
    if ALLOWED_INSTALLATION_IDS is not None and installation_id in (
        ALLOWED_INSTALLATION_IDS
    ):
        return ALLOWLISTED
    try:
        entry = await get_cached_registry().get(installation_id)
    except RegistryUnavailable:
        return Admission(admitted=False, reason="registry_unavailable")
    if entry is None:
        return Admission(admitted=False, reason="not_registered")
    record = entry.approved_at is not None
    if entry.github_status != "active":
        return Admission(admitted=False, reason="github_inactive", record=record)
    if entry.state != "approved":
        reason: Reason = "suspended" if entry.state == "suspended" else "pending"
        return Admission(admitted=False, reason=reason, record=record)
    return Admission(
        admitted=True,
        reason="approved",
        limits=_row_limits(installation_id, entry.limits),
        record=True,
    )


def vm_settings(policy: MachinePolicy, limits: Limits | None) -> tuple[bool, timedelta]:
    """Spot and the VM's runtime cap under an installation's limits:
    `spot_only` forces Spot, and the cap is its runtime plus boot."""
    if limits is None:
        return policy.spot, MAX_VM_RUN_DURATION
    return (
        policy.spot or limits.spot_only,
        timedelta(minutes=limits.max_runtime_minutes) + BOOT_ALLOWANCE,
    )


def exceeds_size(policy: MachinePolicy, limits: Limits) -> bool:
    """Whether the job's machine is bigger than `max_size` allows. A literal
    type needs `literal_machine_types` and is capped by vCPUs; with the
    machine-types cache degraded its size is unknown, so it's refused."""
    cap = SIZE_VCPUS[limits.max_size]
    if not policy.literal:
        size = next(k for k, v in SIZE_TABLE.items() if v == policy.machine_type)
        return SIZE_VCPUS[size] > cap
    if not limits.literal_machine_types:
        return True
    vcpus = machine_type_vcpus(policy.machine_type)
    return vcpus is None or vcpus > cap


# `installation` actions that change GitHub's view of an installation. Others
# (e.g. new_permissions_accepted) don't affect admission.
_GITHUB_STATUS: dict[str, GitHubStatus] = {
    "created": "active",
    "unsuspend": "active",
    "suspend": "suspended",
    "deleted": "deleted",
}


def _account(installation: AppInstallation) -> Account:
    return Account(
        id=installation.account.id,
        login=installation.account.login,
        type=installation.account.type,
    )


async def handle_installation_event(event: InstallationEvent) -> None:
    """Keep the installation's registry row in step with GitHub. A new
    installation starts `pending`; only the owner approves."""
    status = _GITHUB_STATUS.get(event.action)
    installation = event.installation
    if status is None:
        logger.info("Ignoring installation action", extra={"action": event.action})
        return
    await get_registry().record_github_status(
        installation.id, status, account=_account(installation)
    )
    logger.info(
        "Installation event",
        extra={
            # The login is in the registry row; the ID is enough to correlate.
            "action": event.action,
            "installation_id": installation.id,
        },
    )


async def log_shadow_admission(installation_id: int, allowlist_admits: bool) -> None:
    """What the registry would decide for a `queued` job, next to what the
    allow-list did. Logs only; nothing acts on it."""
    if not get_registry().enabled:
        return
    decision = await decide_admission(installation_id)
    logger.info(
        "Admission shadow decision",
        extra={
            "would_admit": decision.admitted,
            "reason": decision.reason,
            "allowlist_admits": allowlist_admits,
            "agrees": decision.admitted == allowlist_admits,
        },
    )


def registry_backfill_due(now: datetime) -> bool:
    """Hourly: the reconciler run in the first 5 minutes of each hour (it
    runs every 5 minutes). Stateless, so any instance can tell."""
    return now.minute < 5


def _github_status(installation: AppInstallation) -> GitHubStatus:
    return "suspended" if installation.suspended_at is not None else "active"


async def sync_registry() -> int:
    """Bring registry rows in line with GitHub's installations: create missing
    rows (`pending`), refresh `github_status`, and mark an installation
    `deleted` only once GitHub confirms it's gone. GitHub doesn't redeliver
    webhooks, so this repairs lost `installation` events. Returns the number
    of rows written."""
    registry = get_registry()
    if not registry.enabled:
        return 0
    # Rows first: a row created by a webhook during the pass is then never
    # compared against a listing taken before it existed.
    rows = await registry.list_all()
    if rows is None:
        logger.warning("Registry backfill skipped", extra={"reason": "read_failed"})
        return 0
    try:
        installations = await list_app_installations()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Registry backfill skipped",
            extra={"reason": "list_failed", "error_class": type(e).__name__},
        )
        return 0

    changes = 0
    listed: set[int] = set()
    for installation in installations:
        listed.add(installation.id)
        row = rows.get(installation.id)
        if row is None or row.github_status != _github_status(installation):
            await registry.record_github_status(
                installation.id,
                _github_status(installation),
                account=_account(installation),
            )
            changes += 1
    for installation_id, row in rows.items():
        if installation_id in listed or row.github_status == "deleted":
            continue
        # The listing pages by number, so an uninstall during the walk can
        # shift a live installation out of it. Later steps treat `deleted`
        # as final (VMs and records go), so only GitHub's 404 decides.
        try:
            confirmed = await get_app_installation(installation_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Registry backfill could not confirm installation",
                extra={
                    "installation_id": installation_id,
                    "error_class": type(e).__name__,
                },
            )
            continue
        status: GitHubStatus = (
            "deleted" if confirmed is None else _github_status(confirmed)
        )
        if status != row.github_status:
            await registry.record_github_status(installation_id, status)
            changes += 1
    return changes
