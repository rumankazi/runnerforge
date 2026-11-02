import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.api_core.exceptions import AlreadyExists, NotFound, ServiceUnavailable
from google.cloud import firestore

from runnerforge import registry
from runnerforge.registry import (
    Account,
    CachedRegistry,
    FirestoreRegistry,
    NoopRegistry,
    RegistryEntry,
    RegistryUnavailable,
)

ACCOUNT = Account(id=42, login="octo", type="Organization")


def _client_with_doc(doc: MagicMock) -> MagicMock:
    client = MagicMock()
    client.collection.return_value.document.return_value = doc
    return client


class _AsyncSnapshots:
    def __init__(self, snaps):
        self._snaps = iter(snaps)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._snaps)
        except StopIteration:
            raise StopAsyncIteration from None


def _snap(doc_id: str, data: dict | None, exists: bool = True) -> MagicMock:
    snap = MagicMock()
    snap.id = doc_id
    snap.exists = exists
    snap.to_dict.return_value = data
    return snap


# --- entries ---


@pytest.mark.parametrize(
    ("state", "github_status", "admitted"),
    [
        ("approved", "active", True),
        ("approved", "suspended", False),
        ("pending", "active", False),
        ("suspended", "active", False),
    ],
)
def test_entry_is_admitted_only_when_approved_and_active(
    state, github_status, admitted
):
    entry = RegistryEntry(1, state=state, github_status=github_status)

    assert entry.admitted is admitted


# --- no-op ---


async def test_noop_registry_is_disabled_and_unavailable():
    noop = NoopRegistry()

    assert noop.enabled is False
    with pytest.raises(RegistryUnavailable):
        await noop.get(1)
    assert await noop.record_github_status(1, "active", account=ACCOUNT) is None
    assert await noop.list_all() is None
    assert await noop.mark_records_purged(1) is None


# --- get ---


async def test_get_reads_the_row():
    approved = datetime(2026, 10, 30, tzinfo=UTC)
    doc = MagicMock()
    doc.get = AsyncMock(
        return_value=_snap(
            "7",
            {
                "state": "approved",
                "github_status": "active",
                "approved_at": approved,
                "limits": {"max_concurrent_vms": 2},
            },
        )
    )
    client = _client_with_doc(doc)

    entry = await FirestoreRegistry(client).get(7)

    client.collection.assert_called_once_with("installations")
    client.collection.return_value.document.assert_called_once_with("7")
    assert entry == RegistryEntry(
        7, "approved", "active", approved, {"max_concurrent_vms": 2}
    )


async def test_get_defaults_a_row_without_decision_to_pending():
    doc = MagicMock()
    doc.get = AsyncMock(return_value=_snap("7", None))

    entry = await FirestoreRegistry(_client_with_doc(doc)).get(7)

    assert (entry.state, entry.github_status) == ("pending", "active")


async def test_get_returns_none_for_unknown_installation():
    doc = MagicMock()
    doc.get = AsyncMock(return_value=_snap("7", None, exists=False))

    assert await FirestoreRegistry(_client_with_doc(doc)).get(7) is None


async def test_get_failure_raises_unavailable_and_logs(caplog):
    doc = MagicMock()
    doc.get = AsyncMock(side_effect=ServiceUnavailable("down"))

    with caplog.at_level(logging.WARNING), pytest.raises(RegistryUnavailable):
        await FirestoreRegistry(_client_with_doc(doc)).get(7)

    [record] = [r for r in caplog.records if r.message == "Registry read failed"]
    assert getattr(record, "operation", None) == "get"
    assert getattr(record, "error_class", None) == "ServiceUnavailable"


# --- record_github_status ---


def _status_doc(current: dict | None) -> MagicMock:
    """A doc whose get() shows `current` (None: no row yet)."""
    doc = MagicMock()
    doc.get = AsyncMock(
        return_value=_snap("7", current or {}, exists=current is not None)
    )
    doc.create = AsyncMock()
    doc.update = AsyncMock()
    doc.set = AsyncMock()
    return doc


