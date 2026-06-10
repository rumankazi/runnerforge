import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from opentelemetry import trace

from runnerforge import compute_client, github_client
from runnerforge.compute_client import wait_for_vm_creation
from runnerforge.config import (
    EXPECTED_AUDIENCE,
    EXPECTED_SCHEDULER_SA_EMAIL,
    RUNNERFORGE_LABELS,
    WEBHOOK_SECRET,
)
from runnerforge.handlers import handle_completed, handle_in_progress, handle_queued
from runnerforge.logger import (
    get_context,
    setup_logging,
    update_context,
    with_log_context,
)
from runnerforge.models import WorkflowJobEvent
from runnerforge.security import verify_github_signature, verify_oidc_token
from runnerforge.sweep import run_sweep
from runnerforge.tracing import setup_tracing
from runnerforge.validation import parse_github_response

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


# Setup client context lifespan and FastAPI App
@asynccontextmanager
async def lifespan(app: FastAPI):
    await github_client.init_http_client()
    compute_client.init_compute_client()
    compute_client.init_runner_image()
    logger.info("Clients initialized")
    yield
    await github_client.close_http_client()
    compute_client.close_compute_client()


# Initialize the app
app = FastAPI(lifespan=lifespan)

# Setup tracing
setup_tracing(app)
tracer = trace.get_tracer(__name__)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = time.monotonic()
    request_id = str(uuid.uuid4())
    update_context(request_id=request_id)
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
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Annotated[str, Header()],
):
    body = await request.body()

    # HMAC validation
    if not verify_github_signature(
        body=body, x_hub_signature_256=x_hub_signature_256, secret=WEBHOOK_SECRET
    ):
        logger.warning(
            "Webhook signature validation failed",
            extra={"signature_prefix": x_hub_signature_256[:10]},
        )
        raise HTTPException(status_code=401, detail="Invalid signature provided")

    with tracer.start_as_current_span("webhook.parse_github_response") as span:
        try:
            json_body = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Failed to load request body")

        event = parse_github_response(
            WorkflowJobEvent,
            data=json_body,
            cause_hint="Webhook payload did not match the expected WorkflowJob schema. GitHub may have changed their payload format, or a non-workflow_job event was delivered.",
            logger=logger,
        )
        span.set_attribute("event_type", event.action)
    # Stop fast for non-runnerforge requests
    if not any(
        runnerforge_label in event.workflow_job.labels
        for runnerforge_label in RUNNERFORGE_LABELS
    ):
        logger.info("Skipping! Not a RunnerForge request")
        return {"ok": True}

    # After "first" response(/webhook hit), setup the context vars
    update_context(
        repo=event.repository.full_name,
        owner=event.repository.owner.login,
        sender=event.sender.login,
        job_id=event.workflow_job.id,
        run_id=event.workflow_job.run_id,
        run_attempt=event.workflow_job.run_attempt,
        runner_name=event.workflow_job.runner_name,
        runner_id=event.workflow_job.runner_id,
        installation_id=event.installation.id,
    )
    # Anchor for the "lost webhook" ratio alert: counts every RunnerForge
    # webhook the handler actually accepts, sliced by event_type. When the
    # ratio of vm_creation_count over queued-events drops, the orchestrator
    # is silently dropping work GitHub thinks it delivered.
    logger.info("Webhook event received", extra={"event_type": event.action})

    match event.action:
        case "queued":
            handle = await handle_queued(event)
            if handle is not None:
                # Snapshot context now; background task re-installs it before polling
                # so log correlation (request_id/job_id/repo) survives across the
                # post-response boundary.
                background_tasks.add_task(
                    with_log_context, get_context(), wait_for_vm_creation, handle
                )
        case "in_progress":
            await handle_in_progress(event)
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
