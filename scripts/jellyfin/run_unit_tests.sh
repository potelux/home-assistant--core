#!/usr/bin/env bash
# Run all Jellyfin integration unit tests locally.
# Usage: ./scripts/jellyfin/run_unit_tests.sh [pytest args]
#
# Examples:
#   ./scripts/jellyfin/run_unit_tests.sh
#   ./scripts/jellyfin/run_unit_tests.sh -k test_media_player
#   ./scripts/jellyfin/run_unit_tests.sh --snapshot-update   # regenerate snapshots

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f ".venv/bin/pytest" ]]; then
  echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt" >&2
  exit 1
fi

echo "=== Running Jellyfin unit tests ==="
.venv/bin/pytest tests/components/jellyfin/ -v "$@"
