"""The zone media player against a mocked server."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.media_player import (
    ATTR_MEDIA_REPEAT,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_SHUFFLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    SERVICE_REPEAT_SET,
    SERVICE_SHUFFLE_SET,
    MediaPlayerEntityFeature,
    RepeatMode,
)
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_SEEK,
    SERVICE_MEDIA_STOP,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pysnapdog import (
    ClientStateChanged,
    SnapDogConnectionError,
    SnapDogResponseError,
    ZoneChanged,
    ZoneVolumeChanged,
)
from pysnapdog import (
    RepeatMode as SnapDogRepeat,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import make_track, make_zone

PLAYER = "media_player.ground_floor"


async def setup(hass: HomeAssistant, entry: MockConfigEntry, listener: MagicMock):
    """Set the entry up and return the coordinator, connected."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    return coordinator


def zone_changed(**changes: object) -> ZoneChanged:
    zone, track = make_zone(), make_track()
    values = {
        "zone": 1,
        "playback": zone.playback,
        "source": zone.source,
        "shuffle": zone.shuffle,
        "repeat": zone.repeat,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "duration_ms": track.duration_ms,
        "position_ms": track.position_ms,
        "seekable": track.seekable,
        "can_next": True,
        "can_prev": True,
        "can_playlist_next": False,
        "can_playlist_prev": False,
        "volume": zone.volume,
        "muted": zone.muted,
        "album_artist": None,
        "genre": None,
        "year": None,
        "track_number": track.track_number,
        "disc_number": None,
        "cover_url": None,
        "bitrate_kbps": None,
        "content_type": None,
        "track_index": 0,
        "track_count": 10,
        "playlist_index": 0,
        "playlist_id": "snapdog:radio",
        "playlist_name": "Radio",
        "playlist_total": 10,
    }
    values.update(changes)
    return ZoneChanged(**values)  # type: ignore[arg-type]


async def test_a_zone_becomes_a_player(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    state = hass.states.get(PLAYER)
    assert state is not None
    assert state.state == STATE_PLAYING
    assert state.attributes["volume_level"] == 0.4
    assert state.attributes["media_title"] == "Song"
    assert state.attributes["media_artist"] == "Artist"
    assert state.attributes["media_duration"] == 200
    assert state.attributes["app_name"] == "Radio"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & MediaPlayerEntityFeature.SEEK


async def test_entities_are_unavailable_until_the_socket_is_open(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).state == STATE_UNAVAILABLE
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).state == STATE_PLAYING
    await listener.call_args.kwargs["on_disconnected"](SnapDogConnectionError("gone"))
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("playback", "expected"),
    [
        ("playing", STATE_PLAYING),
        ("paused", STATE_PAUSED),
        ("stopped", STATE_IDLE),
        ("idle", STATE_IDLE),
    ],
)
async def test_the_state_follows_the_zone(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    playback: str,
    expected: str,
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(playback=playback))
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).state == expected


