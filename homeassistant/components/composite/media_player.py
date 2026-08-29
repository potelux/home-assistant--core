"""Composite media player helper."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    ATTR_APP_ID,
    ATTR_APP_NAME,
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DEVICE_CLASSES_SCHEMA,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
    SERVICE_CLEAR_PLAYLIST,
    SERVICE_PLAY_MEDIA,
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    CONF_ATTRIBUTE,
    CONF_DEVICE_CLASS,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_STATE,
    CONF_UNIQUE_ID,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_STOP,
    SERVICE_TOGGLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_DOWN,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
    SERVICE_VOLUME_UP,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.script import Script
from homeassistant.helpers.template import TemplateStateFromEntityId
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

DOMAIN = "composite"

CONF_ACTIVE_STATE = "active_state"
CONF_CHILDREN = "children"
CONF_COMMANDS = "commands"
CONF_PLAYBACK_ENTITY = "playback_entity"
CONF_SELECT_ACTION = "select_action"
CONF_SOURCES = "sources"
CONF_VOLUME_ENTITY = "volume_entity"

ATTR_ACTIVE_SOURCE = "active_source"
ATTR_PLAYBACK_ENTITY = "playback_entity"
ATTR_VOLUME_ENTITY = "volume_entity"

COMMAND_FEATURES: Mapping[str, MediaPlayerEntityFeature] = {
    SERVICE_TURN_ON: MediaPlayerEntityFeature.TURN_ON,
    SERVICE_TURN_OFF: MediaPlayerEntityFeature.TURN_OFF,
    SERVICE_MEDIA_PLAY: MediaPlayerEntityFeature.PLAY,
    SERVICE_MEDIA_PAUSE: MediaPlayerEntityFeature.PAUSE,
    SERVICE_MEDIA_STOP: MediaPlayerEntityFeature.STOP,
    SERVICE_MEDIA_NEXT_TRACK: MediaPlayerEntityFeature.NEXT_TRACK,
    SERVICE_MEDIA_PREVIOUS_TRACK: MediaPlayerEntityFeature.PREVIOUS_TRACK,
    SERVICE_VOLUME_UP: MediaPlayerEntityFeature.VOLUME_STEP,
    SERVICE_VOLUME_DOWN: MediaPlayerEntityFeature.VOLUME_STEP,
    SERVICE_VOLUME_SET: MediaPlayerEntityFeature.VOLUME_SET,
    SERVICE_VOLUME_MUTE: MediaPlayerEntityFeature.VOLUME_MUTE,
    SERVICE_PLAY_MEDIA: MediaPlayerEntityFeature.PLAY_MEDIA,
    SERVICE_CLEAR_PLAYLIST: MediaPlayerEntityFeature.CLEAR_PLAYLIST,
}

PLAYBACK_FEATURES = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.CLEAR_PLAYLIST
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SEARCH_MEDIA
)

VOLUME_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_STEP
)


@dataclass(slots=True)
class ActiveStateMatcher:
    """A child state matcher that identifies an active composite source."""

    entity_id: str
    state: Any
    attribute: str | None = None


@dataclass(slots=True)
class CompositeSource:
    """A source exposed by a composite media player."""

    name: str
    select_action: Script | None
    active_state: tuple[ActiveStateMatcher, ...]
    playback_entity: str | None
    app_id: str | None
    app_name: str | None
    media_content_id: str | None
    media_content_type: str | None
    media_title: str | None


ACTIVE_STATE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_STATE): vol.Any(str, int, float, bool),
        vol.Optional(CONF_ATTRIBUTE): cv.string,
    }
)

SOURCE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SELECT_ACTION): cv.SCRIPT_SCHEMA,
        vol.Optional(CONF_ACTIVE_STATE, default=[]): vol.All(
            cv.ensure_list, [ACTIVE_STATE_SCHEMA]
        ),
        vol.Optional(CONF_PLAYBACK_ENTITY): cv.entity_domain(MEDIA_PLAYER_DOMAIN),
        vol.Optional(ATTR_APP_ID): cv.string,
        vol.Optional(ATTR_APP_NAME): cv.string,
        vol.Optional(ATTR_MEDIA_CONTENT_ID): cv.string,
        vol.Optional(ATTR_MEDIA_CONTENT_TYPE): cv.string,
        vol.Optional(ATTR_MEDIA_TITLE): cv.string,
    }
)

PLATFORM_SCHEMA = MEDIA_PLAYER_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_SOURCES): vol.Schema({cv.string: SOURCE_SCHEMA}),
        vol.Optional(CONF_PLAYBACK_ENTITY): cv.entity_domain(MEDIA_PLAYER_DOMAIN),
        vol.Optional(CONF_VOLUME_ENTITY): cv.entity_domain(MEDIA_PLAYER_DOMAIN),
        vol.Optional(CONF_CHILDREN, default=[]): cv.entity_ids,
        vol.Optional(CONF_COMMANDS, default={}): cv.schema_with_slug_keys(
            cv.SCRIPT_SCHEMA
        ),
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
    },
    extra=vol.REMOVE_EXTRA,
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up composite media players."""
    await async_setup_reload_service(hass, DOMAIN, [MEDIA_PLAYER_DOMAIN])
    async_add_entities([CompositeMediaPlayer(hass, config)])


