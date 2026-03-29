#!/usr/bin/env bash
# Validate that the Jellyfin image proxy and related features work correctly
# against a live production Home Assistant instance over HTTPS.
#
# Prerequisites:
#   1. Deploy the integration:  ./scripts/jellyfin/deploy_to_production.sh
#   2. Set environment variables (see below)
#   3. Ensure curl and python3 are available locally
#
# Required environment variables:
#   HA_BASE_URL   Full HTTPS URL of your HA instance, e.g. https://ha.example.com
#   HA_TOKEN      Long-lived access token (Settings → Profile → Security → Long-Lived Access Tokens)
#
# Optional:
#   JELLYFIN_ENTRY_ID   Config entry ID (auto-detected if omitted)
#   ITEM_ID             Jellyfin item ID to use for proxy test (auto-detected from active sessions)
#
# Usage:
#   HA_BASE_URL=https://ha.example.com HA_TOKEN=eyJ... ./scripts/jellyfin/test_https_proxy.sh

set -euo pipefail

HA_BASE_URL="${HA_BASE_URL:?ERROR: HA_BASE_URL is required. Example: HA_BASE_URL=https://ha.example.com}"
HA_TOKEN="${HA_TOKEN:?ERROR: HA_TOKEN is required (long-lived access token from HA profile page)}"

PASS=0
FAIL=0

pass() { echo "  [PASS] $1"; ((PASS++)) || true; }
fail() { echo "  [FAIL] $1"; ((FAIL++)) || true; }
section() { echo ""; echo "=== $1 ==="; }

# ── Helper: call HA REST API ─────────────────────────────────────────────────
ha_api() {
  curl -sf \
    -H "Authorization: Bearer $HA_TOKEN" \
    -H "Content-Type: application/json" \
    "$HA_BASE_URL/api/$1"
}

ha_post() {
  local path="$1"; shift
  curl -sf -X POST \
    -H "Authorization: Bearer $HA_TOKEN" \
    -H "Content-Type: application/json" \
    --data "${1:-{\}}" \
    "$HA_BASE_URL/api/$path"
}

# ── 1. Connectivity ───────────────────────────────────────────────────────────
section "1. Home Assistant Connectivity"

if ha_api "" > /dev/null 2>&1; then
  pass "HA API is reachable at $HA_BASE_URL"
else
  fail "Cannot reach HA API at $HA_BASE_URL — check URL and token"
  echo "Aborting remaining tests."
  exit 1
fi

# ── 2. Jellyfin integration loaded ───────────────────────────────────────────
section "2. Jellyfin Integration"

