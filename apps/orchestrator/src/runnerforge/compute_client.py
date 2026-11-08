import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from google.api_core.exceptions import (
    AlreadyExists,
    GoogleAPIError,
    NotFound,
    PreconditionFailed,
)
from google.auth.exceptions import DefaultCredentialsError, GoogleAuthError
from google.cloud import compute_v1
from google.cloud import logging as gcp_logging
from opentelemetry import trace
from pydantic import ValidationError
from requests.exceptions import RequestException

from runnerforge.config import (
    GCP_PROJECT_ID,
    GCP_ZONE,
    RUNNER_IMAGE_PROJECT,
    RUNNER_VM_PROJECT_ID,
    RUNNER_VM_SA_EMAIL,
    RUNNER_VM_SUBNETWORK,
)
from runnerforge.job_store import get_job_store
from runnerforge.models import RunnerForgeVmLabels, VmInfo

# Machine type name -> vCPUs in GCP_ZONE; None when the cache is degraded.
_KNOWN_MACHINE_TYPES: dict[str, int] | None = None
_logging_client: gcp_logging.Client | None = None
_RUNNER_IMAGE_NAME: str | None = None  # full GCE name, used as source_image pin
_RUNNER_IMAGE_VERSION: str | None = None  # dotted semver, used in logs/metrics
_RUNNER_IMAGE_FAMILY = "runnerforge-runner"
_BOOT_IMAGE_FAMILY = (
    f"projects/{RUNNER_IMAGE_PROJECT}/global/images/family/{_RUNNER_IMAGE_FAMILY}"
)
_BOOT_DISK_SIZE_GB = 10
_DATA_DISK_SIZE_GB = 50
# handle_queued writes repo-url with this prefix; the sweep strips it to get
# the exact owner/repo for the GitHub jobs API.
GITHUB_REPO_URL_PREFIX = "https://github.com/"
# GCE deletes the VM (and its auto-delete disks) after this much RUNNING time,
# so a stuck or abandoned runner can't bill indefinitely between daily sweeps.
# 6h matches GitHub's default job timeout (360 min); the 15 min covers boot +
# registration so a job using its full default timeout isn't cut short. The
# sweep's hard limit uses the same value. Becomes a per-tenant limit later.
MAX_VM_RUN_DURATION = timedelta(hours=6, minutes=15)
# GCE instance naming rule (RFC 1035 label); no regex metacharacters.
_GCE_INSTANCE_NAME_RE = re.compile(r"[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?")
SERIAL_LOGGING_KEY = "serial-port-logging-enable"
# Set on every VM, after the caller's metadata so it can't override them
# (ADR-020). Serial logging is on for boot diagnostics and goes off when the
# job starts (turn_off_serial_logging).
SECURITY_METADATA = {
    "enable-oslogin": "TRUE",
    "block-project-ssh-keys": "TRUE",
    SERIAL_LOGGING_KEY: "TRUE",
}
# The whole serial-off, retry included: it runs inside the in_progress
# webhook, within GitHub's 10 s.
SERIAL_OFF_TIMEOUT_SECONDS = 4.0
SerialOffOutcome = Literal[
    "submitted",
    "already_off",
    "vm_gone",
    "not_running",
    "not_owned",
    "conflict",
    "failed",
]

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass(frozen=True, slots=True)
class OperationHandle:
    """What `create_vm` returns so polling code can query the GCE operation later."""

    name: str
    project: str
    zone: str
    spot: bool
    machine_type: str
    image_version: str | None
    # Job record (ADR-018), attached by handle_queued; the watcher writes the
    # `vm` fact with these. The full base fields, not just the key, so a write
    # that creates the record still gets its TTL.
    job_key: str | None = None
    job_base: dict[str, Any] | None = None
    # The instance it creates; the `vm` fact names it, so a late watcher of
    # an earlier start can't be read as the latest start's (ADR-019).
    vm_name: str | None = None


@dataclass(frozen=True, slots=True)
class PreemptionCheckRequest:
    """What `handle_completed` returns when a failed/cancelled job should be
    cross-checked for spot preemption. Consumed by `cross_check_preemption`
    inside a BackgroundTask. Symmetric with `OperationHandle` for the queued
    path — handler returns data, main.py owns the BackgroundTask scheduling."""

    vm_name: str
    job_id: int
    conclusion: str
    # Job record (ADR-018), attached by main.py for allowed installations.
    job_key: str | None = None
    job_base: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class OperationOutcome:
    outcome: Literal["success", "failure", "timeout"]
    op_name: str
    zone: str
    duration_ms: int
    error_code: str | None = None
    error_message: str | None = None


