import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from google.api_core.exceptions import AlreadyExists, GoogleAPIError, NotFound
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import compute_v1
from opentelemetry import trace
from pydantic import ValidationError

from runnerforge.config import GCP_PROJECT_ID, GCP_ZONE, RUNNER_VM_SA_EMAIL
from runnerforge.models import RunnerForgeVmLabels, VmInfo

_RUNNER_IMAGE_NAME: str | None = None  # full GCE name, used as source_image pin
_RUNNER_IMAGE_VERSION: str | None = None  # dotted semver, used in logs/metrics
_BOOT_IMAGE_FAMILY = "projects/runnerforge/global/images/family/runnerforge-runner"
_BOOT_DISK_SIZE_GB = 10
_DATA_DISK_SIZE_GB = 50

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass(frozen=True, slots=True)
class OperationHandle:
    """What `create_vm` returns so polling code can query the GCE operation later."""

    name: str
    zone: str
    machine_type: str
    image_version: str | None


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
    global _compute_client, _zone_ops_client, _images_client
    if _compute_client is not None:
        _compute_client.transport.close()
        _compute_client = None

    if _zone_ops_client is not None:
        _zone_ops_client.transport.close()
        _zone_ops_client = None

    if _images_client is not None:
        _images_client.transport.close()
        _images_client = None


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
            project=GCP_PROJECT_ID, family="runnerforge-runner"
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


async def create_vm(
    instance_name: str,
    machine_type: str,
    labels: dict[str, str],
    metadata: dict[str, str] | None = None,
    data_disk_size_gb: int = _DATA_DISK_SIZE_GB,
    zone: str = GCP_ZONE,
    project_id: str = GCP_PROJECT_ID,
) -> OperationHandle | None:
    """Submits a VM creation request. Returns the operation handle (does not wait for VM to boot)."""
    assert _compute_client is not None
    # Pinned to resolved image name when init_runner_image succeeded; falls back to
    # family alias when it didn't (e.g. local dev without GCP creds).
    source_image = _RUNNER_IMAGE_NAME or _BOOT_IMAGE_FAMILY
    with tracer.start_as_current_span("compute.create_vm") as span:
        span.set_attribute("instance_name", instance_name)
        span.set_attribute("machine_type", machine_type)
        span.set_attribute("source_image", source_image)
        span.set_attributes({"label." + k: v for k, v in labels.items()})
        span.set_attribute("project_id", project_id)
        span.set_attribute("zone", zone)

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
                ),
            ),
            # Data disk - workflow execution space (larger, blank, formatted  at boot)
            compute_v1.AttachedDisk(
                boot=False,
                auto_delete=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    disk_size_gb=data_disk_size_gb,
                    disk_type=f"zones/{zone}/diskTypes/pd-balanced",
                ),
            ),
        ]

        # network
        instance.network_interfaces = [
            compute_v1.NetworkInterface(
                network="global/networks/default",
            )
        ]

        # metadata
        if metadata:
            instance.metadata = compute_v1.Metadata(
                items=[compute_v1.Items(key=k, value=v) for k, v in metadata.items()]
            )
        instance.name = instance_name
        instance.machine_type = f"zones/{zone}/machineTypes/{machine_type}"
        instance.labels = labels

        # Service Accounts
        instance.service_accounts = [
            compute_v1.ServiceAccount(
                email=RUNNER_VM_SA_EMAIL,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        ]

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
                "image_version": _RUNNER_IMAGE_VERSION,
            },
        )
        return OperationHandle(
            name=operation.name,
            zone=zone,
            machine_type=machine_type,
            image_version=_RUNNER_IMAGE_VERSION,
        )


async def wait_for_vm_creation(
    handle: OperationHandle,
    timeout: float = 120.0,
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
                    project=GCP_PROJECT_ID,
                    zone=handle.zone,
                    operation=handle.name,
                )
            except GoogleAPIError as e:
                # Transient — log and keep polling until the deadline catches us.
                logger.warning(
                    "Transient error polling operation",
                    extra={"op_name": handle.name, "error": str(e)},
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
                        "vm.create.outcome",
                        extra={
                            "outcome": outcome.outcome,
                            "op_name": outcome.op_name,
                            "zone": outcome.zone,
                            "duration_ms": outcome.duration_ms,
                            "machine_type": handle.machine_type,
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
                        "vm.create.outcome",
                        extra={
                            "outcome": outcome.outcome,
                            "op_name": outcome.op_name,
                            "zone": outcome.zone,
                            "duration_ms": outcome.duration_ms,
                            "machine_type": handle.machine_type,
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
            "vm.create.outcome",
            extra={
                "outcome": outcome.outcome,
                "op_name": outcome.op_name,
                "zone": outcome.zone,
                "duration_ms": outcome.duration_ms,
                "machine_type": handle.machine_type,
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
    project_id: str = GCP_PROJECT_ID,
) -> list[str]:
    """Returns instance names of VMs labeled with the given job_id."""
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


async def get_vm_labels_by_job(
    job_id: str,
    run_id: str,
    run_attempt: str,
    zone: str = GCP_ZONE,
    project_id: str = GCP_PROJECT_ID,
) -> dict[str, str] | None:
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
        labels = RunnerForgeVmLabels.model_validate(dict(instances[0].labels))

        return labels.model_dump()


async def delete_vm(
    instance_name: str,
    reason: Literal["webhook", "sweep"],
    zone: str = GCP_ZONE,
    project_id: str = GCP_PROJECT_ID,
) -> str | None:
    """Submits VM deletion. Returns operation ID. Does not wait for completion."""
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
    zone: str = GCP_ZONE, project_id: str = GCP_PROJECT_ID
) -> list[VmInfo]:
    """Returns all VMs labeled runner=runnerforge with their creation timestamp + labels."""
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
            valid_vms.append(
                VmInfo.model_validate(
                    {
                        "name": i.name,
                        "creation_timestamp": i.creation_timestamp,
                        "labels": labels,
                    }
                )
            )

        logger.info(
            "VM list scan",
            extra={
                "runnerforge_vm_count": len(valid_vms),
                "skipped_malformed_count": skipped_malformed,
            },
        )
        span.set_attribute("result_count", len(valid_vms))
        span.set_attribute("skipped_malformed_count", skipped_malformed)
        return valid_vms
