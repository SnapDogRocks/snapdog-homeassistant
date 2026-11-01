"""One media player per SnapDog zone."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pysnapdog import RepeatMode as SnapDogRepeat
from pysnapdog import TrackMetadata, Zone

from .const import CONF_SERVER_ID, POSITION_TOLERANCE
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogEntity, request

PARALLEL_UPDATES = 1

_STATES = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
}
_REPEAT_TO_HA = {
    SnapDogRepeat.OFF: RepeatMode.OFF,
    SnapDogRepeat.TRACK: RepeatMode.ONE,
    SnapDogRepeat.PLAYLIST: RepeatMode.ALL,
}
_REPEAT_FROM_HA = {ha: sd for sd, ha in _REPEAT_TO_HA.items()}

_BASE_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a player for every zone the server has, and for zones that appear later."""
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new_zones() -> None:
        new = set(coordinator.data.zones) - known
        known.update(new)
        async_add_entities(SnapDogZonePlayer(coordinator, zone) for zone in sorted(new))

    add_new_zones()
    entry.async_on_unload(coordinator.async_add_listener(add_new_zones))


class SnapDogZonePlayer(SnapDogEntity, MediaPlayerEntity):
    """A zone. Its state comes from the server and never from what was asked for."""

    _attr_name = None
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, coordinator: SnapDogCoordinator, zone: int) -> None:
        """Set the identity and the position bookkeeping."""
        super().__init__(coordinator, zone)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_zone_{zone}_player"
        self._position: int | None = None
        self._position_updated_at: datetime | None = None
        self._position_key: tuple[object, ...] | None = None
        self._remember_position()

    @property
    def _zone_state(self) -> Zone:
        return self.coordinator.data.zones[self._zone]

    @property
    def _track(self) -> TrackMetadata:
        return self.coordinator.data.tracks[self._zone]

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._zone in self.coordinator.data.zones:
            self._remember_position()
        super()._handle_coordinator_update()

    def _remember_position(self) -> None:
        """Move the stored position only when it departs from the extrapolated one.

        A zone reports its position about once a second. Writing each of them would
        change the state every second, so the stored value is extrapolated by Home
        Assistant and replaced only after a jump, a seek or a change of track or state.
        """
        track = self._track
        now = dt_util.utcnow()
        key = (self.state, track.title, track.duration_ms)
        position = round(track.position_ms / 1000)
        if self._position is not None and self._position_updated_at is not None:
            expected: float = self._position
            if self.state is MediaPlayerState.PLAYING:
                expected += (now - self._position_updated_at).total_seconds()
            if key == self._position_key and abs(position - expected) <= POSITION_TOLERANCE:
                return
        self._position = position
        self._position_updated_at = now
        self._position_key = key

    @property
    def state(self) -> MediaPlayerState:
        """Playing, paused or idle. A zone is never off."""
        return _STATES.get(self._zone_state.playback, MediaPlayerState.IDLE)

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Seeking only while the current source allows it."""
        if self._track.seekable:
            return _BASE_FEATURES | MediaPlayerEntityFeature.SEEK
        return _BASE_FEATURES

    @property
    def volume_level(self) -> float:
        """Volume as a fraction."""
        return self._zone_state.volume / 100

    @property
    def is_volume_muted(self) -> bool:
        """Whether the zone is muted."""
        return self._zone_state.muted

    @property
    def shuffle(self) -> bool:
        """Whether the playlist is shuffled."""
        return self._zone_state.shuffle

    @property
    def repeat(self) -> RepeatMode:
        """The repeat mode in Home Assistant's words."""
        return _REPEAT_TO_HA[self._zone_state.repeat]

    @property
    def media_title(self) -> str | None:
        """Title of the current track."""
        return self._track.title or None

    @property
    def media_artist(self) -> str | None:
        """Artist of the current track."""
        return self._track.artist or None

    @property
    def media_album_name(self) -> str | None:
        """Album of the current track."""
        return self._track.album or None

    @property
    def media_album_artist(self) -> str | None:
        """Album artist of the current track."""
        return self._track.album_artist

    @property
    def media_track(self) -> int | None:
        """Track number on the album."""
        return self._track.track_number

    @property
    def media_duration(self) -> int | None:
        """Length of the current track in seconds."""
        return self._track.duration_ms // 1000 if self._track.duration_ms else None

    @property
    def media_position(self) -> int | None:
        """Position in seconds when it was last stored."""
        return self._position if self._track.duration_ms else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When ``media_position`` was valid."""
        return self._position_updated_at if self._track.duration_ms else None

    @property
    def media_playlist(self) -> str | None:
        """Name of the playlist."""
        return self._track.playlist_name

    @property
    def app_name(self) -> str | None:
        """The source that plays: radio, Subsonic, AirPlay, Spotify or a URL."""
        return self._zone_state.source or None

    async def async_media_play(self) -> None:
        """Start or resume."""
        await request(self.coordinator.api.play(self._zone))

    async def async_media_pause(self) -> None:
        """Pause."""
        await request(self.coordinator.api.pause(self._zone))

    async def async_media_stop(self) -> None:
        """Stop."""
        await request(self.coordinator.api.stop(self._zone))

    async def async_media_next_track(self) -> None:
        """Skip to the next track."""
        await request(self.coordinator.api.next_track(self._zone))

    async def async_media_previous_track(self) -> None:
        """Go back to the previous track."""
        await request(self.coordinator.api.previous_track(self._zone))

    async def async_media_seek(self, position: float) -> None:
        """Seek to a position in seconds."""
        await request(self.coordinator.api.seek(self._zone, position_ms=round(position * 1000)))

    async def async_set_volume_level(self, volume: float) -> None:
        """Set an absolute volume. Relative changes lag behind on the server."""
        await request(self.coordinator.api.set_zone_volume(self._zone, round(volume * 100)))

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute. The server needs a connected client in the zone for this."""
        await request(self.coordinator.api.set_zone_mute(self._zone, mute))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Switch shuffle on or off."""
        await request(self.coordinator.api.set_zone_shuffle(self._zone, shuffle))

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set the repeat mode."""
        await request(self.coordinator.api.set_zone_repeat(self._zone, _REPEAT_FROM_HA[repeat]))
