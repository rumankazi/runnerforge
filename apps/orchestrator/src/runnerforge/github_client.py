import time

import httpx
import jwt
from pydantic import ValidationError

from runnerforge.config import GITHUB_APP_ID, PRIVATE_KEY
from runnerforge.models import InstallationTokenResponse, RegistrationTokenResponse


def generate_app_jwt() -> str:
    # issued at
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

    # TODO: this should be validated once at startup, not every request
    try:
        response = InstallationTokenResponse.model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            field = ".".join(str(x) for x in err["loc"]) # e.g. "permissions.actions"

            print(f"    - {field}: {err["msg"]} (got: {err.get('input')!r})")
        raise RuntimeError(
            "Github returned an unexpected token response."
            "Likely cause: App is missing required permissions (Actions: Read)."
            "Check https://github.com/settings/apps/runnerforge-test/permissions. "
            f"Pydantic detail: {e}"

        )
    print(f"Got token, expires at Response data: {response.expires_at}")
    return response.token


async def get_registration_token(installation_token: str, repo_full_name: str) -> str:
    url=f"https://api.github.com/repos/{repo_full_name}/actions/runners/registration-token"
    headers={
        "Authorization": f"Bearer {installation_token}",
        "Accept": "application/vnd.github+json",#TODO: check why this is needed in header and what it implies
        "X-GitHub-Api-Version": "2022-11-28"
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=headers)
        r.raise_for_status()
        data = r.json()

    try:
        response = RegistrationTokenResponse.model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            field = ".".join(str(x) for x in err["loc"]) # e.g. "permissions.actions"

            print(f"    - {field}: {err["msg"]} (got: {err.get('input')!r})")
        raise RuntimeError(
            "Github returned an unexpected token response."
            "Likely cause: App is missing required permissions (Actions: Read, Administration: Write)."
            "Check https://github.com/settings/apps/runnerforge-test/permissions. "
            f"Pydantic detail: {e}"

        )
    print(f"Received data: {response}")
    return response.token
