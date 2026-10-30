import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.api_core.exceptions import AlreadyExists, ServiceUnavailable
from google.auth.exceptions import DefaultCredentialsError

from runnerforge import job_store
from runnerforge.config import parse_job_store
from runnerforge.job_store import (
    CreateResult,
    FirestoreJobStore,
    NoopJobStore,
    base_fields,
    job_key,
)
from runnerforge.models import WorkflowJobEvent

KEY = "111-222-1"
BASE = {"installation_id": 7, "schema_version": 1}


def _event(created_at: datetime | None) -> WorkflowJobEvent:
    return WorkflowJobEvent.model_validate(
        {
            "action": "queued",
            "workflow_job": {
                "id": 222,
                "workflow_name": "ci",
                "labels": ["runnerforge", "small"],
                "run_id": 111,
                "run_attempt": 1,
                "created_at": created_at,
            },
            "repository": {
                "id": 9,
                "full_name": "octo/repo",
                "owner": {"id": 1, "login": "octo", "type": "User"},
            },
            "installation": {"id": 7},
            "sender": {"id": 1, "login": "octo", "type": "User"},
        }
    )


def _client_with_doc(doc: MagicMock) -> MagicMock:
    # collection() and document() are sync; only the document methods await.
    client = MagicMock()
    client.collection.return_value.document.return_value = doc
    return client


def _failure_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.message == "Job store write failed"]


# --- config ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [("none", "none"), ("firestore", "firestore"), (" Firestore ", "firestore")],
)
def test_parse_job_store_accepts_known_values(value, expected):
    assert parse_job_store(value) == expected


def test_parse_job_store_rejects_unknown_value():
    with pytest.raises(ValueError, match="JOB_STORE must be"):
        parse_job_store("firestor")


# --- key and base fields ---


def test_job_key_matches_vm_name_suffix():
    assert job_key(run_id=111, job_id=222, run_attempt=1) == "111-222-1"


def test_base_fields_use_github_created_at():
    created = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)

    base = base_fields(_event(created), received_at=0.0)

    assert base == {
        "installation_id": 7,
        "repository_id": 9,
        "repo": "octo/repo",
        "run_id": 111,
        "job_id": 222,
        "run_attempt": 1,
        "labels": ["runnerforge", "small"],
        "queued_at": created,
        "expire_at": created + timedelta(days=90),
        "next_check_at": created + timedelta(minutes=5),
        "schema_version": 1,
    }


def test_base_fields_fall_back_to_received_at_without_created_at():
    received = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)

    base = base_fields(_event(None), received_at=received.timestamp())

    assert base["queued_at"] == received
    assert base["queued_at"].tzinfo is not None
    assert base["expire_at"] == received + timedelta(days=90)


# --- no-op store ---


async def test_noop_store_reports_unknown_and_writes_nothing():
    store = NoopJobStore()

    assert await store.create_if_absent(KEY, BASE) is CreateResult.UNKNOWN
    assert await store.record_fact(KEY, BASE, "decision", {"kind": "x"}) is None


# --- Firestore store: create_if_absent ---


async def test_create_if_absent_creates_open_record():
    doc = MagicMock()
    doc.create = AsyncMock()
    client = _client_with_doc(doc)

    result = await FirestoreJobStore(client).create_if_absent(KEY, BASE)

    assert result is CreateResult.CREATED
    client.collection.assert_called_once_with("jobs")
    client.collection.return_value.document.assert_called_once_with(KEY)
    doc.create.assert_awaited_once_with({**BASE, "open": True}, retry=job_store._RETRY)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({"open": True}, CreateResult.EXISTS_OPEN),
        ({"open": False}, CreateResult.EXISTS_CLOSED),
        ({}, CreateResult.EXISTS_OPEN),
        (None, CreateResult.EXISTS_OPEN),
    ],
)
async def test_create_if_absent_reads_open_when_record_exists(stored, expected):
    snapshot = MagicMock()
    snapshot.to_dict.return_value = stored
    doc = MagicMock()
    doc.create = AsyncMock(side_effect=AlreadyExists("exists"))
    doc.get = AsyncMock(return_value=snapshot)

    result = await FirestoreJobStore(_client_with_doc(doc)).create_if_absent(KEY, BASE)

    assert result is expected
    doc.get.assert_awaited_once_with(field_paths=["open"], retry=job_store._RETRY)


