"""Searching the media of the server, and playing what a search finds."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pysnapdog import SearchKind, SearchResult, SnapDogUpstreamError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.snapdog.media import AlbumRef, MediaRef, SongRef, parse_media_id

from .conftest import make_search_item
from .test_media_browser import play
from .test_media_player import PLAYER, setup


async def search(client: Any, query: str, classes: list[str] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "media_player/search_media",
        "entity_id": PLAYER,
        "search_query": query,
    }
    if classes is not None:
        message["media_filter_classes"] = classes
    await client.send_json_auto_id(message)
    return await client.receive_json()


@pytest.mark.parametrize(
    ("ref", "media_id"),
    [
        (SongRef("tr-1"), "song/tr-1"),
        (SongRef("a/b c"), "song/a%2Fb%20c"),
        (AlbumRef("al-6"), "album/al-6"),
        (AlbumRef("pl:1"), "album/pl%3A1"),
    ],
)
def test_a_search_media_id_holds_the_id_of_the_track_or_album(
    ref: SongRef | AlbumRef, media_id: str
) -> None:
    assert str(ref) == media_id
    assert parse_media_id(media_id) == ref


@pytest.mark.parametrize("media_id", ["song", "song/", "song/a/b", "album", "album/", "album/a/b"])
def test_a_malformed_search_media_id_is_refused(media_id: str) -> None:
    with pytest.raises(ValueError, match="not a SnapDog media id"):
        parse_media_id(media_id)


def test_the_playlist_ids_keep_their_form() -> None:
    assert parse_media_id("track/subsonic%3A42/7") == MediaRef("subsonic:42", 7)


async def test_the_player_can_search(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    state = hass.states.get(PLAYER)
    assert state is not None
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & MediaPlayerEntityFeature.SEARCH_MEDIA


async def test_a_search_lists_every_kind_with_the_id_to_play_it_by(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    api.search.return_value = SearchResult(
        items=(
            make_search_item(),
            make_search_item(kind=SearchKind.TRACK, title="Blue in Green", subtitle=None),
            make_search_item(
                kind=SearchKind.ALBUM,
                title="Kind of Blue",
                track_id=None,
                album_id="al-6",
            ),
            make_search_item(
                kind=SearchKind.PLAYLIST,
                title="Jazz",
                subtitle=None,
                track_id=None,
                playlist_id="subsonic:42",
                index=1,
            ),
            make_search_item(
                kind=SearchKind.RADIO,
                title="Jazz FM",
                subtitle=None,
                track_id=None,
                playlist_id="snapdog:radio",
                track=4,
            ),
        ),
        next_offset=None,
    )
    await setup(hass, entry, listener)
    response = await search(await hass_ws_client(hass), " jazz ")
    api.search.assert_awaited_once_with("jazz", kinds=list(SearchKind))
    assert response["success"]
    assert [
        (r["title"], r["media_content_id"], r["media_content_type"], r["media_class"])
        for r in response["result"]["result"]
    ] == [
        ("Miles Davis - So What", "song/tr-1", "track", "track"),
        ("Blue in Green", "song/tr-1", "track", "track"),
        ("Miles Davis - Kind of Blue", "album/al-6", "album", "album"),
        ("Jazz", "playlist/subsonic%3A42", "playlist", "playlist"),
        ("Jazz FM", "track/snapdog%3Aradio/4", "track", "track"),
    ]
    assert all(r["can_play"] and not r["can_expand"] for r in response["result"]["result"])


async def test_a_result_without_an_id_is_left_out(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    api.search.return_value = SearchResult(
        items=(
            make_search_item(track_id=None),
            make_search_item(kind=SearchKind.ALBUM, track_id=None),
            make_search_item(kind=SearchKind.PLAYLIST, track_id=None),
            make_search_item(kind=SearchKind.RADIO, track_id=None, playlist_id="snapdog:radio"),
            make_search_item(title="Kept"),
        ),
        next_offset=None,
    )
    await setup(hass, entry, listener)
    response = await search(await hass_ws_client(hass), "kept")
    assert [r["title"] for r in response["result"]["result"]] == ["Miles Davis - Kept"]


@pytest.mark.parametrize(
    ("classes", "kinds"),
    [
        (["track"], [SearchKind.TRACK, SearchKind.RADIO]),
        (["album"], [SearchKind.ALBUM]),
        (["playlist", "album"], [SearchKind.ALBUM, SearchKind.PLAYLIST]),
    ],
)
async def test_the_media_classes_narrow_the_kinds(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    classes: list[str],
    kinds: list[SearchKind],
) -> None:
    await setup(hass, entry, listener)
    assert (await search(await hass_ws_client(hass), "blue", classes))["success"]
    api.search.assert_awaited_once_with("blue", kinds=kinds)


@pytest.mark.parametrize(
    ("query", "classes"), [("", None), ("a", None), (" b ", None), ("blue", ["movie"])]
)
async def test_a_search_that_cannot_match_does_not_ask_the_server(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    query: str,
    classes: list[str] | None,
) -> None:
    await setup(hass, entry, listener)
    response = await search(await hass_ws_client(hass), query, classes)
    assert response["success"]
    assert response["result"]["result"] == []
    api.search.assert_not_called()


async def test_a_failed_search_surfaces(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    api.search.side_effect = SnapDogUpstreamError("Subsonic is down", status=502)
    await setup(hass, entry, listener)
    response = await search(await hass_ws_client(hass), "blue")
    assert not response["success"]
    assert "Subsonic is down" in response["error"]["message"]


async def test_a_track_of_a_search_plays_by_its_own_id(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await play(hass, "track", "song/tr-9")
    api.play_subsonic_track.assert_awaited_once_with(1, "tr-9")
    api.play_playlist_id.assert_not_called()


async def test_an_album_of_a_search_plays_by_its_own_id(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await play(hass, "album", "album/al-6")
    api.play_subsonic_album.assert_awaited_once_with(1, "al-6")
    api.play_playlist_id.assert_not_called()


@pytest.mark.parametrize(
    ("media_type", "media_id"), [("album", "song/tr-1"), ("track", "album/al-6")]
)
async def test_the_media_type_has_to_match_a_search_result(
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
    api.play_subsonic_track.assert_not_called()
    api.play_subsonic_album.assert_not_called()


@pytest.mark.parametrize(
    ("media_type", "media_id", "method"),
    [("track", "song/tr-1", "play_subsonic_track"), ("album", "album/al-6", "play_subsonic_album")],
)
async def test_a_refused_play_of_a_search_result_surfaces(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    media_type: str,
    media_id: str,
    method: str,
) -> None:
    await setup(hass, entry, listener)
    getattr(api, method).side_effect = SnapDogUpstreamError("down", status=502)
    with pytest.raises(HomeAssistantError):
        await play(hass, media_type, media_id)
