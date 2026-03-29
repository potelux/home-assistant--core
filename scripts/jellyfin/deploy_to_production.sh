#!/usr/bin/env bash
# Deploy a Jellyfin integration branch to production Home Assistant.
#
# Designed for HA OS — paste this script directly into the HA SSH/Web terminal.
# Files are downloaded from GitHub, so no SSH or rsync from your dev machine needed.
#
# Usage (paste into HA terminal):
#   curl -fsSL https://raw.githubusercontent.com/potelux/home-assistant--core/personal/jellyfin-all-features/scripts/jellyfin/deploy_to_production.sh | bash
#
# After running: Settings → System → Restart → Restart Home Assistant
# (integration reload is NOT sufficient — Python modules only reload on full restart)

BRANCH="${BRANCH:-personal/jellyfin-all-features}"
REPO="potelux/home-assistant--core"
BASE_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/homeassistant/components/jellyfin"
DEST="/config/custom_components/jellyfin"

echo "=== Deploying Jellyfin integration ==="
echo "  Branch : $BRANCH"
echo "  Dest   : $DEST"
echo ""

mkdir -p "$DEST"

FILES="
__init__.py
browse_media.py
client_wrapper.py
config_flow.py
const.py
coordinator.py
diagnostics.py
entity.py
icons.json
image_proxy.py
manifest.json
media_player.py
media_source.py
remote.py
sensor.py
services.py
services.yaml
strings.json
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


if [[ "$FAILED" -gt 0 ]]; then
    echo ""
    echo "ERROR: $FAILED file(s) failed to download. Check network and try again."
    echo "NOT deploying — fix above errors and re-run."
else

# Inject version key (required for custom components, not present in core)
python3 - <<'EOF'
import json
path = "/config/custom_components/jellyfin/manifest.json"
m = json.load(open(path))
m["version"] = "0.0.1-dev"
json.dump(m, open(path, "w"), indent=2)
print(f"  manifest version: {m['version']}")
EOF

echo ""
echo "=== Deploy complete ==="
echo ""
echo "IMPORTANT: full HA restart required to load new Python code:"
echo "  Settings → System → Restart → Restart Home Assistant"
fi
