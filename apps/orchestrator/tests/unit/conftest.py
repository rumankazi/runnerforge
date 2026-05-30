import pytest
from runnerforge.github_client import _post_with_retry
from tenacity import wait_none


@pytest.fixture(autouse=True)
def disable_retry_wait():
    """Tests should retyr instantly. Production code keeps its real wait strategy"""
    original = _post_with_retry.retry.wait  # type: ignore
    _post_with_retry.retry.wait = wait_none()  # type: ignore
    yield
    _post_with_retry.retry.wait = original  # type: ignore
