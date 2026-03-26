"""Expose regular shell commands as services."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
import logging
import re
import shlex
from typing import Any

import voluptuous as vol

import homeassistant.config as conf_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, TemplateError
from homeassistant.helpers import (
    config_validation as cv,
    service as service_helper,
    template,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.json import JsonObjectType

DOMAIN = "shell_command"
CONF_COMMAND = "command"
COMMAND_TIMEOUT = 60

_LOGGER = logging.getLogger(__name__)

# Matches simple {{ variable_name }} Jinja2 expressions
_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: cv.schema_with_slug_keys(cv.string)}, extra=vol.ALLOW_EXTRA
)


async def _async_execute_command(
    hass: HomeAssistant,
    cache: dict[str, tuple[str, str | None, template.Template | None]],
    cmd: str,
    service: ServiceCall,
) -> ServiceResponse:
    """Execute a shell command and return optional response data."""
    if cmd in cache:
        prog, args, args_compiled = cache[cmd]
    elif " " not in cmd:
        prog = cmd
        args = None
        args_compiled = None
        cache[cmd] = prog, args, args_compiled
    else:
        prog, args = cmd.split(" ", 1)
        args_compiled = template.Template(str(args), hass)
        cache[cmd] = prog, args, args_compiled

    if args_compiled:
        try:
            rendered_args = args_compiled.async_render(
                variables=service.data, parse_result=False
            )
        except TemplateError:
            _LOGGER.exception("Error rendering command template")
            raise
    else:
        rendered_args = None

    if rendered_args == args:
        # No template used. default behavior
        create_process = asyncio.create_subprocess_shell(
            cmd,
            stdin=None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=False,  # required for posix_spawn
        )
    else:
        # Template used. Break into list and use create_subprocess_exec
        # (which uses shell=False) for security
        shlexed_cmd = [prog, *shlex.split(rendered_args)]
        create_process = asyncio.create_subprocess_exec(
            *shlexed_cmd,
            stdin=None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=False,  # required for posix_spawn
        )

    process = await create_process
    try:
        async with asyncio.timeout(COMMAND_TIMEOUT):
            stdout_data, stderr_data = await process.communicate()
    except TimeoutError as err:
        _LOGGER.error(
            "Timed out running command: `%s`, after: %ss", cmd, COMMAND_TIMEOUT
        )
        if process:
            with suppress(TypeError):
                process.kill()
                # https://bugs.python.org/issue43884
                process._transport.close()  # type: ignore[attr-defined]  # noqa: SLF001
            del process

        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="timeout",
            translation_placeholders={
                "command": cmd,
                "timeout": str(COMMAND_TIMEOUT),
            },
        ) from err

    if stdout_data:
        _LOGGER.debug(
            "Stdout of command: `%s`, return code: %s:\n%s",
            cmd,
            process.returncode,
            stdout_data,
        )
    if stderr_data:
        _LOGGER.debug(
            "Stderr of command: `%s`, return code: %s:\n%s",
            cmd,
            process.returncode,
            stderr_data,
        )
    if process.returncode != 0:
        _LOGGER.exception(
            "Error running command: `%s`, return code: %s", cmd, process.returncode
        )

    if service.return_response:
        service_response: JsonObjectType = {
            "stdout": "",
            "stderr": "",
            "returncode": process.returncode,
        }
        try:
            if stdout_data:
                service_response["stdout"] = stdout_data.decode("utf-8").strip()
            if stderr_data:
                service_response["stderr"] = stderr_data.decode("utf-8").strip()
        except UnicodeDecodeError as err:
            _LOGGER.exception("Unable to handle non-utf8 output of command: `%s`", cmd)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="non_utf8_output",
                translation_placeholders={"command": cmd},
            ) from err
        return service_response
    return None


def _make_handler(
    cmd: str,
    hass: HomeAssistant,
    cache: dict[str, tuple[str, str | None, template.Template | None]],
) -> Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]]:
    """Return a service handler that executes the given shell command."""

    async def async_service_handler(service: ServiceCall) -> ServiceResponse:
        return await _async_execute_command(hass, cache, cmd, service)

    return async_service_handler


def _extract_template_variables(cmd: str) -> list[str]:
    """Return deduplicated template variable names found in a command string."""
    return list(dict.fromkeys(_TEMPLATE_VAR_RE.findall(cmd)))


def _register_service_fields(
    hass: HomeAssistant, service_name: str, title: str, cmd: str
) -> None:
    """Register service field descriptions for detected template variables."""
    variables = _extract_template_variables(cmd)
    service_helper.async_set_service_schema(
        hass,
        DOMAIN,
        service_name,
        {
            "name": title,
            "description": cmd,
            "fields": {
                var: {
                    "name": var,
                    "description": f"Value for `{{{{{var}}}}}`",
                    "required": False,
                    "selector": {"text": {}},
                }
                for var in variables
            },
        },
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the shell_command component (handles YAML-configured commands)."""
    conf = config.get(DOMAIN, {})

    # Shared template cache across YAML and config-entry handlers.
    hass.data.setdefault(DOMAIN, {})
    cache: dict[str, tuple[str, str | None, template.Template | None]] = {}
    hass.data[DOMAIN]["cache"] = cache

    for name, command in conf.items():
        hass.services.async_register(
            DOMAIN,
            name,
            _make_handler(command, hass, cache),
            supports_response=SupportsResponse.OPTIONAL,
        )

    async def reload_service_handler(service_call: ServiceCall) -> None:
        """Reload shell_command from YAML configuration."""
        for svc in list(hass.services.async_services_for_domain(DOMAIN)):
            if svc != SERVICE_RELOAD:
                hass.services.async_remove(DOMAIN, svc)
        cache.clear()

        try:
            raw_config = await conf_util.async_hass_config_yaml(hass)
        except HomeAssistantError as err:
            _LOGGER.error("Error loading configuration.yaml: %s", err)
            return

        new_conf = CONFIG_SCHEMA(raw_config).get(DOMAIN, {})
        for name, command in new_conf.items():
            hass.services.async_register(
                DOMAIN,
                name,
                _make_handler(command, hass, cache),
                supports_response=SupportsResponse.OPTIONAL,
            )

        # Re-register config-entry services that were removed above.
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.unique_id and not hass.services.has_service(
                DOMAIN, entry.unique_id
            ):
                await async_setup_entry(hass, entry)

    service_helper.async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        reload_service_handler,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a shell command from a config entry."""
    assert entry.unique_id is not None
    hass.data.setdefault(DOMAIN, {})
    cache: dict[str, tuple[str, str | None, template.Template | None]] = hass.data[
        DOMAIN
    ].setdefault("cache", {})
    service_name = entry.unique_id

    async def async_service_handler(service: ServiceCall) -> ServiceResponse:
        cmd = entry.options.get(CONF_COMMAND) or entry.data[CONF_COMMAND]
        return await _async_execute_command(hass, cache, cmd, service)

    hass.services.async_register(
        DOMAIN,
        service_name,
        async_service_handler,
        supports_response=SupportsResponse.OPTIONAL,
    )

    cmd = entry.options.get(CONF_COMMAND) or entry.data[CONF_COMMAND]
    _register_service_fields(hass, service_name, entry.title, cmd)

    async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a shell command config entry."""
    assert entry.unique_id is not None
    hass.services.async_remove(DOMAIN, entry.unique_id)
    return True