_compute_client: compute_v1.InstancesClient | None = None
_zone_ops_client: compute_v1.ZoneOperationsClient | None = None
_images_client: compute_v1.ImagesClient | None = None


# Not async since these are not async methods (sync operations)
def init_compute_client():
    global _compute_client, _zone_ops_client, _images_client
    try:
        _compute_client = compute_v1.InstancesClient()
        _zone_ops_client = compute_v1.ZoneOperationsClient()
        _images_client = compute_v1.ImagesClient()
    except DefaultCredentialsError:
        logger.warning(
            "compute client init skipped - no GCP credentials available; "
            "VM operations will fail until creds are present"
        )


def close_compute_client():
    global _compute_client, _zone_ops_client, _images_client, _logging_client
    if _compute_client is not None:
        _compute_client.transport.close()
        _compute_client = None

    if _zone_ops_client is not None:
        _zone_ops_client.transport.close()
        _zone_ops_client = None

    if _images_client is not None:
        _images_client.transport.close()
        _images_client = None

    if _logging_client is not None:
        _logging_client.close()
        _logging_client = None


def init_logging_client() -> None:
    """Initialize the Cloud Logging client used for audit-log queries (e.g. the
    preemption cross-check). Tolerant of startup failures — leaves the client
    at None and degrades to 'no preemption signal' if creds aren't available."""
    global _logging_client
    try:
        _logging_client = gcp_logging.Client(project=GCP_PROJECT_ID)
    except DefaultCredentialsError:
        logger.warning(
            "logging client init skipped - no GCP credentials available; "
            "preemption cross-check will degrade to no-signal"
        )


# Helper to convert runnerforge-runner-image-0-1-0 to 0.1.0
def _parse_image_version(name: str) -> str:
    return name.removeprefix("runnerforge-runner-image-").replace("-", ".")


def init_runner_image():
    global _RUNNER_IMAGE_NAME, _RUNNER_IMAGE_VERSION
    if _images_client is None:
        logger.warning(
            "Skipping runner image resolve — images client not initialized; "
            "VMs will boot from family alias"
        )
        return
    try:
        image = _images_client.get_from_family(
            project=RUNNER_IMAGE_PROJECT, family=_RUNNER_IMAGE_FAMILY
        )
    except (DefaultCredentialsError, GoogleAPIError) as e:
        logger.warning(
            "Runner image resolve failed — VMs will boot from family alias",
            extra={"reason": str(e)},
        )
        return
    _RUNNER_IMAGE_NAME = image.name
    _RUNNER_IMAGE_VERSION = _parse_image_version(image.name)
    logger.info(
        "Resolved runner image",
        extra={
            "image_name": _RUNNER_IMAGE_NAME,
            "image_version": _RUNNER_IMAGE_VERSION,
        },
    )


def init_machine_types_cache(zone: str = GCP_ZONE) -> None:
    """Populate the in-process cache of valid GCE machine type in the given zone. Tolerant of startup failures - leaves cache at None to fall back to 'trust the parser' if the API call doesn't succeed"""
    global _KNOWN_MACHINE_TYPES
    try:
        client = compute_v1.MachineTypesClient()
        request = compute_v1.ListMachineTypesRequest(
            project=RUNNER_VM_PROJECT_ID, zone=zone
        )
        types = {t.name: t.guest_cpus for t in client.list(request=request)}
        client.transport.close()
    except (GoogleAPIError, DefaultCredentialsError) as e:
        logger.warning(
            "Failed to load machine types cache - validation in degraded mode",
            extra={"reason": str(e)},
        )
        _KNOWN_MACHINE_TYPES = None
        return
    if not types:
        logger.warning(
            "Machine types list was empty - validation in degraded mode",
            extra={"zone": zone},
        )
        _KNOWN_MACHINE_TYPES = None
        return
    _KNOWN_MACHINE_TYPES = types
    logger.info(
        "Loaded machine types cache",
        extra={"zone": zone, "count": len(_KNOWN_MACHINE_TYPES)},
    )


def is_valid_machine_type(machine_type: str) -> bool:
    """True if the type exists in the cache OR cache is degraded (None). Degraded mode = permissive: trust the parser. GCE will reject on insert"""
    if _KNOWN_MACHINE_TYPES is None:
        return True
    return machine_type in _KNOWN_MACHINE_TYPES


def machine_type_vcpus(machine_type: str) -> int | None:
    """vCPUs of a machine type, or None if unknown or the cache is degraded."""
    if _KNOWN_MACHINE_TYPES is None:
        return None
    return _KNOWN_MACHINE_TYPES.get(machine_type)


