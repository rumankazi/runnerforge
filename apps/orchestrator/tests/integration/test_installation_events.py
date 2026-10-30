"""ADR-019 step 1 through /webhook: installation events and shadow admission."""

import hashlib
import hmac
import json
import logging
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from runnerforge import registry
from runnerforge.main import app
from runnerforge.registry import CachedRegistry, RegistryEntry

SECRET = b"test-secret"
client = TestClient(app)


def _headers(body: bytes, event: str) -> dict[str, str]:
    signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": signature, "X-GitHub-Event": event}


class _Registry:
    enabled = True

    def __init__(self):
        self.writes = []
        self.rows = {}

    async def get(self, installation_id):
        return self.rows.get(installation_id)

    async def record_github_status(self, installation_id, status, account=None):
        self.writes.append((installation_id, status, account))

    async def list_all(self):
        return self.rows


def _use(monkeypatch, fake) -> None:
    monkeypatch.setattr(registry, "_registry", fake)
    monkeypatch.setattr(registry, "_cached", CachedRegistry(fake))


def test_installation_created_registers_pending(monkeypatch, caplog):
    fake = _Registry()
    _use(monkeypatch, fake)
    body = json.dumps(
        {
            "action": "created",
            "installation": {
                "id": 7,
                "account": {"id": 70, "login": "octo", "type": "Organization"},
            },
        }
    ).encode()

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers=_headers(body, "installation")
        )

    assert response.status_code == 200
    [(iid, status, account)] = fake.writes
    assert (iid, status, account.login) == (7, "active", "octo")
    # Not a job: it must not touch the lost-webhook anchor.
    assert not any(r.message == "Webhook event received" for r in caplog.records)


def test_installation_event_with_bad_json_is_rejected():
    body = b"{not json"

    response = client.post(
        "/webhook", content=body, headers=_headers(body, "installation")
    )

    assert response.status_code == 400


def test_installation_event_with_unexpected_shape_fails():
    body = json.dumps({"action": "created", "installation": {"id": 7}}).encode()
    failing = TestClient(app, raise_server_exceptions=False)

    response = failing.post(
        "/webhook", content=body, headers=_headers(body, "installation")
    )

    assert response.status_code == 500


def _queued(fixtures_dir) -> bytes:
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    return json.dumps(payload).encode()


def test_queued_logs_shadow_decision_when_allowlist_rejects(
    fixtures_dir, monkeypatch, caplog
):
    """The shadow runs before the allow-list gate, so it also covers jobs the
    allow-list turns away (e.g. a tenant approved in the registry only)."""
    fake = _Registry()
    fake.rows = {135399152: RegistryEntry(135399152, "approved", "active")}
    _use(monkeypatch, fake)
    monkeypatch.setattr("runnerforge.main.ALLOWED_INSTALLATION_IDS", frozenset({1}))
    monkeypatch.setattr(
        "runnerforge.admission.ALLOWED_INSTALLATION_IDS", frozenset({1})
    )
    queued = AsyncMock()
    monkeypatch.setattr("runnerforge.main.handle_queued", queued)
    body = _queued(fixtures_dir)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/webhook", content=body, headers=_headers(body, "workflow_job")
        )

    assert response.status_code == 200
    queued.assert_not_awaited()
    [shadow] = [r for r in caplog.records if r.message == "Admission shadow decision"]
    assert (shadow.would_admit, shadow.reason, shadow.allowlist_admits) == (
        True,
        "approved",
        False,
    )
    assert shadow.agrees is False


def test_queued_logs_shadow_decision_when_allowlist_admits(
    fixtures_dir, monkeypatch, caplog
):
    fake = _Registry()
    _use(monkeypatch, fake)
    monkeypatch.setattr(
        "runnerforge.main.ALLOWED_INSTALLATION_IDS", frozenset({135399152})
    )
    monkeypatch.setattr(
        "runnerforge.admission.ALLOWED_INSTALLATION_IDS", frozenset({135399152})
    )
    queued = AsyncMock(return_value=None)
    monkeypatch.setattr("runnerforge.main.handle_queued", queued)
    body = _queued(fixtures_dir)

    with caplog.at_level(logging.INFO):
        client.post("/webhook", content=body, headers=_headers(body, "workflow_job"))

    queued.assert_awaited_once()
    [shadow] = [r for r in caplog.records if r.message == "Admission shadow decision"]
    assert (shadow.reason, shadow.agrees) == ("allowlisted", True)
