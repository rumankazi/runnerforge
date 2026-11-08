import asyncio
import copy
import gc
import logging
from datetime import UTC, datetime, timedelta

import pytest

from runnerforge import slots
from runnerforge.models import RunnerForgeVmLabels, VmInfo
from runnerforge.slots import (
    ADOPTED_MIN_TTL,
    EXPIRY_SLACK,
    LEASE_GRACE,
    LEDGER_VERSION,
    NO_CAPS,
    RELEASE_HOLD,
    TOMBSTONE_TTL,
    UNKNOWN_VCPUS,
    Caps,
    FirestoreSlotLedger,
    Holder,
    Ledger,
    NoopSlotLedger,
    Released,
    SlotRequest,
    decide,
    holder_from_value,
    installation_doc,
    machine_vcpus,
    release,
    released_from_value,
    repair,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
LIFETIME = timedelta(hours=6, minutes=15)


def _holder(name: str, iid: int | None = 7, vcpus: int = 4, **kw) -> Holder:
    return Holder(
        name,
        iid,
        kw.get("job_key", "1-2-1"),
        vcpus,
        kw.get("source", "webhook"),
        kw.get("at", NOW - timedelta(minutes=10)),
        kw.get("expires_at", NOW + timedelta(hours=1)),
    )


def _req(name: str = "runnerforge-1-2-1", **kw) -> SlotRequest:
    return SlotRequest(
        name,
        kw.get("iid", 7),
        kw.get("job_key", "1-2-1"),
        kw.get("vcpus", 4),
        kw.get("limit"),
        kw.get("expires_at", NOW + timedelta(hours=1)),
        kw.get("source", "webhook"),
    )


def _live_req(name: str = "runnerforge-1-2-1", **kw) -> SlotRequest:
    """For the adapter, which runs on the wall clock."""
    return _req(name, expires_at=datetime.now(UTC) + timedelta(hours=1), **kw)


def _vm(name: str, *, iid: str = "7", status: str = "RUNNING", **kw) -> VmInfo:
    return VmInfo(
        name=name,
        creation_timestamp=kw.get("created", NOW - timedelta(minutes=30)),
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id=kw.get("job_id", "2"),
            run_id="1",
            run_attempt="1",
            installation_id=iid,
        ),
        status=status,
        machine_type=kw.get("machine_type", "n2-standard-4"),
    )


def _vcpus(machine_type: str) -> int:
    return machine_vcpus(machine_type, None)


# --- pure helpers ---


@pytest.mark.parametrize(
    ("machine_type", "known", "expected"),
    [
        ("n2-standard-4", None, 4),
        ("e2-small", 2, 2),
        ("e2-small", None, UNKNOWN_VCPUS),
        ("n2-standard-4", 8, 8),
    ],
)
def test_machine_vcpus(machine_type, known, expected):
    assert machine_vcpus(machine_type, known) == expected


def test_installation_doc():
    assert installation_doc(42) == "inst-42"


def test_holder_round_trips():
    holder = _holder("vm")
    assert holder.parsed
    assert holder_from_value("vm", holder.to_value()) == holder
    # Adopted from a VM whose installation label doesn't parse.
    unknown = _holder("vm", iid=None)
    assert holder_from_value("vm", unknown.to_value()) == unknown


@pytest.mark.parametrize(
    "value",
    [
        None,
        "garbage",
        {},
        {"vcpus": 4, "at": NOW, "expires_at": NOW},
        {"installation_id": True, "vcpus": 4, "at": NOW, "expires_at": NOW},
        {"installation_id": 7, "vcpus": -1, "at": NOW, "expires_at": NOW},
        {"installation_id": 7, "vcpus": 4, "at": "x", "expires_at": NOW},
        {"installation_id": 7, "vcpus": 4, "at": NOW, "expires_at": None},
        {"installation_id": 7, "vcpus": 4, "at": NOW, "expires_at": NOW, "job_key": 1},
    ],
)
def test_unparseable_holder_counts_as_largest_vm_and_keeps_its_value(value):
    holder = holder_from_value("vm", value)
    assert (holder.installation_id, holder.vcpus, holder.at) == (None, 16, None)
    assert not holder.parsed
    assert holder.to_value() is value
    assert not holder.expired(NOW)


