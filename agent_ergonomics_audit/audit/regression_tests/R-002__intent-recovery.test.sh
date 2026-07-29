#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo"
PYTHONPATH=python python3 -m inkplate_dev_console | rg -q 'Fast paths:'
set +e
hint="$(PYTHONPATH=python python3 -m inkplate_dev_console sttae 2>&1)"
exit_code=$?
set -e
[[ "$exit_code" == "2" ]]
[[ "$hint" == *'inkplate-dev state --help'* ]]

