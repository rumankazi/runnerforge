import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("GITHUB_APP_ID", "12345")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY_PATH", str(FIXTURES / "private_key.pem"))
os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("GCP_ZONE", "europe-west4-a")
os.environ.setdefault("SWEEP_AUTH_TOKEN", "test-sweep-auth-token")


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Path to the tests/fixtures directory. Session-scoped — same value for the whole run."""
    return FIXTURES