def test_holder_without_source_reads_as_unknown():
    value = {"installation_id": 7, "vcpus": 4, "at": NOW, "expires_at": NOW}
    holder = holder_from_value("vm", value)
    assert (holder.source, holder.job_key) == ("unknown", None)
    assert holder.expired(NOW)


def test_released_parse():
    released = Released(7, 4, NOW)
    assert released_from_value(released.to_value()) == released
    assert released_from_value({"released_at": NOW, "vcpus": -3}) == Released(
        None, 0, NOW
    )
    assert released_from_value({"vcpus": 4}) is None
    assert released_from_value("x") is None


def test_released_counts_for_the_hold_unless_gone():
    assert Released(7, 4, NOW).counts(NOW + RELEASE_HOLD - timedelta(seconds=1))
    assert not Released(7, 4, NOW).counts(NOW + RELEASE_HOLD)
    assert not Released(7, 4, NOW, gone=True).counts(NOW)


# --- decide ---


def test_decide_grants_and_records_the_holder():
    decision = decide({}, _req(), NOW)
    assert decision.outcome == "granted" and decision.changed
    [holder] = decision.holders.values()
    assert (holder.vm_name, holder.installation_id, holder.at) == (
        "runnerforge-1-2-1",
        7,
        NOW,
    )


def test_decide_reports_a_held_vm_without_change():
    holders = {"runnerforge-1-2-1": _holder("runnerforge-1-2-1")}
    decision = decide(holders, _req(), NOW, limit=1)
    assert (decision.outcome, decision.changed) == ("held", False)


def test_decide_installation_full_at_the_limit():
    holders = {"a": _holder("a"), "b": _holder("b")}
    assert decide(holders, _req(), NOW, limit=2).outcome == "installation_full"
    assert decide(holders, _req(), NOW, limit=3).outcome == "granted"


def test_decide_drops_expired_holders_first():
    holders = {"old": _holder("old", expires_at=NOW), "b": _holder("b")}
    decision = decide(holders, _req(), NOW, limit=2)
    assert decision.outcome == "granted"
    assert set(decision.holders) == {"b", "runnerforge-1-2-1"}
    full = decide({"old": _holder("old", expires_at=NOW)}, _req(), NOW, limit=0)
    assert (full.outcome, full.changed) == ("installation_full", True)


def test_decide_vm_cap_counts_released_vms_in_their_hold():
    caps = Caps(max_total_vms=2)
    holders = {"a": _holder("a")}
    held = {"b": Released(7, 4, NOW - timedelta(seconds=30))}
    assert decide(holders, _req(), NOW, caps=caps, released=held).outcome == (
        "pool_full"
    )
    gone = {"b": Released(7, 4, NOW - timedelta(seconds=30), gone=True)}
    assert decide(holders, _req(), NOW, caps=caps, released=gone).outcome == "granted"


def test_decide_vcpu_cap():
    holders = {"a": _holder("a", vcpus=28)}
    assert (
        decide(holders, _req(vcpus=4), NOW, caps=Caps(max_total_vcpus=32)).outcome
        == "granted"
    )
    assert (
        decide(holders, _req(vcpus=8), NOW, caps=Caps(max_total_vcpus=32)).outcome
        == "pool_full"
    )


def test_decide_caps_off_never_fill():
    holders = {str(n): _holder(str(n), vcpus=16) for n in range(50)}
    assert decide(holders, _req(), NOW, caps=NO_CAPS).outcome == "granted"


def test_decide_prunes_old_tombstones_and_regrants_a_released_name():
    released = {
        "runnerforge-1-2-1": Released(7, 4, NOW - timedelta(minutes=1)),
        "ancient": Released(7, 4, NOW - TOMBSTONE_TTL),
    }
    decision = decide({}, _req(), NOW, released=released)
    assert decision.outcome == "granted"
    assert decision.released == {}
    held = decide({"runnerforge-1-2-1": _holder("x")}, _req(), NOW, released=released)
    assert (held.outcome, held.changed) == ("held", True)


# --- release ---


def test_release_moves_the_holder_to_released_and_frees_the_installation():
    ledger = Ledger(
        pool={"vm": _holder("vm", vcpus=8)},
        installations={7: {"vm": _holder("vm", vcpus=8)}},
    )
    assert release(ledger, "vm", 7, NOW) == (True, True)
    assert ledger.pool == {} and ledger.installations[7] == {}
    assert ledger.released["vm"] == Released(7, 8, NOW)