async def test_new_installation_is_created_pending():
    doc = _status_doc(None)

    await FirestoreRegistry(_client_with_doc(doc)).record_github_status(
        7, "active", account=ACCOUNT
    )

    (data,), _ = doc.create.await_args
    assert data["installation_id"] == 7
    assert data["account_id"] == 42
    assert data["account_login"] == "octo"
    assert data["account_type"] == "Organization"
    assert data["state"] == "pending"
    assert data["github_status"] == "active"
    assert data["github_status_at"] == data["updated_at"]
    assert "expire_at" not in data


async def test_a_change_of_status_records_when_it_changed():
    doc = _status_doc({"state": "approved", "github_status": "active"})

    await FirestoreRegistry(_client_with_doc(doc)).record_github_status(
        7, "suspended", account=ACCOUNT
    )

    (fields,), _ = doc.update.await_args
    # Never touches `state`: only the owner decides that.
    assert "state" not in fields
    assert fields["github_status"] == "suspended"
    assert fields["github_status_at"] == fields["updated_at"]
    assert fields["expire_at"] is firestore.DELETE_FIELD
    assert fields["account_login"] == "octo"
    doc.create.assert_not_awaited()


async def test_the_same_status_again_keeps_the_transition_time():
    """A duplicate or late event can't move the cut-off time."""
    doc = _status_doc({"github_status": "deleted"})

    await FirestoreRegistry(_client_with_doc(doc)).record_github_status(7, "deleted")

    (fields,), _ = doc.update.await_args
    assert fields["github_status"] == "deleted"
    assert "github_status_at" not in fields
    assert "expire_at" not in fields


async def test_a_row_without_a_status_counts_as_active():
    doc = _status_doc({"state": "pending"})

    await FirestoreRegistry(_client_with_doc(doc)).record_github_status(7, "active")

    (fields,), _ = doc.update.await_args
    assert "github_status_at" not in fields


async def test_deleted_installation_gets_an_expiry_once():
    doc = _status_doc({"github_status": "active"})

    await FirestoreRegistry(_client_with_doc(doc)).record_github_status(7, "deleted")

    (fields,), _ = doc.update.await_args
    assert fields["expire_at"] - fields["updated_at"] == timedelta(days=30)


async def test_status_without_account_creates_no_row():
    doc = _status_doc(None)

    await FirestoreRegistry(_client_with_doc(doc)).record_github_status(7, "deleted")

    doc.create.assert_not_awaited()
    doc.update.assert_not_awaited()


async def test_a_row_deleted_between_read_and_write_is_left_alone(caplog):
    doc = _status_doc({"github_status": "active"})
    doc.update = AsyncMock(side_effect=NotFound("gone"))

    with caplog.at_level(logging.WARNING):
        await FirestoreRegistry(_client_with_doc(doc)).record_github_status(
            7, "deleted"
        )

    assert not [r for r in caplog.records if r.message == "Registry write failed"]


async def test_a_row_created_in_between_gets_the_status_only():
    doc = _status_doc(None)
    doc.create = AsyncMock(side_effect=AlreadyExists("exists"))

    await FirestoreRegistry(_client_with_doc(doc)).record_github_status(
        7, "suspended", account=ACCOUNT
    )

    (fields,), kwargs = doc.set.await_args
    assert kwargs == {"merge": True}
    assert "state" not in fields
    assert "github_status_at" not in fields
    assert fields["github_status"] == "suspended"
    # The expiry follows the status even here.
    assert fields["expire_at"] is firestore.DELETE_FIELD


async def test_a_row_created_in_between_as_deleted_still_expires():
    doc = _status_doc(None)
    doc.create = AsyncMock(side_effect=AlreadyExists("exists"))

    await FirestoreRegistry(_client_with_doc(doc)).record_github_status(
        7, "deleted", account=ACCOUNT
    )

    (fields,), _ = doc.set.await_args
    assert fields["expire_at"] - fields["updated_at"] == timedelta(days=30)


