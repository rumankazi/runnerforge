import os
from pathlib import Path

# Github
WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
GITHUB_APP_ID = os.environ["GITHUB_APP_ID"]
PRIVATE_KEY = Path(os.environ["GITHUB_APP_PRIVATE_KEY_PATH"]).read_text()

# GCP
GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
GCP_ZONE = os.environ.get("GCP_ZONE", "europe-west4-a")

# Startup script path — defaults to the script in the repo's /scripts/ dir.
# Override via env var when the layout differs (e.g., Docker image).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
STARTUP_SCRIPT_PATH = Path(
    os.environ.get("STARTUP_SCRIPT_PATH")
    or _REPO_ROOT / "scripts" / "startup-script.sh"
)
STARTUP_SCRIPT = STARTUP_SCRIPT_PATH.read_text()

# for /sweep endpoint
EXPECTED_SCHEDULER_SA_EMAIL = os.environ["EXPECTED_SCHEDULER_SA_EMAIL"]
EXPECTED_AUDIENCE = os.environ["EXPECTED_AUDIENCE"]
