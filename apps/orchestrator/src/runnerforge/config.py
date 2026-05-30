import os
from pathlib import Path

# Github
WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
GITHUB_APP_ID = os.environ["GITHUB_APP_ID"]
PRIVATE_KEY = Path(os.environ["GITHUB_APP_PRIVATE_KEY_PATH"]).read_text()

# GCP
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", None)