async def test_status_write_fails_open(caplog):
    doc = _status_doc(None)
    doc.create = AsyncMock(side_effect=ServiceUnavailable("down"))

    with caplog.at_level(logging.WARNING):
        await FirestoreRegistry(_client_with_doc(doc)).record_github_status(
            7, "active", account=ACCOUNT
        )

    [record] = [r for r in caplog.records if r.message == "Registry write failed"]
    assert getattr(record, "operation", None) == "github_status:active"
    assert getattr(record, "installation_id", None) == 7


async def test_entry_carries_the_cutoff_fields():
    at = datetime(2026, 11, 2, tzinfo=UTC)
    doc = MagicMock()
    doc.get = AsyncMock(
        return_value=_snap(
            "7",
            {
                "github_status": "deleted",
                "github_status_at": at,
                "expire_at": at + timedelta(days=30),
                "records_purged_at": at,
            },
        )
    )

    entry = await FirestoreRegistry(_client_with_doc(doc)).get(7)

    assert entry.github_status_at == at
    assert entry.expire_at == at + timedelta(days=30)
    assert entry.records_purged_at == at


# --- mark_records_purged ---


async def test_mark_records_purged_stamps_the_row():
    doc = MagicMock()
    doc.update = AsyncMock()

    await FirestoreRegistry(_client_with_doc(doc)).mark_records_purged(7)

    (fields,), _ = doc.update.await_args
    assert isinstance(fields["records_purged_at"], datetime)


async def test_mark_records_purged_fails_open(caplog):
    doc = MagicMock()
    doc.update = AsyncMock(side_effect=ServiceUnavailable("down"))

    with caplog.at_level(logging.WARNING):
        await FirestoreRegistry(_client_with_doc(doc)).mark_records_purged(7)

    [record] = [r for r in caplog.records if r.message == "Registry write failed"]
    assert getattr(record, "operation", None) == "records_purged"


# --- list_all ---


async def test_list_all_reads_every_row():
    client = MagicMock()
    client.collection.return_value.stream = MagicMock(
        return_value=_AsyncSnapshots(
            [_snap("7", {"state": "approved"}), _snap("8", None)]
        )
    )

    rows = await FirestoreRegistry(client).list_all()

    assert set(rows) == {7, 8}
    assert rows[7].state == "approved"
    assert rows[8].state == "pending"


async def test_list_all_failure_returns_none(caplog):
    client = MagicMock()
    client.collection.return_value.stream = MagicMock(
        side_effect=ServiceUnavailable("down")
    )

    with caplog.at_level(logging.WARNING):
        assert await FirestoreRegistry(client).list_all() is None

    [record] = [r for r in caplog.records if r.message == "Registry read failed"]
    assert getattr(record, "operation", None) == "list_all"


# --- cache ---


class _CountingRegistry:
    enabled = True

    def __init__(self, entry, fail=False):
        self.entry = entry
        self.fail = fail
        self.reads = 0

    async def get(self, installation_id):
        self.reads += 1
        if self.fail:
            raise RegistryUnavailable("down")
        return self.entry


async def test_cache_reuses_an_entry_within_its_ttl():
    entry = RegistryEntry(7, "approved", "active")
    inner = _CountingRegistry(entry)
    cached = CachedRegistry(inner)

    assert await cached.get(7) is entry
    assert await cached.get(7) is entry
    assert inner.reads == 1


async def test_cache_rereads_after_ttl(monkeypatch):
    inner = _CountingRegistry(None)
    cached = CachedRegistry(inner)
    await cached.get(7)
    monkeypatch.setattr(registry, "CACHE_TTL_SECONDS", -1.0)

    await cached.get(7)

    assert inner.reads == 2


async def test_cache_falls_back_to_last_known_entry(monkeypatch):
    entry = RegistryEntry(7, "approved", "active")
    inner = _CountingRegistry(entry)
    cached = CachedRegistry(inner)
    await cached.get(7)
    monkeypatch.setattr(registry, "CACHE_TTL_SECONDS", -1.0)
    inner.fail = True

    assert await cached.get(7) is entry


