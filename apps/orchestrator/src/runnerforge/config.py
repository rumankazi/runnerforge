import importlib.resources
import logging
import os
import re
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Github
WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
GITHUB_APP_ID = os.environ["GITHUB_APP_ID"]
PRIVATE_KEY = Path(os.environ["GITHUB_APP_PRIVATE_KEY_PATH"]).read_text()

# GCP
# The control project: Firestore, Trace, log links and the runner image.
GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
GCP_ZONE = os.environ.get("GCP_ZONE", "europe-west4-a")
# Where runner VMs are created, listed and deleted (ADR-020). Same as
# GCP_PROJECT_ID unless set; the hosted service moves VMs to a pool project.
RUNNER_VM_PROJECT_ID = os.environ.get("RUNNER_VM_PROJECT_ID") or GCP_PROJECT_ID
# Empty keeps the VM project's `default` network.
RUNNER_VM_SUBNETWORK = os.environ.get("RUNNER_VM_SUBNETWORK", "")
# Empty means VMs get no service account (and so no token on the metadata
# server). Must be empty when VMs run outside the control project.
RUNNER_VM_SA_EMAIL = os.environ.get("RUNNER_VM_SA_EMAIL", "")
# Project that owns the runner image family. Same as GCP_PROJECT_ID today;
# differs once VMs run elsewhere (a separate hosted pool project, self-host).
RUNNER_IMAGE_PROJECT = os.environ.get("RUNNER_IMAGE_PROJECT", GCP_PROJECT_ID)

_SUBNETWORK_RE = re.compile(
    r"(?:projects/(?P<project>[^/]+)/)?regions/(?P<region>[^/]+)/subnetworks/[^/]+"
)


def validate_runner_vm_config(
    control_project: str,
    vm_project: str,
    zone: str,
    subnetwork: str,
    sa_email: str,
) -> list[str]:
    """Check where runner VMs go. Raises on a setting that would put VMs in
    the wrong place or give pool VMs a service account; logs and returns
    warnings for settings that work only in some projects. Called at import,
    so a bad deploy fails at startup."""
    warnings = _runner_vm_config_warnings(
        control_project, vm_project, zone, subnetwork, sa_email
    )
    for warning in warnings:
        logger.warning(warning)
    return warnings


def _runner_vm_config_warnings(
    control_project: str,
    vm_project: str,
    zone: str,
    subnetwork: str,
    sa_email: str,
) -> list[str]:
    foreign = vm_project != control_project
    if foreign and sa_email:
        # A pool VM with an account has its token on the metadata server,
        # readable by the tenant's job (ADR-020).
        raise ValueError(
            "RUNNER_VM_SA_EMAIL must be empty when RUNNER_VM_PROJECT_ID differs "
            "from GCP_PROJECT_ID"
        )
    if not subnetwork:
        if foreign:
            # GCE falls back to the `default` network, which a project may
            # not have; the insert then fails loudly.
            return [
                f"RUNNER_VM_SUBNETWORK is empty, so VMs in {vm_project} use "
                + "its default network"
            ]
        return []
    match = _SUBNETWORK_RE.fullmatch(subnetwork)
    if match is None:
        raise ValueError(
            "RUNNER_VM_SUBNETWORK must be [projects/P/]regions/R/subnetworks/S, "
            f"got {subnetwork!r}"
        )
    if match["project"] is not None and match["project"] != vm_project:
        raise ValueError(
            f"RUNNER_VM_SUBNETWORK is in project {match['project']}, "
            f"not the VM project {vm_project}"
        )
    region = zone.rsplit("-", 1)[0]
    if match["region"] != region:
        raise ValueError(
            f"RUNNER_VM_SUBNETWORK is in region {match['region']}, "
            f"not {region} (the region of GCP_ZONE)"
        )
    return []


validate_runner_vm_config(
    GCP_PROJECT_ID,
    RUNNER_VM_PROJECT_ID,
    GCP_ZONE,
    RUNNER_VM_SUBNETWORK,
    RUNNER_VM_SA_EMAIL,
)

# Startup script fed into the vm creation metadata
STARTUP_SCRIPT = (
    importlib.resources.files("runnerforge")
    .joinpath("scripts/startup_script.sh")
    .read_text()
)

# for /sweep endpoint
EXPECTED_SCHEDULER_SA_EMAIL = os.environ["EXPECTED_SCHEDULER_SA_EMAIL"]
EXPECTED_AUDIENCE = os.environ["EXPECTED_AUDIENCE"]

# Detect as runnerforge request if one of the labels found
RUNNERFORGE_LABELS = ["runnerforge"]


def parse_installation_ids(value: str | None) -> frozenset[int] | None:
    """None (unset) means "allow every installation" — the self-host default,
    where the App is private to its owner. A set value is a comma-separated
    list; an empty string allows nobody (a deliberate kill switch). A
    malformed id raises at import, so a bad deploy fails at startup."""
    if value is None:
        return None
    return frozenset(int(part) for part in value.split(",") if part.strip())


# GitHub App installations allowed to get VMs. Must be set in prod before the
# App is made public: otherwise anyone could install it and spend our budget.
ALLOWED_INSTALLATION_IDS = parse_installation_ids(
    os.environ.get("ALLOWED_INSTALLATION_IDS")
)


def parse_job_store(value: str) -> Literal["none", "firestore"]:
    """A typo raises at import, so a bad revision fails its canary instead of
    silently not writing job records."""
    normalized = value.strip().lower()
    if normalized == "none":
        return "none"
    if normalized == "firestore":
        return "firestore"
    raise ValueError(f"JOB_STORE must be 'none' or 'firestore', got {value!r}")


def parse_admission(value: str) -> Literal["allowlist", "registry"]:
    """A typo raises at import, so a bad revision fails its canary instead of
    admitting by the wrong rule."""
    normalized = value.strip().lower()
    if normalized == "allowlist":
        return "allowlist"
    if normalized == "registry":
        return "registry"
    raise ValueError(f"ADMISSION must be 'allowlist' or 'registry', got {value!r}")


# Admission (ADR-019). "allowlist" (the default) admits by
# ALLOWED_INSTALLATION_IDS alone, so an install without a registry still runs.
# "registry" admits listed IDs as unlimited and everyone else by their
# registry row and limits; an unset list then means nobody, not everyone.
ADMISSION = parse_admission(os.environ.get("ADMISSION", "allowlist"))


# Per-job store (ADR-018). "none" (the default) writes nothing, so an install
# without Firestore still runs.
JOB_STORE = parse_job_store(os.environ.get("JOB_STORE", "none"))
# An empty value would make the client fall back to "(default)" silently.
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE") or "runnerforge"