def test_release_leaves_another_installations_pool_holder():
    ledger = Ledger(pool={"vm": _holder("vm", iid=8)})
    assert release(ledger, "vm", 7, NOW) == (False, False)
    assert "vm" in ledger.pool and ledger.released == {}


def test_release_of_an_unheld_name_leaves_a_tombstone_once_if_owned():
    ledger = Ledger()
    assert release(ledger, "vm", 7, NOW, owned=True) == (False, True)
    assert ledger.released["vm"] == Released(7, 0, NOW)
    again = release(ledger, "vm", 7, NOW + timedelta(minutes=1), owned=True)
    assert again == (False, False)
    assert ledger.released["vm"].released_at == NOW


def test_release_of_an_unheld_name_nobody_proved_writes_nothing():
    """Any installation can send a `completed` naming a runner that doesn't
    exist: it must not add VMs to the pool's count."""
    ledger = Ledger(installations={7: {"vm": _holder("vm")}})
    assert release(ledger, "vm", 7, NOW) == (True, False)
    assert ledger.released == {} and ledger.installations[7] == {}


def test_release_takes_an_unparseable_holder():
    ledger = Ledger(pool={"vm": holder_from_value("vm", "garbage")})
    assert release(ledger, "vm", 7, NOW) == (False, True)
    assert ledger.released["vm"].vcpus == UNKNOWN_VCPUS


# --- repair ---


def _repair(ledger, vms, deleted=frozenset(), listed_at=NOW):
    return repair(
        ledger,
        vms,
        set(deleted),
        listed_at,
        NOW,
        vcpus_of=_vcpus,
        vm_lifetime=LIFETIME,
    )


def test_repair_adopts_unheld_listed_vms():
    plan = _repair(
        Ledger(),
        [
            _vm("a"),
            _vm("b", status="TERMINATED", machine_type="e2-small", iid="x"),
            _vm("c", status="STOPPING"),
        ],
    )
    assert plan.adopted == 2 and plan.pool_changed
    a, b = plan.ledger.pool["a"], plan.ledger.pool["b"]
    assert (a.installation_id, a.job_key, a.vcpus, a.source) == (
        7,
        "1-2-1",
        4,
        "adopted",
    )
    assert a.expires_at == NOW - timedelta(minutes=30) + LIFETIME + EXPIRY_SLACK
    assert (b.installation_id, b.vcpus) == (None, UNKNOWN_VCPUS)
    assert "c" not in plan.ledger.pool
    assert plan.ledger.installations == {7: {"a": a}}
    assert plan.installations_changed == {7}


def test_repair_adopted_expiry_has_a_floor():
    created = NOW - LIFETIME - EXPIRY_SLACK
    plan = _repair(Ledger(), [_vm("a", created=created, job_id="")])
    holder = plan.ledger.pool["a"]
    assert holder.expires_at == NOW + ADOPTED_MIN_TTL
    assert holder.job_key is None


def test_repair_keeps_listed_and_young_holders_and_drops_the_rest():
    ledger = Ledger(
        pool={
            "listed": _holder("listed", at=NOW - timedelta(hours=1)),
            "young": _holder("young", at=NOW - LEASE_GRACE),
            "gone": _holder("gone", at=NOW - LEASE_GRACE - timedelta(seconds=1)),
            "poison": holder_from_value("poison", "x"),
        }
    )
    plan = _repair(ledger, [_vm("listed", status="STOPPING")])
    assert set(plan.ledger.pool) == {"listed", "young"}
    assert (plan.dropped, plan.adopted) == (2, 0)


def test_repair_releases_vms_this_run_deleted():
    ledger = Ledger(pool={"a": _holder("a", vcpus=8)})
    plan = _repair(ledger, [_vm("a"), _vm("b")], deleted={"a", "b"})
    assert plan.ledger.pool == {}
    assert plan.ledger.released == {
        "a": Released(7, 8, NOW),
        "b": Released(None, 0, NOW),
    }
    assert plan.dropped == 1 and plan.pool_changed
    # Its `completed` webhook released it before the listing, which still
    # showed it: this run deleted it again, and it still counts.
    counting = Released(7, 8, NOW - timedelta(seconds=40))
    plan = _repair(Ledger(released={"b": counting}), [_vm("b")], deleted={"b"})
    assert plan.ledger.released == {"b": counting}
    # Released by its `completed` webhook already: the tombstone stays as is.
    earlier = Released(7, 8, NOW - timedelta(seconds=5), gone=True)
    plan = _repair(Ledger(released={"b": earlier}), [], deleted={"b"})
    assert plan.ledger.released == {"b": earlier}
    assert not plan.pool_changed


