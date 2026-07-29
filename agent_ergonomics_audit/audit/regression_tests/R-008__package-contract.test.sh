#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo"
PYTHONPATH=python python3 -m unittest discover -s tests -q
grep -qx 'version=0.2.0' library.properties
grep -qx 'version = "0.2.0"' pyproject.toml
uv build >/dev/null
