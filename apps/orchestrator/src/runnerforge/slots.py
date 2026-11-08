"""Slot ledger (ADR-020 step 5).

Which runner VMs hold a slot, kept in Firestore so every Cloud Run instance
sees the same count. Two levels, so a tenant's burst contends on its own
document and only its winners reach the shared one:

- `capacity/inst-<id>`: one installation's holders, for its
  `max_concurrent_vms` limit;
- `capacity/pool`: every holder, for the global caps, plus `released`: VMs
  released lately. The pool keeps counting a released VM for RELEASE_HOLD
  (its delete takes a while to free quota), and repair never adopts a name
  released just before or after its listing.

Holders are keyed by VM name, so a retried or redelivered acquire is
idempotent. Most VM ends send no signal (runtime cap, Spot preemption, a guest
powering off, a lost webhook), so slots are released by repairing the ledger
against the reconciler's GCE listing, not by events alone.

Release 1 is shadow mode: decisions are written and logged, not enforced.

Like the registry, this module doesn't import config: caps come in as
parameters. Every Firestore call is bounded (`_bounded`) and never raises.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from google.cloud import firestore

from runnerforge.models import VmInfo

logger = logging.getLogger(__name__)

CAPACITY_COLLECTION = "capacity"
POOL_DOC = "pool"
# Bumped on an incompatible change: an older version then leaves the
# ledger alone instead of rewriting it.
LEDGER_VERSION = 1
# A holder whose VM isn't listed is dropped once it is older than this: an
# insert takes up to a minute to show in a listing (reconcile.START_GRACE).
LEASE_GRACE = timedelta(minutes=3)
# Added to a VM's runtime cap for a holder's expiry, so a VM GCE deleted at
# its cap frees its slot even while the reconciler is down.
EXPIRY_SLACK = timedelta(minutes=15)
# An adopted holder's earliest expiry, so a VM near its cap isn't adopted,
# expired and adopted again every run.
ADOPTED_MIN_TTL = timedelta(minutes=8)
# How long the pool still counts a released VM unless a listing shows it gone.
RELEASE_HOLD = timedelta(minutes=2)
# How long a released name is remembered at all.
TOMBSTONE_TTL = timedelta(minutes=15)
# vCPUs counted for a machine type nobody can size: the largest size.
UNKNOWN_VCPUS = 16
# A VM in these statuses is being stopped or deleted: kept while held, never
# adopted.
STOPPING_STATUSES = frozenset({"STOPPING", "PENDING_STOP"})

# Hard bounds. A transaction's rollback runs on gapic defaults (up to 60 s),
# so a call that times out is left to finish in the background (`_bounded`).
ACQUIRE_TIMEOUT_SECONDS = 3.0
RELEASE_TIMEOUT_SECONDS = 2.0
REPAIR_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3

Outcome = Literal[
    "granted",  # this call added the holder
    "held",  # the VM already holds a slot (a redelivery or a retry)
    "installation_full",
    "pool_full",
    "unavailable",  # the ledger couldn't answer in time
    "skipped",  # no ledger (JOB_STORE=none) or not asked
]
Source = Literal["webhook", "reconcile", "adopted"]

_SUFFIX_VCPUS = re.compile(r"-(\d+)$")


def machine_vcpus(machine_type: str, known: int | None) -> int:
    """vCPUs a VM of this type counts for: `known` (the machine-types
    cache), else the number ending its name (n2-standard-4), else the
    largest size, which over-counts on purpose."""
    if known is not None:
        return known
    match = _SUFFIX_VCPUS.search(machine_type)
    return int(match.group(1)) if match else UNKNOWN_VCPUS


def installation_doc(installation_id: int) -> str:
    return f"inst-{installation_id}"


# `Holder.raw` of a holder that parsed.
PARSED: Any = object()


@dataclass(frozen=True, slots=True)
class Holder:
    vm_name: str
    # None for a VM whose label doesn't say, or a holder that didn't parse.
    installation_id: int | None
    job_key: str | None
    vcpus: int
    source: str
    at: datetime | None
    expires_at: datetime | None
    # What a holder that didn't parse was stored as, written back unchanged:
    # a newer version's holder stays intact, and still counts here.
    raw: Any = PARSED

    @property
    def parsed(self) -> bool:
        return self.raw is PARSED

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def to_value(self) -> Any:
        if not self.parsed:
            return self.raw
        return {
            "installation_id": self.installation_id,
            "job_key": self.job_key,
            "vcpus": self.vcpus,
            "source": self.source,
            "at": self.at,
            "expires_at": self.expires_at,
        }


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def holder_from_value(vm_name: str, value: Any) -> Holder:
    """Parse one stored holder. One that doesn't parse still counts as one
    VM of the largest size, so a bad write can only over-count."""
    data = value if isinstance(value, dict) else {}
    installation_id = _int(data.get("installation_id"))
    vcpus = _int(data.get("vcpus"))
    at, expires_at = data.get("at"), data.get("expires_at")
    job_key = data.get("job_key")
    if (
        "installation_id" not in data
        or (installation_id is None and data["installation_id"] is not None)
        or vcpus is None
        or vcpus < 0
        or not isinstance(at, datetime)
        or not isinstance(expires_at, datetime)
        or not (job_key is None or isinstance(job_key, str))
    ):
        return Holder(vm_name, None, None, UNKNOWN_VCPUS, "unknown", None, None, value)
    return Holder(
        vm_name,
        installation_id,
        job_key,
        vcpus,
        str(data.get("source") or "unknown"),
        at,
        expires_at,
    )


@dataclass(frozen=True, slots=True)
class Released:
    installation_id: int | None
    vcpus: int
    released_at: datetime
    # A listing no longer shows the VM: it stops counting.
    gone: bool = False

    def counts(self, now: datetime) -> bool:
        return not self.gone and now - self.released_at < RELEASE_HOLD

    def to_value(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "vcpus": self.vcpus,
            "released_at": self.released_at,
            "gone": self.gone,
        }


def released_from_value(value: Any) -> Released | None:
    """A tombstone that doesn't parse is dropped: at worst a VM being
    deleted is adopted for one run."""
    data = value if isinstance(value, dict) else {}
    released_at = data.get("released_at")
    if not isinstance(released_at, datetime):
        return None
    return Released(
        _int(data.get("installation_id")),
        max(_int(data.get("vcpus")) or 0, 0),
        released_at,
        data.get("gone") is True,
    )


@dataclass(frozen=True, slots=True)
class Caps:
    """The global caps; 0 is off."""

    max_total_vms: int = 0
    max_total_vcpus: int = 0


NO_CAPS = Caps()


@dataclass(frozen=True, slots=True)
class SlotRequest:
    vm_name: str
    installation_id: int
    job_key: str | None
    vcpus: int
    # The installation's `max_concurrent_vms`; None is unlimited.
    per_installation: int | None
    expires_at: datetime
    source: Literal["webhook", "reconcile"]


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: Outcome
    holders: dict[str, Holder]
    released: dict[str, Released]
    changed: bool


def _counted(
    holders: Mapping[str, Holder], released: Mapping[str, Released], now: datetime
) -> tuple[int, int]:
    counting = [r for r in released.values() if r.counts(now)]
    return (
        len(holders) + len(counting),
        sum(h.vcpus for h in holders.values()) + sum(r.vcpus for r in counting),
    )


def decide(
    holders: Mapping[str, Holder],
    req: SlotRequest,
    now: datetime,
    *,
    limit: int | None = None,
    caps: Caps = NO_CAPS,
    released: Mapping[str, Released] | None = None,
) -> Decision:
    """One level's answer to an acquire. Expired holders and old tombstones
    are dropped first; `changed` says whether the document needs a write."""
    old_released = dict(released or {})
    live = {n: h for n, h in holders.items() if not h.expired(now)}
    kept = {
        n: r for n, r in old_released.items() if now - r.released_at < TOMBSTONE_TTL
    }
    pruned = len(live) != len(holders) or len(kept) != len(old_released)
    if req.vm_name in live:
        return Decision("held", live, kept, pruned)
    if limit is not None and len(live) >= limit:
        return Decision("installation_full", live, kept, pruned)
    vms, vcpus = _counted(live, kept, now)
    if (caps.max_total_vms and vms >= caps.max_total_vms) or (
        caps.max_total_vcpus and vcpus + req.vcpus > caps.max_total_vcpus
    ):
        return Decision("pool_full", live, kept, pruned)
    # Held again: a capacity refund reuses the name of a VM GCE never made.
    kept.pop(req.vm_name, None)
    live[req.vm_name] = Holder(
        req.vm_name,
        req.installation_id,
        req.job_key,
        req.vcpus,
        req.source,
        now,
        req.expires_at,
    )
    return Decision("granted", live, kept, True)


@dataclass
class Ledger:
    """The ledger as one transaction read it."""

    pool: dict[str, Holder] = field(default_factory=dict)
    released: dict[str, Released] = field(default_factory=dict)
    installations: dict[int, dict[str, Holder]] = field(default_factory=dict)


@dataclass
class RepairPlan:
    ledger: Ledger
    # Installations whose document changed, and whether the pool did.
    installations_changed: set[int]
    pool_changed: bool
    dropped: int = 0
    adopted: int = 0
    # Installation documents left holding nothing: deleted, so an
    # uninstalled installation's ID isn't kept.
    installations_emptied: set[int] = field(default_factory=set)


def release(
    ledger: Ledger,
    vm_name: str,
    installation_id: int,
    now: datetime,
    *,
    owned: bool = False,
) -> tuple[bool, bool]:
    """Release one VM in place: its installation counts it no longer, the
    pool for RELEASE_HOLD more, and the name becomes a tombstone repair
    won't adopt. A pool holder of another installation is left alone: a
    runner's name is chosen by whoever registered it. A name nobody holds
    gets a tombstone only if `owned` (the caller checked the VM's labels):
    otherwise anyone could add VMs to the pool's count. Returns whether the
    installation's document and the pool changed."""
    inst = ledger.installations.get(installation_id, {})
    inst_changed = inst.pop(vm_name, None) is not None
    holder = ledger.pool.get(vm_name)
    if holder is not None and holder.installation_id not in (installation_id, None):
        return inst_changed, False
    if holder is None and (vm_name in ledger.released or not owned):
        return inst_changed, False
    ledger.pool.pop(vm_name, None)
    ledger.released[vm_name] = Released(
        installation_id, holder.vcpus if holder else 0, now
    )
    return inst_changed, True


def repair(
    ledger: Ledger,
    vms: Iterable[VmInfo],
    deleted: set[str],
    listed_at: datetime,
    now: datetime,
    *,
    vcpus_of: Callable[[str], int],
    vm_lifetime: timedelta,
) -> RepairPlan:
    """Bring the ledger in line with a GCE listing taken at `listed_at`:
    drop holders of VMs that are gone or that this run deleted, and adopt
    listed VMs nobody holds (made by an older revision, or whose acquire
    failed)."""
    listed = {vm.name: vm for vm in vms if vm.name not in deleted}
    stale = listed_at - LEASE_GRACE
    released = {
        n: r for n, r in ledger.released.items() if now - r.released_at < TOMBSTONE_TTL
    }
    pool_changed = len(released) != len(ledger.released)
    for name, r in list(released.items()):
        # Only a listing taken after the release can say the VM is gone, and
        # not of a VM this run deleted (left out of `listed`, but listed).
        if (
            name not in listed
            and name not in deleted
            and not r.gone
            and r.released_at <= listed_at
        ):
            released[name] = replace(r, gone=True)
            pool_changed = True

    pool: dict[str, Holder] = {}
    dropped = 0
    for name, holder in ledger.pool.items():
        if name in deleted:
            released[name] = Released(holder.installation_id, holder.vcpus, now)
            dropped += 1
        elif name in listed or (holder.at is not None and holder.at >= stale):
            pool[name] = holder
        else:
            dropped += 1
    for name in deleted - set(ledger.pool):
        if name not in released:
            # Unheld (an older revision's VM): still never adopted while its
            # delete runs.
            released[name] = Released(None, 0, now)
            pool_changed = True
    pool_changed = pool_changed or dropped > 0

    adopted = 0
    for name, vm in listed.items():
        current = pool.get(name)
        if current is not None and current.parsed:
            continue
        if vm.status in STOPPING_STATUSES:
            continue
        tomb = released.get(name)
        if tomb is not None and tomb.released_at > listed_at - RELEASE_HOLD:
            # Released after (or just before) the listing: being deleted.
            continue
        released.pop(name, None)
        pool[name] = _adopt(vm, now, vcpus_of, vm_lifetime)
        adopted += 1
        pool_changed = True

    installations: dict[int, dict[str, Holder]] = {}
    changed: set[int] = set()
    emptied: set[int] = set()
    iids = set(ledger.installations) | {
        h.installation_id for h in pool.values() if h.installation_id is not None
    }
    for iid in iids:
        old = ledger.installations.get(iid, {})
        new = {n: h for n, h in pool.items() if h.installation_id == iid}
        # A grant whose pool step hasn't landed yet (or failed): kept until
        # its lease runs out, like an unlisted pool holder.
        for name, holder in old.items():
            if (
                name not in new
                and name not in deleted
                and name not in released
                and holder.at is not None
                and holder.at >= stale
            ):
                new[name] = holder
        if not new:
            # Only a document that exists is in the old ledger.
            emptied.add(iid)
            continue
        installations[iid] = new
        if new != old:
            changed.add(iid)
    return RepairPlan(
        Ledger(pool, released, installations),
        changed,
        pool_changed,
        dropped,
        adopted,
        emptied,
    )


def _adopt(
    vm: VmInfo, now: datetime, vcpus_of: Callable[[str], int], vm_lifetime: timedelta
) -> Holder:
    labels = vm.labels
    iid = int(labels.installation_id) if labels.installation_id.isdigit() else None
    key = (
        f"{labels.run_id}-{labels.job_id}-{labels.run_attempt}"
        if labels.run_id and labels.job_id and labels.run_attempt
        else None
    )
    return Holder(
        vm.name,
        iid,
        key,
        vcpus_of(vm.machine_type),
        "adopted",
        now,
        max(vm.creation_timestamp + vm_lifetime + EXPIRY_SLACK, now + ADOPTED_MIN_TTL),
    )


@dataclass(frozen=True, slots=True)
class SlotResult:
    outcome: Outcome
    # Counts as the deciding level saw them; None if it didn't answer.
    holders: int | None = None
    installation_holders: int | None = None
    vcpus_held: int | None = None
    error_class: str | None = None


@dataclass(frozen=True, slots=True)
class RepairResult:
    holders: int
    dropped: int
    adopted: int


class SlotLedger(Protocol):
    enabled: bool

    async def acquire(self, req: SlotRequest, caps: Caps) -> SlotResult:
        """Take a slot for `req.vm_name`. Never raises."""
        ...

    async def release(
        self, vm_name: str, installation_id: int, *, owned: bool = False
    ) -> None:
        """Release a VM's slot, best effort: repair catches a miss. `owned`:
        the caller checked the VM is this installation's (see `release`)."""
        ...

    async def apply_repair(
        self,
        vms: list[VmInfo],
        deleted: set[str],
        listed_at: datetime,
        *,
        vcpus_of: Callable[[str], int],
        vm_lifetime: timedelta,
    ) -> RepairResult | None:
        """Repair the ledger against a listing; None if it failed."""
        ...


class NoopSlotLedger:
    """`JOB_STORE=none`: no Firestore, so no ledger."""

    enabled = False

    async def acquire(self, req: SlotRequest, caps: Caps) -> SlotResult:
        return SlotResult("skipped")

    async def release(
        self, vm_name: str, installation_id: int, *, owned: bool = False
    ) -> None:
        return None

    async def apply_repair(
        self,
        vms: list[VmInfo],
        deleted: set[str],
        listed_at: datetime,
        *,
        vcpus_of: Callable[[str], int],
        vm_lifetime: timedelta,
    ) -> RepairResult | None:
        return None


class NewerLedger(Exception):
    """The ledger was written by a newer version: leave it alone."""


# Calls that outlived their bound, kept referenced until they finish.
_background: set[asyncio.Task[Any]] = set()


def _finished(task: asyncio.Task[Any]) -> None:
    _background.discard(task)
    if not task.cancelled():
        # Retrieved so asyncio doesn't log "exception never retrieved"; the
        # caller already reported this call as failed.
        task.exception()


async def _bounded[T](coro: Coroutine[Any, Any, T], timeout: float) -> T:
    """Await `coro` for at most `timeout` seconds. Unlike asyncio.timeout,
    the wait doesn't include the rollback a cancelled transaction runs: a
    call past its bound keeps running in the background, and may still
    commit (holders are idempotent, and repair drops a stray one)."""
    task = asyncio.ensure_future(coro)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
    except BaseException:
        # The caller was cancelled: the call carries on alone, like a timeout.
        _keep(task)
        raise
    if task in done:
        return task.result()
    _keep(task)
    raise TimeoutError


def _keep(task: asyncio.Task[Any]) -> None:
    _background.add(task)
    task.add_done_callback(_finished)


def _holders(data: dict[str, Any]) -> dict[str, Holder]:
    raw = data.get("holders")
    if not isinstance(raw, dict):
        return {}
    return {str(n): holder_from_value(str(n), v) for n, v in raw.items()}


def _released(data: dict[str, Any]) -> dict[str, Released]:
    raw = data.get("released")
    if not isinstance(raw, dict):
        return {}
    parsed = {str(n): released_from_value(v) for n, v in raw.items()}
    return {n: r for n, r in parsed.items() if r is not None}


def _check_version(data: dict[str, Any]) -> None:
    version = data.get("version")
    if isinstance(version, int) and version > LEDGER_VERSION:
        raise NewerLedger


def _pool_value(
    holders: Mapping[str, Holder], released: Mapping[str, Released], now: datetime
) -> dict[str, Any]:
    return {
        "version": LEDGER_VERSION,
        "holders": {n: h.to_value() for n, h in holders.items()},
        "released": {n: r.to_value() for n, r in released.items()},
        "updated_at": now,
    }


def _installation_value(
    installation_id: int, holders: Mapping[str, Holder], now: datetime
) -> dict[str, Any]:
    return {
        "version": LEDGER_VERSION,
        "installation_id": installation_id,
        "holders": {n: h.to_value() for n, h in holders.items()},
        "updated_at": now,
    }


def _merge(first: Outcome, second: Outcome) -> Outcome:
    """Two levels' outcomes as one: `granted` only if neither already held
    the VM, so a release on a later failure never frees another call's
    slot."""
    if first == "granted" and second == "granted":
        return "granted"
    return "held"


class FirestoreSlotLedger:
    # Every call catches Exception on purpose, like the job store: a ledger
    # outage must not fail a webhook. Each failure is logged by class only.

    enabled = True

    def __init__(self, client: firestore.AsyncClient):
        self._client = client

    def _ref(self, doc_id: str):
        return self._client.collection(CAPACITY_COLLECTION).document(doc_id)

    async def _transact(self, fn, *args):
        transaction = self._client.transaction(max_attempts=MAX_ATTEMPTS)
        return await firestore.async_transactional(fn)(transaction, *args)

    async def acquire(self, req: SlotRequest, caps: Caps) -> SlotResult:
        started = time.monotonic()
        try:
            result = await _bounded(self._acquire(req, caps), ACQUIRE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            result = SlotResult("unavailable", error_class=type(exc).__name__)
        level = logging.WARNING if result.outcome == "unavailable" else logging.INFO
        logger.log(
            level,
            "Slot decision",
            extra={
                "outcome": result.outcome,
                "source": req.source,
                "installation_id": req.installation_id,
                "vm_name": req.vm_name,
                "vcpus": req.vcpus,
                "holders": result.holders,
                "installation_holders": result.installation_holders,
                "vcpus_held": result.vcpus_held,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "error_class": result.error_class,
            },
        )
        return result

    async def _acquire(self, req: SlotRequest, caps: Caps) -> SlotResult:
        now = datetime.now(UTC)
        # An unlimited installation has no limit to check; repair keeps its
        # document current for when it gets one.
        inst_outcome: Outcome | None = None
        inst_holders: int | None = None
        if req.per_installation is not None:
            inst_outcome, inst_holders = await self._transact(
                self._installation_step, req, now
            )
            if inst_outcome not in ("granted", "held"):
                return SlotResult(inst_outcome, installation_holders=inst_holders)
        outcome, holders, vcpus = await self._transact(self._pool_step, req, caps, now)
        if outcome == "pool_full":
            if inst_outcome == "granted":
                # Give the installation its slot back now, not at repair.
                await self._transact(self._drop_installation_holder, req)
        elif inst_outcome is not None:
            outcome = _merge(inst_outcome, outcome)
        return SlotResult(
            outcome,
            holders=holders,
            installation_holders=inst_holders,
            vcpus_held=vcpus,
        )

    async def _installation_step(
        self, transaction, req: SlotRequest, now: datetime
    ) -> tuple[Outcome, int]:
        ref = self._ref(installation_doc(req.installation_id))
        data = (await ref.get(transaction=transaction)).to_dict() or {}
        _check_version(data)
        decision = decide(_holders(data), req, now, limit=req.per_installation)
        if decision.changed:
            transaction.set(
                ref,
                _installation_value(req.installation_id, decision.holders, now),
            )
        return decision.outcome, len(decision.holders)

    async def _pool_step(
        self, transaction, req: SlotRequest, caps: Caps, now: datetime
    ) -> tuple[Outcome, int, int]:
        ref = self._ref(POOL_DOC)
        data = (await ref.get(transaction=transaction)).to_dict() or {}
        _check_version(data)
        decision = decide(_holders(data), req, now, caps=caps, released=_released(data))
        if decision.changed:
            transaction.set(ref, _pool_value(decision.holders, decision.released, now))
        vms, vcpus = _counted(decision.holders, decision.released, now)
        return decision.outcome, vms, vcpus

    async def _drop_installation_holder(self, transaction, req: SlotRequest) -> None:
        ref = self._ref(installation_doc(req.installation_id))
        data = (await ref.get(transaction=transaction)).to_dict() or {}
        _check_version(data)
        holders = _holders(data)
        if holders.pop(req.vm_name, None) is not None:
            transaction.set(
                ref,
                _installation_value(req.installation_id, holders, datetime.now(UTC)),
            )

    async def release(
        self, vm_name: str, installation_id: int, *, owned: bool = False
    ) -> None:
        try:
            await _bounded(
                self._transact(self._release_step, vm_name, installation_id, owned),
                RELEASE_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Slot release failed",
                extra={
                    "vm_name": vm_name,
                    "installation_id": installation_id,
                    "error_class": type(exc).__name__,
                },
            )

    async def _release_step(
        self, transaction, vm_name: str, installation_id: int, owned: bool
    ) -> None:
        now = datetime.now(UTC)
        inst_ref = self._ref(installation_doc(installation_id))
        pool_ref = self._ref(POOL_DOC)
        inst_data = (await inst_ref.get(transaction=transaction)).to_dict() or {}
        pool_data = (await pool_ref.get(transaction=transaction)).to_dict() or {}
        _check_version(inst_data)
        _check_version(pool_data)
        ledger = Ledger(
            _holders(pool_data),
            _released(pool_data),
            {installation_id: _holders(inst_data)},
        )
        inst_changed, pool_changed = release(
            ledger, vm_name, installation_id, now, owned=owned
        )
        if inst_changed:
            transaction.set(
                inst_ref,
                _installation_value(
                    installation_id, ledger.installations[installation_id], now
                ),
            )
        if pool_changed:
            transaction.set(pool_ref, _pool_value(ledger.pool, ledger.released, now))

    async def apply_repair(
        self,
        vms: list[VmInfo],
        deleted: set[str],
        listed_at: datetime,
        *,
        vcpus_of: Callable[[str], int],
        vm_lifetime: timedelta,
    ) -> RepairResult | None:
        started = time.monotonic()
        try:
            plan = await _bounded(
                self._transact(
                    self._repair_step, vms, deleted, listed_at, vcpus_of, vm_lifetime
                ),
                REPAIR_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Slot repair failed", extra={"error_class": type(exc).__name__}
            )
            return None
        vms_held, vcpus_held = _counted(
            plan.ledger.pool, plan.ledger.released, datetime.now(UTC)
        )
        result = RepairResult(len(plan.ledger.pool), plan.dropped, plan.adopted)
        logger.info(
            "Slot repair",
            extra={
                "holders": result.holders,
                "counted_vms": vms_held,
                "vcpus_held": vcpus_held,
                "dropped": result.dropped,
                "adopted": result.adopted,
                "installations_changed": len(plan.installations_changed)
                + len(plan.installations_emptied),
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return result

    async def _repair_step(
        self,
        transaction,
        vms: list[VmInfo],
        deleted: set[str],
        listed_at: datetime,
        vcpus_of: Callable[[str], int],
        vm_lifetime: timedelta,
    ) -> RepairPlan:
        now = datetime.now(UTC)
        ledger = Ledger()
        # The whole collection: one pool document and one per installation.
        async for snap in self._client.collection(CAPACITY_COLLECTION).stream(
            transaction=transaction
        ):
            data = snap.to_dict() or {}
            _check_version(data)
            if snap.id == POOL_DOC:
                ledger.pool = _holders(data)
                ledger.released = _released(data)
            elif snap.id.startswith("inst-") and snap.id[5:].isdigit():
                ledger.installations[int(snap.id[5:])] = _holders(data)
        plan = repair(
            ledger,
            vms,
            deleted,
            listed_at,
            now,
            vcpus_of=vcpus_of,
            vm_lifetime=vm_lifetime,
        )
        for iid in plan.installations_changed:
            transaction.set(
                self._ref(installation_doc(iid)),
                _installation_value(iid, plan.ledger.installations[iid], now),
            )
        for iid in plan.installations_emptied:
            transaction.delete(self._ref(installation_doc(iid)))
        if plan.pool_changed:
            transaction.set(
                self._ref(POOL_DOC),
                _pool_value(plan.ledger.pool, plan.ledger.released, now),
            )
        return plan


_ledger: SlotLedger = NoopSlotLedger()


def init_slot_ledger(client: firestore.AsyncClient | None) -> None:
    """Use the job store's Firestore client: same database, one channel."""
    global _ledger
    _ledger = FirestoreSlotLedger(client) if client is not None else NoopSlotLedger()


def get_slot_ledger() -> SlotLedger:
    return _ledger