async def test_events_update_the_state_and_never_the_commands(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await hass.services.async_call(
        MEDIA_PLAYER,
        SERVICE_VOLUME_SET,
        {ATTR_ENTITY_ID: PLAYER, ATTR_MEDIA_VOLUME_LEVEL: 0.7},
        blocking=True,
    )
    api.set_zone_volume.assert_awaited_once_with(1, 70)
    # The server has not confirmed yet: the state stays where the server left it.
    assert hass.states.get(PLAYER).attributes["volume_level"] == 0.4
    await coordinator._on_event(ZoneVolumeChanged(zone=1, volume=70, muted=True))
    await hass.async_block_till_done()
    state = hass.states.get(PLAYER)
    assert state.attributes["volume_level"] == 0.7
    assert state.attributes["is_volume_muted"] is True


async def test_events_about_other_zones_or_clients_are_ignored(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(zone=9, volume=99))
    await coordinator._on_event(ZoneVolumeChanged(zone=9, volume=99, muted=False))
    await coordinator._on_event(
        ClientStateChanged(client=9, volume=1, muted=False, connected=True, zone=1, is_snapdog=True)
    )
    assert hass.states.get(PLAYER).attributes["volume_level"] == 0.4


@pytest.mark.parametrize(
    ("service", "data", "method", "args"),
    [
        (SERVICE_MEDIA_PLAY, {}, "play", (1,)),
        (SERVICE_MEDIA_PAUSE, {}, "pause", (1,)),
        (SERVICE_MEDIA_STOP, {}, "stop", (1,)),
        (SERVICE_MEDIA_NEXT_TRACK, {}, "next_track", (1,)),
        (SERVICE_MEDIA_PREVIOUS_TRACK, {}, "previous_track", (1,)),
        (SERVICE_VOLUME_MUTE, {ATTR_MEDIA_VOLUME_MUTED: True}, "set_zone_mute", (1, True)),
        (SERVICE_SHUFFLE_SET, {ATTR_MEDIA_SHUFFLE: True}, "set_zone_shuffle", (1, True)),
        (
            SERVICE_REPEAT_SET,
            {ATTR_MEDIA_REPEAT: RepeatMode.ALL},
            "set_zone_repeat",
            (1, SnapDogRepeat.PLAYLIST),
        ),
        (
            SERVICE_REPEAT_SET,
            {ATTR_MEDIA_REPEAT: RepeatMode.ONE},
            "set_zone_repeat",
            (1, SnapDogRepeat.TRACK),
        ),
    ],
)
async def test_commands_reach_the_server(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    service: str,
    data: dict[str, object],
    method: str,
    args: tuple[object, ...],
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        MEDIA_PLAYER, service, {ATTR_ENTITY_ID: PLAYER, **data}, blocking=True
    )
    getattr(api, method).assert_awaited_once_with(*args)


async def test_seek_sends_milliseconds(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        MEDIA_PLAYER,
        SERVICE_MEDIA_SEEK,
        {ATTR_ENTITY_ID: PLAYER, ATTR_MEDIA_SEEK_POSITION: 42.5},
        blocking=True,
    )
    api.seek.assert_awaited_once_with(1, position_ms=42500)


async def test_a_refused_command_is_an_error_with_the_servers_words(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    api.play.side_effect = SnapDogResponseError("nothing to play", status=400)
    with pytest.raises(HomeAssistantError, match="nothing to play"):
        await hass.services.async_call(
            MEDIA_PLAYER, SERVICE_MEDIA_PLAY, {ATTR_ENTITY_ID: PLAYER}, blocking=True
        )


async def test_seeking_is_offered_only_for_seekable_sources(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(seekable=False))
    await hass.async_block_till_done()
    features = hass.states.get(PLAYER).attributes[ATTR_SUPPORTED_FEATURES]
    assert not features & MediaPlayerEntityFeature.SEEK


async def test_a_position_that_runs_on_its_own_is_not_written_again(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A once-per-second stream must not become a state change per second."""
    coordinator = await setup(hass, entry, listener)
    changes = []
    hass.bus.async_listen("state_changed", changes.append)
    for second in range(1, 31):
        freezer.tick(1)
        await coordinator._on_event(zone_changed(position_ms=10_000 + second * 1000))
    await hass.async_block_till_done()
    assert len(changes) == 0


async def test_a_seek_moves_the_stored_position(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(position_ms=120_000))
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).attributes["media_position"] == 120


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("radio", "Radio"),
        ("subsonicplaylist", "Subsonic"),
        ("subsonic_track", "Subsonic"),
        ("url", "URL"),
        ("airplay", "AirPlay"),
        ("spotify", "Spotify"),
        ("somethingnew", "somethingnew"),
        ("idle", None),
    ],
)
async def test_the_source_is_shown_by_its_name(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    source: str,
    name: str | None,
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(source=source))
    await hass.async_block_till_done()
    state = hass.states.get(PLAYER)
    assert state is not None
    assert state.attributes.get("app_name") == name


async def test_a_zone_changed_event_carries_the_playlist_by_index_and_id(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    """The event has ``playlist_index`` and ``playlist_id`` and no ``playlist`` field."""
    event = zone_changed(playlist_index=4, playlist_id="subsonic:42", playlist_name="Jazz")
    assert "playlist" not in {field.name for field in dataclasses.fields(event)}
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(event)
    track = coordinator.data.tracks[1]
    assert track.playlist_index == 4
    assert track.playlist_id == "subsonic:42"
    assert track.playlist_name == "Jazz"


async def test_a_zone_without_a_playlist_keeps_none(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(
        zone_changed(playlist_index=None, playlist_id=None, playlist_name=None)
    )
    track = coordinator.data.tracks[1]
    assert track.playlist_index is None
    assert track.playlist_id is None
