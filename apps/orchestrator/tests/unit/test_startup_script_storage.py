"""The startup script puts the runner's _work directory and the container
engine's storage on the 50 GB data disk, not the 10 GB boot disk, without
handing the engine's storage to the runner user."""

import shutil
import subprocess
from pathlib import Path

import pytest

from runnerforge.config import STARTUP_SCRIPT

STEP = STARTUP_SCRIPT[
    STARTUP_SCRIPT.index("# --- 3.") : STARTUP_SCRIPT.index("# --- 4.")
]


def _run(tmp_path: Path, *, stop_fails: bool = False) -> list[str]:
    """Run step 3 with every system call stubbed. Each stub appends one line
    to calls; mounts are remembered, so a second run sees them."""
    paths = {
        "/mnt/runnerforge": tmp_path / "data",
        "/home/runner/actions-runner/_work": tmp_path / "work",
        "/var/lib/docker": tmp_path / "docker",
        "/var/lib/containerd": tmp_path / "containerd",
    }
    step = STEP
    for real, fake in paths.items():
        step = step.replace(real, str(fake))
    calls = tmp_path / "calls"
    mounted = tmp_path / "mounted"
    mounted.touch()
    stop_rc = 1 if stop_fails else 0
    harness = f"""
set -euo pipefail
DATA_DISK_DEVICE=/dev/fake-data-disk
log() {{ :; }}
# GNU flags; the script only runs on Linux, the tests may not.
mkdir() {{ command mkdir -p "${{@: -1}}"; }}
timeout() {{ echo "timeout $1" >> {calls}; shift; "$@"; }}
systemctl() {{
    echo "systemctl $*" >> {calls}
    if [ "$1" = stop ]; then return {stop_rc}; fi
}}
mountpoint() {{ grep -qxF "${{@: -1}}" {mounted}; }}
mount() {{ echo "mount $*" >> {calls}; echo "${{@: -1}}" >> {mounted}; }}
install() {{ echo "install $*" >> {calls}; }}
chown() {{ echo "chown $*" >> {calls}; }}
{step}
"""
    bash = shutil.which("bash")
    assert bash is not None
    subprocess.run([bash, "-c", harness], check=not stop_fails)
    return calls.read_text().splitlines() if calls.exists() else []


@pytest.fixture
def calls(tmp_path: Path) -> tuple[Path, list[str]]:
    return tmp_path, _run(tmp_path)


def _index(calls: list[str], prefix: str) -> int:
    return next(i for i, c in enumerate(calls) if c.startswith(prefix))


def test_the_engine_is_stopped_before_its_storage_moves(calls):
    """containerd runs before the script starts, and dockerd races it:
    binding under a live daemon would hide its state from it."""
    _, log = calls
    stop = _index(log, "systemctl stop")
    assert log[stop] == "systemctl stop docker.socket docker.service containerd.service"
    assert log[stop - 1] == "timeout 60"
    first_mount = _index(log, "mount")
    assert stop < first_mount


def test_work_docker_and_containerd_land_on_the_data_disk(calls):
    tmp, log = calls
    mounts = [c for c in log if c.startswith("mount")]
    assert mounts == [
        f"mount /dev/fake-data-disk {tmp / 'data'}",
        f"mount --bind {tmp / 'data' / 'work'} {tmp / 'work'}",
        f"mount --bind {tmp / 'data' / 'docker'} {tmp / 'docker'}",
        f"mount --bind {tmp / 'data' / 'containerd'} {tmp / 'containerd'}",
    ]


def test_the_engine_starts_again_after_the_binds(calls):
    _, log = calls
    start = _index(log, "systemctl start")
    assert log[start] == "systemctl start --no-block docker.socket docker.service"
    last_mount = max(i for i, c in enumerate(log) if c.startswith("mount"))
    assert start > last_mount


def test_only_work_belongs_to_the_runner(calls):
    """Owning the engine's storage is root-equivalent: it must stay root's."""
    tmp, log = calls
    installs = [c for c in log if c.startswith("install")]
    owner_runner = "--owner=runner --group=runner --mode=0755"
    assert installs == [
        f"install --directory {owner_runner} {tmp / 'data' / 'work'}",
        f"install --directory --owner=root --group=root --mode=0710 {tmp / 'data' / 'docker'}",
        f"install --directory --owner=root --group=root --mode=0711 {tmp / 'data' / 'containerd'}",
    ]
    assert not [c for c in log if c.startswith("chown")]
    assert "--recursive" not in STEP


def test_the_disk_is_mounted_before_its_directories_are_made(calls):
    """Directories made before the data-root mount would land on the boot
    disk, hidden under the mount, and every bind would then fail."""
    tmp, log = calls
    data = tmp / "data"
    order = [c for c in log if c.startswith(("systemctl", "mount", "install"))]
    assert [c.split(" ", 2)[:2] for c in order] == [
        ["systemctl", "stop"],
        ["mount", "/dev/fake-data-disk"],
        ["install", "--directory"],
        ["install", "--directory"],
        ["install", "--directory"],
        ["mount", "--bind"],
        ["mount", "--bind"],
        ["mount", "--bind"],
        ["systemctl", "start"],
    ]
    assert order[1].endswith(str(data))


def test_a_second_run_mounts_nothing_again(tmp_path):
    _run(tmp_path)
    (tmp_path / "calls").unlink()
    again = _run(tmp_path)
    assert not [c for c in again if c.startswith("mount")]


def test_a_failed_stop_aborts_before_any_mount(tmp_path):
    log = _run(tmp_path, stop_fails=True)
    assert log[-1] == "systemctl stop docker.socket docker.service containerd.service"
    assert not [c for c in log if c.startswith("mount")]


WAIT = STARTUP_SCRIPT[
    STARTUP_SCRIPT.index("# Not fatal") : STARTUP_SCRIPT.index('log "Starting runner"')
]


@pytest.mark.parametrize("docker_rc", [0, 1, 124])
def test_the_docker_wait_always_lets_the_runner_start(tmp_path, docker_rc):
    """A dockerd that is down, or stalled behind its socket (timeout's 124),
    only costs a warning: the runner still starts for non-container jobs."""
    calls = tmp_path / "calls"
    harness = f"""
set -euo pipefail
log() {{ echo "log $*" >> {calls}; }}
sleep() {{ :; }}
timeout() {{ echo "timeout $1" >> {calls}; shift; "$@"; }}
docker() {{ return {docker_rc}; }}
{WAIT}
echo reached-runner >> {calls}
"""
    bash = shutil.which("bash")
    assert bash is not None
    subprocess.run([bash, "-c", harness], check=True)
    log = calls.read_text().splitlines()
    assert log[-1] == "reached-runner"
    probes = [c for c in log if c.startswith("timeout")]
    assert probes and all(c == "timeout 3" for c in probes)
    warned = any("WARNING: docker not ready" in c for c in log)
    assert warned is (docker_rc != 0)
    assert len(probes) == (2 if docker_rc == 0 else 31)
