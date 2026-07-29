#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
consumer="${CHESSTHING_REPO:-"$repo/../chessthing"}"
[[ -f "$consumer/scripts/test-device-console-wrapper.sh" ]] || {
  printf 'error: set CHESSTHING_REPO to the chessthing checkout\n' >&2
  exit 3
}
bash "$consumer/scripts/test-device-console-wrapper.sh"

