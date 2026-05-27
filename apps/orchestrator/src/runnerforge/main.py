import hashlib
import hmac
import json
import logging
import time
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request

from runnerforge.config import WEBHOOK_SECRET
from runnerforge.github_client import get_installation_token, get_registration_token
from runnerforge.logger import setup_logging
from runnerforge.models import WorkflowJobEvent

setup_logging()
logger = logging.getLogger(__name__)
app = FastAPI()


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - start
    logger.info(
        f"{request.method} {request.url.path}",
        extra={
            "httpRequest": {
                "requestMethod": request.method,
                "requestUrl": str(request.url),
                "status": response.status_code,
                "latency": f"{duration:.6f}s",
                "remoteIp": request.client.host if request.client else "",
                "userAgent": request.headers.get("user-agent", ""),
                "protocol": f"HTTP/{request.scope.get('http_version', '1.1')}",
            }
        },
    )
    return response


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
            logger.warning("Ignoring unknown action", extra={"action": event.action})
    return {"ok": True}


# TODO: should migrate to handlers.py
async def handle_queued(event):
    logger.info(
        "Creating VM",
        extra={"job_id": event.workflow_job.id, "labels": event.workflow_job.labels},
    )
    installation_id = event.installation.id
    installation_token = await get_installation_token(installation_id)
    logger.info("Installation token created", extra={"installation_id": installation_id})
    await get_registration_token(
        installation_token=installation_token,
        repo_full_name=event.repository.full_name,
    )
    logger.info(
        "Registration token received",
        extra={"job_id": event.workflow_job.id, "repo": event.repository.full_name},
    )


def handle_in_progress(event):
    logger.info("Job picked up by a runner", extra={"job_id": event.workflow_job.id})


def handle_completed(event):
    logger.info(
        "Would delete VM",
        extra={
            "job_id": event.workflow_job.id,
            "conclusion": event.workflow_job.conclusion,
        },
    )
