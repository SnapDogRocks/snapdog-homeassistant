"""Browsing and playing the playlists of the server."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    SERVICE_PLAY_MEDIA,
    SERVICE_SELECT_SOURCE,
    MediaPlayerEntityFeature,
)
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pysnapdog import PlaylistsChanged, SnapDogNotFoundError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.snapdog.media import MediaRef, parse_media_id

from .conftest import make_playlist
from .test_media_player import PLAYER, setup, zone_changed


async def browse(client: Any, content_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"type": "media_player/browse_media", "entity_id": PLAYER}
    if content_id is not None:
        message["media_content_id"] = content_id
        message["media_content_type"] = "playlist"
    await client.send_json_auto_id(message)
    return await client.receive_json()


async def play(hass: HomeAssistant, media_type: str, media_id: str) -> None:
    await hass.services.async_call(
        MEDIA_PLAYER,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: PLAYER,
            ATTR_MEDIA_CONTENT_TYPE: media_type,
            ATTR_MEDIA_CONTENT_ID: media_id,
        },
        blocking=True,
    )


@pytest.mark.parametrize(
    ("playlist_id", "track", "media_id"),
    [
        ("snapdog:radio", None, "playlist/snapdog%3Aradio"),
        ("subsonic:42", 7, "track/subsonic%3A42/7"),
        ("subsonic:a/b", 0, "track/subsonic%3Aa%2Fb/0"),
    ],
)
def test_a_media_id_holds_the_playlist_id_and_the_track(
    playlist_id: str, track: int | None, media_id: str
) -> None:
    ref = MediaRef(playlist_id, track)
    assert str(ref) == media_id
    assert parse_media_id(media_id) == ref


@pytest.mark.parametrize(
    "media_id",
    ["", "playlist", "playlist/", "playlist/a/b", "track/a", "track/a/x", "track//1", "radio/1"],
)
def test_a_foreign_media_id_is_refused(media_id: str) -> None:
    with pytest.raises(ValueError, match="not a SnapDog media id"):
        parse_media_id(media_id)


async def test_the_player_can_browse_and_play(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    features = hass.states.get(PLAYER).attributes[ATTR_SUPPORTED_FEATURES]
    assert features & MediaPlayerEntityFeature.BROWSE_MEDIA
    assert features & MediaPlayerEntityFeature.PLAY_MEDIA


async def test_the_root_lists_the_playlists_by_id(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    await setup(hass, entry, listener)
    response = await browse(await hass_ws_client(hass))
    root = response["result"]
    assert root["can_expand"]
    assert [(c["title"], c["media_content_id"], c["media_class"]) for c in root["children"]] == [
        ("Radio", "playlist/snapdog%3Aradio", "playlist"),
        ("Jazz", "playlist/subsonic%3A42", "playlist"),
    ]
    assert all(c["can_play"] and c["can_expand"] for c in root["children"])
    api.playlist_tracks_by_id.assert_not_called()


async def test_a_playlist_lists_its_tracks_with_their_position(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    await setup(hass, entry, listener)
    response = await browse(await hass_ws_client(hass), "playlist/subsonic%3A42")
    api.playlist_tracks_by_id.assert_awaited_once_with("subsonic:42")
    children = response["result"]["children"]
    assert [(c["title"], c["media_content_id"]) for c in children] == [
        ("Miles Davis - So What", "track/subsonic%3A42/0"),
        ("Blue in Green", "track/subsonic%3A42/1"),
    ]
    assert all(
        c["can_play"] and not c["can_expand"] and c["media_class"] == "track" for c in children
    )


async def test_the_browser_follows_a_moved_playlist(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    """The id of a playlist stays, wherever its index went."""
    coordinator = await setup(hass, entry, listener)
    api.playlists.return_value = [
        make_playlist(),
        make_playlist(index=1, playlist_id="subsonic:7", name="New"),
        make_playlist(index=2, playlist_id="subsonic:42", name="Jazz"),
    ]
    await coordinator._on_event(PlaylistsChanged(total=3))
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    root = (await browse(client))["result"]
    assert [c["media_content_id"] for c in root["children"]][-1] == "playlist/subsonic%3A42"
    assert (await browse(client, "playlist/subsonic%3A42"))["success"]


@pytest.mark.parametrize(
    ("content_id", "text"),
    [
        ("nonsense", "is not a playlist or track"),
        ("track/subsonic%3A42/0", "is not a playlist or track"),
        ("playlist/subsonic%3A999", "has no playlist"),
    ],
)
async def test_browsing_something_else_fails(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    content_id: str,
    text: str,
) -> None:
    await setup(hass, entry, listener)
    response = await browse(await hass_ws_client(hass), content_id)
    assert not response["success"]
    assert response["error"]["code"] == "unknown_error"
    assert text in response["error"]["message"]
    api.playlist_tracks_by_id.assert_not_called()


async def test_a_failed_track_list_surfaces(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    await setup(hass, entry, listener)
    api.playlist_tracks_by_id.side_effect = SnapDogNotFoundError("gone", status=404)
    response = await browse(await hass_ws_client(hass), "playlist/subsonic%3A42")
    assert not response["success"]


async def test_a_playlist_plays_by_its_id(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await play(hass, "playlist", "playlist/subsonic%3A42")
    api.play_playlist_id.assert_awaited_once_with(1, "subsonic:42", None)


async def test_a_track_plays_from_its_position_in_the_playlist(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await play(hass, "track", "track/subsonic%3A42/3")
    api.play_playlist_id.assert_awaited_once_with(1, "subsonic:42", 3)


@pytest.mark.parametrize(
    ("media_type", "media_id"),
    [("music", "http://example.com/a.mp3"), ("track", "playlist/subsonic%3A42"), ("playlist", "x")],
)
async def test_something_that_is_not_ours_is_refused(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    media_type: str,
    media_id: str,
) -> None:
    await setup(hass, entry, listener)
    with pytest.raises(ServiceValidationError):
        await play(hass, media_type, media_id)
    api.play_playlist_id.assert_not_called()


async def test_a_refused_play_surfaces(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    api.play_playlist_id.side_effect = SnapDogNotFoundError("unknown", status=404)
    with pytest.raises(HomeAssistantError):
        await play(hass, "playlist", "playlist/subsonic%3A42")


async def test_the_sources_are_the_playlist_names(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    state = hass.states.get(PLAYER)
    assert state is not None
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & MediaPlayerEntityFeature.SELECT_SOURCE
    assert state.attributes["source_list"] == ["Radio", "Jazz"]
    assert state.attributes["source"] == "Radio"


async def test_the_source_follows_the_playlist_id_not_the_index(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    """The zone reports index 1, but the playlist with that index is another one now."""
    coordinator = await setup(hass, entry, listener)
    api.playlists.return_value = [
        make_playlist(),
        make_playlist(index=1, playlist_id="subsonic:7", name="New"),
        make_playlist(index=2, playlist_id="subsonic:42", name="Jazz"),
    ]
    await coordinator._on_event(PlaylistsChanged(total=3))
    await coordinator._on_event(
        zone_changed(playlist_index=1, playlist_id="subsonic:42", playlist_name="Jazz")
    )
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).attributes["source"] == "Jazz"


async def test_no_source_without_a_known_playlist(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(
        zone_changed(source="airplay", playlist_index=None, playlist_id=None, playlist_name=None)
    )
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).attributes.get("source") is None


async def test_equal_names_are_told_apart_and_a_playlist_without_id_is_left_out(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    api.playlists.return_value = [
        make_playlist(index=0, playlist_id="snapdog:radio", name="Radio"),
        make_playlist(index=1, playlist_id="subsonic:1", name="Mix"),
        make_playlist(index=2, playlist_id="subsonic:2", name="Mix"),
        make_playlist(index=3, playlist_id=None, name="Old"),
    ]
    await coordinator._on_event(PlaylistsChanged(total=4))
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).attributes["source_list"] == [
        "Radio",
        "Mix (subsonic:1)",
        "Mix (subsonic:2)",
    ]


async def test_selecting_a_source_selects_the_playlist_by_id(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        MEDIA_PLAYER,
        SERVICE_SELECT_SOURCE,
        {ATTR_ENTITY_ID: PLAYER, ATTR_INPUT_SOURCE: "Jazz"},
        blocking=True,
    )
    api.set_zone_playlist_id.assert_awaited_once_with(1, "subsonic:42")


async def test_an_unknown_source_is_refused(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            MEDIA_PLAYER,
            SERVICE_SELECT_SOURCE,
            {ATTR_ENTITY_ID: PLAYER, ATTR_INPUT_SOURCE: "Nothing"},
            blocking=True,
        )
    api.set_zone_playlist_id.assert_not_called()


async def test_a_refused_source_change_surfaces(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    api.set_zone_playlist_id.side_effect = SnapDogNotFoundError("unknown", status=404)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            MEDIA_PLAYER,
            SERVICE_SELECT_SOURCE,
            {ATTR_ENTITY_ID: PLAYER, ATTR_INPUT_SOURCE: "Jazz"},
            blocking=True,
        )
