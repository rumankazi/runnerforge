from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
from google.cloud import compute_v1
from httpx import AsyncClient, Timeout
from runnerforge import compute_client, github_client
from runnerforge.main import app


async def test_close_http_client_is_noop_when_already_none(monkeypatch):
    monkeypatch.setattr(github_client, "_http_client", None)

    # Should not raise; no client to close
    await github_client.close_http_client()

    assert github_client._http_client is None


def test_close_compute_client_is_noop_when_already_none(monkeypatch):
    monkeypatch.setattr(compute_client, "_compute_client", None)

    # Should not raise; no client to close
    compute_client.close_compute_client()

    assert compute_client._compute_client is None


async def test_init_http_client_creates_async_client_with_correct_timeout(monkeypatch):
    monkeypatch.setattr(github_client, "_http_client", None)
    await github_client.init_http_client()
    assert isinstance(github_client._http_client, AsyncClient)
    assert github_client._http_client is not None
    assert github_client._http_client.timeout == Timeout(10.0, connect=5.0)
    await github_client._http_client.aclose()


async def test_init_compute_client_creates_instances_client(monkeypatch):
    monkeypatch.setattr(compute_client, "_compute_client", None)
    compute_client.init_compute_client()
    assert isinstance(compute_client._compute_client, compute_v1.InstancesClient)
    compute_client._compute_client.transport.close()


def test_lifespan_initializes_and_closes_clients(monkeypatch):
    init_http = AsyncMock()
    close_http = AsyncMock()
    init_compute = MagicMock()
    close_compute = MagicMock()

    monkeypatch.setattr(github_client, "init_http_client", init_http)
    monkeypatch.setattr(github_client, "close_http_client", close_http)
    monkeypatch.setattr(compute_client, "init_compute_client", init_compute)
    monkeypatch.setattr(compute_client, "close_compute_client", close_compute)

    with TestClient(app):
        init_http.assert_awaited_once()
        init_compute.assert_called_once()
    # After exiting the context, shutdown has run
    close_http.assert_awaited_once()
    close_compute.assert_called_once()
