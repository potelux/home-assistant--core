"""Image proxy view for the Jellyfin integration.

Proxies item artwork from the Jellyfin server through Home Assistant's HTTP
server so that thumbnails work over HTTPS when HA is accessed remotely, even
if the Jellyfin server only speaks HTTP on the local network.
"""

from __future__ import annotations

from http import HTTPStatus
import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PROXY_URL = "/api/jellyfin_image_proxy/{entry_id}/{item_id}"
PROXY_VIEW_NAME = "api:jellyfin_image_proxy"


class JellyfinImageProxyView(HomeAssistantView):
    """Proxy Jellyfin item images through Home Assistant.

    This makes artwork URLs work when HA is accessed remotely over HTTPS
    even if the Jellyfin server is only reachable over HTTP on the local LAN.

    URL: GET /api/jellyfin_image_proxy/{entry_id}/{item_id}
         ?tag=<Primary|Backdrop|...>  (default: Primary)
         &max_width=<int>              (default: 500)
    """

    url = PROXY_URL
    name = PROXY_VIEW_NAME
    # No HA auth required – the image is fetched from Jellyfin using the
    # stored API key, so the artwork itself is not sensitive.  An HA auth
    # requirement would also break plain <img> tags in the media browser
    # because browsers do not send Authorization headers for image loads.
    requires_auth = False

    async def get(
        self, request: web.Request, entry_id: str, item_id: str
    ) -> web.Response:
        """Fetch and return a Jellyfin item image."""
        hass: HomeAssistant = request.app["hass"]

        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return web.Response(status=HTTPStatus.NOT_FOUND)

        if entry.state is not ConfigEntryState.LOADED:
            return web.Response(status=HTTPStatus.SERVICE_UNAVAILABLE)

        coordinator = entry.runtime_data

        image_tag = request.rel_url.query.get("tag", "Primary")
        try:
            max_width = int(request.rel_url.query.get("max_width", "500"))
        except ValueError:
            max_width = 500

        artwork_url: str = str(
            coordinator.api_client.jellyfin.artwork(item_id, image_tag, max_width)
        )

        session = async_get_clientsession(hass)
        try:
            async with session.get(artwork_url) as resp:
                if resp.status != HTTPStatus.OK:
                    _LOGGER.debug(
                        "Jellyfin returned %s for image %s/%s",
                        resp.status,
                        item_id,
                        image_tag,
                    )
                    return web.Response(status=resp.status)
                content_type = resp.content_type or "image/jpeg"
                data = await resp.read()
        except aiohttp.ClientError as err:
            _LOGGER.debug("Error fetching Jellyfin image %s: %s", item_id, err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)

        return web.Response(
            body=data,
            content_type=content_type,
            headers={"Cache-Control": "max-age=3600"},
        )
