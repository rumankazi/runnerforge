#!/bin/bash
# Smoke test for runner image — runs as VM startup script.
# Output goes to the serial console; the workflow grep's for SMOKE_OK / SMOKE_NOK.

set -euo pipefail

RUNNER_DIR=/home/runner/actions-runner
reasons=""

# Check run.sh exists (the entry point invoked by the runner)
if [[ ! -f "$RUNNER_DIR/run.sh" ]]; then
  reasons+="- Could not locate ${RUNNER_DIR}/run.sh\n"
fi

# Check config.sh exists AND is executable. Only attempt to run it if both pass.
if [[ ! -f "$RUNNER_DIR/config.sh" ]]; then
  reasons+="- Could not locate ${RUNNER_DIR}/config.sh\n"
elif ! config_output=$("$RUNNER_DIR/config.sh" --help 2>&1); then
  reasons+="- Failed to execute ${RUNNER_DIR}/config.sh: ${config_output}\n"
fi

if [[ -n "$reasons" ]]; then
  echo -e "SMOKE_NOK: ${reasons}" > /dev/console
  exit 1
fi

echo "SMOKE_OK" > /dev/console
exit 0
