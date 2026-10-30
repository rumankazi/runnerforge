import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FIXTURES = Path(__file__).parent / "fixtures"

_KEYS_DIR = Path(tempfile.mkdtemp(prefix="runnerforge-test-keys-"))

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

(_KEYS_DIR / "private_key.pem").write_bytes(
    _private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
)
(_KEYS_DIR / "public_key.pem").write_bytes(
    _private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
)

os.environ.setdefault("GITHUB_APP_PRIVATE_KEY_PATH", str(_KEYS_DIR / "private_key.pem"))
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("GITHUB_APP_ID", "12345")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("GCP_ZONE", "europe-west4-a")
os.environ.setdefault(
    "RUNNER_VM_SA_EMAIL", "test-runner-vm@example.iam.gserviceaccount.com"
)
os.environ.setdefault(
    "EXPECTED_SCHEDULER_SA_EMAIL", "test-scheduler@example.iam.gserviceaccount.com"
)
os.environ.setdefault("EXPECTED_AUDIENCE", "https://test-orchestrator.example")

from runnerforge import (  # noqa: E402, RUF100, this is on purpose as clients can't be initiated without the required env vars
    compute_client,
    github_client,
)


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def _shared_clients():
    mp = pytest.MonkeyPatch()
    mp.setattr(
        "runnerforge.compute_client.compute_v1.InstancesClient",
        lambda: MagicMock(),
    )
    mp.setattr(
        "runnerforge.compute_client.compute_v1.ZoneOperationsClient",
        lambda: MagicMock(),
    )
    mp.setattr(
        "runnerforge.compute_client.compute_v1.ImagesClient",
        lambda: MagicMock(),
    )
    await github_client.init_http_client()
    compute_client.init_compute_client()
    yield
    await github_client.close_http_client()
    compute_client.close_compute_client()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Path to the tests/fixtures directory. Session-scoped — same value for the whole run."""
    return FIXTURES


@pytest.fixture(scope="session")
def rsa_keys() -> tuple[bytes, bytes]:
    """(private_key_pem, public_key_pem) bytes from the session keypair."""
    return (
        (_KEYS_DIR / "private_key.pem").read_bytes(),
        (_KEYS_DIR / "public_key.pem").read_bytes(),
    )


class FakeJobStore:
    """In-memory JobStore (ADR-018): records every call so tests assert on
    the facts a request wrote. `create_result` sets what create_if_absent
    reports."""

    enabled = True

    def __init__(self) -> None:
        from runnerforge.job_store import CreateResult

        self.create_result = CreateResult.CREATED
        self.created: list[tuple[str, dict]] = []
        self.facts: list[dict] = []
        # Reconciler reads: what list_due / list_open return, and patches made.
        self.due: list[tuple[str, dict]] = []
        self.open: list[tuple[str, dict]] = []
        self.patches: list[tuple[str, dict]] = []

    async def create_if_absent(self, key, base):
        self.created.append((key, base))
        return self.create_result

    async def record_fact(self, key, base, name, fact, source="webhook", reopen=True):
        self.facts.append(
            {
                "key": key,
                "base": base,
                "name": name,
                "fact": fact,
                "source": source,
                "reopen": reopen,
            }
        )

    async def list_due(self, now, limit):
        return self.due[:limit]

    async def list_open(self, limit):
        return self.open[:limit]

    async def patch(self, key, fields):
        self.patches.append((key, fields))

    def names(self) -> list[str]:
        return [f["name"] for f in self.facts]

    def fact(self, name: str) -> dict:
        [found] = [f for f in self.facts if f["name"] == name]
        return found


@pytest.fixture
def fake_job_store(monkeypatch) -> FakeJobStore:
    """Swap the process-wide job store for an in-memory fake."""
    store = FakeJobStore()
    monkeypatch.setattr("runnerforge.job_store._store", store)
    return store
