"""Config flow for Shell Command."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.util import slugify

from . import CONF_COMMAND, DOMAIN


def _service_name(name: str) -> str:
    """Return the slugified service name for a given display name."""
    return slugify(name)


class ShellCommandConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Shell Command."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            service_name = _service_name(user_input["name"])

            if not any(c.isalnum() for c in user_input["name"]):
                errors["name"] = "invalid_name"
            else:
                await self.async_set_unique_id(service_name)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=user_input["name"],
                    data={
                        "name": user_input["name"],
                        CONF_COMMAND: user_input[CONF_COMMAND],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): cv.string,
                    vol.Required(CONF_COMMAND): cv.string,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ShellCommandOptionsFlow:
        """Return the options flow."""
        return ShellCommandOptionsFlow()


class ShellCommandOptionsFlow(OptionsFlow):
    """Handle options for Shell Command (edit the command string)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_command = self.config_entry.options.get(
            CONF_COMMAND, self.config_entry.data.get(CONF_COMMAND, "")
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COMMAND, default=current_command): cv.string,
                }
            ),
        )
