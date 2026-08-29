"""Tests for the composite media player platform."""

from __future__ import annotations

from homeassistant.components.media_player import (
    ATTR_APP_NAME,
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    SERVICE_SELECT_SOURCE,
    MediaPlayerEntityFeature,
    MediaType,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES, STATE_PLAYING
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from tests.common import async_mock_service

COMPOSITE_ENTITY_ID = "media_player.living_room_tv"
PLAYBACK_ENTITY_ID = "media_player.shield"
VOLUME_ENTITY_ID = "media_player.receiver"


async def _async_setup_composite(hass: HomeAssistant) -> None:
    """Set up a test composite media player."""
    assert await async_setup_component(
        hass,
        MEDIA_PLAYER_DOMAIN,
        {
            MEDIA_PLAYER_DOMAIN: [
                {
                    "platform": "composite",
                    "name": "Living room TV",
                    "playback_entity": PLAYBACK_ENTITY_ID,
                    "volume_entity": VOLUME_ENTITY_ID,
                    "sources": {
                        "Netflix": {
                            "select_action": [
                                {
                                    "action": "test.select_source",
                                    "data": {"source": "{{ source }}"},
                                }
                            ],
                            "active_state": [
                                {
                                    "entity_id": "media_player.tv",
                                    "attribute": ATTR_INPUT_SOURCE,
                                    "state": "HDMI1",
                                },
                                {
                                    "entity_id": VOLUME_ENTITY_ID,
                                    "attribute": ATTR_INPUT_SOURCE,
                                    "state": "Shield",
                                },
                                {
                                    "entity_id": PLAYBACK_ENTITY_ID,
                                    "attribute": ATTR_APP_NAME,
                                    "state": "Netflix",
                                },
                            ],
                        },
                        "Spectrum": {
                            "select_action": [
                                {
                                    "action": "test.select_source",
                                    "data": {"source": "{{ source }}"},
                                }
                            ],
                            "active_state": [
                                {
                                    "entity_id": "media_player.tv",
                                    "attribute": ATTR_INPUT_SOURCE,
                                    "state": "HDMI1",
                                },
                                {
                                    "entity_id": VOLUME_ENTITY_ID,
                                    "attribute": ATTR_INPUT_SOURCE,
                                    "state": "Xumo",
                                },
                            ],
                        },
                    },
                    "commands": {
                        SERVICE_PLAY_MEDIA: [
                            {
                                "action": "test.play_media",
                                "data": {
                                    ATTR_MEDIA_CONTENT_ID: (
                                        "{{ media_content_id }}"
                                    ),
                                    ATTR_MEDIA_CONTENT_TYPE: (
                                        "{{ media_content_type }}"
                                    ),
                                },
                            }
                        ]
                    },
                }
            ]
        },
    )
    await hass.async_block_till_done()


async def test_composite_state_calculation(hass: HomeAssistant) -> None:
    """Test composite media player state and attributes."""
    hass.states.async_set("media_player.tv", "on", {ATTR_INPUT_SOURCE: "HDMI1"})
    hass.states.async_set(
        VOLUME_ENTITY_ID,
        "on",
        {
            ATTR_INPUT_SOURCE: "Shield",
            ATTR_MEDIA_VOLUME_LEVEL: 0.42,
            ATTR_MEDIA_VOLUME_MUTED: False,
            ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.VOLUME_SET,
        },
    )
    hass.states.async_set(
        PLAYBACK_ENTITY_ID,
        STATE_PLAYING,
        {
            ATTR_APP_NAME: "Netflix",
            ATTR_MEDIA_CONTENT_ID: "movie-123",
            ATTR_MEDIA_CONTENT_TYPE: MediaType.MOVIE,
            ATTR_MEDIA_TITLE: "Braveheart",
            ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.PLAY_MEDIA,
        },
    )

    await _async_setup_composite(hass)

    state = hass.states.get(COMPOSITE_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_PLAYING
    assert state.attributes[ATTR_INPUT_SOURCE] == "Netflix"
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == ["Netflix", "Spectrum"]
    assert state.attributes[ATTR_APP_NAME] == "Netflix"
    assert state.attributes[ATTR_MEDIA_CONTENT_ID] == "movie-123"
    assert state.attributes[ATTR_MEDIA_CONTENT_TYPE] == MediaType.MOVIE
    assert state.attributes[ATTR_MEDIA_TITLE] == "Braveheart"
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.42
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is False


async def test_source_selection_runs_action(hass: HomeAssistant) -> None:
    """Test selecting a source runs the configured action."""
    await _async_setup_composite(hass)
    calls = async_mock_service(hass, "test", "select_source")

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_SELECT_SOURCE,
        {ATTR_ENTITY_ID: COMPOSITE_ENTITY_ID, ATTR_INPUT_SOURCE: "Spectrum"},
        blocking=True,
    )

    assert len(calls) == 1
    assert calls[0].data["source"] == "Spectrum"
    state = hass.states.get(COMPOSITE_ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_INPUT_SOURCE] == "Spectrum"


async def test_composite_play_media_command(hass: HomeAssistant) -> None:
    """Test play_media uses the configured composite command."""
    await _async_setup_composite(hass)
    calls = async_mock_service(hass, "test", "play_media")

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: COMPOSITE_ENTITY_ID,
            ATTR_MEDIA_CONTENT_ID: "movie-123",
            ATTR_MEDIA_CONTENT_TYPE: MediaType.MOVIE,
        },
        blocking=True,
    )

    assert len(calls) == 1
    assert calls[0].data[ATTR_MEDIA_CONTENT_ID] == "movie-123"
    assert calls[0].data[ATTR_MEDIA_CONTENT_TYPE] == MediaType.MOVIE


async def test_scene_restore_selects_composite_source(hass: HomeAssistant) -> None:
    """Test scene restore selects the saved composite source."""
    assert await async_setup_component(hass, "scene", {"scene": {}})
    await _async_setup_composite(hass)
    calls = async_mock_service(hass, "test", "select_source")

    await hass.services.async_call(
        "scene",
        "apply",
        {
            "entities": {
                COMPOSITE_ENTITY_ID: {
                    "state": "on",
                    ATTR_INPUT_SOURCE: "Netflix",
                }
            }
        },
        blocking=True,
    )

    assert len(calls) == 1
    assert calls[0].data["source"] == "Netflix"


async def test_scene_restore_plays_composite_media(hass: HomeAssistant) -> None:
    """Test scene restore replays saved media content on the composite."""
    assert await async_setup_component(hass, "scene", {"scene": {}})
    await _async_setup_composite(hass)
    calls = async_mock_service(hass, "test", "play_media")

    await hass.services.async_call(
        "scene",
        "apply",
        {
            "entities": {
                COMPOSITE_ENTITY_ID: {
                    "state": STATE_PLAYING,
                    ATTR_MEDIA_CONTENT_ID: "movie-123",
                    ATTR_MEDIA_CONTENT_TYPE: MediaType.MOVIE,
                }
            }
        },
        blocking=True,
    )

    assert len(calls) == 1
    assert calls[0].data[ATTR_MEDIA_CONTENT_ID] == "movie-123"
    assert calls[0].data[ATTR_MEDIA_CONTENT_TYPE] == MediaType.MOVIE
