import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from opentelemetry import trace

from runnerforge import compute_client, github_client, job_store, registry
from runnerforge.admission import (
    ALLOWLISTED,
    decide_admission,
    handle_installation_event,
    log_shadow_admission,
)
from runnerforge.compute_client import cross_check_preemption, wait_for_vm_creation
from runnerforge.config import (
    ADMISSION,
    ALLOWED_INSTALLATION_IDS,
    EXPECTED_AUDIENCE,
    EXPECTED_SCHEDULER_SA_EMAIL,
    RUNNERFORGE_LABELS,
    WEBHOOK_SECRET,
)
from runnerforge.handlers import handle_completed, handle_in_progress, handle_queued
from runnerforge.job_store import base_fields, get_job_store, job_key
from runnerforge.logger import (
    get_context,
    setup_logging,
    update_context,
    with_log_context,
)
from runnerforge.models import InstallationEvent, WorkflowJobEvent
from runnerforge.reconcile import run_reconcile
from runnerforge.security import verify_github_signature, verify_oidc_token
from runnerforge.sweep import run_sweep
from runnerforge.tracing import setup_tracing
from runnerforge.validation import parse_github_response

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


def installation_allowed(installation_id: int) -> bool:
    return (
        ALLOWED_INSTALLATION_IDS is None or installation_id in ALLOWED_INSTALLATION_IDS
    )


def log_admission_mode() -> None:
    if ADMISSION == "registry":
        logger.info(
            "Admission by tenant registry",
            extra={
                "allowed_installation_count": len(ALLOWED_INSTALLATION_IDS or ()),
            },
        )
        if not registry.get_registry().enabled:
            logger.warning(
                "ADMISSION=registry without a registry: only allow-listed "
                "installations can create VMs"
            )
        return
    if ALLOWED_INSTALLATION_IDS is None:
        logger.warning(
            "ALLOWED_INSTALLATION_IDS unset: every installation can create VMs"
        )
    else:
        logger.info(
            "Installation allow-list active",
            extra={"allowed_installation_count": len(ALLOWED_INSTALLATION_IDS)},
        )


