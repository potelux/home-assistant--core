"""Scene snapshot support for media players."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, State

from .const import (
    ATTR_APP_ID,
    ATTR_APP_NAME,
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_EXTRA,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    ATTR_SOUND_MODE,
)

SCENE_STATE_ATTRIBUTES = frozenset(
    {
        ATTR_APP_ID,
        ATTR_APP_NAME,
        ATTR_INPUT_SOURCE,
        ATTR_MEDIA_CONTENT_ID,
        ATTR_MEDIA_CONTENT_TYPE,
        ATTR_MEDIA_EXTRA,
        ATTR_MEDIA_TITLE,
        ATTR_MEDIA_VOLUME_LEVEL,
        ATTR_MEDIA_VOLUME_MUTED,
        ATTR_SOUND_MODE,
    }
)


async def async_scene_snapshot_state(_hass: HomeAssistant, state: State) -> State:
    """Return a media player state suitable for scene restore."""
    return State(
        state.entity_id,
        state.state,
        {
            attr: value
            for attr, value in state.attributes.items()
            if attr in SCENE_STATE_ATTRIBUTES
        },
        context=state.context,
    )
