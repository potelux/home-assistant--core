"""Mock remote Home Assistant server for local testing of remote host addon management.

Simulates the HA auth flow and Supervisor addon endpoints that RemoteHostManager calls.

Usage:
    .venv/bin/python script/mock_remote_ha.py [--port 8124]

Then in HA's developer tools WebSocket console, connect a remote host:
    {"id": 1, "type": "hassio/remote/connect",
     "url": "http://localhost:8124", "username": "admin", "password": "password"}
"""

import argparse
import json
import logging
import re
import uuid

import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_LOGGER = logging.getLogger("mock_remote_ha")

# Fake credentials accepted by this mock server
VALID_USERNAME = "admin"
VALID_PASSWORD = "password"

# Issued tokens (flow_id -> state, token -> valid)
_flows: dict[str, str] = {}
_tokens: set[str] = set()

FAKE_ADDONS = [
    {
        "slug": "core_mosquitto",
        "name": "Mosquitto broker",
        "description": "An Open Source MQTT broker",
        "version": "6.4.0",
        "version_latest": "6.4.0",
        "update_available": False,
        "state": "started",
        "repository": "core",
        "url": "https://github.com/home-assistant/addons/tree/master/mosquitto",
        "icon": False,
        "installed": True,
    },
    {
        "slug": "core_ssh",
        "name": "Advanced SSH & Web Terminal",
        "description": "A supercharged SSH & Web Terminal access to your Home Assistant",
        "version": "17.2.0",
        "version_latest": "17.2.0",
        "update_available": False,
        "state": "started",
        "repository": "core",
        "url": "https://github.com/hassio-addons/addon-ssh",
        "icon": False,
        "installed": True,
    },
    {
        "slug": "core_mariadb",
        "name": "MariaDB",
        "description": "An open source relational database",
        "version": "2.7.2",
        "version_latest": "2.7.3",
        "update_available": True,
        "state": "started",
        "repository": "core",
        "url": "https://github.com/home-assistant/addons/tree/master/mariadb",
        "icon": False,
        "installed": True,
    },
]

FAKE_ADDON_INFO = {
    addon["slug"]: {
        **addon,
        "hostname": addon["slug"].replace("_", "-"),
        "dns": [],
        "network": None,
        "options": {},
        "boot": "auto",
        "auto_update": True,
        "watchdog": False,
        "cpu_percent": round(0.1 + hash(addon["slug"]) % 30 / 10, 2),
        "memory_percent": round(0.5 + hash(addon["slug"]) % 50 / 10, 2),
    }
    for addon in FAKE_ADDONS
}

FAKE_LOGS = {
    "core_mosquitto": (
        "[2026-03-29 18:00:00] INFO Starting Mosquitto broker\n"
        "[2026-03-29 18:00:01] INFO Listening on port 1883\n"
        "[2026-03-29 18:00:01] INFO Listening on port 8883 (TLS)\n"
    ),
    "core_ssh": (
        "[2026-03-29 18:00:00] INFO Starting SSH server on port 22\n"
        "[2026-03-29 18:00:01] INFO Web terminal available at /api/hassio_ingress/\n"
    ),
    "core_mariadb": (
        "[2026-03-29 18:00:00] INFO Starting MariaDB\n"
        "[2026-03-29 18:00:01] INFO Ready for connections on port 3306\n"
    ),
}