async def test_cache_without_entry_reraises():
    cached = CachedRegistry(_CountingRegistry(None, fail=True))

    with pytest.raises(RegistryUnavailable):
        await cached.get(7)


# --- module wiring ---


def test_init_registry_with_and_without_client(monkeypatch):
    monkeypatch.setattr(registry, "_registry", NoopRegistry())
    monkeypatch.setattr(registry, "_cached", CachedRegistry(NoopRegistry()))

    registry.init_registry(MagicMock())
    assert isinstance(registry.get_registry(), FirestoreRegistry)
    first_cache = registry.get_cached_registry()

    registry.init_registry(None)
    assert isinstance(registry.get_registry(), NoopRegistry)
    assert registry.get_cached_registry() is not first_cache


def test_job_store_exposes_its_client(monkeypatch):
    from runnerforge import job_store

    client = MagicMock()
    monkeypatch.setattr(job_store, "_client", client)

    assert job_store.get_client() is client


async def test_timeouts_are_bounded(monkeypatch):
    async def hang(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(registry, "CALL_TIMEOUT_SECONDS", 0.01)
    doc = MagicMock()
    doc.get = AsyncMock(side_effect=hang)

    with pytest.raises(RegistryUnavailable):
        await FirestoreRegistry(_client_with_doc(doc)).get(7)


# --- limits ---


def test_limits_defaults_are_the_beta_defaults():
    assert registry.BETA_DEFAULTS.to_dict() == {
        "max_concurrent_vms": 2,
        "max_size": "medium",
        "spot_only": True,
        "max_runtime_minutes": 60,
        "literal_machine_types": False,
    }
    assert registry.UNLIMITED.to_dict() == {"unlimited": True}


@pytest.mark.parametrize(
    "limits",
    [
        registry.BETA_DEFAULTS,
        registry.UNLIMITED,
        registry.Limits(30, "xlarge", False, 360, True),
    ],
)
def test_limits_round_trip(limits):
    assert registry.Limits.from_dict(limits.to_dict()) == limits


@pytest.mark.parametrize(
    "data",
    [
        None,
        "unlimited",
        {"unlimited": False},
        {"unlimited": True, "max_size": "small"},
        {"max_size": "small"},
        {**registry.BETA_DEFAULTS.to_dict(), "extra": 1},
        {**registry.BETA_DEFAULTS.to_dict(), "max_concurrent_vms": True},
        {**registry.BETA_DEFAULTS.to_dict(), "max_concurrent_vms": 2.0},
        {**registry.BETA_DEFAULTS.to_dict(), "max_concurrent_vms": 0},
        {**registry.BETA_DEFAULTS.to_dict(), "max_concurrent_vms": 31},
        {**registry.BETA_DEFAULTS.to_dict(), "max_runtime_minutes": 0},
        {**registry.BETA_DEFAULTS.to_dict(), "max_runtime_minutes": 361},
        {**registry.BETA_DEFAULTS.to_dict(), "max_size": "n2-standard-4"},
        {**registry.BETA_DEFAULTS.to_dict(), "max_size": ["medium"]},
        {**registry.BETA_DEFAULTS.to_dict(), "max_size": {"medium": 1}},
        {"unlimited": 1},
        {"unlimited": 1.0},
        {**registry.BETA_DEFAULTS.to_dict(), "spot_only": 1},
        {**registry.BETA_DEFAULTS.to_dict(), "literal_machine_types": "false"},
    ],
)
def test_limits_reject_invalid_data(data):
    with pytest.raises(ValueError):
        registry.Limits.from_dict(data)


def test_limits_reject_non_bool_unlimited():
    with pytest.raises(ValueError, match="unlimited must be true or false"):
        registry.Limits(unlimited=1)


def test_entry_carries_the_account():
    entry = registry.entry_from_doc(
        7, {"account_login": "octo", "account_type": "User"}
    )
    assert (entry.account_login, entry.account_type) == ("octo", "User")
    assert (entry.state, entry.github_status) == ("pending", "active")
