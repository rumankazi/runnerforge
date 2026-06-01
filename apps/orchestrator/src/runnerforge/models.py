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


class User(BaseModel):
    id: int
    login: str
    type: str


class Repository(BaseModel):
    full_name: str
    owner: User


class Installation(BaseModel):
    id: int


class WorkflowJobEvent(BaseModel):
    action: str  # queued | in_progress | completed
    workflow_job: WorkflowJob
    repository: Repository
    installation: Installation
    sender: User


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


class JobStatusResponse(BaseModel):
    status: Literal[
        "queued", "in_progress", "completed", "waiting", "requested", "pending"
    ]
    conclusion: str | None = None  # null until completed


class RunnerForgeVmLabels(BaseModel):
    runner: Literal["runnerforge"]
    job_id: str
    repo: str
    installation_id: str


class VmInfo(BaseModel):
    name: str
    creation_timestamp: datetime
    labels: RunnerForgeVmLabels


class SweepResult(BaseModel):
    checked: int  # total VMs inspected
    deleted: int  # VMs we deleted this run
    skipped: int  # VMs too young to sweep
    errors: list[
        str
    ]  # any VM deletion or API failures (don't fail whole sweep on one VM)
