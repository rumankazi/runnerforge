import asyncio
import logging

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import compute_v1
from opentelemetry import trace
from pydantic import ValidationError

from runnerforge.config import GCP_PROJECT_ID, GCP_ZONE, RUNNER_VM_SA_EMAIL
from runnerforge.models import RunnerForgeVmLabels, VmInfo

_BOOT_IMAGE_FAMILY = "projects/runnerforge/global/images/family/runnerforge-runner"
_BOOT_DISK_SIZE_GB = 10
_DATA_DISK_SIZE_GB = 50

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)
_compute_client: compute_v1.InstancesClient | None = None


# Not async since these are not async methods (sync operations)
def init_compute_client():
    global _compute_client
    _compute_client = compute_v1.InstancesClient()


def close_compute_client():
    global _compute_client
    if _compute_client is not None:
        _compute_client.transport.close()
        _compute_client = None


async def create_vm(
    instance_name: str,
    machine_type: str,
    labels: dict[str, str],
    metadata: dict[str, str] | None = None,
    data_disk_size_gb: int = _DATA_DISK_SIZE_GB,
    zone: str = GCP_ZONE,
    project_id: str = GCP_PROJECT_ID,
) -> str:
    """Submits a VM creation request. Returns the operation ID (does not wait for VM to boot)."""
    assert _compute_client is not None
    with tracer.start_as_current_span("compute.create_vm") as span:
        span.set_attribute("instance_name", instance_name)
        span.set_attribute("machine_type", machine_type)
        # image is the family alias for now; will be specific after the 3.1 pivot
        span.set_attribute("image_family", _BOOT_IMAGE_FAMILY)
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
                    source_image=_BOOT_IMAGE_FAMILY,
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
                access_configs=[
                    compute_v1.AccessConfig(name="External NAT", type_="ONE_TO_ONE_NAT")
                ],
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
            return instance_name

        logger.info(
            "Submitted VM creation",
            extra={
                "vm_name": instance_name,
                "zone": zone,
                "operation_id": operation.name,
            },
        )
        return operation.name


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
    instance_name: str, zone: str = GCP_ZONE, project_id: str = GCP_PROJECT_ID
) -> str | None:
    """Submits VM deletion. Returns operation ID. Does not wait for completion."""
    assert _compute_client is not None
    with tracer.start_as_current_span("compute.delete_vm") as span:
        span.set_attribute("instance_name", instance_name)
        span.set_attribute("project_id", project_id)
        span.set_attribute("zone", zone)
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
                extra={"vm_name": instance_name, "zone": zone},
            )
            return None
        logger.info(
            "Submitted VM deletion",
            extra={
                "vm_name": instance_name,
                "zone": zone,
                "operation_id": operation.name,
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