def _require_auth(request: web.Request) -> str | None:
    """Return the token if the request is authenticated, else None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer ") :]
    return token if token in _tokens else None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


async def handle_login_flow_start(request: web.Request) -> web.Response:
    """POST /auth/login_flow — start a login flow."""
    flow_id = str(uuid.uuid4())
    _flows[flow_id] = "pending"
    _LOGGER.info("Login flow started: %s", flow_id)
    return web.json_response(
        {
            "flow_id": flow_id,
            "type": "form",
            "step_id": "init",
            "data_schema": [
                {"name": "username", "required": True},
                {"name": "password", "required": True, "type": "string"},
            ],
        }
    )


async def handle_login_flow_step(request: web.Request) -> web.Response:
    """POST /auth/login_flow/{flow_id} — submit credentials."""
    flow_id = request.match_info["flow_id"]
    if flow_id not in _flows:
        return web.json_response({"error": "invalid_flow"}, status=404)

    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")

    if username != VALID_USERNAME or password != VALID_PASSWORD:
        _LOGGER.warning("Bad credentials for flow %s (user=%r)", flow_id, username)
        return web.json_response(
            {"errors": {"base": "invalid_auth"}, "type": "form", "flow_id": flow_id},
            status=401,
        )

    token = f"mock-token-{uuid.uuid4().hex}"
    _tokens.add(token)
    del _flows[flow_id]
    _LOGGER.info("Login success, issued token for user %r", username)
    return web.json_response(
        {
            "type": "create_entry",
            "result": {"access_token": token},
            "flow_id": flow_id,
        }
    )


async def handle_token_delete(request: web.Request) -> web.Response:
    """DELETE /auth/token — revoke a token."""
    body = await request.json()
    token = body.get("token", "")
    _tokens.discard(token)
    _LOGGER.info("Token revoked")
    return web.json_response({"result": "ok"})


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


async def handle_api_root(request: web.Request) -> web.Response:
    """GET /api/ — return basic instance info (used for token verification)."""
    if not _require_auth(request):
        return web.json_response({"message": "Unauthorized"}, status=401)
    return web.json_response(
        {
            "message": "API running.",
            "location_name": "Mock Remote HA",
            "version": "2026.3.0",
            "config_dir": "/config",
        }
    )


# ---------------------------------------------------------------------------
# Supervisor addon endpoints (proxied via /api/hassio/...)
# ---------------------------------------------------------------------------


async def handle_addons_list(request: web.Request) -> web.Response:
    """GET /api/hassio/addons — return installed addon list."""
    if not _require_auth(request):
        return web.json_response({"message": "Unauthorized"}, status=401)
    return web.json_response({"result": "ok", "data": {"addons": FAKE_ADDONS}})


async def handle_addon_info(request: web.Request) -> web.Response:
    """GET /api/hassio/addons/{slug}/info — return addon details."""
    if not _require_auth(request):
        return web.json_response({"message": "Unauthorized"}, status=401)
    slug = request.match_info["slug"]
    if slug not in FAKE_ADDON_INFO:
        return web.json_response(
            {"result": "error", "message": f"Addon {slug!r} not found"}, status=404
        )
    return web.json_response({"result": "ok", "data": FAKE_ADDON_INFO[slug]})


async def handle_addon_logs(request: web.Request) -> web.Response:
    """GET /api/hassio/addons/{slug}/logs — return addon logs."""
    if not _require_auth(request):
        return web.Response(text="Unauthorized", status=401)
    slug = request.match_info["slug"]
    logs = FAKE_LOGS.get(slug, f"[mock] No logs available for addon '{slug}'\n")
    return web.Response(text=logs, content_type="text/plain")


# ---------------------------------------------------------------------------
# WebSocket endpoint (mirrors real HA's /api/websocket)
# ---------------------------------------------------------------------------

_ADDON_INFO_RE = re.compile(r"^/addons/([^/]+)/info$")


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    """GET /api/websocket — WebSocket endpoint for supervisor/api commands."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    await ws.send_json({"type": "auth_required", "ha_version": "2026.3.0"})

    auth_msg = await ws.receive_json()
    if auth_msg.get("type") != "auth" or auth_msg.get("access_token") not in _tokens:
        await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
        await ws.close()
        return ws

    await ws.send_json({"type": "auth_ok", "ha_version": "2026.3.0"})
    _LOGGER.info("WebSocket client authenticated")

    async for raw_msg in ws:
        if raw_msg.type == aiohttp.WSMsgType.TEXT:
            msg = json.loads(raw_msg.data)
            msg_id = msg.get("id", 1)
            msg_type = msg.get("type", "")

            if msg_type == "supervisor/api":
                endpoint = msg.get("endpoint", "")
                if endpoint == "/addons":
                    await ws.send_json(
                        {
                            "id": msg_id,
                            "type": "result",
                            "success": True,
                            "result": {"addons": FAKE_ADDONS, "repositories": []},
                        }
                    )
                elif m := _ADDON_INFO_RE.match(endpoint):
                    slug = m.group(1)
                    if slug in FAKE_ADDON_INFO:
                        await ws.send_json(
                            {
                                "id": msg_id,
                                "type": "result",
                                "success": True,
                                "result": FAKE_ADDON_INFO[slug],
                            }
                        )
                    else:
                        await ws.send_json(
                            {
                                "id": msg_id,
                                "type": "result",
                                "success": False,
                                "error": {
                                    "code": "unknown_error",
                                    "message": f"Addon {slug!r} not found",
                                },
                            }
                        )
                else:
                    await ws.send_json(
                        {
                            "id": msg_id,
                            "type": "result",
                            "success": False,
                            "error": {
                                "code": "unknown_command",
                                "message": f"Unknown endpoint: {endpoint}",
                            },
                        }
                    )
        elif raw_msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
            break

    return ws


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def build_app() -> web.Application:
    """Build and return the aiohttp application."""
    app = web.Application()
    app.router.add_post("/auth/login_flow", handle_login_flow_start)
    app.router.add_post("/auth/login_flow/{flow_id}", handle_login_flow_step)
    app.router.add_delete("/auth/token", handle_token_delete)
    app.router.add_get("/api/", handle_api_root)
    app.router.add_get("/api/websocket", handle_websocket)
    app.router.add_get("/api/hassio/addons/{slug}/logs", handle_addon_logs)
    return app


def main() -> None:
    """Start the mock remote HA server."""
    import sys  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Mock remote HA server")
    parser.add_argument("--port", type=int, default=8124, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    sys.stdout.write(
        f"\n{'=' * 60}\n"
        f"  Mock Remote HA server running at http://{args.host}:{args.port}\n"
        f"  Credentials: username=admin  password=password\n"
        f"  Addons: {', '.join(a['slug'] for a in FAKE_ADDONS)}\n"
        f"{'=' * 60}\n"
        f"  WebSocket commands to try in HA Dev Tools:\n\n"
        f"  Connect:\n"
        f'  {{"id":1,"type":"hassio/remote/connect",\n'
        f'   "url":"http://{args.host}:{args.port}",\n'
        f'   "username":"admin","password":"password"}}\n\n'
        f"  List hosts:\n"
        f'  {{"id":2,"type":"hassio/remote/hosts/list"}}\n\n'
        f"  List addons (use id from connect result):\n"
        f'  {{"id":3,"type":"hassio/remote/hosts/addons","host_id":"<id>"}}\n'
        f"{'=' * 60}\n"
    )
    sys.stdout.flush()

    web.run_app(build_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
