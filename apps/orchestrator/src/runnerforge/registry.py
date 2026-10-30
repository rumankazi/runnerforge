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

logger = logging.getLogger(__name__)

INSTALLATIONS_COLLECTION = "installations"
# A row of an uninstalled installation is kept this long, then TTL deletes it.
DELETED_RETENTION = timedelta(days=30)
CALL_TIMEOUT_SECONDS = 2.0
# The whole collection is listed once an hour by the reconciler.
LIST_TIMEOUT_SECONDS = 10.0
CACHE_TTL_SECONDS = 60.0

State = Literal["pending", "approved", "suspended"]
GitHubStatus = Literal["active", "suspended", "deleted"]


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    login: str
    type: str  # "User" | "Organization"


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    installation_id: int
    state: str
    github_status: str
    approved_at: datetime | None = None
    limits: dict[str, Any] | None = None

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


def _entry(installation_id: int, data: dict[str, Any]) -> RegistryEntry:
    return RegistryEntry(
        installation_id=installation_id,
        # A row created by a status event before `created` arrived has no
        # decision yet: that's pending.
        state=data.get("state") or "pending",
        github_status=data.get("github_status") or "active",
        approved_at=data.get("approved_at"),
        limits=data.get("limits"),
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
        return _entry(installation_id, snapshot.to_dict() or {})

    async def record_github_status(
        self,
        installation_id: int,
        status: GitHubStatus,
        account: Account | None = None,
    ) -> None:
        now = datetime.now(UTC)
        fields: dict[str, Any] = {
            "github_status": status,
            "updated_at": now,
            # Only an uninstalled installation's row expires.
            "expire_at": now + DELETED_RETENTION
            if status == "deleted"
            else firestore.DELETE_FIELD,
        }
        doc = self._doc(installation_id)
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                if account is None:
                    try:
                        await doc.update(fields)
                    except NotFound:
                        pass  # no row to update (deleted by TTL in between)
                    return
                fields["account_login"] = account.login
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
                                for k, v in fields.items()
                                if v is not firestore.DELETE_FIELD
                            },
                        }
                    )
                except AlreadyExists:
                    # Never touches `state`: only the owner decides that.
                    await doc.set(fields, merge=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Registry write failed",
                extra={
                    "operation": f"github_status:{status}",
                    "installation_id": installation_id,
                    "error_class": type(exc).__name__,
                },
            )

    async def list_all(self) -> dict[int, RegistryEntry] | None:
        try:
            async with asyncio.timeout(LIST_TIMEOUT_SECONDS):
                return {
                    int(snap.id): _entry(int(snap.id), snap.to_dict() or {})
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
