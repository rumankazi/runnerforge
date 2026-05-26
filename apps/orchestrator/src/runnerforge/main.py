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


@app.post("/webhook", status_code=200)
async def webhook(request: Request, x_hub_signature_256: Annotated[str, Header()]):
    body = await request.body()
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature provided")

    event = WorkflowJobEvent.model_validate(json.loads(body))
    print(event.action, event.workflow_job.id, event.workflow_job.labels)
    return {"ok": True}
