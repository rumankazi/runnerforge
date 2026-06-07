#!/bin/bash
#
# GCE startup script for runnerforge-runner VMs.
# Embedded in instance metadata as `startup-script`; GCE runs it on every boot
# as root, before any user logs in.
#
# Responsibilities:
#   1. Format + mount the data disk at /home/runner/_work
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

# --- 3. Mount and chown ---
DATA_DISK_MOUNT_LOCATION="/home/runner/actions-runner/_work"
mkdir --parents "$DATA_DISK_MOUNT_LOCATION"
mount "$DATA_DISK_DEVICE" "$DATA_DISK_MOUNT_LOCATION"
# transfer ownership so that runner has permissions to execute the jobs/steps
chown --recursive runner:runner "$DATA_DISK_MOUNT_LOCATION"
log "Data disk mounted at $DATA_DISK_MOUNT_LOCATION"

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
INSTANCE_NAME=$(fetch_meta name)
RUNNER_LABELS=$(fetch_meta runner-labels)

log "Fetched metadata for repo $REPO_URL"

# --- 5. Configure + start runner as the `runner` user ---
# sudo -u runs the command as another user. We're root in this script,
# but the runner MUST run as the unprivileged 'runner' user.
# The runner runs in the foreground; the startup script will block here until
# the runner exits (--ephemeral means after one job).
RUNNER_DIR=/home/runner/actions-runner

log "Configuring runner"
sudo --user=runner bash -c "
    cd $RUNNER_DIR && \
    ./config.sh \
        --url '$REPO_URL' \
        --token '$REGISTRATION_TOKEN' \
        --name '$INSTANCE_NAME' \
        --labels '$RUNNER_LABELS' \
        --ephemeral \
        --unattended
"

log "Starting runner"
sudo --user=runner bash -c "cd $RUNNER_DIR && ./run.sh"

log "Runner exited"
