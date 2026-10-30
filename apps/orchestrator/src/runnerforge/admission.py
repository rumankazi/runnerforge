"""Admission by the tenant registry (ADR-019).

Step 1 is shadow mode: admission still uses the env allow-list, and each
`queued` logs what the registry would have decided next to it, so a wrong
rejection shows up before the registry decides anything.
"""

import logging
from datetime import datetime

from runnerforge.config import ALLOWED_INSTALLATION_IDS
from runnerforge.github_client import get_app_installation, list_app_installations
from runnerforge.models import AppInstallation, InstallationEvent
from runnerforge.registry import (
    Account,
    GitHubStatus,
    RegistryUnavailable,
    get_cached_registry,
    get_registry,
)

logger = logging.getLogger(__name__)

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
    if ALLOWED_INSTALLATION_IDS is not None and installation_id in (
        ALLOWED_INSTALLATION_IDS
    ):
        # Always admitted in registry mode (the owner's installation).
        would_admit, reason = True, "allowlisted"
    else:
        try:
            entry = await get_cached_registry().get(installation_id)
        except RegistryUnavailable:
            would_admit, reason = False, "registry_unavailable"
        else:
            if entry is None:
                would_admit, reason = False, "not_registered"
            elif entry.admitted:
                would_admit, reason = True, "approved"
            else:
                would_admit, reason = False, "not_approved"
    logger.info(
        "Admission shadow decision",
        extra={
            "would_admit": would_admit,
            "reason": reason,
            "allowlist_admits": allowlist_admits,
            "agrees": would_admit == allowlist_admits,
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
