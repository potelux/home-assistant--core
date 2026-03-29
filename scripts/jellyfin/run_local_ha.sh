#!/usr/bin/env bash
# Start a local Home Assistant instance from this repo's source code,
# pointed at the ha-test-config directory.
#
# Because HA runs directly from src/core, any edits to
# homeassistant/components/jellyfin/ are picked up on the next HA restart —
# no custom_components copy or version injection needed.
#
# Usage:
#   ./scripts/jellyfin/run_local_ha.sh
#
# Optional env vars:
#   HA_CONFIG   Path to the HA config dir (default: ~/ha-test-config)
#   HA_PORT     HTTP port to listen on   (default: 8124)
#
# After startup, open: http://localhost:${HA_PORT}
# Logs stream to stdout. Ctrl-C to stop.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HA_CONFIG="${HA_CONFIG:-$HOME/ha-test-config}"
HA_PORT="${HA_PORT:-8124}"

if [[ ! -f "$REPO_ROOT/.venv/bin/hass" ]]; then
  echo "ERROR: .venv/bin/hass not found."
  echo "Run: pip install -e .[dev] inside the repo first." >&2
  exit 1
fi

if [[ ! -d "$HA_CONFIG" ]]; then
  echo "ERROR: Config dir not found: $HA_CONFIG" >&2
  exit 1
fi

BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
echo "=== Starting Home Assistant ==="
echo "  Branch : $BRANCH"
echo "  Config : $HA_CONFIG"
echo "  URL    : http://localhost:$HA_PORT"
echo ""
echo "After startup, add the Jellyfin integration via:"
echo "  Settings → Devices & Services → Add Integration → Jellyfin"
echo ""

cd "$REPO_ROOT"

# Patch the http port in configuration.yaml if HA_PORT != 8123
if [[ "$HA_PORT" != "8123" ]]; then
  if ! grep -q "^http:" "$HA_CONFIG/configuration.yaml"; then
    printf '\nhttp:\n  server_port: %s\n' "$HA_PORT" >> "$HA_CONFIG/configuration.yaml"
    echo "Set HTTP port to $HA_PORT in configuration.yaml"
  fi
fi

.venv/bin/hass \
  --config "$HA_CONFIG" \
  --log-rotate-days 1