class CompositeMediaPlayer(MediaPlayerEntity):
    """A media player that orchestrates a complete AV setup."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:
        """Initialize the composite media player."""
        self.hass = hass
        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = config.get(CONF_UNIQUE_ID)
        self._attr_device_class = config.get(CONF_DEVICE_CLASS)
        self._playback_entity: str | None = config.get(CONF_PLAYBACK_ENTITY)
        self._volume_entity: str | None = config.get(CONF_VOLUME_ENTITY)
        self._children: list[str] = config[CONF_CHILDREN]
        self._active_source: str | None = None
        self._optimistic_source: str | None = None

        self._commands: dict[str, Script] = {
            command: Script(hass, action, f"{self._attr_name} {command}", DOMAIN)
            for command, action in config[CONF_COMMANDS].items()
        }
        self._sources: dict[str, CompositeSource] = {
            name: self._source_from_config(name, source_config)
            for name, source_config in config[CONF_SOURCES].items()
        }

    def _source_from_config(
        self, name: str, source_config: Mapping[str, Any]
    ) -> CompositeSource:
        """Build a source definition from configuration."""
        action_config = source_config.get(CONF_SELECT_ACTION)
        select_action = (
            Script(self.hass, action_config, f"{self._attr_name} select {name}", DOMAIN)
            if action_config
            else None
        )
        return CompositeSource(
            name=name,
            select_action=select_action,
            active_state=tuple(
                ActiveStateMatcher(
                    matcher[CONF_ENTITY_ID],
                    matcher[CONF_STATE],
                    matcher.get(CONF_ATTRIBUTE),
                )
                for matcher in source_config[CONF_ACTIVE_STATE]
            ),
            playback_entity=source_config.get(CONF_PLAYBACK_ENTITY),
            app_id=source_config.get(ATTR_APP_ID),
            app_name=source_config.get(ATTR_APP_NAME),
            media_content_id=source_config.get(ATTR_MEDIA_CONTENT_ID),
            media_content_type=source_config.get(ATTR_MEDIA_CONTENT_TYPE),
            media_title=source_config.get(ATTR_MEDIA_TITLE),
        )

    async def async_added_to_hass(self) -> None:
        """Register listeners for child entities."""

        @callback
        def async_state_changed(event: Event[EventStateChangedData]) -> None:
            """Handle a child state update."""
            self.async_set_context(event.context)
            self._async_update_active_source()
            self.async_write_ha_state()

        entity_ids = set(self._children)
        if self._playback_entity:
            entity_ids.add(self._playback_entity)
        if self._volume_entity:
            entity_ids.add(self._volume_entity)
        for source in self._sources.values():
            if source.playback_entity:
                entity_ids.add(source.playback_entity)
            for matcher in source.active_state:
                entity_ids.add(matcher.entity_id)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, list(entity_ids), async_state_changed
            )
        )
        self._async_update_active_source()

    @property
    def source_list(self) -> list[str]:
        """Return available composite sources."""
        return list(self._sources)

    @property
    def source(self) -> str | None:
        """Return the active composite source."""
        return self._active_source or self._optimistic_source

    @property
    def state(self) -> MediaPlayerState | str:
        """Return the current media player state."""
        media_state = self._active_media_state
        if media_state and media_state.state not in (
            STATE_OFF,
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            return media_state.state
        if self.source:
            return MediaPlayerState.ON
        if media_state:
            return media_state.state
        return MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        """Return the configured volume target level."""
        return self._target_attr(self._volume_entity, ATTR_MEDIA_VOLUME_LEVEL)

    @property
    def is_volume_muted(self) -> bool | None:
        """Return whether the configured volume target is muted."""
        return self._target_attr(self._volume_entity, ATTR_MEDIA_VOLUME_MUTED)

    @property
    def media_content_id(self) -> str | None:
        """Return the active playable media content id."""
        return self._media_attr(ATTR_MEDIA_CONTENT_ID, "media_content_id")

    @property
    def media_content_type(self) -> str | None:
        """Return the active playable media content type."""
        return self._media_attr(ATTR_MEDIA_CONTENT_TYPE, "media_content_type")

    @property
    def media_title(self) -> str | None:
        """Return the active media title."""
        return self._media_attr(ATTR_MEDIA_TITLE, "media_title")

    @property
    def app_id(self) -> str | None:
        """Return the active app id."""
        return self._media_attr(ATTR_APP_ID, "app_id")

    @property
    def app_name(self) -> str | None:
        """Return the active app name."""
        return self._media_attr(ATTR_APP_NAME, "app_name")

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag supported media player features."""
        features = MediaPlayerEntityFeature.SELECT_SOURCE

        if playback_features := self._target_features(self._active_playback_entity):
            features |= playback_features & PLAYBACK_FEATURES
        if volume_features := self._target_features(self._volume_entity):
            features |= volume_features & VOLUME_FEATURES

        for command, command_features in COMMAND_FEATURES.items():
            if command in self._commands:
                features |= command_features

        return features

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return composite-specific state attributes."""
        attrs: dict[str, Any] = {}
        if self._active_source:
            attrs[ATTR_ACTIVE_SOURCE] = self._active_source
        if self._active_playback_entity:
            attrs[ATTR_PLAYBACK_ENTITY] = self._active_playback_entity
        if self._volume_entity:
            attrs[ATTR_VOLUME_ENTITY] = self._volume_entity
        return attrs

    @property
    def _active_source_config(self) -> CompositeSource | None:
        """Return the active source configuration."""
        if source := self.source:
            return self._sources.get(source)
        return None

    @property
    def _active_playback_entity(self) -> str | None:
        """Return the media player that should receive playback commands."""
        if (
            source_config := self._active_source_config
        ) and source_config.playback_entity:
            return source_config.playback_entity
        return self._playback_entity

    @property
    def _active_media_state(self) -> State | None:
        """Return the active media state."""
        if active_playback_entity := self._active_playback_entity:
            return self.hass.states.get(active_playback_entity)
        return None

    def _target_attr(self, entity_id: str | None, attr: str) -> Any:
        """Return an attribute from a target entity state."""
        if entity_id is None or (state := self.hass.states.get(entity_id)) is None:
            return None
        return state.attributes.get(attr)

    def _media_attr(self, attr: str, source_field: str) -> Any:
        """Return an attribute from the active media target or source config."""
        if (
            media_state := self._active_media_state
        ) and (value := media_state.attributes.get(attr)) is not None:
            return value
        if source_config := self._active_source_config:
            return getattr(source_config, source_field)
        return None

    def _target_features(self, entity_id: str | None) -> MediaPlayerEntityFeature:
        """Return supported features for a target media player."""
        if entity_id is None or (state := self.hass.states.get(entity_id)) is None:
            return MediaPlayerEntityFeature(0)
        return MediaPlayerEntityFeature(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0))

    async def _async_run_script(
        self,
        script: Script,
        run_variables: dict[str, Any] | None = None,
    ) -> None:
        """Run a configured action script."""
        await script.async_run(
            run_variables={
                "this": TemplateStateFromEntityId(self.hass, self.entity_id),
                **(run_variables or {}),
            },
            context=self._context,
        )

    async def _async_call_command(
        self,
        command: str,
        target: str | None,
        service_data: dict[str, Any] | None = None,
    ) -> None:
        """Run a configured command or forward to a media player target."""
        if command in self._commands:
            await self._async_run_script(self._commands[command], service_data)
            return

        if target is None:
            return

        data = {ATTR_ENTITY_ID: target, **(service_data or {})}
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            command,
            data,
            blocking=True,
            context=self._context,
        )

    async def async_turn_on(self) -> None:
        """Turn the composite setup on."""
        await self._async_call_command(SERVICE_TURN_ON, self._active_playback_entity)

    async def async_turn_off(self) -> None:
        """Turn the composite setup off."""
        await self._async_call_command(SERVICE_TURN_OFF, self._active_playback_entity)

    async def async_toggle(self) -> None:
        """Toggle the composite setup."""
        if SERVICE_TOGGLE in self._commands:
            await self._async_run_script(self._commands[SERVICE_TOGGLE])
            return
        await super().async_toggle()

    async def async_select_source(self, source: str) -> None:
        """Select a composite source."""
        if source not in self._sources:
            raise HomeAssistantError(f"Source {source} is not available")
        source_config = self._sources[source]
        if source_config.select_action:
            await self._async_run_script(source_config.select_action, {"source": source})
        elif source_config.media_content_id and source_config.media_content_type:
            await self.async_play_media(
                source_config.media_content_type,
                source_config.media_content_id,
            )
        elif source_config.app_id:
            await self.async_play_media(MediaType.APP, source_config.app_id)

        self._optimistic_source = source
        self._async_update_active_source()
        self.async_write_ha_state()

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play media on the active playback target."""
        await self._async_call_command(
            SERVICE_PLAY_MEDIA,
            self._active_playback_entity,
            {
                ATTR_MEDIA_CONTENT_TYPE: media_type,
                ATTR_MEDIA_CONTENT_ID: media_id,
                **kwargs,
            },
        )

    async def async_media_play(self) -> None:
        """Send play command."""
        await self._async_call_command(SERVICE_MEDIA_PLAY, self._active_playback_entity)

    async def async_media_pause(self) -> None:
        """Send pause command."""
        await self._async_call_command(SERVICE_MEDIA_PAUSE, self._active_playback_entity)

    async def async_media_stop(self) -> None:
        """Send stop command."""
        await self._async_call_command(SERVICE_MEDIA_STOP, self._active_playback_entity)

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        await self._async_call_command(
            SERVICE_MEDIA_NEXT_TRACK, self._active_playback_entity
        )

    async def async_media_previous_track(self) -> None:
        """Send previous track command."""
        await self._async_call_command(
            SERVICE_MEDIA_PREVIOUS_TRACK, self._active_playback_entity
        )

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level on the configured volume target."""
        await self._async_call_command(
            SERVICE_VOLUME_SET,
            self._volume_entity,
            {ATTR_MEDIA_VOLUME_LEVEL: volume},
        )

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute the configured volume target."""
        await self._async_call_command(
            SERVICE_VOLUME_MUTE,
            self._volume_entity,
            {ATTR_MEDIA_VOLUME_MUTED: mute},
        )

    async def async_volume_up(self) -> None:
        """Turn volume up on the configured volume target."""
        await self._async_call_command(SERVICE_VOLUME_UP, self._volume_entity)

    async def async_volume_down(self) -> None:
        """Turn volume down on the configured volume target."""
        await self._async_call_command(SERVICE_VOLUME_DOWN, self._volume_entity)

    async def async_clear_playlist(self) -> None:
        """Clear the active player playlist."""
        await self._async_call_command(
            SERVICE_CLEAR_PLAYLIST, self._active_playback_entity
        )

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Return browse media from the active playback target."""
        component: EntityComponent[MediaPlayerEntity] = self.hass.data[
            MEDIA_PLAYER_DOMAIN
        ]
        if (
            (entity_id := self._active_playback_entity)
            and (entity := component.get_entity(entity_id))
        ):
            return await entity.async_browse_media(media_content_type, media_content_id)
        raise NotImplementedError

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search media from the active playback target."""
        component: EntityComponent[MediaPlayerEntity] = self.hass.data[
            MEDIA_PLAYER_DOMAIN
        ]
        if (
            (entity_id := self._active_playback_entity)
            and (entity := component.get_entity(entity_id))
        ):
            return await entity.async_search_media(query)
        raise NotImplementedError

    @callback
    def _async_update_active_source(self) -> None:
        """Update the active source from configured child-state matchers."""
        self._active_source = None
        for source in self._sources.values():
            if not source.active_state:
                continue
            if all(self._matcher_matches(matcher) for matcher in source.active_state):
                self._active_source = source.name
                self._optimistic_source = None
                return

    def _matcher_matches(self, matcher: ActiveStateMatcher) -> bool:
        """Return true if a child state matcher currently matches."""
        if (state := self.hass.states.get(matcher.entity_id)) is None:
            return False
        actual = (
            state.attributes.get(matcher.attribute)
            if matcher.attribute is not None
            else state.state
        )
        expected = matcher.state
        return actual == expected or str(actual) == str(expected)

    async def async_update(self) -> None:
        """Update active source state."""
        self._async_update_active_source()
