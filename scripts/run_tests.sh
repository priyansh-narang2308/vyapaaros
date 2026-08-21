#!/usr/bin/env bash
# Test runner: pytest is not installed into .venv in this environment, so we
# assemble it from the uv wheel cache. Usage: scripts/run_tests.sh [pytest args]
set -uo pipefail
cd "$(dirname "$0")/.."
C="$HOME/.cache/uv/archive-v0"
PP="."
for p in pytest pluggy iniconfig packaging pytest_asyncio; do
  d=$(find "$C" -maxdepth 2 -name "$p" -type d 2>/dev/null | head -1)
  [ -n "$d" ] && PP="$PP:$(dirname "$d")"
done
export PYTHONPATH="$PP"
exec .venv/bin/python -m pytest "$@"
