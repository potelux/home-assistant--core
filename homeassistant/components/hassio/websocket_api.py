"""Websocekt API handlers for the hassio integration."""

import logging
from numbers import Number
import re
from typing import Any, cast

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.const import ATTR_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)

from . import HassioAPIError
from .config import HassioUpdateParametersDict
from .const import (
    ATTR_DATA,
    ATTR_ENDPOINT,
    ATTR_METHOD,
    ATTR_PARAMS,
    ATTR_PASSWORD,
    ATTR_SESSION_DATA_USER_ID,
    ATTR_SLUG,
    ATTR_TIMEOUT,
    ATTR_URL,
    ATTR_USERNAME,
    ATTR_VERSION,
    ATTR_WS_EVENT,
    DATA_COMPONENT,
    DATA_CONFIG_STORE,
    DATA_REMOTE_HOST_MANAGER,
    EVENT_SUPERVISOR_EVENT,
    WS_ID,
    WS_TYPE,
    WS_TYPE_API,
    WS_TYPE_EVENT,
    WS_TYPE_SUBSCRIBE,
)
from .coordinator import get_addons_list
from .remote_host import (
    RemoteHostAuthError,
    RemoteHostConnectionError,
    RemoteHostError,
    RemoteHostNotFoundError,
)
from .update_helper import update_addon, update_core

SCHEMA_WEBSOCKET_EVENT = vol.Schema(
    {vol.Required(ATTR_WS_EVENT): cv.string},
    extra=vol.ALLOW_EXTRA,
)

# Endpoints needed for ingress can't require admin because addons can set `panel_admin: false`
# fmt: off
WS_NO_ADMIN_ENDPOINTS = re.compile(
    r"^(?:"
    r"|/ingress/(session|validate_session)"
    r"|/addons/[^/]+/info"
    r")$"
)
# fmt: on

_LOGGER: logging.Logger = logging.getLogger(__package__)


@callback
def async_load_websocket_api(hass: HomeAssistant) -> None:
    """Set up the websocket API."""
    websocket_api.async_register_command(hass, websocket_supervisor_event)
    websocket_api.async_register_command(hass, websocket_supervisor_api)
    websocket_api.async_register_command(hass, websocket_subscribe)
    websocket_api.async_register_command(hass, websocket_update_addon)
    websocket_api.async_register_command(hass, websocket_update_core)
    websocket_api.async_register_command(hass, websocket_update_config_info)
    websocket_api.async_register_command(hass, websocket_update_config_update)
    websocket_api.async_register_command(hass, websocket_remote_hosts_list)
    websocket_api.async_register_command(hass, websocket_remote_discover)
    websocket_api.async_register_command(hass, websocket_remote_connect)
    websocket_api.async_register_command(hass, websocket_remote_remove)
    websocket_api.async_register_command(hass, websocket_remote_ping)
    websocket_api.async_register_command(hass, websocket_remote_addons_list)
    websocket_api.async_register_command(hass, websocket_remote_addon_info)
    websocket_api.async_register_command(hass, websocket_remote_addon_logs)


