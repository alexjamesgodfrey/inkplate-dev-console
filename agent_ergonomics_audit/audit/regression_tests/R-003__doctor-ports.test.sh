#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo"
PYTHONPATH=python python3 -m inkplate_dev_console ports --json |
  python3 -c 'import json,sys; value=json.load(sys.stdin); assert "selectionOrder" in value'
PYTHONPATH=python python3 -m unittest -q tests/test_ports.py