async def was_preempted(vm_name: str) -> bool:
    """True if the VM was reclaimed by GCE's Spot scheduler; False otherwise,
    including when the answer is unknown (no client, API error)."""
    return await preemption_status(vm_name) is True


async def preemption_status(vm_name: str) -> bool | None:
    """Query Cloud Audit Log for `compute.instances.preempted` entries matching
    the given VM. True if found, False if not, None if the answer is unknown
    (no logging client, or the query failed). The reconciler waits on None
    instead of recording a permanent verdict.

    24h lookback covers any reasonable runner lifetime (sweep HARD_LIMIT=6h;
    GH job timeout ~24h). Filter is VM-name-scoped so it's specific even
    without a tighter timestamp bound.

    The VM may be in the control project (created before VMs moved) or the
    VM project, so each is asked on its own: a failed read of one can't hide
    the other's answer.
    """
    if _logging_client is None:
        return None
    # The name comes from a webhook payload and is interpolated into a regex
    # filter: anything that isn't a valid GCE instance name can't be ours.
    if not _GCE_INSTANCE_NAME_RE.fullmatch(vm_name):
        return False
    answers = [
        await _preempted_in(_logging_client, project, vm_name)
        for project in dict.fromkeys((GCP_PROJECT_ID, RUNNER_VM_PROJECT_ID))
    ]
    if True in answers:
        return True
    if None in answers:
        return None
    return False


async def _preempted_in(
    client: gcp_logging.Client, project: str, vm_name: str
) -> bool | None:
    since = datetime.now(UTC) - timedelta(hours=24)
    filter_str = (
        # Only GCE writes this log: an entry a VM's own credentials wrote
        # elsewhere can't pass for a preemption.
        'LOG_ID("cloudaudit.googleapis.com/system_event") '
        f'AND protoPayload.methodName="compute.instances.preempted" '
        # Anchored regex on the instance name, not `:` (case-insensitive
        # substring): runnerforge-1-2-1 must not match runnerforge-1-2-10.
        f'AND protoPayload.resourceName=~"/instances/{vm_name}$" '
        f'AND timestamp >= "{since.isoformat()}"'
    )

    try:
        entries = await asyncio.to_thread(
            lambda: list(
                client.list_entries(
                    resource_names=[f"projects/{project}"],
                    filter_=filter_str,
                    max_results=1,
                )
            )
        )
    except GoogleAPIError as e:
        logger.warning(
            "Failed to query audit log for preemption check",
            extra={"vm_name": vm_name, "project_id": project, "reason": str(e)},
        )
        return None

    return bool(entries)


async def cross_check_preemption(request: PreemptionCheckRequest) -> None:
    """BackgroundTask body: ask the audit log whether the VM in `request` was
    reclaimed by GCE's Spot scheduler. If so, emit a structured event so the
    operator (and the future user-facing frontend) can distinguish preemption
    from a normal runner failure.

    Mirrors `wait_for_vm_creation` in shape: takes a typed request object,
    queries GCE-side state, emits a structured log on the result.
    """
    if await was_preempted(request.vm_name):
        logger.warning(
            "Job ended due to spot preemption",
            extra={
                "vm_name": request.vm_name,
                "job_id": request.job_id,
                "conclusion": request.conclusion,
            },
        )
        if request.job_key is not None and request.job_base is not None:
            # Lands after `completed`, so it must not reopen the record.
            await get_job_store().record_fact(
                request.job_key,
                request.job_base,
                "preempted",
                {"phase": "mid_job"},
                source="watcher",
                reopen=False,
            )


