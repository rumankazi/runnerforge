"""ADR-019 step 1: installation events, shadow admission and the backfill."""

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from runnerforge import admission, registry
from runnerforge.admission import (
    handle_installation_event,
    log_shadow_admission,
    registry_backfill_due,
    sync_registry,
)
from runnerforge.models import AppInstallation, InstallationEvent
from runnerforge.registry import (
    CachedRegistry,
    NoopRegistry,
    RegistryEntry,
    RegistryUnavailable,
)


class FakeRegistry:
    enabled = True

    def __init__(self):
        self.rows: dict[int, RegistryEntry] | None = {}
        self.writes: list[tuple] = []
        self.unavailable = False

    async def get(self, installation_id):
        if self.unavailable:
            raise RegistryUnavailable("down")
        return (self.rows or {}).get(installation_id)

    async def record_github_status(self, installation_id, status, account=None):
        self.writes.append((installation_id, status, account))

    async def list_all(self):
        return self.rows


@pytest.fixture
def fake_registry(monkeypatch) -> FakeRegistry:
    fake = FakeRegistry()
    monkeypatch.setattr(registry, "_registry", fake)
    monkeypatch.setattr(registry, "_cached", CachedRegistry(fake))
    return fake


def _installation(iid: int, suspended: bool = False) -> AppInstallation:
    return AppInstallation.model_validate(
        {
            "id": iid,
            "account": {"id": iid * 10, "login": f"acct{iid}", "type": "User"},
            "suspended_at": "2026-10-30T10:00:00Z" if suspended else None,
        }
    )


# --- installation events ---


@pytest.mark.parametrize(
    ("action", "status"),
    [
        ("created", "active"),
        ("unsuspend", "active"),
        ("suspend", "suspended"),
        ("deleted", "deleted"),
    ],
)
async def test_installation_event_sets_github_status(
    fake_registry, caplog, action, status
):
    event = InstallationEvent(action=action, installation=_installation(7))

    with caplog.at_level(logging.INFO):
        await handle_installation_event(event)

    [(iid, written, account)] = fake_registry.writes
    assert (iid, written) == (7, status)
    assert (account.id, account.login, account.type) == (70, "acct7", "User")
    [log] = [r for r in caplog.records if r.message == "Installation event"]
    assert (log.action, log.installation_id) == (action, 7)
    assert not hasattr(log, "account_login")  # the login stays in the registry


async def test_other_installation_actions_are_ignored(fake_registry, caplog):
    event = InstallationEvent(
        action="new_permissions_accepted", installation=_installation(7)
    )

    with caplog.at_level(logging.INFO):
        await handle_installation_event(event)

    assert fake_registry.writes == []
    assert any(r.message == "Ignoring installation action" for r in caplog.records)


# --- shadow admission ---


def _shadow(caplog) -> logging.LogRecord:
    [record] = [r for r in caplog.records if r.message == "Admission shadow decision"]
    return record


async def test_shadow_is_silent_without_a_registry(monkeypatch, caplog):
    monkeypatch.setattr(registry, "_registry", NoopRegistry())

    with caplog.at_level(logging.INFO):
        await log_shadow_admission(7, True)

    assert not [r for r in caplog.records if r.message == "Admission shadow decision"]


async def test_shadow_admits_allowlisted_installation(
    fake_registry, monkeypatch, caplog
):
    monkeypatch.setattr(admission, "ALLOWED_INSTALLATION_IDS", frozenset({7}))

    with caplog.at_level(logging.INFO):
        await log_shadow_admission(7, True)

    record = _shadow(caplog)
    assert (record.would_admit, record.reason, record.agrees) == (
        True,
        "allowlisted",
        True,
    )


@pytest.mark.parametrize(
    ("row", "unavailable", "would_admit", "reason"),
    [
        (None, False, False, "not_registered"),
        (RegistryEntry(7, "approved", "active"), False, True, "approved"),
        (RegistryEntry(7, "pending", "active"), False, False, "not_approved"),
        (RegistryEntry(7, "approved", "suspended"), False, False, "not_approved"),
        (None, True, False, "registry_unavailable"),
    ],
)
async def test_shadow_reads_the_registry(
    fake_registry, monkeypatch, caplog, row, unavailable, would_admit, reason
):
    # Unset allow-list: in registry mode that admits nobody without a row.
    monkeypatch.setattr(admission, "ALLOWED_INSTALLATION_IDS", None)
    fake_registry.rows = {7: row} if row else {}
    fake_registry.unavailable = unavailable

    with caplog.at_level(logging.INFO):
        await log_shadow_admission(7, False)

    record = _shadow(caplog)
    assert (record.would_admit, record.reason) == (would_admit, reason)
    assert record.allowlist_admits is False
    assert record.agrees is (would_admit is False)