# Setup client context lifespan and FastAPI App
@asynccontextmanager
async def lifespan(app: FastAPI):
    await github_client.init_http_client()
    compute_client.init_compute_client()
    compute_client.init_runner_image()
    compute_client.init_machine_types_cache()
    compute_client.init_logging_client()
    await job_store.init_job_store()
    registry.init_registry(job_store.get_client())
    # After the registry, so registry mode can say whether it has one.
    log_admission_mode()
    logger.info("Clients initialized")
    yield
    await github_client.close_http_client()
    compute_client.close_compute_client()
    await job_store.close_job_store()


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
    x_github_event: Annotated[str, Header()],
):
    # Captured as close to the wire as possible so the resulting
    # runner_readiness_seconds metric reflects "time since we owned the
    # event" — not "time since GitHub thinks it sent the event". Wall-clock
    # (time.time) rather than monotonic because downstream code compares
    # against GitHub-stamped timestamps from the payload.
    received_at = time.time()
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

    # Dispatch on the event header, never on payload shape. GitHub Apps get
    # `installation`/`installation_repositories` whether subscribed or not, and
    # `ping` on webhook setup; parsing those as workflow_job used to 500 them.
    # `installation` updates the tenant registry; the rest are acknowledged
    # with 200 so GitHub's delivery log stays clean. Both return before the
    # "Webhook event received" anchor, so lost_webhook_ratio is unaffected.
    if x_github_event == "installation":
        # Keeps the tenant registry in step with GitHub (ADR-019). Not a job,
        # so it never reaches the lost-webhook anchor below.
        try:
            installation_body = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Failed to load request body")
        await handle_installation_event(
            parse_github_response(
                InstallationEvent,
                data=installation_body,
                cause_hint="installation payload did not match the expected schema. GitHub may have changed their payload format.",
                logger=logger,
            )
        )
        return {"ok": True}
    if x_github_event != "workflow_job":
        logger.info("Ignoring GitHub event", extra={"github_event": x_github_event})
        return {"ok": True}

    with tracer.start_as_current_span("webhook.parse_github_response") as span:
        try:
            json_body = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Failed to load request body")

        event = parse_github_response(
            WorkflowJobEvent,
            data=json_body,
            cause_hint="workflow_job payload did not match the expected WorkflowJob schema. GitHub may have changed their payload format.",
            logger=logger,
        )
        span.set_attribute("event_type", event.action)
    # Stop fast for non-runnerforge requests
    # Case-insensitive, like GitHub's own label matching: `RunnerForge` in
    # runs-on still routes to our runners, so it must not be skipped here.
    job_labels = {label.lower() for label in event.workflow_job.labels}
    if not any(
        runnerforge_label in job_labels for runnerforge_label in RUNNERFORGE_LABELS
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
    # Admission: only `queued` creates VMs, so only `queued` is gated.
    # in_progress/completed always pass — VMs of an installation removed from
    # the list mid-job must still be deleted when their jobs finish.
    # ADMISSION=allowlist turns installations away here, before the
    # lost-webhook anchor; ADMISSION=registry decides after it (below), so
    # every rejection's outcome line has a matching anchor line.
    if event.action == "queued" and ADMISSION == "allowlist":
        admitted = installation_allowed(event.installation.id)
        # ADR-019 step 1: log what the registry would decide; it decides nothing.
        await log_shadow_admission(event.installation.id, admitted)
        if not admitted:
            # installation_id/repo/sender are on the record via the log context.
            logger.warning("Installation not allowed")
            return {"ok": True}

    # Denominator of the lost_webhook_ratio alert: every RunnerForge webhook
    # the handler accepts, sliced by event_type. Logged after admission on
    # purpose, so disallowed installations don't count. For `queued`, the
    # numerator is "Queued webhook outcome" (handlers.handle_queued): an
    # accepted event that never reaches an outcome is work GitHub thinks we
    # took and silently dropped.
    logger.info("Webhook event received", extra={"event_type": event.action})

    # Job record (ADR-018). Only allowed installations get records: once the
    # App is public, anyone's in_progress/completed events would otherwise
    # grow the store. Written before the handler, so a failing GCE lookup in
    # the handler doesn't lose the fact. Bounded and fail-open.
    if ADMISSION == "registry":
        # Records follow "allow-listed, or approved at least once", so a
        # tenant suspended mid-job still gets its in_progress/completed facts.
        admission = await decide_admission(event.installation.id)
        if event.action == "queued":
            logger.info(
                "Admission decision",
                extra={
                    "admitted": admission.admitted,
                    "reason": admission.reason,
                    "limited": admission.limits is not None,
                },
            )
    else:
        admission = ALLOWLISTED
    record_job = (
        admission.record
        if ADMISSION == "registry"
        else (installation_allowed(event.installation.id))
    )
    key = job_key(
        event.workflow_job.run_id, event.workflow_job.id, event.workflow_job.run_attempt
    )
    base = base_fields(event, received_at)

    match event.action:
        case "queued":
            handle = await handle_queued(
                event, received_at=received_at, admission=admission
            )
            if handle is not None:
                # Snapshot context now; background task re-installs it before polling
                # so log correlation (request_id/job_id/repo) survives across the
                # post-response boundary.
                background_tasks.add_task(
                    with_log_context, get_context(), wait_for_vm_creation, handle
                )
        case "in_progress":
            if record_job:
                await get_job_store().record_fact(
                    key,
                    base,
                    "in_progress",
                    {"runner_name": event.workflow_job.runner_name},
                )
            await handle_in_progress(event, received_at=received_at)
        case "completed":
            if record_job:
                await get_job_store().record_fact(
                    key,
                    base,
                    "completed",
                    {
                        "conclusion": event.workflow_job.conclusion,
                        "runner_name": event.workflow_job.runner_name,
                    },
                )
            preempt_req = await handle_completed(event)
            # The cross-check queries audit logs by a runner name taken from the
            # payload, and the VM may already be gone (so ownership can't be
            # checked). Only allow-listed installations can have our VMs, so
            # only they may spend the Logging read quota (60/min, fixed).
            if preempt_req is not None and record_job:
                # The cross-check records a positive result as the job's
                # `preempted` fact.
                preempt_req = replace(preempt_req, job_key=key, job_base=base)
                # Snapshot context now; background task re-installs it so
                # request_id/job_id/repo correlation survives the post-response
                # hop into the audit-log query.
                background_tasks.add_task(
                    with_log_context,
                    get_context(),
                    cross_check_preemption,
                    preempt_req,
                )
        case _:
            logger.warning("Ignoring unknown action", extra={"action": event.action})
    return {"ok": True}


@app.post(
    "/sweep",
    status_code=200,
    summary="Orphan VM cleanup sweep",
    description="Iterates RunnerForge VMs, deletes orphans whose runner GitHub reports idle or de-registered. Auth via bearer token.",
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


@app.post(
    "/reconcile",
    status_code=200,
    summary="Job record reconciler",
    description="Brings open job records in line with GitHub and GCE (ADR-018). Auth via bearer token.",
    tags=["reconcile"],
)
async def reconcile(
    authorization: Annotated[str, Header()], background_tasks: BackgroundTasks
):
    # Same caller and audience as /sweep: the runnerforge-scheduler SA.
    if not verify_oidc_token(
        authorization,
        expected_audience=EXPECTED_AUDIENCE,
        expected_email=EXPECTED_SCHEDULER_SA_EMAIL,
    ):
        raise HTTPException(status_code=401, detail="Invalid auth")

    def watch(handle) -> None:
        # Like the webhook path: the watcher records the started VM's `vm`
        # fact, so a GCE failure on the operation shows up (ADR-019).
        background_tasks.add_task(
            with_log_context, get_context(), wait_for_vm_creation, handle
        )

    result = await run_reconcile(on_started=watch)
    logger.info(
        "Reconcile completed",
        extra={
            "checked": result.checked,
            "closed": result.closed,
            "facts": result.facts,
            "stuck": result.stuck,
            "backfilled": result.backfilled,
            "registry_changes": result.registry_changes,
            "started": result.started,
            "more_due": result.more_due,
            "error_count": len(result.errors),
        },
    )
    return result.model_dump()
