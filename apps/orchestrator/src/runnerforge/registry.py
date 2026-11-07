"""Tenant registry (ADR-019).

One document per GitHub App installation in the `installations` collection:
who it is, the owner's decision (`state`), GitHub's view (`github_status`) and
its limits. GitHub's own `installation` events and the reconciler's backfill
keep `github_status` current; only the owner sets `state` to `approved`.

This module takes its Firestore client from outside and doesn't import
`runnerforge.config`, so the admin command can use it without the service's
secrets. Every call is bounded and fails open: a write failure is logged and
the caller carries on, and a failed read is reported as unknown.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import firestore

from runnerforge.machine_policy import SIZE_TABLE

logger = logging.getLogger(__name__)

INSTALLATIONS_COLLECTION = "installations"
# A row of an uninstalled installation is kept this long, then TTL deletes it.
DELETED_RETENTION = timedelta(days=30)
CALL_TIMEOUT_SECONDS = 2.0
# The whole collection is listed once an hour by the reconciler.
LIST_TIMEOUT_SECONDS = 10.0
CACHE_TTL_SECONDS = 60.0
# Upper bounds for limits, to catch typos: the shared pool holds about 30
# runners (guides/limits.mdx), and GCE ends every VM after 6 h 15 min.
MAX_CONCURRENT_VMS_CAP = 30
MAX_RUNTIME_MINUTES_CAP = 360

State = Literal["pending", "approved", "suspended"]
GitHubStatus = Literal["active", "suspended", "deleted"]


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    login: str
    type: str  # "User" | "Organization"


@dataclass(frozen=True, slots=True)
class Limits:
    """An installation's limits, stored as `{"unlimited": true}` or as the
    limit fields. Invalid values raise ValueError, both when the owner
    writes them and when a stored row is read."""

    max_concurrent_vms: int = 2
    max_size: str = "medium"
    spot_only: bool = True
    max_runtime_minutes: int = 60
    literal_machine_types: bool = False
    unlimited: bool = False

    def __post_init__(self) -> None:
        for name in ("max_concurrent_vms", "max_runtime_minutes"):
            value = getattr(self, name)
            # bool is an int subclass: True must not pass as 1.
            if type(value) is not int:
                raise ValueError(f"{name} must be a whole number")
        for name in ("spot_only", "literal_machine_types", "unlimited"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        if not 1 <= self.max_concurrent_vms <= MAX_CONCURRENT_VMS_CAP:
            raise ValueError(
                f"max_concurrent_vms must be 1 to {MAX_CONCURRENT_VMS_CAP}"
            )
        if not 1 <= self.max_runtime_minutes <= MAX_RUNTIME_MINUTES_CAP:
            raise ValueError(
                f"max_runtime_minutes must be 1 to {MAX_RUNTIME_MINUTES_CAP}"
            )
        # A type check first: an unhashable value can't be looked up.
        if type(self.max_size) is not str or self.max_size not in SIZE_TABLE:
            raise ValueError(f"max_size must be one of {', '.join(SIZE_TABLE)}")

    def to_dict(self) -> dict[str, Any]:
        if self.unlimited:
            return {"unlimited": True}
        return {
            "max_concurrent_vms": self.max_concurrent_vms,
            "max_size": self.max_size,
            "spot_only": self.spot_only,
            "max_runtime_minutes": self.max_runtime_minutes,
            "literal_machine_types": self.literal_machine_types,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Limits:
        """Parse a stored row, including one written by another version.

        A field this version doesn't know is ignored (`unknown_limit_fields`
        names them), and a field added after the first five takes its
        default when missing. So a new field must ship in this order: first
        a release that reads it, with a default that keeps the old
        behaviour; then, once no revision that ignores it can serve,
        rows that set it. Otherwise a canary or rollback revision runs the
        tenant looser than the owner set.
        """
        if not isinstance(data, dict) or not data:
            raise ValueError("limits must be {unlimited: true} or the limit fields")
        if "unlimited" in data:
            # Nothing qualifies unlimited, not even a field from a newer
            # version. `is True`: 1 or 1.0 would compare equal.
            if set(data) != {"unlimited"}:
                raise ValueError("unlimited can't be combined with other limits")
            if data["unlimited"] is True:
                return UNLIMITED
            raise ValueError("unlimited must be true")
        known = {name: value for name, value in data.items() if name in _LIMIT_FIELDS}
        missing = _REQUIRED_LIMIT_FIELDS - set(known)
        if missing:
            raise ValueError(f"limits are missing {', '.join(sorted(missing))}")
        return cls(**known)


_LIMIT_FIELDS = frozenset(Limits().to_dict())
# The fields every row has had since ADR-019. A literal, so that a field added
# later is optional: rows written before it existed still parse.
_REQUIRED_LIMIT_FIELDS = frozenset(
    {
        "max_concurrent_vms",
        "max_size",
        "spot_only",
        "max_runtime_minutes",
        "literal_machine_types",
    }
)


def unknown_limit_fields(data: Any) -> list[str]:
    """The fields of stored limits this version doesn't know, sorted."""
    if not isinstance(data, dict):
        return []
    return sorted(
        str(name) for name in data if name not in _LIMIT_FIELDS | {"unlimited"}
    )


