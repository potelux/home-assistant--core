#!/usr/bin/env bash
# Deploy the remote-host addon management backend to a production Home Assistant instance.
#
# Locates the hassio component directory and overwrites the relevant files.
# Does NOT interact with the running HA process — just places files so they
# are picked up on the next restart.
#
# Usage (paste into HA terminal):
#   curl -fsSL https://raw.githubusercontent.com/potelux/home-assistant--core/feature/remote-host-addon-management/scripts/remote_host/deploy_to_production.sh | bash
#
# After running: Settings → System → Restart → Restart Home Assistant

set -euo pipefail

BRANCH="feature/remote-host-addon-management"
REPO="potelux/home-assistant--core"
BASE_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/homeassistant/components/hassio"
CONTAINER="${CONTAINER:-homeassistant}"

echo "=== Deploying remote-host addon management backend ==="
echo "  Branch : $BRANCH"
echo ""

# --- Locate the hassio component directory ---
HASSIO_DIR="${HASSIO_DIR:-}"

if [[ -z "$HASSIO_DIR" ]]; then
    # Strategy 1: look for it in the Docker container's merged filesystem (HA OS / Container)
    if command -v docker &>/dev/null && docker inspect "$CONTAINER" &>/dev/null 2>&1; then
        ROOTFS=$(docker inspect "$CONTAINER" --format='{{.GraphDriver.Data.MergedDir}}' 2>/dev/null || true)
        if [[ -n "$ROOTFS" ]]; then
            HASSIO_DIR=$(find "$ROOTFS" -maxdepth 15 -type f \
                -name "websocket_api.py" -path "*/homeassistant/components/hassio/*" \
                2>/dev/null | head -1 | xargs -r dirname)
        fi
    fi
fi

if [[ -z "$HASSIO_DIR" ]]; then
    # Strategy 2: search common local install paths (HA Core in a venv)
    HASSIO_DIR=$(find /usr /home /srv /opt /root -maxdepth 15 -type f \
        -name "websocket_api.py" -path "*/homeassistant/components/hassio/*" \
        2>/dev/null | head -1 | xargs -r dirname || true)
fi

if [[ -z "$HASSIO_DIR" ]]; then
    echo "ERROR: Could not locate homeassistant/components/hassio/"
    echo ""
    echo "Set the path manually and retry:"
    echo "  HASSIO_DIR=/path/to/homeassistant/components/hassio \\"
    echo "    bash <(curl -fsSL $BASE_URL/../../../scripts/remote_host/deploy_to_production.sh)"
    exit 1
fi

echo "  Target : $HASSIO_DIR"
echo ""

FILES="__init__.py const.py websocket_api.py remote_host.py"

# Download and install each file
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

FAILED=0
for f in $FILES; do
    if curl -fsSL "$BASE_URL/$f" -o "$TMPDIR/$f" 2>/dev/null; then
        cp "$TMPDIR/$f" "$HASSIO_DIR/$f"
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
echo "Restart Home Assistant to apply: Settings → System → Restart → Restart Home Assistant"
