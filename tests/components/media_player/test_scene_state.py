"""Tests for media player scene snapshots."""

from __future__ import annotations

from homeassistant.components.media_player import (
    ATTR_APP_ID,
    ATTR_APP_NAME,
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_SOUND_MODE_LIST,
)
from homeassistant.components.media_player.scene_state import async_scene_snapshot_state
from homeassistant.const import (
    ATTR_ENTITY_PICTURE,
    ATTR_FRIENDLY_NAME,
    ATTR_SUPPORTED_FEATURES,
    STATE_PLAYING,
)
from homeassistant.core import HomeAssistant, State


async def test_scene_snapshot_filters_media_player_attributes(
    hass: HomeAssistant,
) -> None:
    """Test media player scene snapshots only keep restorable attributes."""
    snapshot = await async_scene_snapshot_state(
        hass,
        State(
            "media_player.living_room_tv",
            STATE_PLAYING,
            {
                ATTR_INPUT_SOURCE: "Netflix",
                ATTR_MEDIA_CONTENT_ID: "movie-123",
                ATTR_MEDIA_CONTENT_TYPE: "movie",
                ATTR_MEDIA_TITLE: "Braveheart",
                ATTR_APP_ID: "netflix",
                ATTR_APP_NAME: "Netflix",
                ATTR_MEDIA_VOLUME_LEVEL: 0.4,
                ATTR_INPUT_SOURCE_LIST: ["Netflix", "Spectrum"],
                ATTR_SOUND_MODE_LIST: ["Movie"],
                ATTR_SUPPORTED_FEATURES: 2048,
                ATTR_ENTITY_PICTURE: "https://example.com/poster.jpg",
                ATTR_FRIENDLY_NAME: "Living room TV",
            },
        ),
    )

    assert snapshot.attributes == {
        ATTR_INPUT_SOURCE: "Netflix",
        ATTR_MEDIA_CONTENT_ID: "movie-123",
        ATTR_MEDIA_CONTENT_TYPE: "movie",
        ATTR_MEDIA_TITLE: "Braveheart",
        ATTR_APP_ID: "netflix",
        ATTR_APP_NAME: "Netflix",
        ATTR_MEDIA_VOLUME_LEVEL: 0.4,
    }
