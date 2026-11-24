#!/bin/bash
#
# GCE startup script for runnerforge-runner VMs.
# Embedded in instance metadata as `startup-script`; GCE runs it on every boot
# as root, before any user logs in.
#
# Responsibilities:
#   1. Format + mount the data disk, and put the runner's _work directory and
#      Docker's and containerd's storage on it (the boot disk is only 10 GB)
#   2. Fetch per-VM metadata (registration token, repo URL, runner labels)
#   3. Configure + start the GitHub Actions runner as the `runner` user

# -e: any command has a return non-zero exit causes the program to exit immediately
# -u: Reference to undefined variable causes the program to exit immediately
# -o pipefail: This setting prevents errors in a pipeline from being masked
set -euo pipefail

log() {
    echo "[startup] $*" | logger -t startup-script    # echo + syslog
}

# --- 1. Wait for data disk to appear ---
# Block devices can take a beat after VM boot. The GCE-provided stable symlink
# at /dev/disk/by-id/google-persistent-disk-1 is the second attached disk
# (the boot disk is google-persistent-disk-0).
DATA_DISK_DEVICE=/dev/disk/by-id/google-persistent-disk-1
for i in {1..10}; do # waits for 10 seconds, usually takes <1s
    if [ -b "$DATA_DISK_DEVICE" ]; then # -b stricter: ensures this is block device that's ready
        break
    fi
    log "Waiting for data disk... ($i/10)"
    sleep 1
done

if [ ! -b "$DATA_DISK_DEVICE" ]; then
    log "ERROR: data disk never appeared at $DATA_DISK_DEVICE"
    exit 1
fi

# --- 2. Format the data disk if not already formatted (idempotent) ---
# blkid prints filesystem info if a filesystem exists. Use this as a probe.
if ! blkid "$DATA_DISK_DEVICE" >/dev/null 2>&1; then
    log "Formatting data disk as ext4"
    mkfs.ext4 -F "$DATA_DISK_DEVICE"
else
    log "Data disk already formatted, skipping mkfs"
fi

# --- 3. Mount, and move the runner's and the container engine's storage ---
# The data disk is mounted at a neutral root, and three directories on it are
# bound onto the paths that fill up: the runner's _work, /var/lib/docker and
# /var/lib/containerd (Docker 29 keeps images in containerd's store). Bind
# mounts keep every default path, so no daemon config changes, and this works
# on any runner image.
DATA_ROOT=/mnt/runnerforge
WORK_DIR=/home/runner/actions-runner/_work
# containerd starts before this script and dockerd races it, so stop both
# (the socket first, so nothing reactivates dockerd) before their storage is
# swapped. Nothing is copied: no image ships pre-pulled images, and both
# start cleanly on an empty root. timeout: never bind under a live daemon.
timeout 60 systemctl stop docker.socket docker.service containerd.service
mkdir --parents "$DATA_ROOT"
mountpoint --quiet "$DATA_ROOT" || mount "$DATA_DISK_DEVICE" "$DATA_ROOT"
# Only work/ belongs to runner; the engine's directories stay root's, as
# owning them would be root-equivalent.
install --directory --owner=runner --group=runner --mode=0755 "$DATA_ROOT/work"
install --directory --owner=root --group=root --mode=0710 "$DATA_ROOT/docker"
install --directory --owner=root --group=root --mode=0711 "$DATA_ROOT/containerd"
bind_dir() {
    mkdir --parents "$2"
    mountpoint --quiet "$2" || mount --bind "$1" "$2"
}
bind_dir "$DATA_ROOT/work" "$WORK_DIR"
bind_dir "$DATA_ROOT/docker" /var/lib/docker
bind_dir "$DATA_ROOT/containerd" /var/lib/containerd
# --no-block: dockerd comes up while the runner configures. The socket queues
# early clients instead of refusing them.
systemctl start --no-block docker.socket docker.service
log "Data disk at $DATA_ROOT; _work, /var/lib/docker and /var/lib/containerd on it"

# --- 4. Fetch metadata ---
# GCE's metadata server requires the Metadata-Flavor header (security check
# that ensures requests come from inside the VM, not a relayed external request).
METADATA_URL="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
METADATA_HEADER="Metadata-Flavor: Google"

fetch_meta() {
    curl --fail --silent --show-error \
        --header "$METADATA_HEADER" \
        "$METADATA_URL/$1"
}
# Metadata is populated by handle_queued (Python) at VM creation time.
# GCE stores it on the VM; the metadata server serves it locally to us here.
REGISTRATION_TOKEN=$(fetch_meta registration-token)
REPO_URL=$(fetch_meta repo-url)
RUNNER_LABELS=$(fetch_meta runner-labels)

# Instance name is a top-level metadata field, not a custom attribute, so it
# lives at /instance/name (not /instance/attributes/name).
INSTANCE_NAME=$(curl --fail --silent --show-error \
    --header "$METADATA_HEADER" \
    "http://metadata.google.internal/computeMetadata/v1/instance/name")

log "Fetched metadata for repo $REPO_URL"

# --- 5. Configure + start runner as the `runner` user ---
# sudo -u runs the command as another user. We're root in this script,
# but the runner MUST run as the unprivileged 'runner' user.
# The runner runs in the foreground; the startup script will block here until
# the runner exits (--ephemeral means after one job).
RUNNER_DIR=/home/runner/actions-runner

# The token never goes on sudo's command line: sudo logs that line to the
# journal, which reaches Cloud Logging (ADR-020). config.sh reads it from a
# file only root and the runner user can read, removed once it's used.
# printf is a shell builtin, so the token isn't in any process's arguments.
TOKEN_FILE=/run/runnerforge-registration-token
trap 'rm -f "$TOKEN_FILE"' EXIT
install --mode=0600 --owner=runner --group=runner /dev/null "$TOKEN_FILE"
printf '%s' "$REGISTRATION_TOKEN" > "$TOKEN_FILE"
unset REGISTRATION_TOKEN

log "Configuring runner"
# \$(cat ...) is escaped, so this shell passes it through unexpanded and the
# runner user's shell reads the file.
sudo --user=runner bash -c "
    cd $RUNNER_DIR && \
    ./config.sh \
        --url '$REPO_URL' \
        --token \"\$(cat $TOKEN_FILE)\" \
        --name '$INSTANCE_NAME' \
        --labels '$RUNNER_LABELS' \
        --ephemeral \
        --unattended
"
rm -f "$TOKEN_FILE"

# Not fatal: jobs without containers still run. A clear line here beats a
# job failing on "Cannot connect to the Docker daemon" with no cause. Each
# probe is timed: docker.socket accepts connections a stalled dockerd never
# answers, and `docker info` has no deadline of its own.
for _ in {1..30}; do
    timeout 3 docker info >/dev/null 2>&1 && break
    sleep 1
done
timeout 3 docker info >/dev/null 2>&1 || log "WARNING: docker not ready; container jobs will fail"

log "Starting runner"
sudo --user=runner bash -c "cd $RUNNER_DIR && ./run.sh"

log "Runner exited"
