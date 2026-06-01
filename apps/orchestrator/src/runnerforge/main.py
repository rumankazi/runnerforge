import json
import logging
import time
import uuid
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request

from runnerforge.config import (
    EXPECTED_AUDIENCE,
    EXPECTED_SCHEDULER_SA_EMAIL,
    WEBHOOK_SECRET,
)
from runnerforge.handlers import handle_completed, handle_in_progress, handle_queued
from runnerforge.logger import (
    setup_logging,
    update_context,
)
from runnerforge.models import WorkflowJobEvent
from runnerforge.security import verify_github_signature, verify_oidc_token
from runnerforge.sweep import run_sweep
from runnerforge.validation import parse_github_response

setup_logging()
logger = logging.getLogger(__name__)
app = FastAPI()


def parse_trace_id(header: str | None) -> str | None:
    """Extracts TRACE_ID from 'TRACE_ID/SPAN_ID;o=1' format."""
    if not header:
        return None
    trace_id = header.split("/", 1)[0]
    return trace_id or None


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = time.monotonic()
    trace_id = parse_trace_id(request.headers.get("X-Cloud-Trace-Context"))
    request_id = trace_id or str(uuid.uuid4())
    update_context(request_id=request_id, trace_id=trace_id)
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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post(
    "/webhook",
    status_code=200,
    summary="Receive GitHub workflow_job webhook",
    description="Validates HMAC signature, parses the event, dispatches to handlers.",
    tags=["webhooks"],
)
async def webhook(request: Request, x_hub_signature_256: Annotated[str, Header()]):
    body = await request.body()

    # HMAC validation
    if not verify_github_signature(
        body=body, header_signature=x_hub_signature_256, secret=WEBHOOK_SECRET
    ):
        logger.warning(
            "Webhook signature validation failed",
            extra={"signature_prefix": x_hub_signature_256[:10]},
        )
        raise HTTPException(status_code=401, detail="Invalid signature provided")

    event = parse_github_response(
        WorkflowJobEvent,
        data=json.loads(body),
        cause_hint="Webhook payload did not match the expected WorkflowJob schema. GitHub may have changed their payload format, or a non-workflow_job event was delivered.",
        logger=logger,
    )
    # After "first" response(/webhook hit), setup the context vars
    update_context(
        repo=event.repository.full_name,
        owner=event.repository.owner.login,
        sender=event.sender.login,
        run_id=event.workflow_job.run_id,
        installation_id=event.installation.id,
    )

    match event.action:
        case "queued":
            await handle_queued(event)
        case "in_progress":
            handle_in_progress(event)
        case "completed":
            await handle_completed(event)
        case _:
            logger.warning("Ignoring unknown action", extra={"action": event.action})
    return {"ok": True}


@app.post(
    "/sweep",
    status_code=200,
    summary="Orphan VM cleanup sweep",
    description="Iterates RunnerForge VMs, deletes orphans by checking job status against GitHub. Auth via bearer token.",
    tags=["sweep"],
)
async def sweep(authorization: Annotated[str, Header()]):
    if not verify_oidc_token(
        authorization,
        expected_audience=EXPECTED_AUDIENCE,
        expected_email=EXPECTED_SCHEDULER_SA_EMAIL,
    ):
        raise HTTPException(status_code=401, detail="Invalid auth")

    result = await run_sweep()
    logger.info(
        "Sweep completed",
        extra={
            "checked": result.checked,
            "deleted": result.deleted,
            "skipped": result.skipped,
            "error_count": len(result.errors),
        },
    )
    return result.model_dump()
