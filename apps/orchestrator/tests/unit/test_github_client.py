import logging

import httpx
import pytest
import respx
from pydantic import ValidationError

from runnerforge.github_client import (
    get_installation_token,
    get_job_status,
    get_registration_token,
)


@respx.mock
async def test_get_installation_token_returns_token(caplog):
    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_test_token",
                "expires_at": "2026-05-30T11:00:00Z",
                "permissions": {
                    "actions": "read",
                    "metadata": "read",
                    "administration": "write",
                },
                "repository_selection": "selected",
            },
        )
    )

    with caplog.at_level(logging.INFO):
        token = await get_installation_token(installation_id=999)

    assert token == "ghs_test_token"
    assert route.called
    request = route.calls.last.request
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["Authorization"].startswith("Bearer ")
    assert any(
        record.message == "Installation token created"
        and getattr(record, "expires_at", None) is not None
        for record in caplog.records
    )

    assert all(record.levelno == logging.INFO for record in caplog.records)


@respx.mock
async def test_get_installation_token_raises_on_malformed_response(caplog):
    respx.post("https://api.github.com/app/installations/999/access_tokens").mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_test_token",
                "expires_at": "2026-05-30T11:00:00Z",
                "permissions": {"metadata": "read", "administration": "write"},
                "repository_selection": "selected",
            },
        )
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as excinfo:
        await get_installation_token(installation_id=999)

    assert isinstance(excinfo.value.__cause__, ValidationError)

    # at least one logged ERROR mentions the missing field
    assert any(
        "- permissions.actions: Field required" in record.message
        for record in caplog.records
    )
    assert all(record.levelno == logging.ERROR for record in caplog.records)


@respx.mock
async def test_get_installation_token_raises_on_malformed_timestamp(caplog):
    respx.post("https://api.github.com/app/installations/999/access_tokens").mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_test_token",
                "expires_at": "not-a-real-timestamp",  # ← bad
                "permissions": {
                    "actions": "read",
                    "metadata": "read",
                    "administration": "write",
                },
                "repository_selection": "selected",
            },
        )
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as excinfo:
        await get_installation_token(installation_id=999)

    assert isinstance(excinfo.value.__cause__, ValidationError)
    assert any("expires_at" in record.message for record in caplog.records)


@respx.mock
async def test_get_installation_token_raises_on_http_error():
    respx.post("https://api.github.com/app/installations/999/access_tokens").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await get_installation_token(installation_id=999)


@respx.mock
async def test_get_installation_token_retries_on_5xx():
    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(
        side_effect=[
            httpx.Response(503, json={"message": "service unavailable"}),
            httpx.Response(503, json={"message": "service unavailable"}),
            httpx.Response(
                200,
                json={
                    "token": "ghs_test_token",
                    "expires_at": "2026-05-30T11:00:00Z",
                    "permissions": {
                        "actions": "read",
                        "metadata": "read",
                        "administration": "write",
                    },
                    "repository_selection": "selected",
                },
            ),
        ]
    )
    token = await get_installation_token(installation_id=999)
    assert token == "ghs_test_token"
    assert route.call_count == 3


@respx.mock
async def test_get_installation_token_does_not_retry_on_4xx():
    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(return_value=httpx.Response(401, json={"message": "bad"}))
    with pytest.raises(httpx.HTTPStatusError):
        await get_installation_token(installation_id=999)
    assert route.call_count == 1  # NOT retried


@respx.mock
async def test_get_installation_token_retries_on_network_error():
    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(side_effect=httpx.NetworkError(message="Network Error"))
    with pytest.raises(httpx.NetworkError):
        await get_installation_token(installation_id=999)
    assert route.call_count == 3  # NOT retried


@respx.mock
async def test_get_installation_token_retries_on_timeout():
    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(
        side_effect=httpx.TimeoutException("Timeout"),
    )
    with pytest.raises(httpx.TimeoutException):
        await get_installation_token(installation_id=999)
    assert route.call_count == 3


@respx.mock
async def test_get_installation_token_does_not_retry_on_unknown_error():
    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(side_effect=ValueError("unexpected"))

    with pytest.raises(ValueError):
        await get_installation_token(installation_id=999)
    assert route.call_count == 1  # NOT retried — unknown exception


# --- Registration Token ---


@respx.mock
async def test_get_registration_token_returns_token(caplog):
    route = respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_test_installation_token",
                "expires_at": "2026-05-30T11:00:00Z",
            },
        )
    )

    with caplog.at_level(logging.INFO):
        token = await get_registration_token(
            installation_token="ghs_test_installation_token", repo_full_name="foo/bar"
        )

    assert token == "ghs_test_installation_token"
    assert route.called
    request = route.calls.last.request
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["Authorization"].startswith("Bearer ")
    assert any(
        "Registration token created" in record.message for record in caplog.records
    )
    assert all(record.levelno == logging.INFO for record in caplog.records)


@respx.mock
async def test_get_registration_token_raises_on_malformed_response(caplog):
    respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_test_installation_token",
            },
        )
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as excinfo:
        await get_registration_token(
            installation_token="ghs_test_installation_token", repo_full_name="foo/bar"
        )

    assert isinstance(excinfo.value.__cause__, ValidationError)

    # at least one logged ERROR mentions the missing field
    assert any(
        "- expires_at: Field required" in record.message for record in caplog.records
    )
    assert all(record.levelno == logging.ERROR for record in caplog.records)


