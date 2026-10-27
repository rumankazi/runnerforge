import importlib.resources
import os
from pathlib import Path

# Github
WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
GITHUB_APP_ID = os.environ["GITHUB_APP_ID"]
PRIVATE_KEY = Path(os.environ["GITHUB_APP_PRIVATE_KEY_PATH"]).read_text()

# GCP
GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
GCP_ZONE = os.environ.get("GCP_ZONE", "europe-west4-a")
RUNNER_VM_SA_EMAIL = os.environ["RUNNER_VM_SA_EMAIL"]
# Project that owns the runner image family. Same as GCP_PROJECT_ID today;
# differs once VMs run elsewhere (a separate hosted pool project, self-host).
RUNNER_IMAGE_PROJECT = os.environ.get("RUNNER_IMAGE_PROJECT", GCP_PROJECT_ID)

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


# GitHub App installations allowed to get VMs. Must be set in prod: the App is
# public, so anyone can install it and spend our GCP budget.
ALLOWED_INSTALLATION_IDS = parse_installation_ids(
    os.environ.get("ALLOWED_INSTALLATION_IDS")
)