def test_repair_never_adopts_a_name_released_around_the_listing():
    just_released = {"a": Released(7, 4, NOW - RELEASE_HOLD + timedelta(seconds=1))}
    plan = _repair(Ledger(released=dict(just_released)), [_vm("a")])
    assert plan.ledger.pool == {} and plan.adopted == 0
    assert not plan.pool_changed
    # Released long before the listing and still running: its delete failed.
    long_ago = {"a": Released(7, 4, NOW - RELEASE_HOLD)}
    plan = _repair(Ledger(released=dict(long_ago)), [_vm("a")])
    assert set(plan.ledger.pool) == {"a"} and plan.ledger.released == {}


def test_repair_keeps_counting_a_vm_released_after_the_listing():
    """The listing can't show a VM made, or released, after it was taken."""
    later = Released(7, 4, NOW + timedelta(seconds=30))
    plan = _repair(Ledger(released={"a": later}), [])
    assert plan.ledger.released == {"a": later}
    assert not plan.pool_changed


def test_repair_marks_unlisted_tombstones_gone_and_prunes_old_ones():
    ledger = Ledger(
        released={
            "gone": Released(7, 4, NOW),
            "known": Released(7, 4, NOW, gone=True),
            "old": Released(7, 4, NOW - TOMBSTONE_TTL),
        }
    )
    plan = _repair(ledger, [])
    assert plan.ledger.released == {
        "gone": Released(7, 4, NOW, gone=True),
        "known": Released(7, 4, NOW, gone=True),
    }
    assert plan.pool_changed
    unchanged = _repair(Ledger(released={"k": Released(7, 4, NOW, gone=True)}), [])
    assert not unchanged.pool_changed


def test_repair_replaces_an_unparseable_holder_of_a_listed_vm():
    plan = _repair(Ledger(pool={"a": holder_from_value("a", "x")}), [_vm("a")])
    assert plan.ledger.pool["a"].source == "adopted" and plan.adopted == 1


def test_repair_rebuilds_installation_documents():
    in_flight = _holder("flight", iid=7, at=NOW - timedelta(minutes=1))
    ledger = Ledger(
        pool={"a": _holder("a", iid=7)},
        installations={
            7: {
                "a": _holder("a", iid=7),
                "flight": in_flight,
                "stale": _holder("stale", iid=7, at=NOW - timedelta(hours=1)),
                "deleted": _holder("deleted", iid=7, at=NOW),
                "released": _holder("released", iid=7, at=NOW),
                "poison": holder_from_value("poison", "x"),
            },
            8: {},
            9: {"old": _holder("old", iid=9, at=NOW - timedelta(hours=1))},
        },
        released={"released": Released(7, 4, NOW)},
    )
    plan = _repair(ledger, [_vm("a")], deleted={"deleted"})
    assert plan.ledger.installations == {
        7: {"a": _holder("a", iid=7), "flight": in_flight},
    }
    assert plan.installations_changed == {7}
    # Holding nothing: deleted, so an uninstalled installation's ID goes too.
    assert plan.installations_emptied == {8, 9}


def test_repair_of_a_current_ledger_changes_nothing():
    holder = _holder("a")
    ledger = Ledger(pool={"a": holder}, installations={7: {"a": holder}})
    plan = _repair(ledger, [_vm("a")])
    assert not plan.pool_changed and not plan.installations_changed


# --- Firestore adapter, against an in-memory fake ---


class _Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return copy.deepcopy(self._data)


class _Doc:
    def __init__(self, db, doc_id):
        self._db = db
        self.id = doc_id

    async def get(self, transaction=None):
        assert transaction is not None
        await self._db.before_read()
        return _Snap(self.id, self._db.docs.get(self.id))


