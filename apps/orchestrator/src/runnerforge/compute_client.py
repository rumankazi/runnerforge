import asyncio
import logging

from google.cloud import compute_v1

from runnerforge.config import GCP_PROJECT_ID, GCP_ZONE

_DEFAULT_IMAGE = "projects/debian-cloud/global/images/family/debian-12"
_BOOT_DISK_SIZE_GB = 10

logger = logging.getLogger(__name__)


async def create_vm(
    instance_name: str,
    machine_type: str,
    labels: dict[str, str],
    metadata: dict[str, str] | None = None,
    image: str = _DEFAULT_IMAGE,
    disk_size_gb: int = _BOOT_DISK_SIZE_GB,
    zone: str = GCP_ZONE,
    project_id: str = GCP_PROJECT_ID,
) -> str:
    """Submits a VM creation request. Returns the operation ID (does not wait for VM to boot)."""
    instance_client = compute_v1.InstancesClient()

    instance = compute_v1.Instance()

    # Boot disk
    instance.disks = [
        compute_v1.AttachedDisk(
            boot=True,
            auto_delete=True,  # this is THE boot disk
            initialize_params=compute_v1.AttachedDiskInitializeParams(
                source_image=image,
                disk_size_gb=disk_size_gb,
            ),
        )
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

    # Prepare the request to insert an instance
    request = compute_v1.InsertInstanceRequest()
    request.zone = zone
    request.project = project_id
    request.instance_resource = instance
    operation = await asyncio.to_thread(instance_client.insert, request=request)

    logger.info(
        "Submitted VM creation",
        extra={"vm_name": instance_name, "zone": zone, "operation_id": operation.name},
    )
    return operation.name


async def find_vms_by_job_id(
    job_id: str, zone: str = GCP_ZONE, project_id: str = GCP_PROJECT_ID
) -> list[str]:
    """Returns instance names of VMs labeled with the given job_id."""
    client = compute_v1.InstancesClient()
    request = compute_v1.ListInstancesRequest()
    request.zone = zone
    request.project = project_id
    request.filter = f"labels.job_id={job_id}"

    instances = await asyncio.to_thread(lambda: list(client.list(request=request)))

    return [i.name for i in instances]


async def delete_vm(
    instance_name: str, zone: str = GCP_ZONE, project_id: str = GCP_PROJECT_ID
) -> str:
    """Submits VM deletion. Returns operation ID. Does not wait for completion."""
    client = compute_v1.InstancesClient()

    operation = await asyncio.to_thread(
        client.delete, project=project_id, zone=zone, instance=instance_name
    )
    logger.info(
        "Submitted VM deletion",
        extra={"vm_name": instance_name, "zone": zone, "operation_id": operation.name},
    )
    return operation.name