async def create_vm(
    instance_name: str,
    machine_type: str,
    labels: dict[str, str],
    spot: bool = False,
    metadata: dict[str, str] | None = None,
    max_run_duration: timedelta = MAX_VM_RUN_DURATION,
    data_disk_size_gb: int = _DATA_DISK_SIZE_GB,
    zone: str = GCP_ZONE,
    project_id: str | None = None,
) -> OperationHandle | None:
    """Submits a VM creation request. Returns the operation handle (does not wait for VM to boot)."""
    project_id = project_id or RUNNER_VM_PROJECT_ID
    assert _compute_client is not None
    # GCE source_image requires either a project-qualified image path or a
    # family alias — a bare image name is rejected as a malformed URL. When
    # init_runner_image resolved a specific image, qualify its short name
    # into the canonical projects/<project>/global/images/<name> form;
    # otherwise fall back to the family alias (which is already qualified).
    # The image lives in RUNNER_IMAGE_PROJECT, not necessarily project_id
    # (the project the VM is created in).
    source_image = (
        f"projects/{RUNNER_IMAGE_PROJECT}/global/images/{_RUNNER_IMAGE_NAME}"
        if _RUNNER_IMAGE_NAME
        else _BOOT_IMAGE_FAMILY
    )
    with tracer.start_as_current_span("compute.create_vm") as span:
        span.set_attribute("instance_name", instance_name)
        span.set_attribute("machine_type", machine_type)
        span.set_attribute("source_image", source_image)
        span.set_attributes({"label." + k: v for k, v in labels.items()})
        span.set_attribute("project_id", project_id)
        span.set_attribute("zone", zone)
        span.set_attribute("spot", spot)

        instance = compute_v1.Instance()
        # Disks
        instance.disks = [
            # Boot disk - OS + runner binary (small, from our Packer image)
            compute_v1.AttachedDisk(
                boot=True,
                auto_delete=True,  # this is THE boot disk
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    source_image=source_image,
                    disk_size_gb=_BOOT_DISK_SIZE_GB,
                    # Disks don't inherit instance labels; without these the
                    # billing export can't attribute disk cost to a job/tenant.
                    labels=labels,
                ),
            ),
            # Data disk - workflow execution space (larger, blank, formatted  at boot)
            compute_v1.AttachedDisk(
                boot=False,
                auto_delete=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    disk_size_gb=data_disk_size_gb,
                    disk_type=f"zones/{zone}/diskTypes/pd-balanced",
                    labels=labels,
                ),
            ),
        ]

        # network. With only a subnetwork, GCE takes the network from it. No
        # access config either way, so the VM gets no external IP.
        instance.network_interfaces = [
            compute_v1.NetworkInterface(subnetwork=RUNNER_VM_SUBNETWORK)
            if RUNNER_VM_SUBNETWORK
            else compute_v1.NetworkInterface(network="global/networks/default")
        ]

        # metadata
        items = {**(metadata or {}), **SECURITY_METADATA}
        instance.metadata = compute_v1.Metadata(
            items=[compute_v1.Items(key=k, value=v) for k, v in items.items()]
        )
        instance.name = instance_name
        instance.machine_type = f"zones/{zone}/machineTypes/{machine_type}"
        instance.labels = labels

        # Service Accounts. None configured means none attached (ADR-020):
        # the job can't get a Google token from the metadata server.
        if RUNNER_VM_SA_EMAIL:
            instance.service_accounts = [
                compute_v1.ServiceAccount(
                    email=RUNNER_VM_SA_EMAIL,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
            ]

        # Every VM gets a hard runtime cap enforced by GCE itself. DELETE (not
        # STOP): a stopped VM clears its termination timestamp and its disks
        # keep billing. The action is required for standard VMs and defaults
        # to STOP for Spot, so it's always set explicitly.
        scheduling = compute_v1.Scheduling(
            instance_termination_action="DELETE",
            max_run_duration=compute_v1.Duration(
                seconds=int(max_run_duration.total_seconds())
            ),
        )
        if spot:
            scheduling.provisioning_model = "SPOT"
            scheduling.automatic_restart = False
            scheduling.on_host_maintenance = "TERMINATE"
        instance.scheduling = scheduling

        # Prepare the request to insert an instance
        request = compute_v1.InsertInstanceRequest()
        request.zone = zone
        request.project = project_id
        request.instance_resource = instance
        try:
            # NOTE: do not call our logger from inside `to_thread` callables —
            # contextvars (request_id, job_id, etc.) don't propagate across the
            # thread boundary, so logs would be missing request-scoped tags.
            # .insert is sync, would block the whole application
            operation = await asyncio.to_thread(_compute_client.insert, request=request)
        except AlreadyExists as e:
            logger.warning(
                "VM already exists",
                extra={"instance_name": instance_name, "error": str(e)},
            )
            return None

        logger.info(
            "Submitted VM creation",
            extra={
                "vm_name": instance_name,
                "zone": zone,
                "operation_id": operation.name,
                "machine_type": machine_type,
                "spot": spot,
                "image_version": _RUNNER_IMAGE_VERSION,
            },
        )
        return OperationHandle(
            name=operation.name,
            project=project_id,
            zone=zone,
            machine_type=machine_type,
            spot=spot,
            image_version=_RUNNER_IMAGE_VERSION,
            vm_name=instance_name,
        )


async def wait_for_vm_creation(
    handle: OperationHandle,
    timeout: float = 300.0,  # 5 minutes (under 10 minutes limit from github timeout)
) -> OperationOutcome:
    """BackgroundTask body: poll the create operation, then record the outcome
    as the job's `vm` fact (ADR-018) when the handle carries a job record."""
    outcome = await _poll_vm_creation(handle, timeout)
    if handle.job_key is not None and handle.job_base is not None:
        # Can land after `completed` (job cancelled while the VM boots), so it
        # must not reopen the record.
        await get_job_store().record_fact(
            handle.job_key,
            handle.job_base,
            "vm",
            {
                "outcome": outcome.outcome,
                "error_code": outcome.error_code,
                "vm_name": handle.vm_name,
            },
            source="watcher",
            reopen=False,
        )
    return outcome


async def _poll_vm_creation(
    handle: OperationHandle, timeout: float
) -> OperationOutcome:
    """Poll a GCE create-instance operation until DONE or timeout.

    Emits a structured 'vm.create.outcome' log event on the terminal state.
    Each poll is a ~50ms thread hop via asyncio.to_thread; between polls the
    event loop is free for other coroutines. Transient errors during a single
    poll are logged and the loop continues until the deadline.
    """
    assert _zone_ops_client is not None
    started = time.monotonic()
    deadline = started + timeout

    with tracer.start_as_current_span("compute.wait_for_vm_creation") as span:
        span.set_attribute("op_name", handle.name)
        span.set_attribute("zone", handle.zone)
        span.set_attribute("timeout_s", timeout)

        while time.monotonic() < deadline:
            try:
                op = await asyncio.to_thread(
                    _zone_ops_client.get,
                    # The operation lives in the project the VM was created in.
                    project=handle.project,
                    zone=handle.zone,
                    operation=handle.name,
                )
            # RequestException: the REST transport's own connection errors
            # (SSLError, ConnectionError), which aren't GoogleAPIError. This
            # runs after the response, while Cloud Run throttles the CPU, so a
            # pooled TLS connection can go stale between polls; uncaught, it
            # ended the watcher with no outcome line.
            except (GoogleAPIError, RequestException) as e:
                # Transient — log and keep polling until the deadline catches us.
                logger.warning(
                    "Transient error polling operation",
                    extra={
                        "op_name": handle.name,
                        "error": str(e),
                        "error_class": type(e).__name__,
                    },
                )
                await asyncio.sleep(1.0)
                continue

            if op.status == compute_v1.Operation.Status.DONE:
                duration_ms = int((time.monotonic() - started) * 1000)
                if op.error and op.error.errors:
                    first = op.error.errors[0]
                    outcome = OperationOutcome(
                        outcome="failure",
                        op_name=handle.name,
                        zone=handle.zone,
                        duration_ms=duration_ms,
                        error_code=first.code,
                        error_message=first.message,
                    )
                    logger.warning(
                        "VM creation outcome",
                        extra={
                            "outcome": outcome.outcome,
                            "op_name": outcome.op_name,
                            "zone": outcome.zone,
                            "duration_ms": outcome.duration_ms,
                            "machine_type": handle.machine_type,
                            "spot": handle.spot,
                            "image_version": handle.image_version,
                            "error_code": outcome.error_code,
                            "error_message": outcome.error_message,
                        },
                    )
                else:
                    outcome = OperationOutcome(
                        outcome="success",
                        op_name=handle.name,
                        zone=handle.zone,
                        duration_ms=duration_ms,
                    )
                    logger.info(
                        "VM creation outcome",
                        extra={
                            "outcome": outcome.outcome,
                            "op_name": outcome.op_name,
                            "zone": outcome.zone,
                            "duration_ms": outcome.duration_ms,
                            "machine_type": handle.machine_type,
                            "spot": handle.spot,
                            "image_version": handle.image_version,
                        },
                    )
                span.set_attribute("outcome", outcome.outcome)
                return outcome

            await asyncio.sleep(1.0)

        duration_ms = int((time.monotonic() - started) * 1000)
        outcome = OperationOutcome(
            outcome="timeout",
            op_name=handle.name,
            zone=handle.zone,
            duration_ms=duration_ms,
        )
        logger.warning(
            "VM creation outcome",
            extra={
                "outcome": outcome.outcome,
                "op_name": outcome.op_name,
                "zone": outcome.zone,
                "duration_ms": outcome.duration_ms,
                "machine_type": handle.machine_type,
                "spot": handle.spot,
                "image_version": handle.image_version,
            },
        )
        span.set_attribute("outcome", outcome.outcome)
        return outcome


async def find_vms_by_job_id(
    job_id: str,
    run_id: str,
    run_attempt: str,
    zone: str = GCP_ZONE,
    project_id: str | None = None,
) -> list[str]:
    """Returns instance names of VMs labeled with the given job_id."""
    project_id = project_id or RUNNER_VM_PROJECT_ID
    assert _compute_client is not None
    client = _compute_client
    request = compute_v1.ListInstancesRequest()
    request.zone = zone
    request.project = project_id
    request.filter = (
        f"labels.job_id={job_id} AND "
        f"labels.run_id={run_id} AND "
        f"labels.run_attempt={run_attempt}"
    )

    instances = await asyncio.to_thread(lambda: list(client.list(request=request)))

    result = [i.name for i in instances]
    logger.info(
        "VM lookup by job_id",
        extra={
            "job_id": job_id,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "match_count": len(result),
        },
    )
    return result


async def get_vm_by_name(
    instance_name: str,
    zone: str = GCP_ZONE,
    project_id: str | None = None,
) -> compute_v1.Instance | None:
    """Returns the named VM, or None if it doesn't exist."""
    project_id = project_id or RUNNER_VM_PROJECT_ID
    assert _compute_client is not None
    # Callers pass runner names from payloads, chosen by whoever registered
    # the runner. GCE answers 400, not 404, for a name it can't hold.
    if not _GCE_INSTANCE_NAME_RE.fullmatch(instance_name):
        return None
    with tracer.start_as_current_span("compute.get_vm_by_name") as span:
        span.set_attribute("instance_name", instance_name)
        try:
            return await asyncio.to_thread(
                _compute_client.get,
                project=project_id,
                zone=zone,
                instance=instance_name,
            )
        except NotFound:
            return None


async def get_vm_labels_by_name(
    instance_name: str,
    zone: str = GCP_ZONE,
    project_id: str | None = None,
) -> dict[str, str] | None:
    """Returns the raw labels of the named VM, or None if it doesn't exist.

    Raw dict on purpose: callers use this to decide whether a VM is ours, so
    a VM with foreign/missing labels must come back as data, not raise a
    ValidationError like RunnerForgeVmLabels.model_validate would.
    """
    instance = await get_vm_by_name(instance_name, zone=zone, project_id=project_id)
    return None if instance is None else dict(instance.labels)


async def get_vm_labels_by_job(
    job_id: str,
    run_id: str,
    run_attempt: str,
    zone: str = GCP_ZONE,
    project_id: str | None = None,
) -> dict[str, str] | None:
    project_id = project_id or RUNNER_VM_PROJECT_ID
    assert _compute_client is not None
    with tracer.start_as_current_span("compute.get_vm_labels_by_job") as span:
        span.set_attribute("job_id", job_id)
        client = _compute_client
        request = compute_v1.ListInstancesRequest()
        request.zone = zone
        request.project = project_id
        request.filter = (
            f"labels.job_id={job_id} AND "
            f"labels.run_id={run_id} AND "
            f"labels.run_attempt={run_attempt}"
        )
        instances = await asyncio.to_thread(lambda: list(client.list(request=request)))
        if not instances:
            return None

        result = [i.name for i in instances]
        logger.info(
            "VM lookup by job_id",
            extra={
                "job_id": job_id,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "match_count": len(result),
            },
        )
        try:
            labels = RunnerForgeVmLabels.model_validate(dict(instances[0].labels))
        except ValidationError:
            # Callers only use these labels for trace links and the readiness
            # metric. A shape mismatch (e.g. a VM from another revision during a
            # canary) must degrade those, not 500 the webhook and block a delete.
            logger.warning(
                "VM labels have unexpected shape",
                extra={
                    "vm_name": instances[0].name,
                    "raw_labels": dict(instances[0].labels),
                },
            )
            return None

        return labels.model_dump()


async def delete_vm(
    instance_name: str,
    reason: Literal["webhook", "sweep", "reconcile", "cutoff", "terminated"],
    zone: str = GCP_ZONE,
    project_id: str | None = None,
) -> str | None:
    """Submits VM deletion. Returns operation ID. Does not wait for completion."""
    project_id = project_id or RUNNER_VM_PROJECT_ID
    assert _compute_client is not None
    with tracer.start_as_current_span("compute.delete_vm") as span:
        span.set_attribute("instance_name", instance_name)
        span.set_attribute("project_id", project_id)
        span.set_attribute("zone", zone)
        span.set_attribute("reason", reason)
        try:
            operation = await asyncio.to_thread(
                _compute_client.delete,
                project=project_id,
                zone=zone,
                instance=instance_name,
            )
        except NotFound:
            logger.info(
                "VM already deleted (idempotent no-op)",
                extra={"vm_name": instance_name, "zone": zone, "reason": reason},
            )
            return None
        logger.info(
            "Submitted VM deletion",
            extra={
                "vm_name": instance_name,
                "zone": zone,
                "operation_id": operation.name,
                "reason": reason,
            },
        )
        return operation.name


async def list_runnerforge_vms(
    zone: str = GCP_ZONE, project_id: str | None = None
) -> list[VmInfo]:
    """Returns all VMs labeled runner=runnerforge with their creation timestamp + labels."""
    project_id = project_id or RUNNER_VM_PROJECT_ID
    assert _compute_client is not None
    with tracer.start_as_current_span("compute.list_runnerforge_vms") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("zone", zone)

        client = _compute_client
        request = compute_v1.ListInstancesRequest()
        request.zone = zone
        request.project = project_id

        # TODO: filtering based on labels. less secure of ensuring the runners are the ones we want to delete, either use some secured handshake, or use uuid while creation (still problematic once you want users to bring their projects)
        request.filter = "labels.runner=runnerforge"

        instances = await asyncio.to_thread(lambda: list(client.list(request=request)))
        valid_vms: list[VmInfo] = []
        skipped_malformed = 0
        for i in instances:
            try:
                labels = RunnerForgeVmLabels.model_validate(dict(i.labels))
            except ValidationError:
                logger.warning(
                    "Skipping VM with unexpected label shape",
                    extra={"vm_name": i.name, "raw_labels": dict(i.labels)},
                )
                skipped_malformed += 1
                continue
            # The exact owner/repo lives in metadata (written by handle_queued for
            # the startup script). Labels can't hold it: GCE label values are
            # lowercase-only and max 63 chars.
            metadata = {item.key: item.value for item in i.metadata.items}
            repo_url = metadata.get("repo-url", "")
            # None (not "") when unknown, so the sweep can still apply the
            # hard-limit delete but must never ask GitHub about a guessed repo.
            repo_full_name = (
                repo_url.removeprefix(GITHUB_REPO_URL_PREFIX)
                if repo_url.startswith(GITHUB_REPO_URL_PREFIX)
                else None
            )
            runner_labels = [
                label for label in metadata.get("runner-labels", "").split(",") if label
            ]
            valid_vms.append(
                VmInfo.model_validate(
                    {
                        "name": i.name,
                        "creation_timestamp": i.creation_timestamp,
                        "labels": labels,
                        "repo_full_name": repo_full_name,
                        "runner_labels": runner_labels,
                        "status": i.status,
                        "serial_logging": serial_logging_state(i),
                        # A zonal URL; the type is its last segment.
                        "machine_type": (i.machine_type or "").rsplit("/", 1)[-1],
                    }
                )
            )

        logger.info(
            "VM list scan",
            extra={
                "project_id": project_id,
                "runnerforge_vm_count": len(valid_vms),
                "skipped_malformed_count": skipped_malformed,
            },
        )
        span.set_attribute("result_count", len(valid_vms))
        span.set_attribute("skipped_malformed_count", skipped_malformed)
        return valid_vms


# Statuses that hold a slot under a concurrency limit (ADR-019): not VMs being
# stopped or deleted.
ACTIVE_STATUSES = frozenset({"PROVISIONING", "STAGING", "RUNNING"})


# On the `queued` path: a slow list fails the check (and admits) instead of
# eating GitHub's 10 s.
ACTIVE_VMS_TIMEOUT_SECONDS = 2.0


async def active_vm_names(
    installation_id: int, zone: str = GCP_ZONE, project_id: str | None = None
) -> set[str]:
    """Names of the installation's RunnerForge VMs that count against its
    limit."""
    project_id = project_id or RUNNER_VM_PROJECT_ID
    assert _compute_client is not None
    with tracer.start_as_current_span("compute.active_vm_names") as span:
        span.set_attribute("installation_id", installation_id)
        client = _compute_client
        request = compute_v1.ListInstancesRequest()
        request.zone = zone
        request.project = project_id
        request.filter = (
            f"labels.runner=runnerforge AND labels.installation_id={installation_id}"
        )
        instances = await asyncio.to_thread(
            lambda: list(
                client.list(request=request, timeout=ACTIVE_VMS_TIMEOUT_SECONDS)
            )
        )
        names = {i.name for i in instances if i.status in ACTIVE_STATUSES}
        span.set_attribute("result_count", len(names))
        return names


# --- serial logging off at job start (ADR-020) ---


def serial_logging_state(instance: compute_v1.Instance) -> bool | None:
    """Whether GCE copies the VM's serial port to Cloud Logging. GCE's own
    consolidated value when the response has it, else the VM's item; None
    when unknown. A VM without the item can still inherit TRUE from its
    project, so absent never means off."""
    effective = instance.resource_status.effective_instance_metadata
    if "serial_port_logging_enable_metadata_value" in effective:
        return effective.serial_port_logging_enable_metadata_value
    for item in instance.metadata.items:
        if item.key == SERIAL_LOGGING_KEY:
            value = item.value.strip().lower()
            return value == "true" if value in ("true", "false") else None
    return None


async def turn_off_serial_logging(
    vm_name: str,
    trigger: Literal["in_progress", "reconcile"],
    reason: Literal["webhook", "busy", "aged", "runner_busy"],
    *,
    installation_id: int | None,
    instance: compute_v1.Instance | None = None,
    zone: str = GCP_ZONE,
    project_id: str | None = None,
) -> SerialOffOutcome:
    """Set the VM's serial-port-logging-enable to FALSE, so the tenant's job
    output stops reaching Cloud Logging. `instance` is a get the caller
    already made; `installation_id`, when given, must match the VM's label.

    Never raises for a GCE error and never waits for the operation: the
    reconciler reads the state back on its next run. Its log line holds no
    metadata values or error text, since the VM's metadata carries its
    registration token."""
    project_id = project_id or RUNNER_VM_PROJECT_ID
    operation_id: str | None = None
    error_class: str | None = None
    outcome: SerialOffOutcome
    if not _GCE_INSTANCE_NAME_RE.fullmatch(vm_name):
        outcome = "not_owned"
    else:
        try:
            async with asyncio.timeout(SERIAL_OFF_TIMEOUT_SECONDS):
                outcome, operation_id = await _serial_off(
                    vm_name, installation_id, instance, zone, project_id
                )
        except NotFound:
            outcome = "vm_gone"
        # RequestException: the REST transport's own connection errors.
        # GoogleAuthError: a failed token refresh before the call.
        except (GoogleAPIError, GoogleAuthError, RequestException, TimeoutError) as e:
            outcome, error_class = "failed", type(e).__name__
    logger.log(
        logging.WARNING if outcome in ("failed", "conflict") else logging.INFO,
        "Serial logging outcome",
        extra={
            "vm_name": vm_name,
            "project_id": project_id,
            "trigger": trigger,
            "reason": reason,
            "outcome": outcome,
            "error_class": error_class,
            "operation_id": operation_id,
        },
    )
    return outcome


async def _serial_off(
    vm_name: str,
    installation_id: int | None,
    instance: compute_v1.Instance | None,
    zone: str,
    project_id: str,
) -> tuple[SerialOffOutcome, str | None]:
    assert _compute_client is not None
    client = _compute_client
    # setMetadata replaces every item, and the fingerprint makes it fail if
    # the metadata changed since our get: then read once more and retry.
    for _ in range(2):
        if instance is None:
            instance = await asyncio.to_thread(
                client.get,
                project=project_id,
                zone=zone,
                instance=vm_name,
                # The client's default retry runs for up to 600 s, which
                # would keep this thread long after our timeout gives up.
                retry=None,
                timeout=SERIAL_OFF_TIMEOUT_SECONDS,
            )
        # Whoever registers a runner chooses its name: change only a VM whose
        # own labels say we made it (for this installation).
        labels = instance.labels
        if labels.get("runner") != "runnerforge" or (
            installation_id is not None
            and labels.get("installation_id") != str(installation_id)
        ):
            return "not_owned", None
        if instance.status != "RUNNING":
            return "not_running", None
        if serial_logging_state(instance) is False:
            return "already_off", None
        items = [
            compute_v1.Items(
                key=item.key,
                value="FALSE" if item.key == SERIAL_LOGGING_KEY else item.value,
            )
            for item in instance.metadata.items
        ]
        if all(item.key != SERIAL_LOGGING_KEY for item in items):
            # Absent: it may come from the project, so say FALSE outright.
            items.append(compute_v1.Items(key=SERIAL_LOGGING_KEY, value="FALSE"))
        request = compute_v1.SetMetadataInstanceRequest(
            project=project_id,
            zone=zone,
            instance=vm_name,
            metadata_resource=compute_v1.Metadata(
                fingerprint=instance.metadata.fingerprint, items=items
            ),
            # A new one per attempt: GCE ignores a repeated ID.
            request_id=str(uuid.uuid4()),
        )
        try:
            operation = await asyncio.to_thread(
                client.set_metadata, request=request, timeout=SERIAL_OFF_TIMEOUT_SECONDS
            )
        except PreconditionFailed:
            instance = None
            continue
        return "submitted", operation.name
    return "conflict", None
