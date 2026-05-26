from pydantic import BaseModel

class WorkflowJob(BaseModel):
    id: int
    labels: list[str]
    conclusion: str | None = None

class Repository(BaseModel):
    full_name: str

class Installation(BaseModel):
    id: int

class WorkflowJobEvent(BaseModel):
    action: str # queued | in_progress | completed
    workflow_job: WorkflowJob
    repository: Repository
    installation: Installation
