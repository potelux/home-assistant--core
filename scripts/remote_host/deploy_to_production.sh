#!/usr/bin/env bash
# Deploy the remote-host addon management backend to a production Home Assistant instance.
#
# Downloads all hassio component files into /config/custom_components/hassio/
# so they are picked up on the next restart.
#
# Usage (paste into HA terminal):
#   curl -fsSL https://raw.githubusercontent.com/potelux/home-assistant--core/feature/remote-host-addon-management/scripts/remote_host/deploy_to_production.sh | bash
#
# After running: Settings → System → Restart → Restart Home Assistant

set -euo pipefail

BRANCH="feature/remote-host-addon-management"
REPO="potelux/home-assistant--core"
BASE_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/homeassistant/components/hassio"
DEST="/config/custom_components/hassio"

echo "=== Deploying remote-host addon management ==="
echo "  Branch : $BRANCH"
echo "  Dest   : $DEST"
echo ""

mkdir -p "$DEST"

FILES="
__init__.py
addon_manager.py
addon_panel.py
auth.py
backup.py
binary_sensor.py
config.py
config_flow.py
const.py
coordinator.py
diagnostics.py
discovery.py
entity.py
handler.py
http.py
icons.json
ingress.py
issues.py
jobs.py
manifest.json
remote_host.py
repairs.py
sensor.py
services.yaml
strings.json
switch.py
system_health.py
update.py
update_helper.py
websocket_api.py
"

FAILED=0
for f in $FILES; do
    if curl -fsSL "$BASE_URL/$f" -o "$DEST/$f" 2>/dev/null; then
        echo "  ok: $f"
    else
        echo "  FAIL: $f"
        FAILED=$((FAILED + 1))
    fi
done

# Download translations
mkdir -p "$DEST/translations"
for lang in en; do
    if curl -fsSL "$BASE_URL/translations/$lang.json" -o "$DEST/translations/$lang.json" 2>/dev/null; then
        echo "  ok: translations/$lang.json"
    fi
done

if [[ "$FAILED" -gt 0 ]]; then
    echo ""
    echo "ERROR: $FAILED file(s) failed to download. Check network and try again."
    exit 1
fi

# Inject version (required for custom components)
python3 - <<'EOF'
import json
path = "/config/custom_components/hassio/manifest.json"
m = json.load(open(path))
m["version"] = "0.0.1-dev"
json.dump(m, open(path, "w"), indent=2)
print(f"  manifest version: {m['version']}")
EOF

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Restart Home Assistant to apply: Settings → System → Restart → Restart Home Assistant"
