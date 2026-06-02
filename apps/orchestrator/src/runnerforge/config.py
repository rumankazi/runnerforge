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

# Startup script fed into the vm creation metadata
STARTUP_SCRIPT = (
    importlib.resources.files("runnerforge")
    .joinpath("scripts/startup_script.sh")
    .read_text()
)

# for /sweep endpoint
EXPECTED_SCHEDULER_SA_EMAIL = os.environ["EXPECTED_SCHEDULER_SA_EMAIL"]
EXPECTED_AUDIENCE = os.environ["EXPECTED_AUDIENCE"]
