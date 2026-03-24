"""Support to enter a value into a text box."""

from __future__ import annotations

import logging
from typing import Any, Self

import voluptuous as vol

from homeassistant.const import (
    ATTR_EDITABLE,
    ATTR_MODE,
    CONF_ICON,
    CONF_ID,
    CONF_MODE,
    CONF_NAME,
    MAX_LENGTH_STATE_STATE,
    SERVICE_RELOAD,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    collection,
    config_validation as cv,
    entity_registry as er,
)
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.restore_state import RestoreEntity
import homeassistant.helpers.service
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType, VolDictType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "input_text"

CONF_INITIAL = "initial"
CONF_MIN = "min"
CONF_MIN_VALUE = 0
CONF_MAX = "max"
CONF_MAX_VALUE = 100
CONF_PATTERN = "pattern"
CONF_VALUE = "value"

MODE_TEXT = "text"
MODE_PASSWORD = "password"

ATTR_VALUE = CONF_VALUE
ATTR_MIN = "min"
ATTR_MAX = "max"
ATTR_PATTERN = CONF_PATTERN

SERVICE_SET_VALUE = "set_value"
SERVICE_CREATE = "create"
SERVICE_DELETE = "delete"
STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

STORAGE_FIELDS: VolDictType = {
    vol.Required(CONF_NAME): vol.All(str, vol.Length(min=1)),
    vol.Optional(CONF_MIN, default=CONF_MIN_VALUE): vol.All(
        vol.Coerce(int), vol.Range(0, MAX_LENGTH_STATE_STATE)
    ),
    vol.Optional(CONF_MAX, default=CONF_MAX_VALUE): vol.All(
        vol.Coerce(int), vol.Range(1, MAX_LENGTH_STATE_STATE)
    ),
    vol.Optional(CONF_INITIAL, ""): cv.string,
    vol.Optional(CONF_ICON): cv.icon,
    vol.Optional(CONF_PATTERN): cv.string,
    vol.Optional(CONF_MODE, default=MODE_TEXT): vol.In([MODE_TEXT, MODE_PASSWORD]),
}

CREATE_FIELDS: VolDictType = {
    vol.Required(CONF_NAME): vol.All(str, vol.Length(min=1)),
    vol.Optional(CONF_ID): cv.slug,
    vol.Optional(CONF_MIN, default=0): vol.Coerce(int),
    vol.Optional(CONF_MAX, default=100): vol.Coerce(int),
    vol.Optional(CONF_INITIAL, default=""): cv.string,
    vol.Optional(CONF_ICON): cv.icon,
    vol.Optional(CONF_PATTERN): cv.string,
    vol.Optional(CONF_MODE, default=MODE_TEXT): vol.In([MODE_TEXT, MODE_PASSWORD]),
}


def _cv_input_text(config: dict[str, Any]) -> dict[str, Any]:
    """Configure validation helper for input box (voluptuous)."""
    minimum: int = config[CONF_MIN]
    maximum: int = config[CONF_MAX]
    if minimum > maximum:
        raise vol.Invalid(
            f"Max len ({minimum}) is not greater than min len ({maximum})"
        )
    state: str | None = config.get(CONF_INITIAL)
    if state is not None and (len(state) < minimum or len(state) > maximum):
        raise vol.Invalid(
            f"Initial value {state} length not in range {minimum}-{maximum}"
        )
    return config