# --- backfill ---


@pytest.mark.parametrize(("minute", "due"), [(0, True), (4, True), (5, False)])
def test_backfill_runs_once_an_hour(minute, due):
    assert registry_backfill_due(datetime(2026, 10, 30, 12, minute, tzinfo=UTC)) is due


async def test_backfill_does_nothing_without_a_registry(monkeypatch):
    monkeypatch.setattr(registry, "_registry", NoopRegistry())
    listing = AsyncMock()
    monkeypatch.setattr(admission, "list_app_installations", listing)

    assert await sync_registry() == 0
    listing.assert_not_awaited()


async def test_backfill_skips_when_listing_fails(fake_registry, monkeypatch, caplog):
    monkeypatch.setattr(
        admission, "list_app_installations", AsyncMock(side_effect=RuntimeError("x"))
    )
    fake_registry.rows = {9: RegistryEntry(9, "approved", "active")}

    with caplog.at_level(logging.WARNING):
        assert await sync_registry() == 0

    # A failed listing must never mark anything deleted.
    assert fake_registry.writes == []
    [record] = [r for r in caplog.records if r.message == "Registry backfill skipped"]
    assert record.reason == "list_failed"
    assert record.error_class == "RuntimeError"


async def test_backfill_skips_when_rows_cannot_be_read(
    fake_registry, monkeypatch, caplog
):
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(admission, "list_app_installations", listing)
    fake_registry.rows = None

    with caplog.at_level(logging.WARNING):
        assert await sync_registry() == 0

    listing.assert_not_awaited()  # rows are read first
    assert fake_registry.writes == []
    [record] = [r for r in caplog.records if r.message == "Registry backfill skipped"]
    assert record.reason == "read_failed"


async def test_backfill_creates_and_refreshes_listed_rows(fake_registry, monkeypatch):
    monkeypatch.setattr(
        admission,
        "list_app_installations",
        AsyncMock(
            return_value=[
                _installation(1),  # missing row → created
                _installation(2, suspended=True),  # active row → suspended
                _installation(3),  # unchanged → no write
            ]
        ),
    )
    confirm = AsyncMock()
    monkeypatch.setattr(admission, "get_app_installation", confirm)
    fake_registry.rows = {
        2: RegistryEntry(2, "approved", "active"),
        3: RegistryEntry(3, "approved", "active"),
        5: RegistryEntry(5, "pending", "deleted"),  # already deleted → skipped
    }

    assert await sync_registry() == 2

    written = {(iid, status) for iid, status, _ in fake_registry.writes}
    assert written == {(1, "active"), (2, "suspended")}
    assert {iid: a.login for iid, _, a in fake_registry.writes}[1] == "acct1"
    confirm.assert_not_awaited()


@pytest.mark.parametrize(
    ("confirmed", "expected"),
    [
        (None, [(4, "deleted", None)]),  # GitHub 404: gone
        (_installation(4), []),  # still active: the listing skipped it
        (_installation(4, suspended=True), [(4, "suspended", None)]),
    ],
    ids=["gone", "still-active", "suspended"],
)
async def test_unlisted_installation_is_confirmed_before_marking(
    fake_registry, monkeypatch, confirmed, expected
):
    """Page-number listing can skip a live installation; only GitHub's 404
    may mark it deleted, because later steps delete its VMs and records."""
    monkeypatch.setattr(admission, "list_app_installations", AsyncMock(return_value=[]))
    confirm = AsyncMock(return_value=confirmed)
    monkeypatch.setattr(admission, "get_app_installation", confirm)
    fake_registry.rows = {4: RegistryEntry(4, "approved", "active")}

    await sync_registry()

    confirm.assert_awaited_once_with(4)
    assert fake_registry.writes == expected


async def test_unconfirmed_installation_is_left_alone(
    fake_registry, monkeypatch, caplog
):
    monkeypatch.setattr(admission, "list_app_installations", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        admission, "get_app_installation", AsyncMock(side_effect=RuntimeError("x"))
    )
    fake_registry.rows = {4: RegistryEntry(4, "approved", "active")}

    with caplog.at_level(logging.WARNING):
        assert await sync_registry() == 0

    assert fake_registry.writes == []
    [record] = [
        r
        for r in caplog.records
        if r.message == "Registry backfill could not confirm installation"
    ]
    assert (record.installation_id, record.error_class) == (4, "RuntimeError")