# ADR-019's beta defaults, written by `approve` when a row has no limits.
BETA_DEFAULTS = Limits()
UNLIMITED = Limits(unlimited=True)


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    installation_id: int
    state: str
    github_status: str
    approved_at: datetime | None = None
    limits: dict[str, Any] | None = None
    account_login: str | None = None
    account_type: str | None = None
    # When `github_status` last changed (ADR-019 step 4); None on rows that
    # haven't changed status since it was added.
    github_status_at: datetime | None = None
    expire_at: datetime | None = None
    # The reconciler deleted this uninstalled installation's job records.
    records_purged_at: datetime | None = None

    @property
    def admitted(self) -> bool:
        return self.state == "approved" and self.github_status == "active"


class RegistryUnavailable(Exception):
    """The registry couldn't be read and nothing usable was cached."""


class Registry(Protocol):
    enabled: bool

    async def get(self, installation_id: int) -> RegistryEntry | None:
        """The installation's row, or None if it has none. Raises
        RegistryUnavailable if the read fails."""
        ...

    async def record_github_status(
        self,
        installation_id: int,
        status: GitHubStatus,
        account: Account | None = None,
    ) -> None:
        """Set `github_status`. With an account, a missing row is created as
        `pending`; without one, only an existing row is updated."""
        ...

    async def list_all(self) -> dict[int, RegistryEntry] | None:
        """Every row, or None if the listing failed."""
        ...

    async def mark_records_purged(self, installation_id: int) -> None:
        """Note that an uninstalled installation's job records are gone."""
        ...


def entry_from_doc(installation_id: int, data: dict[str, Any]) -> RegistryEntry:
    return RegistryEntry(
        installation_id=installation_id,
        # A row created by a status event before `created` arrived has no
        # decision yet: that's pending.
        state=data.get("state") or "pending",
        github_status=data.get("github_status") or "active",
        approved_at=data.get("approved_at"),
        limits=data.get("limits"),
        account_login=data.get("account_login"),
        account_type=data.get("account_type"),
        github_status_at=data.get("github_status_at"),
        expire_at=data.get("expire_at"),
        records_purged_at=data.get("records_purged_at"),
    )


class NoopRegistry:
    """No job store, no registry (JOB_STORE=none)."""

    enabled = False

    async def get(self, installation_id: int) -> RegistryEntry | None:
        raise RegistryUnavailable("registry disabled")

    async def record_github_status(
        self,
        installation_id: int,
        status: GitHubStatus,
        account: Account | None = None,
    ) -> None:
        return None

    async def list_all(self) -> dict[int, RegistryEntry] | None:
        return None

    async def mark_records_purged(self, installation_id: int) -> None:
        return None


