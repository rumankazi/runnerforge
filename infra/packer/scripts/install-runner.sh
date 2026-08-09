#!/bin/bash
#
# Provisioning script for the runnerforge-runner Packer image.
# Run ONCE during `packer build` on the temporary build VM.
# Output is baked into the image; runs as root via `sudo bash`.
#
# Order of operations:
#   1. Create the unprivileged `runner` user.
#   2. Install system packages the runner depends on.
#   3. Download + extract the GitHub Actions runner binary.
#   4. Run the runner's own dependency installer (still root).
#   5. Hand off ownership of /home/runner to the runner user.
#   6. Install Docker for workflows that use it.

set -euo pipefail # exit on error, undefined var, pipe failure

# --- Pinned versions ---
RUNNER_VERSION="2.336.0" # check https://github.com/actions/runner/releases
RUNNER_ARCH="x64"        # or arm64

# --- 1. Create runner user ---
# --create-home creates /home/runner/ with default skeleton files
# --shell sets the default login shell (the runner expects bash)
useradd \
  --create-home \
  --shell /bin/bash \
  runner

# --append adds to listed groups WITHOUT replacing the user's existing groups.
# Without --append, --groups would REPLACE all groups — bad.
# TODO: revisit sudo membership — only some workflows need it; tighten if not needed.
# usermod \
#   --append \
#   --groups sudo \
#   runner

# --- 2. System dependencies the runner needs ---
# libicu-dev is required by the runner's .NET runtime.
# git is needed for actions/checkout.
apt-get update
apt-get install --yes \
  curl ca-certificates \
  git \
  libicu-dev

# --- 3. Download + extract the runner binary ---
RUNNER_DIR="/home/runner/actions-runner"
mkdir --parents "$RUNNER_DIR"
cd "$RUNNER_DIR"

RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
# curl flags:
#   --fail        : exit non-zero on HTTP errors (default writes error page to stdout)
#   --silent      : no progress bar
#   --show-error  : but still show errors (paired with --silent)
#   --location    : follow redirects (GitHub releases redirect to a CDN)
#   --output      : write to file
curl --fail --silent --show-error --location \
  --output runner.tar.gz \
  "$RUNNER_URL"

# tar flags:
#   --extract     : extract mode
#   --gzip        : auto-decompress gzip
#   --file        : input file
tar --extract --gzip --file runner.tar.gz
rm runner.tar.gz

# --- 4. Runner's own dependency installer (root required) ---
# This installs .NET system deps via apt. Must run as root.
./bin/installdependencies.sh

# --- 5. Transfer ownership ---
# Everything above ran as root → all files in /home/runner are owned by root.
# Hand them over to the runner user so the runner process can read/write them.
# --recursive applies to all files and subdirectories.
# `runner:runner` is "owner:group" — both are the user we created in step 1.
chown --recursive runner:runner /home/runner

# --- 6. Install Docker ---
# Convenience script is acceptable here because the image is immutable —
# we bake one specific Docker version per build and don't update in place.
curl --fail --silent --show-error --location https://get.docker.com | sh

# --- 7. Install Cloud Ops Agent ---
# Ships VM logs (journalctl + /var/log) and basic system metrics to
# Cloud Logging and Cloud Monitoring. Without this, our startup script's
# `logger -t startup-script` output lives only in the serial console
# and is lost when the VM is deleted.
# Note that this adds extra delay to the build time by 30-60s, acceptable.
curl --fail --silent --show-error --location \
    --remote-name \
    https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
bash add-google-cloud-ops-agent-repo.sh --also-install
rm add-google-cloud-ops-agent-repo.sh

# Allow the runner user to run docker without sudo.
usermod --append --groups docker runner

echo "Provisioning complete. Runner version: $RUNNER_VERSION"