ENTRIES=$(ha_api "config/config_entries/entry")
JELLYFIN_ENTRY=$(echo "$ENTRIES" | python3 -c "
import json, sys
entries = json.load(sys.stdin)
j = [e for e in entries if e['domain'] == 'jellyfin']
print(json.dumps(j[0]) if j else 'null')
")

if [[ "$JELLYFIN_ENTRY" == "null" ]]; then
  fail "No Jellyfin config entry found — is the integration added?"
  exit 1
fi

ENTRY_ID="${JELLYFIN_ENTRY_ID:-$(echo "$JELLYFIN_ENTRY" | python3 -c "import json,sys; print(json.load(sys.stdin)['entry_id'])")}"
ENTRY_STATE=$(echo "$JELLYFIN_ENTRY" | python3 -c "import json,sys; print(json.load(sys.stdin)['state'])")

if [[ "$ENTRY_STATE" == "loaded" ]]; then
  pass "Jellyfin integration is loaded (entry_id: $ENTRY_ID)"
else
  fail "Jellyfin integration state: $ENTRY_STATE (expected: loaded)"
fi

# ── 3. Image Proxy (HTTPS mixed-content fix) ──────────────────────────────────
section "3. Image Proxy — GET /api/jellyfin_image_proxy/{entry_id}/{item_id}"

# Try to find an item_id from active media player state
if [[ -z "${ITEM_ID:-}" ]]; then
  ITEM_ID=$(ha_api "states" | python3 -c "
import json, sys
states = json.load(sys.stdin)
for s in states:
    if s['entity_id'].startswith('media_player.') and \
       s['attributes'].get('media_content_id') and \
       'jellyfin' in s['entity_id']:
        print(s['attributes']['media_content_id'])
        break
else:
    print('')
" 2>/dev/null || true)
fi

if [[ -z "${ITEM_ID:-}" ]]; then
  # Fall back to a known item from the library (use a static test item ID if known)
  echo "  INFO: No active Jellyfin session found. Set ITEM_ID=<jellyfin-item-uuid> to test image proxy."
  echo "        To find an item ID: browse Jellyfin, right-click → Get Info, or check HA media player state."
else
  PROXY_URL="$HA_BASE_URL/api/jellyfin_image_proxy/$ENTRY_ID/$ITEM_ID"
  HTTP_STATUS=$(curl -so /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $HA_TOKEN" \
    "$PROXY_URL" || echo "000")

  if [[ "$HTTP_STATUS" == "200" ]]; then
    pass "Image proxy returned 200 for item $ITEM_ID"
  elif [[ "$HTTP_STATUS" == "404" ]]; then
    fail "Image proxy returned 404 — item not found or proxy not registered (check logs)"
  else
    fail "Image proxy returned HTTP $HTTP_STATUS for $PROXY_URL"
  fi

  # Verify no auth is required for plain image loads (mimics browser <img> tag)
  HTTP_NOAUTH=$(curl -so /dev/null -w "%{http_code}" "$PROXY_URL" || echo "000")
  if [[ "$HTTP_NOAUTH" == "200" ]]; then
    pass "Image proxy is accessible without auth (requires_auth=False working)"
  else
    fail "Image proxy requires auth (got $HTTP_NOAUTH without token) — <img> tags will fail in browser"
  fi

  # Confirm the URL is same-origin HTTPS (not HTTP) — no mixed content
  if [[ "$PROXY_URL" == https://* ]]; then
    pass "Proxy URL is HTTPS — no mixed-content blocking"
  else
    fail "Proxy URL is not HTTPS: $PROXY_URL"
  fi

  # Confirm Cache-Control header is present
  CACHE_HEADER=$(curl -sI \
    -H "Authorization: Bearer $HA_TOKEN" \
    "$PROXY_URL" | grep -i "cache-control" || true)
  if echo "$CACHE_HEADER" | grep -qi "max-age"; then
    pass "Cache-Control header present: $(echo "$CACHE_HEADER" | tr -d '\r\n')"
  else
    fail "Cache-Control header missing — images will not be cached by the browser"
  fi
fi

# ── 4. Media Player Entities ──────────────────────────────────────────────────
section "4. Media Player Entities"

PLAYERS=$(ha_api "states" | python3 -c "
import json, sys
states = json.load(sys.stdin)
players = [s for s in states if s['entity_id'].startswith('media_player.') and 'jellyfin' in s['entity_id'].lower()]
print(json.dumps(players))
")

PLAYER_COUNT=$(echo "$PLAYERS" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
if [[ "$PLAYER_COUNT" -gt 0 ]]; then
  pass "Found $PLAYER_COUNT Jellyfin media player(s)"
  echo "$PLAYERS" | python3 -c "
import json, sys
for p in json.load(sys.stdin):
    print(f\"    {p['entity_id']}: {p['state']}\")
"
else
  fail "No Jellyfin media player entities found"
fi

# ── 5. Server Media Player (persistent, always present) ───────────────────────
section "5. Server Media Player — browse/search without active session"

SERVER_PLAYER=$(ha_api "states" | python3 -c "
import json, sys
states = json.load(sys.stdin)
# Server player unique_id ends with '-server-player'; entity_id is typically media_player.jellyfin
for s in states:
    if 'jellyfin' in s['entity_id'] and s['entity_id'].startswith('media_player.'):
        attrs = s['attributes']
        # Server player has no device_class and supports browse/search
        features = attrs.get('supported_features', 0)
        # BROWSE_MEDIA=2048, SEARCH_MEDIA=131072
        if features & 2048 and features & 131072 and not (features & 1):  # no PAUSE
            print(json.dumps(s))
            break
else:
    print('null')
")

if [[ "$SERVER_PLAYER" != "null" ]]; then
  SERVER_ENTITY=$(echo "$SERVER_PLAYER" | python3 -c "import json,sys; print(json.load(sys.stdin)['entity_id'])")
  SERVER_STATE=$(echo "$SERVER_PLAYER" | python3 -c "import json,sys; print(json.load(sys.stdin)['state'])")
  pass "Server media player found: $SERVER_ENTITY (state: $SERVER_STATE)"

  # State should be idle (server reachable) or off (server unreachable)
  if [[ "$SERVER_STATE" == "idle" || "$SERVER_STATE" == "off" ]]; then
    pass "Server player state is valid: $SERVER_STATE"
  else
    fail "Unexpected server player state: $SERVER_STATE (expected idle or off)"
  fi
else
  echo "  INFO: Could not auto-detect server media player (heuristic based on supported_features)."
  echo "        Check manually: look for a Jellyfin media_player entity that persists even without active sessions."
fi

# ── 6. Device Persistence (offline entities show OFF, not unavailable) ─────────
section "6. Persistent Device Entities"

ALL_JELLYFIN=$(ha_api "states" | python3 -c "
import json, sys
states = json.load(sys.stdin)
j = [s for s in states if 'jellyfin' in s['entity_id']]
print(json.dumps(j))
")

OFF_COUNT=$(echo "$ALL_JELLYFIN" | python3 -c "
import json, sys
states = json.load(sys.stdin)
off = [s for s in states if s['state'] == 'off' and s['entity_id'].startswith('media_player.')]
print(len(off))
")

UNAVAIL_COUNT=$(echo "$ALL_JELLYFIN" | python3 -c "
import json, sys
states = json.load(sys.stdin)
u = [s for s in states if s['state'] == 'unavailable' and s['entity_id'].startswith('media_player.')]
for s in u: print(f\"    UNAVAILABLE: {s['entity_id']}\", flush=True)
print(len(u))
" | tail -1)

if [[ "$UNAVAIL_COUNT" -eq 0 ]]; then
  pass "No Jellyfin media players are in 'unavailable' state (offline devices show OFF)"
else
  fail "$UNAVAIL_COUNT Jellyfin media player(s) are 'unavailable' — expected 'off' for offline devices"
fi

if [[ "$OFF_COUNT" -gt 0 ]]; then
  pass "$OFF_COUNT offline device(s) correctly showing state: off"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
section "Results"
TOTAL=$((PASS + FAIL))
echo "  Passed: $PASS / $TOTAL"
echo "  Failed: $FAIL / $TOTAL"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  echo "Some tests failed. Check HA logs:"
  echo "  ssh root@\${SSH_HOST} 'grep -i jellyfin /config/home-assistant.log | tail -50'"
  exit 1
fi

echo "All tests passed."
