"""Tests for the Shell Command config flow."""

from __future__ import annotations

from homeassistant.components.shell_command import CONF_COMMAND, DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.setup import async_setup_component


async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    """Test a config entry is created via user step."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "say hello",
            CONF_COMMAND: "echo hello",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "say hello"
    assert result["data"][CONF_COMMAND] == "echo hello"

    # Service should be registered under the slugified name
    assert hass.services.has_service(DOMAIN, "say_hello")


async def test_config_flow_duplicate_aborts(hass: HomeAssistant) -> None:
    """Test that a duplicate service name is rejected."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    # Create first entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "my cmd", CONF_COMMAND: "echo first"},
    )
    await hass.async_block_till_done()

    # Attempt duplicate
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "my cmd", CONF_COMMAND: "echo second"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_config_flow_invalid_name(hass: HomeAssistant) -> None:
    """Test that an empty/invalid name shows an error."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "---", CONF_COMMAND: "echo hello"},
    )
    assert result["type"] is FlowResultType.FORM
    assert "name" in result["errors"]


async def test_options_flow_updates_command(hass: HomeAssistant) -> None:
    """Test that the options flow lets the user edit the command."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    # Create entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "edit me", CONF_COMMAND: "echo original"},
    )
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]

    # Edit via options flow
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_COMMAND: "echo updated"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_COMMAND] == "echo updated"


async def test_template_variables_registered_as_service_fields(
    hass: HomeAssistant,
) -> None:
    """Test that {{ var }} placeholders in the command appear as service fields."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "write file",
            CONF_COMMAND: 'python3 -c "..." "{{ file_path }}" "{{ content }}"',
        },
    )
    await hass.async_block_till_done()

    descriptions = await async_get_all_descriptions(hass)
    fields = descriptions[DOMAIN]["write_file"]["fields"]
    assert "file_path" in fields
    assert "content" in fields


async def test_no_template_variables_no_fields(hass: HomeAssistant) -> None:
    """Test that a command without templates registers no service fields."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "say hi", CONF_COMMAND: "echo hello"},
    )
    await hass.async_block_till_done()

    descriptions = await async_get_all_descriptions(hass)
    assert descriptions[DOMAIN]["say_hi"]["fields"] == {}


async def test_unload_entry_removes_service(hass: HomeAssistant) -> None:
    """Test that unloading an entry removes its service."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "temp cmd", CONF_COMMAND: "echo bye"},
    )
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "temp_cmd")

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, "temp_cmd")