async def test_create_if_absent_fails_open_on_error_without_reading(caplog):
    doc = MagicMock()
    doc.create = AsyncMock(side_effect=ServiceUnavailable("down"))
    doc.get = AsyncMock()

    with caplog.at_level(logging.WARNING):
        result = await FirestoreJobStore(_client_with_doc(doc)).create_if_absent(
            KEY, BASE
        )

    assert result is CreateResult.UNKNOWN
    doc.get.assert_not_awaited()
    [record] = _failure_records(caplog)
    assert record.levelno == logging.WARNING
    assert getattr(record, "operation", None) == "create"
    assert getattr(record, "job_key", None) == KEY
    assert getattr(record, "error_class", None) == "ServiceUnavailable"


async def test_create_if_absent_fails_open_on_timeout(monkeypatch, caplog):
    async def hang(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(job_store, "CALL_TIMEOUT_SECONDS", 0.01)
    doc = MagicMock()
    doc.create = AsyncMock(side_effect=hang)

    with caplog.at_level(logging.WARNING):
        result = await FirestoreJobStore(_client_with_doc(doc)).create_if_absent(
            KEY, BASE
        )

    assert result is CreateResult.UNKNOWN
    [record] = _failure_records(caplog)
    assert getattr(record, "error_class", None) == "TimeoutError"


async def test_create_if_absent_fails_open_when_read_fails(caplog):
    doc = MagicMock()
    doc.create = AsyncMock(side_effect=AlreadyExists("exists"))
    doc.get = AsyncMock(side_effect=ServiceUnavailable("down"))

    with caplog.at_level(logging.WARNING):
        result = await FirestoreJobStore(_client_with_doc(doc)).create_if_absent(
            KEY, BASE
        )

    assert result is CreateResult.UNKNOWN
    [record] = _failure_records(caplog)
    assert getattr(record, "operation", None) == "get"


# --- Firestore store: record_fact ---


async def test_record_fact_merges_fact_and_base_fields():
    doc = MagicMock()
    doc.set = AsyncMock()

    await FirestoreJobStore(_client_with_doc(doc)).record_fact(
        KEY, BASE, "decision", {"kind": "vm_submitted"}
    )

    doc.set.assert_awaited_once()
    (data,), kwargs = doc.set.await_args
    assert kwargs == {"merge": True, "retry": job_store._RETRY}
    assert data["installation_id"] == 7
    assert data["schema_version"] == 1
    assert data["open"] is True
    assert data["decision"]["kind"] == "vm_submitted"
    assert data["decision"]["source"] == "webhook"
    assert data["decision"]["at"].tzinfo is not None


async def test_record_fact_completed_closes_record_with_given_source():
    doc = MagicMock()
    doc.set = AsyncMock()

    await FirestoreJobStore(_client_with_doc(doc)).record_fact(
        KEY, BASE, "completed", {"conclusion": "success"}, source="reconcile"
    )

    (data,), _ = doc.set.await_args
    assert data["open"] is False
    assert data["completed"]["conclusion"] == "success"
    assert data["completed"]["source"] == "reconcile"


async def test_record_fact_fails_open(caplog):
    doc = MagicMock()
    doc.set = AsyncMock(side_effect=RuntimeError("bug"))

    with caplog.at_level(logging.WARNING):
        result = await FirestoreJobStore(_client_with_doc(doc)).record_fact(
            KEY, BASE, "in_progress", {"runner_name": "runnerforge-111-222-1"}
        )

    assert result is None
    [record] = _failure_records(caplog)
    assert getattr(record, "operation", None) == "record_fact:in_progress"
    assert getattr(record, "error_class", None) == "RuntimeError"


# --- warm-up ---


async def test_warm_up_reads_one_document():
    doc = MagicMock()
    doc.get = AsyncMock()
    client = _client_with_doc(doc)

    await FirestoreJobStore(client).warm_up()

    client.collection.return_value.document.assert_called_once_with("_warmup")
    doc.get.assert_awaited_once_with(retry=job_store._RETRY)


async def test_warm_up_failure_is_logged_not_raised(caplog):
    doc = MagicMock()
    doc.get = AsyncMock(side_effect=ServiceUnavailable("down"))

    with caplog.at_level(logging.WARNING):
        await FirestoreJobStore(_client_with_doc(doc)).warm_up()

    [record] = [r for r in caplog.records if r.message == "Job store warm-up failed"]
    assert getattr(record, "error_class", None) == "ServiceUnavailable"


# --- init / close / get ---


@pytest.fixture
def reset_store(monkeypatch):
    monkeypatch.setattr(job_store, "_client", None)
    monkeypatch.setattr(job_store, "_store", NoopJobStore())
    monkeypatch.setattr(job_store, "_warmup_task", None)


async def test_init_job_store_disabled_keeps_noop(monkeypatch, reset_store, caplog):
    constructor = MagicMock()
    monkeypatch.setattr(job_store, "JOB_STORE", "none")
    monkeypatch.setattr("runnerforge.job_store.firestore.AsyncClient", constructor)

    with caplog.at_level(logging.INFO):
        await job_store.init_job_store()

    constructor.assert_not_called()
    assert isinstance(job_store.get_job_store(), NoopJobStore)
    assert any(
        r.message == "Job store disabled (JOB_STORE=none)" for r in caplog.records
    )


async def test_init_job_store_builds_client_and_warms_up(
    monkeypatch, reset_store, caplog
):
    doc = MagicMock()
    doc.get = AsyncMock()
    client = _client_with_doc(doc)
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(job_store, "JOB_STORE", "firestore")
    monkeypatch.setattr(job_store, "FIRESTORE_DATABASE", "test-db")
    monkeypatch.setattr("runnerforge.job_store.firestore.AsyncClient", constructor)

    with caplog.at_level(logging.INFO):
        await job_store.init_job_store()
    # The warm-up runs in the background so it never delays startup.
    await job_store._warmup_task

    constructor.assert_called_once_with(
        project=job_store.GCP_PROJECT_ID, database="test-db"
    )
    doc.get.assert_awaited_once()
    assert isinstance(job_store.get_job_store(), FirestoreJobStore)
    assert job_store._client is client
    [ready] = [r for r in caplog.records if r.message == "Job store ready"]
    assert getattr(ready, "database", None) == "test-db"


async def test_init_job_store_without_credentials_keeps_noop(
    monkeypatch, reset_store, caplog
):
    monkeypatch.setattr(job_store, "JOB_STORE", "firestore")
    monkeypatch.setattr(
        "runnerforge.job_store.firestore.AsyncClient",
        MagicMock(side_effect=DefaultCredentialsError("no creds")),
    )

    with caplog.at_level(logging.WARNING):
        await job_store.init_job_store()

    assert isinstance(job_store.get_job_store(), NoopJobStore)
    assert job_store._client is None
    assert any("job store init skipped" in r.message for r in caplog.records)


async def test_close_job_store_closes_open_channel(monkeypatch, reset_store):
    client = MagicMock()
    client._firestore_api_internal.transport.close = AsyncMock()
    monkeypatch.setattr(job_store, "_client", client)
    monkeypatch.setattr(job_store, "_store", FirestoreJobStore(client))

    await job_store.close_job_store()

    client._firestore_api_internal.transport.close.assert_awaited_once()
    assert job_store._client is None
    assert isinstance(job_store.get_job_store(), NoopJobStore)


async def test_close_job_store_without_channel_is_noop(monkeypatch, reset_store):
    client = MagicMock()
    client._firestore_api_internal = None
    monkeypatch.setattr(job_store, "_client", client)

    await job_store.close_job_store()

    assert job_store._client is None


async def test_close_job_store_without_client_is_noop(reset_store):
    await job_store.close_job_store()

    assert job_store._client is None
    assert isinstance(job_store.get_job_store(), NoopJobStore)


async def test_close_job_store_cancels_pending_warm_up(monkeypatch, reset_store):
    task = asyncio.create_task(asyncio.sleep(10))
    monkeypatch.setattr(job_store, "_warmup_task", task)

    await job_store.close_job_store()

    assert task.cancelled()
    assert job_store._warmup_task is None


async def test_close_job_store_after_finished_warm_up(monkeypatch, reset_store):
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    monkeypatch.setattr(job_store, "_warmup_task", task)

    await job_store.close_job_store()

    assert not task.cancelled()
    assert job_store._warmup_task is None
