"""Playback errors, playlist buttons, diagnostic sensors and equalizer actions of a zone."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pysnapdog import PlaybackError, SnapDogConnectionError, ZonePresenceChanged
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.snapdog.const import DOMAIN

from .conftest import SERVER_ID, make_eq, make_track
from .test_media_player import setup

ERROR = "event.ground_floor_playback_error"
NEXT = "button.ground_floor_next_playlist"
PREVIOUS = "button.ground_floor_previous_playlist"
SOURCE = "sensor.ground_floor_source"
BITRATE = "sensor.ground_floor_bitrate"
CONTENT_TYPE = "sensor.ground_floor_content_type"
PLAYER = "media_player.ground_floor"
TIMEOUT = "number.ground_floor_presence_timeout"
BAND = {"freq": 250, "gain": -2.5, "q": 1.2, "type": "peaking"}


async def send(hass: HomeAssistant, listener: MagicMock, event: object) -> None:
    await listener.call_args.kwargs["on_event"](event)
    await hass.async_block_till_done()


def state(hass: HomeAssistant, entity: str) -> str:
    found = hass.states.get(entity)
    assert found is not None, entity
    return found.state


async def test_the_zone_has_the_new_entities(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    registry = er.async_get(hass)
    expected = {
        ERROR: ("playback_error", None),
        NEXT: ("next_playlist", None),
        PREVIOUS: ("previous_playlist", None),
        SOURCE: ("source", "diagnostic"),
        BITRATE: ("bitrate", "diagnostic"),
        CONTENT_TYPE: ("content_type", "diagnostic"),
    }
    for entity, (suffix, category) in expected.items():
        found = registry.async_get(entity)
        assert found is not None, entity
        assert found.unique_id == f"{SERVER_ID}_zone_1_{suffix}"
        assert found.entity_category == category, entity
    for entity in (SOURCE, BITRATE, CONTENT_TYPE):
        assert registry.async_get(entity).disabled_by == er.RegistryEntryDisabler.INTEGRATION  # type: ignore[union-attr]


async def test_a_playback_error_is_an_event_with_its_details(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    assert state(hass, ERROR) == STATE_UNKNOWN
    await send(hass, listener, PlaybackError(1, "Stream is gone", "HTTP 404", recoverable=False))
    found = hass.states.get(ERROR)
    assert found is not None
    assert found.attributes["event_type"] == "playback_error"
    assert found.attributes["message"] == "Stream is gone"
    assert found.attributes["details"] == "HTTP 404"
    assert found.attributes["recoverable"] is False


async def test_a_playback_error_of_another_zone_is_not_this_zones(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await send(hass, listener, PlaybackError(9, "x", None, recoverable=True))
    assert state(hass, ERROR) == STATE_UNKNOWN


async def test_a_playback_error_changes_no_state_and_is_logged(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup(hass, entry, listener)
    before = state(hass, PLAYER)
    await send(hass, listener, PlaybackError(1, "Stream is gone", None, recoverable=True))
    assert state(hass, PLAYER) == before
    assert "Zone 1 cannot play: Stream is gone" in caplog.text


async def test_unloading_removes_the_error_listener(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    assert len(coordinator._playback_error_listeners) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert coordinator._playback_error_listeners == []


async def test_the_playlist_buttons_follow_what_the_server_allows(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zone_track.return_value = make_track(can_playlist_next=True, can_playlist_prev=False)
    await setup(hass, entry, listener)
    assert state(hass, NEXT) != STATE_UNAVAILABLE
    assert state(hass, PREVIOUS) == STATE_UNAVAILABLE


async def test_the_playlist_buttons_ask_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zone_track.return_value = make_track(can_playlist_next=True, can_playlist_prev=True)
    await setup(hass, entry, listener)
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: NEXT}, blocking=True)
    api.next_playlist.assert_awaited_once_with(1)
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: PREVIOUS}, blocking=True)
    api.previous_playlist.assert_awaited_once_with(1)


async def test_a_failed_playlist_change_is_an_error(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zone_track.return_value = make_track(can_playlist_next=True)
    api.next_playlist.side_effect = SnapDogConnectionError("down")
    await setup(hass, entry, listener)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: NEXT}, blocking=True)


async def test_the_diagnostic_sensors_tell_where_the_sound_comes_from(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zone_track.return_value = make_track(bitrate_kbps=320, content_type="audio/mpeg")
    await setup(hass, entry, listener)
    registry = er.async_get(hass)
    for entity in (SOURCE, BITRATE, CONTENT_TYPE):
        registry.async_update_entity(entity, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    assert state(hass, SOURCE) == "radio"
    assert state(hass, BITRATE) == "320"
    assert hass.states.get(BITRATE).attributes["unit_of_measurement"] == "kbit/s"  # type: ignore[union-attr]
    assert state(hass, CONTENT_TYPE) == "audio/mpeg"


async def test_set_zone_eq_replaces_the_bands_and_keeps_the_switch(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN, "set_zone_eq", {ATTR_ENTITY_ID: PLAYER, "bands": [BAND]}, blocking=True
    )
    index, sent = api.set_zone_eq.await_args.args
    assert index == 1
    assert sent.enabled is True
    assert [(b.freq, b.gain, b.q, str(b.type)) for b in sent.bands] == [
        (250.0, -2.5, 1.2, "peaking")
    ]
    assert sent.preset is None


async def test_set_zone_eq_can_only_switch(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN, "set_zone_eq", {ATTR_ENTITY_ID: PLAYER, "enabled": False}, blocking=True
    )
    assert api.set_zone_eq.await_args.args[1] == dataclasses.replace(make_eq(), enabled=False)


async def test_set_zone_eq_with_nothing_to_change_is_refused(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_zone_eq", {ATTR_ENTITY_ID: PLAYER}, blocking=True
        )


async def test_set_zone_eq_needs_an_equalizer_the_server_told(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zone_eq.side_effect = SnapDogConnectionError("no")
    await setup(hass, entry, listener)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_zone_eq", {ATTR_ENTITY_ID: PLAYER, "enabled": True}, blocking=True
        )
    api.set_zone_eq.assert_not_called()


async def test_set_zone_eq_band_counts_from_one(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN, "set_zone_eq_band", {ATTR_ENTITY_ID: PLAYER, "band": 3, **BAND}, blocking=True
    )
    index, band, value = api.set_zone_eq_band.await_args.args
    assert (index, band) == (1, 2)
    assert (value.freq, value.gain) == (250.0, -2.5)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_zone_eq_band",
            {ATTR_ENTITY_ID: PLAYER, "band": 11, **BAND},
            blocking=True,
        )


async def test_a_failed_equalizer_request_is_an_error(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    api.set_zone_eq.side_effect = SnapDogConnectionError("down")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "set_zone_eq", {ATTR_ENTITY_ID: PLAYER, "enabled": False}, blocking=True
        )


async def test_the_timeout_follows_the_presence_event_that_carries_it(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    assert state(hass, TIMEOUT) == "900"
    await send(
        hass,
        listener,
        ZonePresenceChanged(
            zone=1, presence=False, enabled=True, timer_active=False, auto_off_delay=120
        ),
    )
    assert state(hass, TIMEOUT) == "120"


async def test_an_event_without_the_timeout_leaves_it_as_it_is(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    """A server before the field sends none, and the value is the last one read or written."""
    await setup(hass, entry, listener)
    await send(
        hass,
        listener,
        ZonePresenceChanged(zone=1, presence=True, enabled=True, timer_active=True),
    )
    assert state(hass, TIMEOUT) == "900"
