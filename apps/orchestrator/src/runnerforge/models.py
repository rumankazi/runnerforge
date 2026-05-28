from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class WorkflowJob(BaseModel):
    id: int
    workflow_name: str
    labels: list[str]
    conclusion: str | None = None
    run_id: int
    run_attempt: int


class Login(BaseModel):
    id: int
    login: str
    type: str


class Repository(BaseModel):
    full_name: str
    owner: Login


class Installation(BaseModel):
    id: int


class WorkflowJobEvent(BaseModel):
    action: str  # queued | in_progress | completed
    workflow_job: WorkflowJob
    repository: Repository
    installation: Installation
    sender: Login


class Permissions(BaseModel):
    # required minimum permissions
    actions: Literal["read", "write"]
    metadata: Literal["read", "write"]
    administration: Literal["write"]


class InstallationTokenResponse(BaseModel):
    # Github API response for fetching installation token
    token: str  # the actual installation token
    expires_at: datetime
    permissions: Permissions
    repository_selection: Literal["selected", "all"]  # 'selected'


class RegistrationTokenResponse(BaseModel):
    # Github API response for requesting registration token
    token: str  # the actual registration token
    expires_at: datetime
