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

from runnerforge.utils import parse_github_response
from runnerforge.config import GITHUB_APP_ID, PRIVATE_KEY
from runnerforge.models import InstallationTokenResponse, RegistrationTokenResponse

logger = logging.getLogger(__name__)


def generate_app_jwt() -> str:
    iat = int(time.time()) - 60  # to absorb small clock skew
    jwt_payload = {
        "iat": iat,  # issued at
        "exp": iat + 600,  # expires at
        "iss": GITHUB_APP_ID,  # github app id
    }
    return jwt.encode(jwt_payload, PRIVATE_KEY, algorithm="RS256")


async def get_installation_token(installation_id: int) -> str:

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {generate_app_jwt()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url=url, headers=headers)
        r.raise_for_status()
        data = r.json()

    response = parse_github_response(
        InstallationTokenResponse,
        data=data,
        cause_hint="Github returned an unexpected token response while fetching installation token.",
        logger=logger,
    )
    logger.info("Installation token created", extra={"expires_at": response.expires_at})
    return response.token


async def get_registration_token(installation_token: str, repo_full_name: str) -> str:
    url = f"https://api.github.com/repos/{repo_full_name}/actions/runners/registration-token"
    headers = {
        "Authorization": f"Bearer {installation_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=headers)
        r.raise_for_status()
        data = r.json()

    response = parse_github_response(
        RegistrationTokenResponse,
        data=data,
        cause_hint="Github returned an unexpected token response while requesting the registration token.",
        logger=logger,
    )
    logger.info("Registration token created", extra={"expires_at": response.expires_at})
    return response.token
