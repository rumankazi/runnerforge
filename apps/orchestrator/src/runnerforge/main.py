import hashlib
import hmac
import json
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request

from runnerforge.config import WEBHOOK_SECRET
from runnerforge.github_client import get_installation_token, get_registration_token
from runnerforge.models import WorkflowJobEvent

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World"}


# TODO: migrate to webhook.py
@app.post("/webhook", status_code=200)
async def webhook(request: Request, x_hub_signature_256: Annotated[str, Header()]):
    body = await request.body()
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature provided")

    event = WorkflowJobEvent.model_validate(json.loads(body))

    match event.action:
        case "queued":
            await handle_queued(event)
        case "in_progress":
            handle_in_progress(event)
        case "completed":
            handle_completed(event)
        case _:
            print(f"Ignoring unknown action: {event.action}")
    return {"ok": True}


# TODO: should migrate to handlers.py
async def handle_queued(event):
    print(
        f"Would create VM for the job {event.workflow_job.id}, labels={event.workflow_job.labels}"
    )

    installation_id = event.installation.id
    installation_token = await get_installation_token(installation_id)
    print("Got Installation Token!")
    registration_token = await get_registration_token(installation_token=installation_token, repo_full_name=event.repository.full_name)
    print(f"Got the registration token {registration_token}")


def handle_in_progress(event):
    print(f"job {event.workflow_job.id} picked up by a runner")


def handle_completed(event):
    print(
        f"would delete VM for job {event.workflow_job.id}, conclusion={event.workflow_job.conclusion}"
    )
