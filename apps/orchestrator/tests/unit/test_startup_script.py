"""The startup script keeps the registration token off sudo's command line,
which sudo logs to the journal (ADR-020)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from runnerforge.config import STARTUP_SCRIPT

TOKEN = "ghr_SECRET_TOKEN"


@pytest.fixture
def ran(tmp_path: Path) -> Path:
    """Run the script's configure step with stubs: sudo records its own
    arguments, config.sh records the ones it got."""
    start = STARTUP_SCRIPT.index("TOKEN_FILE=")
    end = STARTUP_SCRIPT.index('log "Starting runner"')
    step = STARTUP_SCRIPT[start:end].replace(
        "/run/runnerforge-registration-token", str(tmp_path / "token")
    )
    config = tmp_path / "config.sh"
    config.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > {tmp_path}/config-argv\n')
    config.chmod(0o755)
    harness = f"""
set -euo pipefail
REGISTRATION_TOKEN={TOKEN}
REPO_URL=https://github.com/octo/repo
INSTANCE_NAME=runnerforge-1-2-1
RUNNER_LABELS=runnerforge,small
RUNNER_DIR={tmp_path}
log() {{ :; }}
install() {{ : > "${{@: -1}}"; chmod 600 "${{@: -1}}"; }}
sudo() {{ printf "%s\\n" "$@" >> {tmp_path}/sudo-argv; shift; "$@"; }}
{step}
# Before the EXIT trap: the file must be gone before run.sh starts the job.
if [ -e {tmp_path}/token ]; then : > {tmp_path}/token-left; fi
"""
    bash = shutil.which("bash")
    assert bash is not None
    subprocess.run([bash, "-c", harness], check=True)
    return tmp_path


def test_the_token_never_reaches_sudos_arguments(ran):
    argv = (ran / "sudo-argv").read_text()
    assert TOKEN not in argv
    assert f'--token "$(cat {ran}/token)"' in argv


def test_config_sh_still_gets_the_token(ran):
    args = (ran / "config-argv").read_text().splitlines()
    assert args[args.index("--token") + 1] == TOKEN
    assert args[args.index("--url") + 1] == "https://github.com/octo/repo"
    assert args[args.index("--name") + 1] == "runnerforge-1-2-1"


def test_the_token_file_is_gone_after_configuring(ran):
    assert not (ran / "token-left").exists()
    assert not (ran / "token").exists()
