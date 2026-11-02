"""Setup, failure and unload of an entry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pysnapdog import (
    PlaylistsChanged,
    SnapDogAuthError,
    SnapDogConnectionError,
    SnapDogResponseError,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import make_playlist, make_zone


async def test_an_unreachable_server_retries_later(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.version.side_effect = SnapDogConnectionError("down")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    listener.return_value.start.assert_not_called()


async def test_a_rejected_key_starts_reauth(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.version.side_effect = SnapDogAuthError("no")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(hass.config_entries.flow.async_progress_by_handler("snapdog"))


async def test_a_key_rejected_later_starts_reauth(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await listener.call_args.kwargs["on_disconnected"](SnapDogAuthError("no"))
    await hass.async_block_till_done()
    assert any(hass.config_entries.flow.async_progress_by_handler("snapdog"))


async def test_the_listener_starts_after_setup_and_stops_on_unload(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    listener.return_value.start.assert_called_once()
    await hass.config_entries.async_unload(entry.entry_id)
    listener.return_value.stop.assert_awaited_once()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_the_server_and_its_zone_are_devices(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    registry = dr.async_get(hass)
    server = registry.async_get_device_by_identifier(
        ("snapdog", entry.data["server_id"]), entry.entry_id
    )
    zone = registry.async_get_device_by_identifier(
        ("snapdog", f"{entry.data['server_id']}_zone_1"), entry.entry_id
    )
    assert server is not None
    assert server.sw_version == "0.31.0"
    assert zone is not None
    assert zone.name == "Ground floor"
    assert zone.via_device_id == server.id


async def test_a_zone_that_appears_later_gets_a_player(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    api.zones.return_value = [make_zone(), make_zone(index=2, name="Kitchen")]
    api.zone_track.return_value = api.zone_track.return_value
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    assert hass.states.get("media_player.kitchen") is not None


@pytest.mark.parametrize("error", [SnapDogResponseError("boom", status=500)])
async def test_a_failed_reload_after_a_reconnect_does_not_kill_the_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    error: Exception,
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    api.zones.side_effect = error
    with pytest.raises(SnapDogResponseError):
        await listener.call_args.kwargs["on_connected"]()
    assert entry.state is ConfigEntryState.LOADED


async def test_the_playlists_are_read_with_the_state(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    playlists = entry.runtime_data.data.playlists
    assert [(p.index, p.playlist_id) for p in playlists] == [
        (0, "snapdog:radio"),
        (1, "subsonic:42"),
    ]


async def test_playlists_changed_reloads_the_list(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    coordinator = entry.runtime_data
    api.playlists.return_value = [make_playlist(), make_playlist(index=1, playlist_id="subsonic:7")]
    await coordinator._on_event(PlaylistsChanged(total=2))
    assert [p.playlist_id for p in coordinator.data.playlists] == ["snapdog:radio", "subsonic:7"]


async def test_the_playlists_are_read_again_after_a_reconnect(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    api.playlists.return_value = [make_playlist(index=0, playlist_id="snapdog:radio")]
    await listener.call_args.kwargs["on_connected"]()
    assert len(entry.runtime_data.data.playlists) == 1


async def test_a_failed_playlist_reload_keeps_the_old_list(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    coordinator = entry.runtime_data
    api.playlists.side_effect = SnapDogConnectionError("down")
    await coordinator._on_event(PlaylistsChanged(total=5))
    assert len(coordinator.data.playlists) == 2
