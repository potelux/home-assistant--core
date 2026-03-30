#!/usr/bin/env bash
# Deploy the remote-host addon management backend to a production Home Assistant instance.
#
# Patches homeassistant/components/hassio/ in-place with the new/modified files.
# Designed to run inside the HA host (Terminal & SSH add-on, or direct SSH to HA OS).
#
# Usage (paste into HA terminal):
#   curl -fsSL https://raw.githubusercontent.com/potelux/home-assistant--core/feature/remote-host-addon-management/scripts/remote_host/deploy_to_production.sh | bash
#
# After running: Settings → System → Restart → Restart Home Assistant

set -euo pipefail

BRANCH="feature/remote-host-addon-management"
REPO="potelux/home-assistant--core"
BASE_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/homeassistant/components/hassio"

echo "=== Deploying remote-host addon management backend ==="
echo "  Branch : $BRANCH"
echo ""

# Find where HA's hassio component lives
HASSIO_DIR=$(python3 - <<'EOF'
import importlib.util, os
spec = importlib.util.find_spec("homeassistant.components.hassio")
if spec and spec.origin:
    print(os.path.dirname(spec.origin))
EOF
)

if [[ -z "$HASSIO_DIR" ]]; then
    echo "ERROR: Could not locate homeassistant.components.hassio — is Home Assistant installed?"
    exit 1
fi

echo "  Target : $HASSIO_DIR"
echo ""

FILES="
__init__.py
const.py
websocket_api.py
remote_host.py
"

FAILED=0
for f in $FILES; do
    if curl -fsSL "$BASE_URL/$f" -o "$HASSIO_DIR/$f" 2>/dev/null; then
        echo "  ok: $f"
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
echo "=== Deploy complete ==="
echo ""
echo "IMPORTANT: full HA restart required to load new Python code:"
echo "  Settings → System → Restart → Restart Home Assistant"