@callback
@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required(WS_TYPE): WS_TYPE_SUBSCRIBE})
def websocket_subscribe(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Subscribe to supervisor events."""

    @callback
    def forward_messages(data: dict[str, str]) -> None:
        """Forward events to websocket."""
        connection.send_message(websocket_api.event_message(msg[WS_ID], data))

    connection.subscriptions[msg[WS_ID]] = async_dispatcher_connect(
        hass, EVENT_SUPERVISOR_EVENT, forward_messages
    )
    connection.send_message(websocket_api.result_message(msg[WS_ID]))


@callback
@websocket_api.websocket_command(
    {
        vol.Required(WS_TYPE): WS_TYPE_EVENT,
        vol.Required(ATTR_DATA): SCHEMA_WEBSOCKET_EVENT,
    }
)
def websocket_supervisor_event(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Publish events from the Supervisor."""
    connection.send_result(msg[WS_ID])
    async_dispatcher_send(hass, EVENT_SUPERVISOR_EVENT, msg[ATTR_DATA])


@websocket_api.websocket_command(
    {
        vol.Required(WS_TYPE): WS_TYPE_API,
        vol.Required(ATTR_ENDPOINT): cv.string,
        vol.Required(ATTR_METHOD): cv.string,
        vol.Optional(ATTR_DATA): dict,
        vol.Optional(ATTR_PARAMS): dict,
        vol.Optional(ATTR_TIMEOUT): vol.Any(Number, None),
    }
)
@websocket_api.async_response
async def websocket_supervisor_api(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Websocket handler to call Supervisor API."""
    if not connection.user.is_admin and not WS_NO_ADMIN_ENDPOINTS.match(
        msg[ATTR_ENDPOINT]
    ):
        raise Unauthorized
    supervisor = hass.data[DATA_COMPONENT]

    command = msg[ATTR_ENDPOINT]
    payload = msg.get(ATTR_DATA, {})

    if command == "/ingress/session":
        # Send user ID on session creation, so the supervisor can correlate session tokens with users
        # for every request that is authenticated with the given ingress session token.
        payload[ATTR_SESSION_DATA_USER_ID] = connection.user.id

    try:
        result = await supervisor.send_command(
            command,
            method=msg[ATTR_METHOD],
            timeout=msg.get(ATTR_TIMEOUT, 10),
            payload=payload,
            source="core.websocket_api",
            params=msg.get(ATTR_PARAMS),
        )
    except HassioAPIError as err:
        _LOGGER.error("Failed to to call %s - %s", msg[ATTR_ENDPOINT], err)
        connection.send_error(
            msg[WS_ID], code=websocket_api.ERR_UNKNOWN_ERROR, message=str(err)
        )
    else:
        connection.send_result(msg[WS_ID], result.get(ATTR_DATA, {}))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required(WS_TYPE): "hassio/update/addon",
        vol.Required("addon"): str,
        vol.Required("backup"): bool,
    }
)
@websocket_api.async_response
async def websocket_update_addon(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Websocket handler to update an addon."""
    addon_name: str | None = None
    addon_version: str | None = None
    addons_list: list[dict[str, Any]] = get_addons_list(hass) or []
    for addon in addons_list:
        if addon[ATTR_SLUG] == msg["addon"]:
            addon_name = addon[ATTR_NAME]
            addon_version = addon[ATTR_VERSION]
            break
    await update_addon(hass, msg["addon"], msg["backup"], addon_name, addon_version)
    connection.send_result(msg[WS_ID])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required(WS_TYPE): "hassio/update/core",
        vol.Required("backup"): bool,
    }
)
@websocket_api.async_response
async def websocket_update_core(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Websocket handler to update Home Assistant Core."""
    await update_core(hass, None, msg["backup"])
    connection.send_result(msg[WS_ID])


@callback
@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "hassio/update/config/info"})
def websocket_update_config_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send the stored backup config."""
    connection.send_result(
        msg["id"], hass.data[DATA_CONFIG_STORE].data.update_config.to_dict()
    )


@callback
@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassio/update/config/update",
        vol.Optional("add_on_backup_before_update"): bool,
        vol.Optional("add_on_backup_retain_copies"): vol.All(int, vol.Range(min=1)),
        vol.Optional("core_backup_before_update"): bool,
    }
)
def websocket_update_config_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update the stored backup config."""
    changes = dict(msg)
    changes.pop("id")
    changes.pop("type")
    hass.data[DATA_CONFIG_STORE].update(
        update_config=cast(HassioUpdateParametersDict, changes)
    )
    connection.send_result(msg["id"])


# ---------------------------------------------------------------------------
# Remote host management WebSocket commands
# ---------------------------------------------------------------------------


def _require_remote_manager(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg_id: int
) -> Any:
    """Return the RemoteHostManager or send an error and return None."""
    manager = hass.data.get(DATA_REMOTE_HOST_MANAGER)
    if manager is None:
        connection.send_error(
            msg_id,
            websocket_api.ERR_NOT_SUPPORTED,
            "Remote host management is not available",
        )
    return manager


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "hassio/remote/hosts/list"})
@callback
def websocket_remote_hosts_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the list of connected remote hosts."""
    manager = _require_remote_manager(hass, connection, msg[WS_ID])
    if manager is None:
        return
    connection.send_result(
        msg[WS_ID],
        {"hosts": [host.to_dict() for host in manager.get_hosts()]},
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "hassio/remote/discover"})
@websocket_api.async_response
async def websocket_remote_discover(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Discover Home Assistant instances on the local network via mDNS."""
    manager = _require_remote_manager(hass, connection, msg[WS_ID])
    if manager is None:
        return
    try:
        discovered = await manager.async_discover()
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Remote discovery failed: %s", err)
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNKNOWN_ERROR, str(err))
        return
    connection.send_result(msg[WS_ID], {"hosts": discovered})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassio/remote/connect",
        vol.Required(ATTR_URL): str,
        vol.Required(ATTR_USERNAME): str,
        vol.Required(ATTR_PASSWORD): str,
    }
)
@websocket_api.async_response
async def websocket_remote_connect(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Connect to a remote Home Assistant instance."""
    manager = _require_remote_manager(hass, connection, msg[WS_ID])
    if manager is None:
        return
    try:
        host = await manager.async_connect(
            msg[ATTR_URL], msg[ATTR_USERNAME], msg[ATTR_PASSWORD]
        )
    except RemoteHostAuthError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNAUTHORIZED, str(err))
        return
    except RemoteHostConnectionError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNKNOWN_ERROR, str(err))
        return
    except RemoteHostError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNKNOWN_ERROR, str(err))
        return
    connection.send_result(msg[WS_ID], host.to_dict())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassio/remote/hosts/remove",
        vol.Required("host_id"): str,
    }
)
@websocket_api.async_response
async def websocket_remote_remove(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove a connected remote host."""
    manager = _require_remote_manager(hass, connection, msg[WS_ID])
    if manager is None:
        return
    removed = await manager.async_remove_host(msg["host_id"])
    if not removed:
        connection.send_error(
            msg[WS_ID], websocket_api.ERR_NOT_FOUND, "Remote host not found"
        )
        return
    connection.send_result(msg[WS_ID], {})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassio/remote/hosts/ping",
        vol.Required("host_id"): str,
    }
)
@websocket_api.async_response
async def websocket_remote_ping(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Test connectivity to a remote host and update its status."""
    manager = _require_remote_manager(hass, connection, msg[WS_ID])
    if manager is None:
        return
    try:
        host = await manager.async_ping_host(msg["host_id"])
    except RemoteHostNotFoundError:
        connection.send_error(
            msg[WS_ID], websocket_api.ERR_NOT_FOUND, "Remote host not found"
        )
        return
    connection.send_result(msg[WS_ID], host.to_dict())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassio/remote/hosts/addons",
        vol.Required("host_id"): str,
    }
)
@websocket_api.async_response
async def websocket_remote_addons_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the list of addons installed on a remote host."""
    manager = _require_remote_manager(hass, connection, msg[WS_ID])
    if manager is None:
        return
    try:
        addons = await manager.async_get_remote_addons(msg["host_id"])
    except RemoteHostNotFoundError:
        connection.send_error(
            msg[WS_ID], websocket_api.ERR_NOT_FOUND, "Remote host not found"
        )
        return
    except RemoteHostAuthError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNAUTHORIZED, str(err))
        return
    except RemoteHostConnectionError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNKNOWN_ERROR, str(err))
        return
    connection.send_result(msg[WS_ID], {"addons": addons})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassio/remote/hosts/addon/info",
        vol.Required("host_id"): str,
        vol.Required(ATTR_SLUG): str,
    }
)
@websocket_api.async_response
async def websocket_remote_addon_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return detailed info for a specific addon on a remote host."""
    manager = _require_remote_manager(hass, connection, msg[WS_ID])
    if manager is None:
        return
    try:
        info = await manager.async_get_remote_addon_info(msg["host_id"], msg[ATTR_SLUG])
    except RemoteHostNotFoundError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_NOT_FOUND, str(err))
        return
    except RemoteHostAuthError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNAUTHORIZED, str(err))
        return
    except RemoteHostConnectionError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNKNOWN_ERROR, str(err))
        return
    connection.send_result(msg[WS_ID], info)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassio/remote/hosts/addon/logs",
        vol.Required("host_id"): str,
        vol.Required(ATTR_SLUG): str,
    }
)
@websocket_api.async_response
async def websocket_remote_addon_logs(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return logs for a specific addon on a remote host."""
    manager = _require_remote_manager(hass, connection, msg[WS_ID])
    if manager is None:
        return
    try:
        logs = await manager.async_get_remote_addon_logs(msg["host_id"], msg[ATTR_SLUG])
    except RemoteHostNotFoundError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_NOT_FOUND, str(err))
        return
    except RemoteHostAuthError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNAUTHORIZED, str(err))
        return
    except RemoteHostConnectionError as err:
        connection.send_error(msg[WS_ID], websocket_api.ERR_UNKNOWN_ERROR, str(err))
        return
    connection.send_result(msg[WS_ID], {"logs": logs})