class FirestoreRegistry:
    # Every call catches Exception on purpose, like the job store: failing
    # open covers bugs and auth errors too. Each failure is logged.

    enabled = True

    def __init__(self, client: firestore.AsyncClient):
        self._client = client

    def _doc(self, installation_id: int):
        return self._client.collection(INSTALLATIONS_COLLECTION).document(
            str(installation_id)
        )

    async def get(self, installation_id: int) -> RegistryEntry | None:
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                snapshot = await self._doc(installation_id).get()
        except Exception as exc:
            logger.warning(
                "Registry read failed",
                extra={
                    "operation": "get",
                    "installation_id": installation_id,
                    "error_class": type(exc).__name__,
                },
            )
            raise RegistryUnavailable(str(exc)) from exc
        if not snapshot.exists:
            return None
        return entry_from_doc(installation_id, snapshot.to_dict() or {})

    async def record_github_status(
        self,
        installation_id: int,
        status: GitHubStatus,
        account: Account | None = None,
    ) -> None:
        now = datetime.now(UTC)
        fields: dict[str, Any] = {"github_status": status, "updated_at": now}
        if account is not None:
            fields["account_login"] = account.login
        # Written only on a real change, so a duplicate or late event can't
        # move the cut-off time (the reconciler's grace runs from it), nor
        # push a deleted row's expiry out.
        transition: dict[str, Any] = {
            "github_status_at": now,
            # Only an uninstalled installation's row expires.
            "expire_at": now + DELETED_RETENTION
            if status == "deleted"
            else firestore.DELETE_FIELD,
        }
        doc = self._doc(installation_id)
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                snapshot = await doc.get()
                if snapshot.exists:
                    current = (snapshot.to_dict() or {}).get("github_status")
                    changed = (current or "active") != status
                    try:
                        # Never touches `state`: only the owner decides that.
                        await doc.update(fields | (transition if changed else {}))
                    except NotFound:
                        pass  # no row to update (deleted by TTL in between)
                    return
                if account is None:
                    return  # nothing to update, and no account to create from
                try:
                    await doc.create(
                        {
                            "installation_id": installation_id,
                            "account_id": account.id,
                            "account_type": account.type,
                            "state": "pending",
                            "created_at": now,
                            **{
                                k: v
                                for k, v in (fields | transition).items()
                                if v is not firestore.DELETE_FIELD
                            },
                        }
                    )
                except AlreadyExists:
                    # Another event created it in between: refresh the status
                    # without claiming a transition, but keep its expiry in
                    # step with it (only an uninstalled row expires).
                    await doc.set(
                        fields | {"expire_at": transition["expire_at"]}, merge=True
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Registry write failed",
                extra={
                    "operation": f"github_status:{status}",
                    "installation_id": installation_id,
                    "error_class": type(exc).__name__,
                },
            )

    async def mark_records_purged(self, installation_id: int) -> None:
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                await self._doc(installation_id).update(
                    {"records_purged_at": datetime.now(UTC)}
                )
        except Exception as exc:  # noqa: BLE001
            # Harmless: the next run purges nothing and tries again.
            logger.warning(
                "Registry write failed",
                extra={
                    "operation": "records_purged",
                    "installation_id": installation_id,
                    "error_class": type(exc).__name__,
                },
            )

    async def list_all(self) -> dict[int, RegistryEntry] | None:
        try:
            async with asyncio.timeout(LIST_TIMEOUT_SECONDS):
                return {
                    int(snap.id): entry_from_doc(int(snap.id), snap.to_dict() or {})
                    async for snap in self._client.collection(
                        INSTALLATIONS_COLLECTION
                    ).stream()
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Registry read failed",
                extra={"operation": "list_all", "error_class": type(exc).__name__},
            )
            return None


class CachedRegistry:
    """Last-known-good cache for admission: an entry is reused for
    CACHE_TTL_SECONDS, and after that only if a fresh read fails."""

    def __init__(self, registry: Registry):
        self._registry = registry
        self._entries: dict[int, tuple[float, RegistryEntry | None]] = {}

    async def get(self, installation_id: int) -> RegistryEntry | None:
        cached = self._entries.get(installation_id)
        if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        try:
            entry = await self._registry.get(installation_id)
        except RegistryUnavailable:
            if cached is None:
                raise
            return cached[1]
        self._entries[installation_id] = (time.monotonic(), entry)
        return entry


_registry: Registry = NoopRegistry()
_cached = CachedRegistry(_registry)


def init_registry(client: firestore.AsyncClient | None) -> None:
    """Use the job store's Firestore client: same database, one channel."""
    global _registry, _cached
    _registry = FirestoreRegistry(client) if client is not None else NoopRegistry()
    _cached = CachedRegistry(_registry)


def get_registry() -> Registry:
    return _registry


def get_cached_registry() -> CachedRegistry:
    return _cached