CREATE_SCHEMA = vol.All(vol.Schema(CREATE_FIELDS), _cv_input_text)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: cv.schema_with_slug_keys(
            vol.All(
                lambda value: value or {},
                {
                    vol.Optional(CONF_NAME): cv.string,
                    vol.Optional(CONF_MIN, default=CONF_MIN_VALUE): vol.All(
                        vol.Coerce(int), vol.Range(0, MAX_LENGTH_STATE_STATE)
                    ),
                    vol.Optional(CONF_MAX, default=CONF_MAX_VALUE): vol.All(
                        vol.Coerce(int), vol.Range(1, MAX_LENGTH_STATE_STATE)
                    ),
                    vol.Optional(CONF_INITIAL): cv.string,
                    vol.Optional(CONF_ICON): cv.icon,
                    vol.Optional(CONF_PATTERN): cv.string,
                    vol.Optional(CONF_MODE, default=MODE_TEXT): vol.In(
                        [MODE_TEXT, MODE_PASSWORD]
                    ),
                },
                _cv_input_text,
            ),
        )
    },
    extra=vol.ALLOW_EXTRA,
)
RELOAD_SERVICE_SCHEMA = vol.Schema({})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up an input text."""
    component = EntityComponent[InputText](_LOGGER, DOMAIN, hass)

    id_manager = collection.IDManager()

    yaml_collection = collection.YamlCollection(
        logging.getLogger(f"{__name__}.yaml_collection"), id_manager
    )
    collection.sync_entity_lifecycle(
        hass, DOMAIN, DOMAIN, component, yaml_collection, InputText
    )

    storage_collection = InputTextStorageCollection(
        Store(hass, STORAGE_VERSION, STORAGE_KEY),
        id_manager,
    )
    collection.sync_entity_lifecycle(
        hass, DOMAIN, DOMAIN, component, storage_collection, InputText
    )

    await yaml_collection.async_load(
        [{CONF_ID: id_, **(conf or {})} for id_, conf in config.get(DOMAIN, {}).items()]
    )
    await storage_collection.async_load()

    collection.DictStorageCollectionWebsocket(
        storage_collection, DOMAIN, DOMAIN, STORAGE_FIELDS, STORAGE_FIELDS
    ).async_setup(hass)

    async def reload_service_handler(service_call: ServiceCall) -> None:
        """Reload yaml entities."""
        conf = await component.async_prepare_reload(skip_reset=True)
        await yaml_collection.async_load(
            [{CONF_ID: id_, **(cfg or {})} for id_, cfg in conf.get(DOMAIN, {}).items()]
        )

    homeassistant.helpers.service.async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        reload_service_handler,
        schema=RELOAD_SERVICE_SCHEMA,
    )

    component.async_register_entity_service(
        SERVICE_SET_VALUE,
        {vol.Required(ATTR_VALUE): cv.string},
        "async_set_value",
    )

    async def handle_create(call: ServiceCall) -> None:
        """Create a new helper programmatically."""
        create_data = {k: v for k, v in call.data.items() if k != CONF_ID}
        if CONF_ID in call.data:
            item_id: str = call.data[CONF_ID]
            if id_manager.has_id(item_id):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="id_already_exists",
                    translation_placeholders={"item_id": item_id},
                )
        await storage_collection.async_create_item(create_data)

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE, handle_create, schema=CREATE_SCHEMA
    )

    async def handle_delete(call: ServiceCall) -> None:
        """Delete a helper."""
        registry = er.async_get(hass)
        entity_ids = await homeassistant.helpers.service.async_extract_entity_ids(call)
        for entity_id in entity_ids:
            entry = registry.async_get(entity_id)
            if entry is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="entity_not_found",
                    translation_placeholders={"entity_id": entity_id},
                )
            item_id = entry.unique_id
            if not any(
                item["id"] == item_id for item in storage_collection.async_items()
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="entity_not_storage_based",
                    translation_placeholders={"entity_id": entity_id},
                )
            await storage_collection.async_delete_item(item_id)

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE,
        handle_delete,
        schema=cv.make_entity_service_schema({}),
    )

    return True


class InputTextStorageCollection(collection.DictStorageCollection):
    """Input storage based collection."""

    CREATE_UPDATE_SCHEMA = vol.Schema(vol.All(STORAGE_FIELDS, _cv_input_text))

    async def _process_create_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate the config is valid."""
        return self.CREATE_UPDATE_SCHEMA(data)  # type: ignore[no-any-return]

    @callback
    def _get_suggested_id(self, info: dict[str, Any]) -> str:
        """Suggest an ID based on the config."""
        return info[CONF_NAME]  # type: ignore[no-any-return]

    async def _update_data(
        self, item: dict[str, Any], update_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a new updated data object."""
        update_data = self.CREATE_UPDATE_SCHEMA(update_data)
        return {CONF_ID: item[CONF_ID]} | update_data


class InputText(collection.CollectionEntity, RestoreEntity):
    """Represent a text box."""

    _unrecorded_attributes = frozenset(
        {ATTR_EDITABLE, ATTR_MAX, ATTR_MIN, ATTR_MODE, ATTR_PATTERN}
    )

    _attr_should_poll = False
    _current_value: str | None
    editable: bool

    def __init__(self, config: ConfigType) -> None:
        """Initialize a text input."""
        self._config = config
        self._current_value = config.get(CONF_INITIAL)

    @classmethod
    def from_storage(cls, config: ConfigType) -> Self:
        """Return entity instance initialized from storage."""
        input_text: Self = cls(config)
        input_text.editable = True
        return input_text

    @classmethod
    def from_yaml(cls, config: ConfigType) -> Self:
        """Return entity instance initialized from yaml."""
        input_text: Self = cls(config)
        input_text.entity_id = f"{DOMAIN}.{config[CONF_ID]}"
        input_text.editable = False
        return input_text

    @property
    def name(self) -> str | None:
        """Return the name of the text input entity."""
        return self._config.get(CONF_NAME)

    @property
    def icon(self) -> str | None:
        """Return the icon to be used for this entity."""
        return self._config.get(CONF_ICON)

    @property
    def _maximum(self) -> int:
        """Return max len of the text."""
        return self._config[CONF_MAX]  # type: ignore[no-any-return]

    @property
    def _minimum(self) -> int:
        """Return min len of the text."""
        return self._config[CONF_MIN]  # type: ignore[no-any-return]

    @property
    def state(self) -> str | None:
        """Return the state of the component."""
        return self._current_value

    @property
    def unique_id(self) -> str:
        """Return unique id for the entity."""
        return self._config[CONF_ID]  # type: ignore[no-any-return]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            ATTR_EDITABLE: self.editable,
            ATTR_MIN: self._minimum,
            ATTR_MAX: self._maximum,
            ATTR_PATTERN: self._config.get(CONF_PATTERN),
            ATTR_MODE: self._config[CONF_MODE],
        }

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        if self._current_value is not None:
            return

        state = await self.async_get_last_state()
        value = state.state if state else None

        # Check against None because value can be 0
        if value is not None and self._minimum <= len(value) <= self._maximum:
            self._current_value = value

    async def async_set_value(self, value: str) -> None:
        """Select new value."""
        if len(value) < self._minimum or len(value) > self._maximum:
            _LOGGER.warning(
                "Invalid value: %s (length range %s - %s)",
                value,
                self._minimum,
                self._maximum,
            )
            return
        self._current_value = value
        self.async_write_ha_state()

    async def async_update_config(self, config: ConfigType) -> None:
        """Handle when the config is updated."""
        self._config = config
        self.async_write_ha_state()
