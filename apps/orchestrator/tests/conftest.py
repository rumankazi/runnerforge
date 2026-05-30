import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("GITHUB_APP_ID", "12345")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY_PATH", str(FIXTURES / "private_key.pem"))


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Path to the tests/fixtures directory. Session-scoped — same value for the whole run."""
    return FIXTURES
