import os
import tempfile
from pathlib import Path

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
async def _shared_clients():  # async def + a more descriptive name
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
