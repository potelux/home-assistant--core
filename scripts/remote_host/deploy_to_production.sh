#!/usr/bin/env bash
# Deploy the remote-host addon management backend to a production Home Assistant instance.
#
# Supports HA OS / HA Container (via docker exec + docker cp) and HA Core (direct).
# Run from the HA host shell (Terminal & SSH add-on, or direct SSH into HA OS).
#
# Usage (paste into HA terminal):
#   curl -fsSL https://raw.githubusercontent.com/potelux/home-assistant--core/feature/remote-host-addon-management/scripts/remote_host/deploy_to_production.sh | bash
#
# After running: Settings → System → Restart → Restart Home Assistant

set -euo pipefail

BRANCH="feature/remote-host-addon-management"
REPO="potelux/home-assistant--core"
BASE_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/homeassistant/components/hassio"
CONTAINER="homeassistant"

echo "=== Deploying remote-host addon management backend ==="
echo "  Branch : $BRANCH"
echo ""

# Detect whether HA is running in a Docker container or installed locally
USE_DOCKER=false
if command -v docker &>/dev/null && docker inspect "$CONTAINER" &>/dev/null 2>&1; then
    USE_DOCKER=true
fi

# Find the hassio component directory
if [[ "$USE_DOCKER" == "true" ]]; then
    echo "  Mode   : Docker (container: $CONTAINER)"
    HASSIO_DIR=$(docker exec "$CONTAINER" python3 -c "
import importlib.util, os
spec = importlib.util.find_spec('homeassistant.components.hassio')
print(os.path.dirname(spec.origin))
")
else
    echo "  Mode   : Local Python"
    HASSIO_DIR=$(python3 -c "
import importlib.util, os
spec = importlib.util.find_spec('homeassistant.components.hassio')
print(os.path.dirname(spec.origin))
" 2>/dev/null || true)
fi

if [[ -z "$HASSIO_DIR" ]]; then
    echo "ERROR: Could not locate homeassistant.components.hassio"
    echo "  Tried Docker container '$CONTAINER' and local Python."
    echo "  If using a different container name, set: CONTAINER=<name> before running."
    exit 1
fi

echo "  Target : $HASSIO_DIR"
echo ""

FILES="__init__.py const.py websocket_api.py remote_host.py"

# Download files to a temp directory
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

FAILED=0
for f in $FILES; do
    if curl -fsSL "$BASE_URL/$f" -o "$TMPDIR/$f" 2>/dev/null; then
        echo "  downloaded: $f"
    else
        echo "  FAIL: $f"
        FAILED=$((FAILED + 1))
    fi
done

if [[ "$FAILED" -gt 0 ]]; then
    echo ""
    echo "ERROR: $FAILED file(s) failed to download. Check network and try again."
    exit 1
fi

echo ""

# Copy files into place
for f in $FILES; do
    if [[ "$USE_DOCKER" == "true" ]]; then
        docker cp "$TMPDIR/$f" "$CONTAINER:$HASSIO_DIR/$f"
    else
        cp "$TMPDIR/$f" "$HASSIO_DIR/$f"
    fi
    echo "  installed: $f"
done

echo ""
echo "=== Deploy complete ==="
echo ""
echo "IMPORTANT: full HA restart required to load new Python code:"
echo "  Settings → System → Restart → Restart Home Assistant"
