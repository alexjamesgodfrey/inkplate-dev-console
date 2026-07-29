#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo"
PYTHONPATH=python python3 -m unittest -q \
  tests.test_cli.CliTests.test_snapshot_reuses_one_client_and_orders_state_before_frame

