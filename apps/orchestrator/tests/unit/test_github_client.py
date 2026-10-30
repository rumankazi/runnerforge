import logging

import httpx
import pytest
import respx
from pydantic import ValidationError

from runnerforge.github_client import (
    JobNotFound,
    RepoNotFound,
    get_app_installation,
    get_installation_token,
    get_job,
    get_registration_token,
    get_runner,
    list_app_installations,
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


# --- get_runner ---
_RUNNERS_URL = "https://api.github.com/repos/RumanKazi/Foo.js/actions/runners"


def _runner(name: str, busy: bool, status: str = "online") -> dict:
    return {"id": 7, "name": name, "os": "Linux", "status": status, "busy": busy}


@respx.mock
async def test_get_runner_returns_exact_name_match():
    """GitHub doesn't document `name` as an exact filter; a prefix-sharing
    runner (e.g. a later attempt) must not be mistaken for ours."""
    route = respx.get(_RUNNERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 2,
                "runners": [
                    _runner("runnerforge-1-2-10", busy=True),
                    _runner("runnerforge-1-2-1", busy=False),
                ],
            },
        )
    )

    runner = await get_runner(
        installation_token="ghs_test",
        repo_full_name="RumanKazi/Foo.js",
        runner_name="runnerforge-1-2-1",
    )

    assert runner is not None
    assert runner.name == "runnerforge-1-2-1"
    assert runner.busy is False
    request = route.calls.last.request
    assert request.url.params["name"] == "runnerforge-1-2-1"
    assert request.url.params["per_page"] == "100"
    assert request.headers["Authorization"] == "Bearer ghs_test"


@respx.mock
async def test_get_runner_returns_none_when_not_registered():
    """Ephemeral runners are de-registered after their one job."""
    respx.get(_RUNNERS_URL).mock(
        return_value=httpx.Response(200, json={"total_count": 0, "runners": []})
    )

    runner = await get_runner(
        installation_token="ghs_test",
        repo_full_name="RumanKazi/Foo.js",
        runner_name="runnerforge-1-2-1",
    )

    assert runner is None


@respx.mock
async def test_get_runner_raises_repo_not_found_on_404():
    respx.get(_RUNNERS_URL).mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(RepoNotFound):
        await get_runner(
            installation_token="ghs_test",
            repo_full_name="RumanKazi/Foo.js",
            runner_name="runnerforge-1-2-1",
        )


@respx.mock
async def test_get_runner_propagates_5xx():
    respx.get(_RUNNERS_URL).mock(
        return_value=httpx.Response(503, json={"message": "Service not available"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await get_runner(
            installation_token="ghs_test",
            repo_full_name="RumanKazi/Foo.js",
            runner_name="runnerforge-1-2-1",
        )


_JOB_URL = "https://api.github.com/repos/RumanKazi/Foo.js/actions/jobs/77"


@respx.mock
async def test_get_job_returns_status_conclusion_and_runner():
    route = respx.get(_JOB_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 77,
                "status": "completed",
                "conclusion": "success",
                "runner_name": "runnerforge-1-2-1",
                "run_id": 1,
            },
        )
    )

    job = await get_job(
        installation_token="ghs_test", repo_full_name="RumanKazi/Foo.js", job_id=77
    )

    assert (job.id, job.status, job.conclusion, job.runner_name) == (
        77,
        "completed",
        "success",
        "runnerforge-1-2-1",
    )
    assert route.calls.last.request.headers["Authorization"] == "Bearer ghs_test"


@respx.mock
async def test_get_job_raises_job_not_found_on_404():
    respx.get(_JOB_URL).mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(JobNotFound):
        await get_job(
            installation_token="ghs_test", repo_full_name="RumanKazi/Foo.js", job_id=77
        )


@respx.mock
async def test_get_job_propagates_other_errors():
    respx.get(_JOB_URL).mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await get_job(
            installation_token="ghs_test", repo_full_name="RumanKazi/Foo.js", job_id=77
        )


def _installations(start: int, count: int) -> list[dict]:
    return [
        {"id": i, "account": {"id": i, "login": f"a{i}", "type": "User"}}
        for i in range(start, start + count)
    ]


@respx.mock
async def test_list_app_installations_follows_pages():
    route = respx.get("https://api.github.com/app/installations").mock(
        side_effect=[
            httpx.Response(200, json=_installations(0, 100)),
            httpx.Response(200, json=_installations(100, 3)),
        ]
    )

    installations = await list_app_installations()

    assert [i.id for i in installations] == list(range(103))
    assert [c.request.url.params["page"] for c in route.calls] == ["1", "2"]
    assert route.calls.last.request.url.params["per_page"] == "100"
    assert route.calls.last.request.headers["Authorization"].startswith("Bearer ")


@respx.mock
async def test_list_app_installations_raises_on_error():
    respx.get("https://api.github.com/app/installations").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await list_app_installations()


@respx.mock
async def test_list_app_installations_raises_when_a_later_page_fails():
    """A partial listing must never be returned as the whole."""
    respx.get("https://api.github.com/app/installations").mock(
        side_effect=[
            httpx.Response(200, json=_installations(0, 100)),
            httpx.Response(404, json={"message": "Not Found"}),
        ]
    )

    with pytest.raises(httpx.HTTPStatusError):
        await list_app_installations()


_INSTALLATION_URL = "https://api.github.com/app/installations/7"


@respx.mock
async def test_get_app_installation_returns_it():
    respx.get(_INSTALLATION_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 7,
                "account": {"id": 70, "login": "octo", "type": "User"},
                "suspended_at": "2026-10-30T10:00:00Z",
            },
        )
    )

    installation = await get_app_installation(7)

    assert installation.id == 7
    assert installation.suspended_at is not None


@respx.mock
async def test_get_app_installation_returns_none_when_gone():
    respx.get(_INSTALLATION_URL).mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    assert await get_app_installation(7) is None


@respx.mock
async def test_get_app_installation_propagates_other_errors():
    respx.get(_INSTALLATION_URL).mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await get_app_installation(7)
