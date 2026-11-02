"""Fixtures: a SnapDog server that exists only as mocks of pysnapdog."""

from __future__ import annotations

import dataclasses
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from pysnapdog import RepeatMode, TrackMetadata, VersionInfo, Zone
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
        client.version = AsyncMock(return_value=VersionInfo("SnapDog", "0.30.0", "1.90"))
        client.zones = AsyncMock(return_value=[make_zone()])
        client.clients = AsyncMock(return_value=[])
        client.zone_track = AsyncMock(return_value=make_track())
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
        ):
            setattr(client, name, AsyncMock(return_value=None))
        yield client


@pytest.fixture
def listener() -> Generator[MagicMock]:
    """The listener does not connect. Tests call its callbacks themselves."""
    with patch("custom_components.snapdog.coordinator.SnapDogListener") as cls:
        instance = MagicMock()
        instance.stop = AsyncMock()
        cls.return_value = instance
        yield cls
