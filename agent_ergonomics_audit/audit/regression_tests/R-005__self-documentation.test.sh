#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo"
PYTHONPATH=python python3 -m inkplate_dev_console capabilities --json |
  python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["contractVersion"] == "1"; assert value["exitCodes"]["device"] == 4'
PYTHONPATH=python python3 -m inkplate_dev_console robot-docs guide | rg -q 'Canonical loop'

