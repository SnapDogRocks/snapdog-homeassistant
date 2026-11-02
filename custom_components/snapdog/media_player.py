"""One media player per SnapDog zone."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.browse_media import BrowseMedia
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pysnapdog import RepeatMode as SnapDogRepeat
from pysnapdog import TrackMetadata, Zone

from .const import CONF_SERVER_ID, DOMAIN, POSITION_TOLERANCE
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogEntity, request
from .media import MediaRef, browse_playlist, browse_root, parse_media_id

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

# The server names its sources by type. Both spellings of the Subsonic ones occur: REST and
# WebSocket send the serde form, MQTT the display form. An unknown name is shown as it is.
_SOURCE_NAMES = {
    "radio": "Radio",
    "subsonicplaylist": "Subsonic",
    "subsonic_playlist": "Subsonic",
    "subsonictrack": "Subsonic",
    "subsonic_track": "Subsonic",
    "url": "URL",
    "airplay": "AirPlay",
    "spotify": "Spotify",
}

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
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.PLAY_MEDIA
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
        """The source that plays, by its name. Nothing while the zone is idle."""
        source = self._zone_state.source
        if not source or source == "idle":
            return None
        return _SOURCE_NAMES.get(source, source)

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

    async def async_browse_media(
        self, media_content_type: MediaType | str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        """The playlists of the server, and the tracks of one playlist.

        The tracks are read when a playlist is opened, never for the whole library.
        """
        playlists = self.coordinator.data.playlists
        if not media_content_id:
            return browse_root(playlists)
        ref = _media_ref(media_content_id, BrowseError)
        if ref.track is not None:
            raise _error(BrowseError, "invalid_media_id", media_id=media_content_id)
        name = next((item.name for item in playlists if item.playlist_id == ref.playlist_id), None)
        if name is None:
            raise _error(BrowseError, "unknown_playlist", playlist_id=ref.playlist_id)
        tracks = await request(self.coordinator.api.playlist_tracks_by_id(ref.playlist_id))
        return browse_playlist(ref.playlist_id, name, tracks, expanded=True)

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a playlist, or one track of it, by the media id the browser hands out.

        The server switches the zone to the playlist and starts at the track. The request names
        the playlist by its ``playlist_id``, so a playlist that moved meanwhile is still found.
        """
        ref = _media_ref(media_id, ServiceValidationError)
        if ref.media_type != media_type:
            raise _error(ServiceValidationError, "invalid_media_id", media_id=media_id)
        await request(self.coordinator.api.play_playlist_id(self._zone, ref.playlist_id, ref.track))


def _error(error: type[HomeAssistantError], key: str, **placeholders: str) -> HomeAssistantError:
    """A translated error of the given class."""
    return error(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
    )


def _media_ref(media_id: str, error: type[HomeAssistantError]) -> MediaRef:
    """Read a media id, or fail with ``error`` when it is not one of ours."""
    try:
        return parse_media_id(media_id)
    except ValueError as err:
        raise _error(error, "invalid_media_id", media_id=media_id) from err
