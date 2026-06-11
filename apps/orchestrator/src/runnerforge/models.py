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
    runner_name: str | None = None
    runner_id: int | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


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
    run_id: str
    run_attempt: str
    repo: str
    installation_id: str
    queued_trace_id: str = ""
    queued_span_id: str = ""
    # Unix epoch seconds (integer, stringified) captured at /webhook entry for
    # the `queued` event. Read back on `in_progress` to compute
    # runner_readiness_seconds — the on-our-side latency that strips out
    # GitHub-side webhook delivery delay. Default empty for VMs created
    # before this field shipped (back-compat with in-flight runners during
    # the deploy window).
    queued_received_at: str = ""


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
