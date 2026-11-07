"""The covers of the media browser and of a search, read from the server on request."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient
from homeassistant.core import HomeAssistant
from pysnapdog import (
    PlaylistsChanged,
    SearchKind,
    SearchResult,
    SnapDogBusyError,
    SnapDogNotFoundError,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator, WebSocketGenerator

from .conftest import make_media_track, make_playlist, make_search_item
from .test_media_browser import browse
from .test_media_player import setup
from .test_media_search import search

COVER = b"\x89PNG cover"


def with_covers(api: MagicMock) -> None:
    """The radio list has no cover, the Jazz playlist and its first track have one."""
    api.playlists.return_value = [
        make_playlist(),
        make_playlist(index=1, playlist_id="subsonic:42", name="Jazz", cover_art="/c/pl"),
    ]
    api.playlist_tracks_by_id.return_value = [
        make_media_track(cover_art="/c/tr"),
        make_media_track(track=1, title="Blue in Green", cover_art=None),
    ]


async def fetch(client: TestClient, url: str) -> tuple[int, bytes, str]:
    response = await client.get(url)
    return response.status, await response.read(), response.content_type


async def test_a_playlist_has_a_thumbnail_only_when_the_server_has_a_cover(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    with_covers(api)
    await setup(hass, entry, listener)
    root = (await browse(await hass_ws_client(hass)))["result"]
    radio, jazz = root["children"]
    assert radio["thumbnail"] is None
    assert jazz["thumbnail"].startswith(
        "/api/media_player_proxy/media_player.ground_floor/browse_media/playlist/"
        "playlist/subsonic%253A42?token="
    )


async def test_the_cover_of_a_playlist_is_read_by_its_index(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    with_covers(api)
    await setup(hass, entry, listener)
    jazz = (await browse(await hass_ws_client(hass)))["result"]["children"][1]
    status, body, content_type = await fetch(await hass_client_no_auth(), jazz["thumbnail"])
    assert (status, body, content_type) == (200, COVER, "image/png")
    api.playlist_cover.assert_awaited_once_with(1)
    api.media_cover.assert_not_called()


async def test_the_cover_of_a_track_is_read_by_the_playlist_index_and_the_position(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    with_covers(api)
    await setup(hass, entry, listener)
    opened = (await browse(await hass_ws_client(hass), "playlist/subsonic%3A42"))["result"]
    assert opened["thumbnail"] is not None
    with_cover, without = opened["children"]
    assert without["thumbnail"] is None
    status, body, _ = await fetch(await hass_client_no_auth(), with_cover["thumbnail"])
    assert (status, body) == (200, COVER)
    api.track_cover.assert_awaited_once_with(1, 0)


async def test_a_cover_follows_a_playlist_that_moved(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    """The url holds the playlist id, and the index is looked up when the image is fetched."""
    with_covers(api)
    coordinator = await setup(hass, entry, listener)
    jazz = (await browse(await hass_ws_client(hass)))["result"]["children"][1]
    api.playlists.return_value = [
        make_playlist(),
        make_playlist(index=1, playlist_id="subsonic:7", name="New"),
        make_playlist(index=2, playlist_id="subsonic:42", name="Jazz", cover_art="/c/pl"),
    ]
    await coordinator._on_event(PlaylistsChanged(total=3))
    await hass.async_block_till_done()
    status, _, _ = await fetch(await hass_client_no_auth(), jazz["thumbnail"])
    assert status == 200
    api.playlist_cover.assert_awaited_once_with(2)


async def test_a_search_result_names_its_cover(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    api.search.return_value = SearchResult(
        items=(
            make_search_item(
                kind=SearchKind.ALBUM,
                track_id=None,
                album_id="al-1/x",
                cover_art="/api/v1/media/cover/al-1%2Fx",
            ),
            make_search_item(title="No cover"),
        ),
        next_offset=None,
    )
    await setup(hass, entry, listener)
    result = (await search(await hass_ws_client(hass), "blue"))["result"]["result"]
    album, plain = result
    assert plain["thumbnail"] is None
    assert album["thumbnail"].endswith("&media_image_id=al-1/x")
    status, body, _ = await fetch(await hass_client_no_auth(), album["thumbnail"])
    assert (status, body) == (200, COVER)
    api.media_cover.assert_awaited_once_with("al-1/x")


@pytest.mark.parametrize(
    "error", [SnapDogNotFoundError("none", status=404), SnapDogBusyError("busy", status=503)]
)
async def test_a_cover_the_server_does_not_deliver_is_a_missing_image(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    error: Exception,
) -> None:
    with_covers(api)
    api.playlist_cover.side_effect = error
    await setup(hass, entry, listener)
    jazz = (await browse(await hass_ws_client(hass)))["result"]["children"][1]
    status, _, _ = await fetch(await hass_client_no_auth(), jazz["thumbnail"])
    assert status == 404


async def test_what_has_no_cover_to_read_is_a_missing_image(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    """A playlist that is gone, an id that is not ours and a track or album without a cover id."""
    with_covers(api)
    await setup(hass, entry, listener)
    jazz = (await browse(await hass_ws_client(hass)))["result"]["children"][1]
    base, _, query = (
        jazz["thumbnail"].partition("/browse_media/")[0],
        None,
        jazz["thumbnail"].partition("?")[2],
    )
    client = await hass_client_no_auth()
    for kind, media_id in (
        ("playlist", "playlist/subsonic%253A999"),
        ("playlist", "nonsense"),
        ("track", "song/tr-1"),
        ("album", "album/al-6"),
    ):
        status, _, _ = await fetch(client, f"{base}/browse_media/{kind}/{media_id}?{query}")
        assert status == 404, media_id
    api.playlist_cover.assert_not_called()
    api.track_cover.assert_not_called()
    api.media_cover.assert_not_called()


async def test_the_proxy_refuses_a_wrong_token(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    with_covers(api)
    await setup(hass, entry, listener)
    jazz = (await browse(await hass_ws_client(hass)))["result"]["children"][1]
    status, _, _ = await fetch(
        await hass_client_no_auth(), jazz["thumbnail"].split("token=")[0] + "token=wrong"
    )
    assert status == 403
    api.playlist_cover.assert_not_called()
