"""The actions that manage the playlists of the media server."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import probatio
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pysnapdog import SnapDogReadOnlyError, SnapDogUpstreamError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.snapdog.const import CONF_SERVER_ID, DOMAIN

from .conftest import make_playlist
from .test_media_player import setup

PLAYLIST = "subsonic:42"
WRITES = ("create_playlist", "update_playlist", "replace_playlist_tracks", "delete_playlist")


async def call(
    hass: HomeAssistant, name: str, data: dict[str, Any], *, response: bool = False
) -> Any:
    return await hass.services.async_call(
        DOMAIN, name, data, blocking=True, return_response=response
    )


@pytest.fixture
def writes(api: MagicMock) -> MagicMock:
    api.create_playlist = AsyncMock(
        return_value=make_playlist(index=2, playlist_id="subsonic:9", name="Evening")
    )
    for name in WRITES[1:]:
        setattr(api, name, AsyncMock(return_value=None))
    return api


async def test_creating_a_playlist_answers_with_its_id(
    hass: HomeAssistant, entry: MockConfigEntry, writes: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    assert hass.services.supports_response(DOMAIN, "create_playlist") is SupportsResponse.OPTIONAL
    assert hass.services.supports_response(DOMAIN, "delete_playlist") is SupportsResponse.NONE
    result = await call(
        hass,
        "create_playlist",
        {"name": "Evening", "track_ids": ["tr-1", "song/tr%2F2"]},
        response=True,
    )
    assert result == {"playlist_id": "subsonic:9", "name": "Evening", "index": 2}
    writes.create_playlist.assert_awaited_once_with("Evening", ["tr-1", "tr/2"])


async def test_a_playlist_can_be_created_empty(
    hass: HomeAssistant, entry: MockConfigEntry, writes: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "create_playlist", {"name": "Empty"})
    writes.create_playlist.assert_awaited_once_with("Empty", [])


@pytest.mark.parametrize(
    ("name", "data", "method", "args", "kwargs"),
    [
        (
            "rename_playlist",
            {"playlist_id": PLAYLIST, "name": "New"},
            "update_playlist",
            (PLAYLIST,),
            {"name": "New"},
        ),
        (
            "add_tracks_to_playlist",
            {"playlist_id": PLAYLIST, "track_ids": ["a", "song/b"]},
            "update_playlist",
            (PLAYLIST,),
            {"add_track_ids": ["a", "b"]},
        ),
        (
            "remove_tracks_from_playlist",
            {"playlist_id": PLAYLIST, "positions": [0, 3]},
            "update_playlist",
            (PLAYLIST,),
            {"remove_positions": [0, 3]},
        ),
        (
            "replace_playlist_tracks",
            {"playlist_id": PLAYLIST, "track_ids": ["b", "a"]},
            "replace_playlist_tracks",
            (PLAYLIST, ["b", "a"]),
            {},
        ),
        (
            "replace_playlist_tracks",
            {"playlist_id": PLAYLIST, "track_ids": []},
            "replace_playlist_tracks",
            (PLAYLIST, []),
            {},
        ),
        ("delete_playlist", {"playlist_id": PLAYLIST}, "delete_playlist", (PLAYLIST,), {}),
    ],
)
async def test_each_action_calls_the_matching_method(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    writes: MagicMock,
    listener: MagicMock,
    name: str,
    data: dict[str, Any],
    method: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    await setup(hass, entry, listener)
    assert await call(hass, name, data) is None
    getattr(writes, method).assert_awaited_once_with(*args, **kwargs)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("create_playlist", {"name": ""}),
        ("create_playlist", {"name": "x", "track_ids": ["album/al-6"]}),
        ("create_playlist", {"name": "x", "track_ids": ["playlist/subsonic%3A1"]}),
        ("rename_playlist", {"playlist_id": "", "name": "x"}),
        ("add_tracks_to_playlist", {"playlist_id": "p", "track_ids": []}),
        ("remove_tracks_from_playlist", {"playlist_id": "p", "positions": []}),
        ("remove_tracks_from_playlist", {"playlist_id": "p", "positions": [-1]}),
        ("delete_playlist", {}),
    ],
)
async def test_a_call_that_makes_no_sense_never_reaches_the_server(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    writes: MagicMock,
    listener: MagicMock,
    name: str,
    data: dict[str, Any],
) -> None:
    await setup(hass, entry, listener)
    with pytest.raises(probatio.Invalid):
        await call(hass, name, data)
    for method in WRITES:
        getattr(writes, method).assert_not_called()


async def test_a_server_that_does_not_allow_writes_says_how_to_change_that(
    hass: HomeAssistant, entry: MockConfigEntry, writes: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    writes.delete_playlist.side_effect = SnapDogReadOnlyError("read_only", status=403)
    with pytest.raises(ServiceValidationError) as raised:
        await call(hass, "delete_playlist", {"playlist_id": PLAYLIST})
    assert raised.value.translation_key == "read_only"


async def test_a_refusal_of_the_media_server_surfaces(
    hass: HomeAssistant, entry: MockConfigEntry, writes: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    writes.delete_playlist.side_effect = SnapDogUpstreamError("no right to edit", status=502)
    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "delete_playlist", {"playlist_id": PLAYLIST})
    assert not isinstance(raised.value, ServiceValidationError)
    assert raised.value.translation_key == "request_failed"
    assert "no right to edit" in raised.value.translation_placeholders["error"]


def another_server(entry: MockConfigEntry) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Upstairs",
        unique_id="fedcba9876543210",
        data={**entry.data, CONF_SERVER_ID: "fedcba9876543210"},
    )


async def test_with_two_servers_the_call_has_to_name_one(
    hass: HomeAssistant, entry: MockConfigEntry, writes: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    other = another_server(entry)
    other.add_to_hass(hass)
    await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    assert other.state is ConfigEntryState.LOADED
    with pytest.raises(ServiceValidationError) as raised:
        await call(hass, "delete_playlist", {"playlist_id": "p"})
    assert raised.value.translation_key == "server_required"
    assert raised.value.translation_placeholders == {"count": "2"}
    writes.delete_playlist.assert_not_called()
    await call(hass, "delete_playlist", {"playlist_id": "p", "config_entry_id": other.entry_id})
    writes.delete_playlist.assert_awaited_once_with("p")


async def test_a_server_that_is_not_ours_is_refused(
    hass: HomeAssistant, entry: MockConfigEntry, writes: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    stranger = MockConfigEntry(domain="other", title="Other")
    stranger.add_to_hass(hass)
    for entry_id in ("nonsense", stranger.entry_id):
        with pytest.raises(ServiceValidationError) as raised:
            await call(hass, "delete_playlist", {"playlist_id": "p", "config_entry_id": entry_id})
        assert raised.value.translation_key == "unknown_server"
    writes.delete_playlist.assert_not_called()


async def test_a_server_that_is_not_set_up_is_refused(
    hass: HomeAssistant, entry: MockConfigEntry, writes: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    other = another_server(entry)
    other.add_to_hass(hass)
    with pytest.raises(ServiceValidationError) as raised:
        await call(hass, "delete_playlist", {"playlist_id": "p", "config_entry_id": other.entry_id})
    assert raised.value.translation_key == "server_not_loaded"
    writes.delete_playlist.assert_not_called()
