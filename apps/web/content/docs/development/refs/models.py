from typing import Literal, Annotated
from pydantic import BaseModel, Field

class Installation(BaseModel):
    id: int

class Repository(BaseModel):
    full_name: str

class QueuedJob(BaseModel):
    id: int
    labels: list[str]

class CompletedJob(BaseModel):
    id: int
    labels: list[str]
    conclusion: str          # required, not Optional

class QueuedEvent(BaseModel):
    action: Literal["queued"]            # ← the discriminator value
    workflow_job: QueuedJob
    repository: Repository
    installation: Installation

class InProgressEvent(BaseModel):
    action: Literal["in_progress"]
    workflow_job: QueuedJob
    repository: Repository
    installation: Installation

class CompletedEvent(BaseModel):
    action: Literal["completed"]
    workflow_job: CompletedJob
    repository: Repository
    installation: Installation

WorkflowJobEvent = Annotated[
    QueuedEvent | InProgressEvent | CompletedEvent,
    Field(discriminator="action"),
]
