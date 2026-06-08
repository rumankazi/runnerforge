######
# Github Client
# For registering the GCP VM with github, we need registration token.
# More details in docs/development/phase-1/phase-1.3#why-do-we-need-three-tokens-and-not-one
# 1. Generate JWT (JSON Web Token) RS256 encoded [10 min expiry] : The App
# 2. Use JWT to request installation token https://api.github.com/app/installations/{installation_id}/access_tokens [1h expiry] : The App, acting for installation_id
# 3. Use installation token to get registration token https://api.github.com/repos/{repo_full_name}/actions/runners/registration-token [1hr, single-use] : Holder may register ONE runner on this repo

import logging
import time

import httpx
import jwt
from opentelemetry import trace
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from runnerforge.config import GITHUB_APP_ID, PRIVATE_KEY
from runnerforge.models import (
    InstallationTokenResponse,
    JobStatusResponse,
    RegistrationTokenResponse,
)
from runnerforge.validation import parse_github_response

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def _is_retriable_github_error(exc: BaseException) -> bool:
    """Retry on network errors and 5xx server errors, NOT on 4xx (bad request, bad auth)"""
    if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_retriable_github_error),
    reraise=True,
)
async def _post_with_retry(
    client: httpx.AsyncClient, url: str, headers: dict
) -> httpx.Response:
    r = await client.post(url, headers=headers)
    r.raise_for_status()
    return r


@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_retriable_github_error),
    reraise=True,
)
async def _get_with_retry(
    client: httpx.AsyncClient, url: str, headers: dict
) -> httpx.Response:
    r = await client.get(url, headers=headers)
    r.raise_for_status()
    return r


def generate_app_jwt(
    app_id: str | None = None, private_key: str | None = None, now: int | None = None
) -> str:
    app_id = app_id or GITHUB_APP_ID
    private_key = private_key or PRIVATE_KEY
    now = now if now is not None else int(time.time())
    iat = now - 60  # to absorb small clock skew
    jwt_payload = {
        "iat": iat,  # issued at
        "exp": iat + 600,  # expires at
        "iss": app_id,  # issuer
    }
    return jwt.encode(jwt_payload, private_key, algorithm="RS256")


async def get_installation_token(installation_id: int) -> str:
    with tracer.start_as_current_span("github.get_installation_token") as span:
        span.set_attribute("installation_id", installation_id)
        url = (
            f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        )
        headers = {
            "Authorization": f"Bearer {generate_app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0)
        ) as client:
            r = await _post_with_retry(client=client, url=url, headers=headers)
            data = r.json()

        response = parse_github_response(
            InstallationTokenResponse,
            data=data,
            cause_hint="Github returned an unexpected token response while fetching installation token.",
            logger=logger,
        )
        logger.info(
            "Installation token created", extra={"expires_at": response.expires_at}
        )
        return response.token


async def get_registration_token(installation_token: str, repo_full_name: str) -> str:
    with tracer.start_as_current_span("github.get_registration_token") as span:
        span.set_attribute("repo", repo_full_name)
        url = f"https://api.github.com/repos/{repo_full_name}/actions/runners/registration-token"
        headers = {
            "Authorization": f"Bearer {installation_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0)
        ) as client:
            r = await _post_with_retry(client=client, url=url, headers=headers)
            data = r.json()

        response = parse_github_response(
            RegistrationTokenResponse,
            data=data,
            cause_hint="Github returned an unexpected token response while requesting the registration token.",
            logger=logger,
        )
        logger.info(
            "Registration token created", extra={"expires_at": response.expires_at}
        )
        return response.token


async def get_job_status(
    installation_token: str,
    repo_full_name: str,
    job_id: str,
) -> JobStatusResponse | None:
    """
    Returns the job's status + conclusion, or None if the job doesn't exist (404).
    Other errors (auth, 5xx) propagate.
    """
    with tracer.start_as_current_span("github.get_job_status") as span:
        span.set_attribute("repo", repo_full_name)
        span.set_attribute("job_id", job_id)
        url = f"https://api.github.com/repos/{repo_full_name}/actions/jobs/{job_id}"

        headers = {
            "Authorization": f"Bearer {installation_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0)
        ) as client:
            try:
                r = await _get_with_retry(client=client, url=url, headers=headers)
                data = r.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.info(
                        "Job not found (treating as orphan)",
                        extra={"job_id": job_id},
                    )
                    return None
                raise
        response = parse_github_response(
            JobStatusResponse,
            data=data,
            cause_hint="Github returned an unexpected response while fetching job status.",
            logger=logger,
        )
        logger.info(
            "Job status retrieved",
            extra={
                "job_id": job_id,
                "status": response.status,
                "conclusion": response.conclusion,
            },
        )
        span.set_attribute("status", response.status)
        if response.conclusion:
            span.set_attribute("conclusion", response.conclusion)
        return response