@respx.mock
async def test_get_registration_token_raises_on_malformed_timestamp(caplog):
    respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "ghs_test_installation_token",
                "expires_at": "not-a-real-timestamp",  # ← bad
            },
        )
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as excinfo:
        await get_registration_token(
            installation_token="ghs_test_installation_token", repo_full_name="foo/bar"
        )

    assert isinstance(excinfo.value.__cause__, ValidationError)
    assert any("expires_at" in record.message for record in caplog.records)


@respx.mock
async def test_get_registration_token_raises_on_http_error():
    respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(httpx.HTTPStatusError):
        await get_registration_token(
            installation_token="ghs_test_installation_token", repo_full_name="foo/bar"
        )


@respx.mock
async def test_get_registration_token_retries_on_5xx(caplog):
    route = respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(
        side_effect=[
            httpx.Response(503, json={"message": "service unavailable"}),
            httpx.Response(503, json={"message": "service unavailable"}),
            httpx.Response(
                200,
                json={
                    "token": "ghs_test_installation_token",
                    "expires_at": "2026-05-30T11:00:00Z",
                },
            ),
        ]
    )
    token = await get_registration_token(
        installation_token="ghs_test_installation_token", repo_full_name="foo/bar"
    )
    assert token == "ghs_test_installation_token"
    assert route.call_count == 3


@respx.mock
async def test_get_registration_token_does_not_retry_on_4xx():
    route = respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(return_value=httpx.Response(401, json={"message": "bad"}))
    with pytest.raises(httpx.HTTPStatusError):
        await get_registration_token(
            installation_token="ghs_test_installation_token", repo_full_name="foo/bar"
        )
    assert route.call_count == 1  # NOT retried


@respx.mock
async def test_get_registration_token_retries_on_network_error():
    route = respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(side_effect=httpx.NetworkError(message="Network Error"))
    with pytest.raises(httpx.NetworkError):
        await get_registration_token(
            installation_token="ghs_test_installation_token", repo_full_name="foo/bar"
        )
    assert route.call_count == 3  # NOT retried


@respx.mock
async def test_get_registration_token_retries_on_timeout():
    route = respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(
        side_effect=httpx.TimeoutException("Timeout"),
    )
    with pytest.raises(httpx.TimeoutException):
        await get_registration_token(
            installation_token="ghs_test_installation_token", repo_full_name="foo/bar"
        )
    assert route.call_count == 3


@respx.mock
async def test_get_registration_token_does_not_retry_on_unknown_error():
    route = respx.post(
        "https://api.github.com/repos/foo/bar/actions/runners/registration-token"
    ).mock(side_effect=ValueError("unexpected"))

    with pytest.raises(ValueError):
        await get_registration_token(
            installation_token="ghs_test_installation_token",
            repo_full_name="foo/bar",
        )

    assert route.call_count == 1  # NOT retried — unknown exception


# --- get_job_status ---
@respx.mock
async def test_get_job_status_returns_job_status(caplog):
    route = respx.get("https://api.github.com/repos/foo/bar/actions/jobs/12").mock(
        return_value=httpx.Response(
            200, json={"status": "completed", "conclusion": "success"}
        )
    )

    with caplog.at_level(logging.INFO):
        response = await get_job_status(
            installation_token="ghs_test_installation_token",
            repo_full_name="foo/bar",
            job_id="12",
        )
    assert route.called
    assert response is not None
    assert response.status == "completed"
    assert response.conclusion == "success"
    assert any("Job status retrieved" in record.message for record in caplog.records)
    assert all(record.levelno == logging.INFO for record in caplog.records)


@respx.mock
async def test_get_job_status_handles_null_conclusion_for_in_progress_jobs():
    # In-progress jobs return conclusion=null from GitHub.
    # Exercise the `conclusion is not None` branch where conclusion IS None,
    # so we don't attempt to set None as an OTel attribute.
    route = respx.get("https://api.github.com/repos/foo/bar/actions/jobs/13").mock(
        return_value=httpx.Response(
            200, json={"status": "in_progress", "conclusion": None}
        )
    )

    response = await get_job_status(
        installation_token="ghs_test_installation_token",
        repo_full_name="foo/bar",
        job_id="13",
    )
    assert route.called
    assert response is not None
    assert response.status == "in_progress"
    assert response.conclusion is None


@respx.mock
async def test_get_job_status_returns_none_on_404(caplog):
    route = respx.get("https://api.github.com/repos/foo/bar/actions/jobs/12").mock(
        return_value=httpx.Response(404, json={"message": "Job not found"})
    )

    with caplog.at_level(logging.INFO):
        response = await get_job_status(
            installation_token="ghs_test_installation_token",
            repo_full_name="foo/bar",
            job_id="12",
        )
    assert route.called
    assert response is None
    assert any("Job not found" in record.message for record in caplog.records)
    assert all(record.levelno == logging.INFO for record in caplog.records)


@respx.mock
async def test_get_job_status_propagates_5xx(caplog):
    route = respx.get("https://api.github.com/repos/foo/bar/actions/jobs/12").mock(
        return_value=httpx.Response(503, json={"message": "Service not available"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await get_job_status(
            installation_token="ghs_test_installation_token",
            repo_full_name="foo/bar",
            job_id="12",
        )
    assert route.called
