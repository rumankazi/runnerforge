#!/bin/bash
# Smoke test for runner image — runs as VM startup script.
# Output goes to the serial console; the workflow grep's for SMOKE_OK / SMOKE_NOK.

set -euo pipefail

RUNNER_DIR=/home/runner/actions-runner
reasons=""

# Keep in sync with step 2b of infra/packer/scripts/install-runner.sh.
ESSENTIAL_PACKAGES=(
  libatomic1 unzip zstd xz-utils bzip2 jq python3 python3-venv
  build-essential git-lfs openssh-client gnupg
)
# Shared libraries prebuilt toolchains and the runner itself load.
ESSENTIAL_LIBS=(libatomic.so.1 libstdc++.so.6 libgcc_s.so.1 libz.so.1 libssl.so.3)
# Commands a job must find on PATH as `runner`.
ESSENTIAL_COMMANDS=(
  unzip zstd xz bzip2 jq python3 cc gcc g++ make git git-lfs ssh gpg curl docker
)
# Official Node builds are what broke (libatomic.so.1); run the newest v26.
NODE_DIST=https://nodejs.org/dist/latest-v26.x

as_runner() {
  sudo -u runner -H bash -c "$1" 2>&1
}

# Check run.sh exists (the entry point invoked by the runner)
if [[ ! -f "$RUNNER_DIR/run.sh" ]]; then
  reasons+="- Could not locate ${RUNNER_DIR}/run.sh\n"
fi

# Check config.sh exists AND is executable. Only attempt to run it if both pass.
if [[ ! -f "$RUNNER_DIR/config.sh" ]]; then
  reasons+="- Could not locate ${RUNNER_DIR}/config.sh\n"
elif ! config_output=$(sudo -u runner "$RUNNER_DIR/config.sh" --help 2>&1); then
  reasons+="- Failed to execute ${RUNNER_DIR}/config.sh: ${config_output}\n"
fi

# --- Essential packages, libraries and commands ---
for pkg in "${ESSENTIAL_PACKAGES[@]}"; do
  if ! dpkg -s "$pkg" > /dev/null 2>&1; then
    reasons+="- Package not installed: ${pkg}\n"
  fi
done

ldconfig_cache=$(ldconfig -p)
for lib in "${ESSENTIAL_LIBS[@]}"; do
  if ! grep -qF "$lib" <<< "$ldconfig_cache"; then
    reasons+="- Shared library missing: ${lib}\n"
  fi
done

for cmd in "${ESSENTIAL_COMMANDS[@]}"; do
  if ! as_runner "command -v $cmd" > /dev/null; then
    reasons+="- Command not on runner's PATH: ${cmd}\n"
  fi
done

# @actions/cache treats empty `zstd --quiet --version` output as "no zstd".
if [[ -z "$(as_runner 'zstd --quiet --version' || true)" ]]; then
  reasons+="- zstd --quiet --version printed nothing\n"
fi

# --- Behaviour, as runner ---
if ! out=$(as_runner 'docker compose version'); then
  reasons+="- docker compose version failed: ${out}\n"
fi

if ! out=$(as_runner 'git lfs version'); then
  reasons+="- git lfs version failed: ${out}\n"
fi

if ! out=$(as_runner 'd=$(mktemp -d) && python3 -m venv "$d/v" && "$d/v/bin/pip" --version; rc=$?; rm -rf "$d"; exit $rc'); then
  reasons+="- python3 -m venv with pip failed: ${out}\n"
fi

if ! out=$(as_runner 'd=$(mktemp -d) && printf "int main(void){return 0;}\n" | cc -x c - -o "$d/a" && "$d/a"; rc=$?; rm -rf "$d"; exit $rc'); then
  reasons+="- Compiling and running a C program failed: ${out}\n"
fi

# The real failure: download the official Node build, verify its checksum,
# and start it as runner. A network error is reported as such.
node_check='
set -euo pipefail
d=$(mktemp -d)
trap "rm -rf \"$d\"" EXIT
cd "$d"
curl --fail --silent --show-error --location --connect-timeout 10 --max-time 60 --retry 2 --retry-connrefused --output SHASUMS256.txt "'"$NODE_DIST"'/SHASUMS256.txt" \
  || { echo "download of SHASUMS256.txt failed"; exit 1; }
tarball=$(grep -oE "node-v[0-9.]+-linux-x64\.tar\.xz$" SHASUMS256.txt | head -n 1 || true)
[[ -n "$tarball" ]] || { echo "no linux-x64 tarball in SHASUMS256.txt"; exit 1; }
curl --fail --silent --show-error --location --connect-timeout 10 --max-time 60 --retry 2 --retry-connrefused --output "$tarball" "'"$NODE_DIST"'/$tarball" \
  || { echo "download of $tarball failed"; exit 1; }
grep " $tarball\$" SHASUMS256.txt | sha256sum --check --status \
  || { echo "checksum mismatch for $tarball"; exit 1; }
tar --extract --xz --file "$tarball"
"${tarball%.tar.xz}/bin/node" --version
'
# timeout keeps a stall inside the workflow's 5-minute serial poll, so it
# still ends in SMOKE_NOK with a reason.
if ! out=$(timeout 150 sudo -u runner -H bash -c "$node_check" 2>&1); then
  reasons+="- Official Node build (latest v26) didn't run: ${out:-timed out after 150 s}\n"
fi

if [[ -n "$reasons" ]]; then
  echo -e "SMOKE_NOK: \n${reasons}" > /dev/console
  exit 1
fi

echo "SMOKE_OK" > /dev/console
exit 0
