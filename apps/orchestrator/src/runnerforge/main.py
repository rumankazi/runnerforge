import hashlib
import hmac
import json
import os
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request

from runnerforge.models import WorkflowJobEvent

app = FastAPI()

WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()


@app.get("/")
async def root():
    return {"message": "Hello World"}

#TODO: migrate to webhook.py
@app.post("/webhook", status_code=200)
async def webhook(request: Request, x_hub_signature_256: Annotated[str, Header()]):
    body = await request.body()
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature provided")

    event = WorkflowJobEvent.model_validate(json.loads(body))
    match event.action:
        case "queued":
            handle_queued(event)
        case "in_progress":
            handle_in_progress(event)
        case "completed":
            handle_completed(event)
        case _:
            print(f"Ignoring unknown action: {event.action}")
    return {"ok": True}

#TODO: should migrate to handlers.py
def handle_queued(event):
    print(f"Would create VM for the job {event.workflow_job.id}, labels={event.workflow_job.labels}")

def handle_in_progress(event):
    print(f"job {event.workflow_job.id} picked up by a runner")

def handle_completed(event):
    print(f"would delete VM for job {event.workflow_job.id}, conclusion={event.workflow_job.conclusion}")
