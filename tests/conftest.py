"""Fixtures: a SnapDog server that exists only as mocks of pysnapdog."""

from __future__ import annotations

import dataclasses
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from pysnapdog import (
    Client,
    EqBand,
    EqConfig,
    EqPreset,
    FilterType,
    Image,
    MediaTrack,
    Playlist,
    RepeatMode,
    SearchItem,
    SearchKind,
    SearchResult,
    TrackMetadata,
    VersionInfo,
    Zone,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.snapdog.const import (
    CONF_API_KEY,
    CONF_SERVER_ID,
    CONF_TLS,
    CONF_VERIFY_TLS,
    DOMAIN,
)

SERVER_ID = "0123456789abcdef"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load ``custom_components/snapdog``."""


def make_zone(**changes: Any) -> Zone:
    zone = Zone(
        index=1,
        name="Ground floor",
        icon="mdi:home",
        volume=40,
        muted=False,
        playback="playing",
        source="radio",
        shuffle=False,
        repeat=RepeatMode.OFF,
        presence=False,
        presence_enabled=False,
        presence_timer_active=False,
    )
    return _replace(zone, changes)


def make_client(**changes: Any) -> Client:
    client = Client(
        index=1,
        name="Kitchen speaker",
        icon="mdi:speaker",
        mac="02:42:ac:11:00:10",
        volume=30,
        max_volume=80,
        muted=False,
        connected=True,
        is_snapdog=True,
        zone_index=1,
    )
    return _replace(client, changes)


def make_eq(**changes: Any) -> EqConfig:
    config = EqConfig(
        enabled=True,
        bands=(EqBand(freq=100.0, gain=3.0, q=0.7, type=FilterType.LOW_SHELF),) * 10,
        preset="Flat",
    )
    return _replace(config, changes)


def _replace(obj: Any, changes: dict[str, Any]) -> Any:
    return dataclasses.replace(obj, **changes)


def make_track(**changes: Any) -> TrackMetadata:
    track = TrackMetadata(
        title="Song",
        artist="Artist",
        album="Album",
        source="radio",
        duration_ms=200_000,
        position_ms=10_000,
        seekable=True,
        can_next=True,
        can_prev=True,
        can_playlist_next=False,
        can_playlist_prev=False,
        album_artist=None,
        genre=None,
        year=None,
        track_number=3,
        disc_number=None,
        cover_url=None,
        bitrate_kbps=None,
        sample_rate=None,
        content_type=None,
        playlist_index=0,
        playlist_id="snapdog:radio",
        playlist_name="Radio",
        playlist_total=10,
        playlist_track_index=2,
        playlist_track_count=10,
    )
    return _replace(track, changes)


def make_playlist(**changes: Any) -> Playlist:
    playlist = Playlist(
        index=0,
        playlist_id="snapdog:radio",
        name="Radio",
        song_count=10,
        duration=0,
        cover_art=None,
    )
    return _replace(playlist, changes)


def make_media_track(**changes: Any) -> MediaTrack:
    track = MediaTrack(
        id="tr-1",
        track=0,
        title="So What",
        artist="Miles Davis",
        album="Kind of Blue",
        duration=545,
        cover_art=None,
    )
    return _replace(track, changes)


def make_search_item(**changes: Any) -> SearchItem:
    item = SearchItem(
        kind=SearchKind.TRACK,
        title="So What",
        subtitle="Miles Davis",
        duration=545,
        cover_art=None,
        track_id="tr-1",
        album_id=None,
        playlist_id=None,
        index=None,
        track=None,
    )
    return _replace(item, changes)


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="SnapDog 192.0.2.10",
        unique_id=SERVER_ID,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 5555,
            CONF_API_KEY: "secret",
            CONF_TLS: False,
            CONF_VERIFY_TLS: True,
            CONF_SERVER_ID: SERVER_ID,
        },
    )


@pytest.fixture
def api() -> Generator[MagicMock]:
    """The client the coordinator builds, with one zone."""
    with patch("custom_components.snapdog.coordinator.SnapDogClient") as cls:
        client = cls.return_value
        client.version = AsyncMock(return_value=VersionInfo("SnapDog", "0.31.0", "1.90"))
        client.zones = AsyncMock(return_value=[make_zone()])
        client.clients = AsyncMock(return_value=[])
        client.client_latency = AsyncMock(return_value=20)
        client.client_eq = AsyncMock(return_value=make_eq())
        client.client_speaker = AsyncMock(return_value=make_eq(preset="Genelec 8030C"))
        client.eq_presets = AsyncMock(
            return_value=[EqPreset("Flat", make_eq().bands), EqPreset("Bass", make_eq().bands)]
        )
        client.speakers = AsyncMock(return_value=["Genelec 8030C", "KEF LS50"])
        client.zone_track = AsyncMock(return_value=make_track())
        client.playlists = AsyncMock(
            return_value=[
                make_playlist(),
                make_playlist(index=1, playlist_id="subsonic:42", name="Jazz", song_count=3),
            ]
        )
        client.playlist_tracks_by_id = AsyncMock(
            return_value=[
                make_media_track(),
                make_media_track(id="tr-2", track=1, title="Blue in Green", artist=""),
            ]
        )
        cover = Image(content=b"\x89PNG cover", content_type="image/png")
        for name in ("zone_cover", "playlist_cover", "track_cover", "media_cover"):
            setattr(client, name, AsyncMock(return_value=cover))
        client.search = AsyncMock(return_value=SearchResult(items=(), next_offset=None))
        for name in (
            "play",
            "pause",
            "stop",
            "next_track",
            "previous_track",
            "seek",
            "set_zone_volume",
            "set_zone_mute",
            "set_zone_shuffle",
            "set_zone_repeat",
            "set_zone_playlist_id",
            "play_playlist_id",
            "play_subsonic_track",
            "play_subsonic_album",
            "set_client_volume",
            "set_client_mute",
            "set_client_zone",
        ):
            setattr(client, name, AsyncMock(return_value=None))
        client.set_client_latency = AsyncMock(side_effect=lambda _index, value: value)
        client.rename_client = AsyncMock(side_effect=lambda _index, name: name)
        client.set_client_eq = AsyncMock(side_effect=lambda _index, config: config)
        client.set_client_eq_band = AsyncMock(return_value=make_eq())
        client.apply_client_eq_preset = AsyncMock(return_value=make_eq(preset="Bass"))
        client.apply_client_speaker = AsyncMock(return_value=make_eq(preset="KEF LS50"))
        yield client


@pytest.fixture
def listener() -> Generator[MagicMock]:
    """The listener does not connect. Tests call its callbacks themselves."""
    with patch("custom_components.snapdog.coordinator.SnapDogListener") as cls:
        instance = MagicMock()
        instance.stop = AsyncMock()
        cls.return_value = instance
        yield cls