class _Collection:
    def __init__(self, db):
        self._db = db

    def document(self, doc_id):
        return _Doc(self._db, doc_id)

    async def stream(self, transaction=None):
        assert transaction is not None
        await self._db.before_read()
        for doc_id, data in sorted(self._db.docs.items()):
            yield _Snap(doc_id, data)


class _Transaction:
    def __init__(self, max_attempts):
        self.max_attempts = max_attempts
        self.writes = []

    def set(self, ref, data):
        self.writes.append((ref.id, data))

    def delete(self, ref):
        self.writes.append((ref.id, None))


class _Db:
    def __init__(self):
        self.docs: dict = {}
        self.transactions: list[_Transaction] = []
        self.fail: Exception | None = None
        self.delay = 0.0

    async def before_read(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail

    def collection(self, name):
        assert name == "capacity"
        return _Collection(self)

    def transaction(self, max_attempts):
        transaction = _Transaction(max_attempts)
        self.transactions.append(transaction)
        return transaction


@pytest.fixture
def db(monkeypatch) -> _Db:
    db = _Db()

    def transactional(fn):
        # Commits the writes when the function returns, like the real one.
        async def run(transaction, *args):
            result = await fn(transaction, *args)
            for doc_id, data in transaction.writes:
                if data is None:
                    db.docs.pop(doc_id, None)
                else:
                    db.docs[doc_id] = copy.deepcopy(data)
            return result

        return run

    monkeypatch.setattr(slots.firestore, "async_transactional", transactional)
    return db


def _ledger(db: _Db) -> FirestoreSlotLedger:
    return FirestoreSlotLedger(db)  # type: ignore[arg-type]


def _live(name: str, iid: int = 7) -> dict:
    now = datetime.now(UTC)
    return _holder(
        name, iid=iid, at=now, expires_at=now + timedelta(hours=1)
    ).to_value()


async def test_acquire_unlimited_takes_one_pool_transaction(db, caplog):
    with caplog.at_level(logging.INFO):
        result = await _ledger(db).acquire(_live_req(vcpus=4), NO_CAPS)
    assert result.outcome == "granted"
    assert (result.holders, result.vcpus_held, result.installation_holders) == (
        1,
        4,
        None,
    )
    assert [t.max_attempts for t in db.transactions] == [3]
    pool = db.docs["pool"]
    assert pool["version"] == LEDGER_VERSION
    assert pool["holders"]["runnerforge-1-2-1"]["installation_id"] == 7
    assert "inst-7" not in db.docs
    [record] = [r for r in caplog.records if r.message == "Slot decision"]
    assert (record.levelno, record.outcome, record.source) == (
        logging.INFO,
        "granted",
        "webhook",
    )


async def test_acquire_limited_takes_its_installation_slot_first(db):
    result = await _ledger(db).acquire(_live_req(limit=2), NO_CAPS)
    assert (result.outcome, result.installation_holders, result.holders) == (
        "granted",
        1,
        1,
    )
    assert set(db.docs["inst-7"]["holders"]) == {"runnerforge-1-2-1"}
    assert db.docs["inst-7"]["installation_id"] == 7
    # A redelivery: held at both levels, nothing rewritten.
    before = copy.deepcopy(db.docs)
    again = await _ledger(db).acquire(_live_req(limit=2), NO_CAPS)
    assert again.outcome == "held" and db.docs == before


async def test_acquire_installation_full_skips_the_pool(db):
    db.docs["inst-7"] = {"holders": {"a": _live("a"), "b": _live("b")}}
    result = await _ledger(db).acquire(_live_req(limit=2), NO_CAPS)
    assert (result.outcome, result.installation_holders) == ("installation_full", 2)
    assert "pool" not in db.docs and len(db.transactions) == 1


async def test_acquire_pool_held_after_an_installation_grant_reports_held(db):
    db.docs["pool"] = {"holders": {"runnerforge-1-2-1": _live("runnerforge-1-2-1")}}
    result = await _ledger(db).acquire(_live_req(limit=2), NO_CAPS)
    assert result.outcome == "held"


async def test_acquire_pool_full_gives_the_installation_slot_back(db):
    db.docs["pool"] = {"holders": {"a": _live("a", iid=8)}}
    result = await _ledger(db).acquire(_live_req(limit=2), Caps(max_total_vms=1))
    assert result.outcome == "pool_full"
    assert db.docs["inst-7"]["holders"] == {}
    # Unlimited: there was no installation slot to give back.
    unlimited = await _ledger(db).acquire(_live_req("other"), Caps(max_total_vms=1))
    assert unlimited.outcome == "pool_full"


async def test_acquire_pool_full_after_a_held_installation_slot_keeps_it(db):
    db.docs["inst-7"] = {"holders": {"runnerforge-1-2-1": _live("runnerforge-1-2-1")}}
    db.docs["pool"] = {"holders": {"a": _live("a", iid=8)}}
    result = await _ledger(db).acquire(_live_req(limit=2), Caps(max_total_vms=1))
    assert result.outcome == "pool_full"
    assert set(db.docs["inst-7"]["holders"]) == {"runnerforge-1-2-1"}


async def test_acquire_installation_held_pool_granted_reports_held(db):
    """The pool step of an earlier delivery didn't land: this call didn't
    take the installation's slot, so a later failure mustn't release it."""
    db.docs["inst-7"] = {"holders": {"runnerforge-1-2-1": _live("runnerforge-1-2-1")}}
    result = await _ledger(db).acquire(_live_req(limit=2), NO_CAPS)
    assert result.outcome == "held"
    assert set(db.docs["pool"]["holders"]) == {"runnerforge-1-2-1"}


@pytest.mark.parametrize(
    ("first", "second", "merged"),
    [
        ("granted", "granted", "granted"),
        ("granted", "held", "held"),
        ("held", "granted", "held"),
        ("held", "held", "held"),
    ],
)
def test_merge(first, second, merged):
    assert slots._merge(first, second) == merged


async def test_drop_installation_holder_writes_only_on_change(db):
    ledger = _ledger(db)
    transaction = db.transaction(1)
    await ledger._drop_installation_holder(transaction, _live_req())
    assert transaction.writes == []


async def test_acquire_failure_is_unavailable_and_logged_by_class(db, caplog):
    db.fail = RuntimeError("secret detail")
    with caplog.at_level(logging.INFO):
        result = await _ledger(db).acquire(_live_req(), NO_CAPS)
    assert (result.outcome, result.error_class) == ("unavailable", "RuntimeError")
    [record] = [r for r in caplog.records if r.message == "Slot decision"]
    assert record.levelno == logging.WARNING
    assert "secret detail" not in caplog.text


async def test_acquire_refuses_a_newer_ledger(db):
    db.docs["pool"] = {"version": LEDGER_VERSION + 1, "holders": {}}
    result = await _ledger(db).acquire(_live_req(), NO_CAPS)
    assert (result.outcome, result.error_class) == ("unavailable", "NewerLedger")
    assert db.docs["pool"] == {"version": LEDGER_VERSION + 1, "holders": {}}


async def test_acquire_is_bounded_and_finishes_in_the_background(db, monkeypatch):
    monkeypatch.setattr(slots, "ACQUIRE_TIMEOUT_SECONDS", 0.01)
    db.delay = 0.05
    result = await _ledger(db).acquire(_live_req(), NO_CAPS)
    assert (result.outcome, result.error_class) == ("unavailable", "TimeoutError")
    assert len(slots._background) == 1
    await asyncio.sleep(0.1)
    # It committed after all: a stray holder, which repair handles.
    assert slots._background == set()
    assert "runnerforge-1-2-1" in db.docs["pool"]["holders"]


async def test_a_bounded_call_that_fails_later_is_not_reported_twice(db, monkeypatch):
    monkeypatch.setattr(slots, "RELEASE_TIMEOUT_SECONDS", 0.01)
    seen: list[dict] = []
    asyncio.get_running_loop().set_exception_handler(lambda _, ctx: seen.append(ctx))
    db.delay = 0.05
    db.fail = RuntimeError("late")
    await _ledger(db).release("vm", 7)
    await asyncio.sleep(0.1)
    assert slots._background == set()
    gc.collect()
    # Its exception was retrieved: asyncio doesn't log it as never retrieved.
    assert seen == []


async def test_a_cancelled_caller_leaves_the_call_running_in_the_background():
    finish = asyncio.Event()

    async def slow():
        await finish.wait()
        raise RuntimeError("late")

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await slots._bounded(slow(), 10)
    [task] = slots._background
    finish.set()
    await asyncio.wait({task})
    await asyncio.sleep(0)
    assert slots._background == set()


async def test_a_cancelled_background_call_is_dropped():
    async def forever():
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await slots._bounded(forever(), 0.01)
    [task] = slots._background
    task.cancel()
    await asyncio.wait({task})
    await asyncio.sleep(0)  # the done callback runs on the next turn
    assert slots._background == set()


async def test_release_writes_both_levels(db):
    db.docs["pool"] = {"holders": {"vm": _live("vm")}}
    db.docs["inst-7"] = {"holders": {"vm": _live("vm")}}
    await _ledger(db).release("vm", 7)
    assert db.docs["pool"]["holders"] == {}
    assert db.docs["pool"]["released"]["vm"]["installation_id"] == 7
    assert db.docs["inst-7"]["holders"] == {}


async def test_release_passes_owned_through(db):
    await _ledger(db).release("vm", 7)
    assert db.transactions[0].writes == []
    await _ledger(db).release("vm", 7, owned=True)
    assert db.docs["pool"]["released"]["vm"]["vcpus"] == 0


async def test_release_of_another_installations_vm_writes_nothing(db):
    db.docs["pool"] = {"holders": {"vm": _live("vm", iid=8)}}
    await _ledger(db).release("vm", 7)
    assert db.transactions[0].writes == []


async def test_release_failure_is_logged_not_raised(db, caplog):
    db.fail = RuntimeError("down")
    await _ledger(db).release("vm", 7)
    [record] = [r for r in caplog.records if r.message == "Slot release failed"]
    assert (record.vm_name, record.error_class) == ("vm", "RuntimeError")


async def test_apply_repair_writes_the_plan(db, caplog):
    old = datetime.now(UTC) - timedelta(hours=1)
    db.docs["pool"] = {"holders": {"gone": _holder("gone", at=old).to_value()}}
    db.docs["inst-7"] = {"holders": {}}
    db.docs["inst-8"] = {"holders": {}}
    db.docs["inst-x"] = {"holders": {"stray": _live("stray")}}
    with caplog.at_level(logging.INFO):
        result = await _ledger(db).apply_repair(
            [_vm("a")],
            set(),
            datetime.now(UTC),
            vcpus_of=_vcpus,
            vm_lifetime=LIFETIME,
        )
    assert (result.holders, result.dropped, result.adopted) == (1, 1, 1)
    assert set(db.docs["pool"]["holders"]) == {"a"}
    assert set(db.docs["inst-7"]["holders"]) == {"a"}
    # Holding nothing: deleted.
    assert "inst-8" not in db.docs
    # Not an installation document: left alone.
    assert set(db.docs["inst-x"]["holders"]) == {"stray"}
    [record] = [r for r in caplog.records if r.message == "Slot repair"]
    assert (record.holders, record.adopted, record.installations_changed) == (
        1,
        1,
        2,
    )


async def test_apply_repair_of_a_current_ledger_writes_nothing(db):
    await _ledger(db).apply_repair(
        [], set(), datetime.now(UTC), vcpus_of=_vcpus, vm_lifetime=LIFETIME
    )
    assert db.transactions[0].writes == []


async def test_apply_repair_failure_is_logged(db, caplog):
    db.docs["pool"] = {"version": LEDGER_VERSION + 1}
    result = await _ledger(db).apply_repair(
        [], set(), NOW, vcpus_of=_vcpus, vm_lifetime=LIFETIME
    )
    assert result is None
    [record] = [r for r in caplog.records if r.message == "Slot repair failed"]
    assert record.error_class == "NewerLedger"


async def test_noop_ledger():
    ledger = NoopSlotLedger()
    assert not ledger.enabled
    assert (await ledger.acquire(_live_req(), NO_CAPS)).outcome == "skipped"
    assert await ledger.release("vm", 7) is None
    assert (
        await ledger.apply_repair([], set(), NOW, vcpus_of=_vcpus, vm_lifetime=LIFETIME)
        is None
    )


def test_init_slot_ledger(monkeypatch):
    monkeypatch.setattr(slots, "_ledger", NoopSlotLedger())
    client = object()
    slots.init_slot_ledger(client)  # type: ignore[arg-type]
    assert isinstance(slots.get_slot_ledger(), FirestoreSlotLedger)
    slots.init_slot_ledger(None)
    assert isinstance(slots.get_slot_ledger(), NoopSlotLedger)
