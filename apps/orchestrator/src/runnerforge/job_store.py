"""Per-job record store (ADR-018).

One document per job, keyed like the VM name without its `runnerforge-` prefix.
The document holds facts (decision, vm, in_progress, completed, ...), never a
status; readers derive the status from the facts.

Every store call is bounded by a hard timeout and fails open: a slow or broken
store logs `Job store write failed` and the webhook carries on. GitHub never
retries a webhook, so a store outage must not turn into stuck jobs.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol

from google.api_core.exceptions import AlreadyExists, NotFound, ServiceUnavailable
from google.api_core.retry import AsyncRetry, if_exception_type
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import firestore
from google.cloud.firestore import FieldFilter

from runnerforge.config import FIRESTORE_DATABASE, GCP_PROJECT_ID, JOB_STORE
from runnerforge.models import WorkflowJobEvent

logger = logging.getLogger(__name__)

JOBS_COLLECTION = "jobs"
SCHEMA_VERSION = 1
# Drives the TTL policy on `expire_at` (infra/terraform/firestore.tf).
RETENTION = timedelta(days=90)
# First reconciler look at a job: the Time-to-Register SLO threshold.
FIRST_CHECK_DELAY = timedelta(minutes=5)
# Hard bound per store call. The client's own `timeout=` only bounds one
# attempt, and its default write retry runs for up to 60 s, so the bound is
# asyncio's, not the client's.
CALL_TIMEOUT_SECONDS = 2.0
# Bound for the startup warm-up read, which runs in the background.
WARMUP_TIMEOUT_SECONDS = 3.0
# Reconciler queries run inside a scheduled request, not a webhook, and return
# many documents, so they get a longer bound.
QUERY_TIMEOUT_SECONDS = 10.0
# Only UNAVAILABLE is retried (e.g. a stale connection after an idle period);
# the hard timeout above still ends the call.
_RETRY = AsyncRetry(
    predicate=if_exception_type(ServiceUnavailable),
    initial=0.1,
    maximum=0.5,
    multiplier=2.0,
    timeout=CALL_TIMEOUT_SECONDS,
)

Source = Literal["webhook", "watcher", "reconcile"]


class CreateResult(StrEnum):
    CREATED = "created"
    EXISTS_OPEN = "exists_open"
    EXISTS_CLOSED = "exists_closed"
    # The store couldn't say (disabled, timed out, errored). Callers proceed.
    UNKNOWN = "unknown"


Record = tuple[str, dict[str, Any]]


class JobStore(Protocol):
    # False for the no-op store: the reconciler then has nothing to do.
    enabled: bool

    async def create_if_absent(self, key: str, base: dict[str, Any]) -> CreateResult:
        """Create the job's record; if it already exists, report whether it
        is still open (a closed record means `completed` arrived first)."""
        ...

    async def record_fact(
        self,
        key: str,
        base: dict[str, Any],
        name: str,
        fact: dict[str, Any],
        source: Source = "webhook",
        reopen: bool = True,
    ) -> None:
        """Merge one fact plus the base fields into the job's record.

        `completed` closes the record. Other facts reopen it unless
        `reopen=False`, for facts that can land after `completed`
        (the VM watcher, the preemption cross-check)."""
        ...

    async def list_due(self, now: datetime, limit: int) -> list[Record]:
        """Open records whose `next_check_at` has passed, earliest first."""
        ...

    async def list_open(self, limit: int) -> list[Record]:
        """Open records, any order."""
        ...

    async def patch(self, key: str, fields: dict[str, Any]) -> None:
        """Update fields of an existing record; never creates one."""
        ...


def job_key(run_id: int, job_id: int, run_attempt: int) -> str:
    return f"{run_id}-{job_id}-{run_attempt}"


def base_fields(event: WorkflowJobEvent, received_at: float) -> dict[str, Any]:
    """Fields every write carries, so a record created by whichever event
    arrives first still gets its TTL and is visible to the reconciler.

    Rewriting `next_check_at` resets a reconciler backoff to the first check;
    that costs at most one extra check per webhook, which is cheap."""
    job = event.workflow_job
    queued_at = job.created_at or datetime.fromtimestamp(received_at, UTC)
    return {
        "installation_id": event.installation.id,
        "repository_id": event.repository.id,
        "repo": event.repository.full_name,
        "run_id": job.run_id,
        "job_id": job.id,
        "run_attempt": job.run_attempt,
        "labels": job.labels,
        "queued_at": queued_at,
        "expire_at": queued_at + RETENTION,
        "next_check_at": queued_at + FIRST_CHECK_DELAY,
        "schema_version": SCHEMA_VERSION,
    }


def _log_failure(operation: str, key: str, exc: BaseException) -> None:
    logger.warning(
        "Job store write failed",
        extra={
            "operation": operation,
            "job_key": key,
            "error_class": type(exc).__name__,
        },
    )


class NoopJobStore:
    """`JOB_STORE=none`: the default, so an install without Firestore runs."""

    enabled = False

    async def create_if_absent(self, key: str, base: dict[str, Any]) -> CreateResult:
        return CreateResult.UNKNOWN

    async def record_fact(
        self,
        key: str,
        base: dict[str, Any],
        name: str,
        fact: dict[str, Any],
        source: Source = "webhook",
        reopen: bool = True,
    ) -> None:
        return None

    async def list_due(self, now: datetime, limit: int) -> list[Record]:
        return []

    async def list_open(self, limit: int) -> list[Record]:
        return []

    async def patch(self, key: str, fields: dict[str, Any]) -> None:
        return None


class FirestoreJobStore:
    # Every call catches Exception on purpose: failing open covers bugs and
    # auth errors too, not only GoogleAPIError. Each failure is logged.

    enabled = True

    def __init__(self, client: firestore.AsyncClient):
        self._client = client

    def _doc(self, key: str):
        return self._client.collection(JOBS_COLLECTION).document(key)

    async def create_if_absent(self, key: str, base: dict[str, Any]) -> CreateResult:
        doc = self._doc(key)
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                await doc.create({**base, "open": True}, retry=_RETRY)
            return CreateResult.CREATED
        except AlreadyExists:
            # Also raised when a retried or cancelled attempt of this same
            # create had already committed, so it's never treated as an error.
            pass
        except Exception as exc:  # noqa: BLE001
            # Skip the read: a second slow call would double the delay.
            _log_failure("create", key, exc)
            return CreateResult.UNKNOWN

        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                snapshot = await doc.get(field_paths=["open"], retry=_RETRY)
        except Exception as exc:  # noqa: BLE001
            _log_failure("get", key, exc)
            return CreateResult.UNKNOWN
        # Every write that creates a record sets `open`; treat a missing one
        # as open rather than skipping a VM.
        if (snapshot.to_dict() or {}).get("open", True) is False:
            return CreateResult.EXISTS_CLOSED
        return CreateResult.EXISTS_OPEN

    async def record_fact(
        self,
        key: str,
        base: dict[str, Any],
        name: str,
        fact: dict[str, Any],
        source: Source = "webhook",
        reopen: bool = True,
    ) -> None:
        data: dict[str, Any] = {
            **base,
            name: {**fact, "at": datetime.now(UTC), "source": source},
        }
        doc = self._doc(key)
        try:
            # One bound for the whole write, however many calls it takes.
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                if name == "completed" or reopen:
                    # A late in_progress after completed reopens the record;
                    # the reconciler closes it again.
                    data["open"] = name != "completed"
                    # merge=True deep-merges nested maps: other facts are kept.
                    await doc.set(data, merge=True, retry=_RETRY)
                else:
                    await self._merge_keeping_open(doc, data)
        except Exception as exc:  # noqa: BLE001
            _log_failure(f"record_fact:{name}", key, exc)

    async def _merge_keeping_open(self, doc, data: dict[str, Any]) -> None:
        """For facts that can land after `completed` (background tasks only):
        leave an existing `open` alone, but give a record this write creates
        `open: true`, or the reconciler's `open == true` query never sees it.
        A missing record can't have been closed: `completed` would have
        created it."""
        try:
            await doc.update(data, retry=_RETRY)
            return
        except NotFound:
            pass
        try:
            await doc.create({**data, "open": True}, retry=_RETRY)
        except AlreadyExists:
            # Another write created it in between: merge without `open`.
            await doc.set(data, merge=True, retry=_RETRY)

    async def list_due(self, now: datetime, limit: int) -> list[Record]:
        query = (
            self._client.collection(JOBS_COLLECTION)
            .where(filter=FieldFilter("open", "==", True))
            .where(filter=FieldFilter("next_check_at", "<=", now))
            .order_by("next_check_at")
            .limit(limit)
        )
        return await self._run_query("list_due", query)

    async def list_open(self, limit: int) -> list[Record]:
        query = (
            self._client.collection(JOBS_COLLECTION)
            .where(filter=FieldFilter("open", "==", True))
            .limit(limit)
        )
        return await self._run_query("list_open", query)

    async def _run_query(self, operation: str, query) -> list[Record]:
        try:
            async with asyncio.timeout(QUERY_TIMEOUT_SECONDS):
                return [
                    (snap.id, snap.to_dict() or {})
                    async for snap in query.stream(retry=_RETRY)
                ]
        except Exception as exc:  # noqa: BLE001
            # Not "write failed": the write alert counts only writes.
            logger.warning(
                "Job store read failed",
                extra={"operation": operation, "error_class": type(exc).__name__},
            )
            return []

    async def patch(self, key: str, fields: dict[str, Any]) -> None:
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                await self._doc(key).update(fields, retry=_RETRY)
        except NotFound:
            # Deleted by TTL in between: nothing to update.
            pass
        except Exception as exc:  # noqa: BLE001
            _log_failure("patch", key, exc)

    async def warm_up(self) -> None:
        """One bounded read so channel, TLS and token setup happen at startup."""
        try:
            async with asyncio.timeout(WARMUP_TIMEOUT_SECONDS):
                await self._doc("_warmup").get(retry=_RETRY)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Job store warm-up failed", extra={"error_class": type(exc).__name__}
            )


_client: firestore.AsyncClient | None = None
_store: JobStore = NoopJobStore()
_warmup_task: asyncio.Task[None] | None = None


async def init_job_store() -> None:
    global _client, _store, _warmup_task
    if JOB_STORE == "none":
        logger.info("Job store disabled (JOB_STORE=none)")
        return
    try:
        # The constructor looks up credentials synchronously: do it here, at
        # startup, never inside a request.
        _client = firestore.AsyncClient(
            project=GCP_PROJECT_ID, database=FIRESTORE_DATABASE
        )
    except DefaultCredentialsError:
        logger.warning(
            "job store init skipped - no GCP credentials available; "
            "job records will not be written"
        )
        return
    store = FirestoreJobStore(_client)
    # Not awaited: uvicorn opens the port only after lifespan startup, so
    # awaiting would add the warm-up to every cold start (all of its bound if
    # Firestore hangs). Requests share the channel it is opening.
    _warmup_task = asyncio.create_task(store.warm_up())
    _store = store
    logger.info("Job store ready", extra={"database": FIRESTORE_DATABASE})


async def close_job_store() -> None:
    global _client, _store, _warmup_task
    _store = NoopJobStore()
    if _warmup_task is not None:
        _warmup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _warmup_task
        _warmup_task = None
    if _client is None:
        return
    # AsyncClient has no public close(). Read the private attribute rather than
    # `_firestore_api`, which would open a channel just to close it.
    api = _client._firestore_api_internal
    if api is not None:
        await api.transport.close()
    _client = None


def get_job_store() -> JobStore:
    return _store


def get_client() -> firestore.AsyncClient | None:
    """The Firestore client, for the registry (same database, one channel)."""
    return _client
