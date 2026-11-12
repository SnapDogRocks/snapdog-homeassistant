"""Keep the state of one SnapDog server and push it to the entities."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pysnapdog import (
    Client,
    ClientStateChanged,
    EqConfig,
    Event,
    Playlist,
    PlaylistsChanged,
    SnapDogAuthError,
    SnapDogClient,
    SnapDogError,
    SnapDogListener,
    TrackMetadata,
    VersionInfo,
    Zone,
    ZoneChanged,
    ZoneVolumeChanged,
)

from .const import (
    CONF_API_KEY,
    CONF_TLS,
    CONF_VERIFY_TLS,
    DEFAULT_PORT,
    DOMAIN,
    LOGGER,
)
from .layout import ZoneLayout

type SnapDogConfigEntry = ConfigEntry[SnapDogCoordinator]


@dataclass(slots=True)
class SnapDogData:
    """Everything read from the server, replaced as events arrive."""

    version: VersionInfo
    zones: dict[int, Zone] = field(default_factory=dict)
    tracks: dict[int, TrackMetadata] = field(default_factory=dict)
    clients: dict[int, Client] = field(default_factory=dict)
    playlists: list[Playlist] = field(default_factory=list)
    # What the server reports about a client only on request and never as an event: the latency,
    # and the equalizer and the speaker correction of a SnapDog client. They are read at every
    # connect and follow the changes made through this integration.
    client_latency: dict[int, int] = field(default_factory=dict)
    client_eq: dict[int, EqConfig] = field(default_factory=dict)
    client_speaker: dict[int, EqConfig] = field(default_factory=dict)
    eq_presets: list[str] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)


class SnapDogCoordinator(DataUpdateCoordinator[SnapDogData]):
    """Load the state over REST and keep it current from the WebSocket.

    There is no polling. The listener opens the socket, calls ``_load`` and then delivers
    events, again after every reconnect and after the server closed the socket because this
    client fell behind. While the socket is down every entity is unavailable.
    """

    config_entry: SnapDogConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SnapDogConfigEntry) -> None:
        """Prepare the client and the listener. Nothing connects until ``async_start``."""
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.api = SnapDogClient(
            async_get_clientsession(hass, verify_ssl=entry.data[CONF_VERIFY_TLS]),
            entry.data[CONF_HOST],
            port=entry.data.get(CONF_PORT, DEFAULT_PORT),
            api_key=entry.data.get(CONF_API_KEY),
            tls=entry.data[CONF_TLS],
            verify_tls=entry.data[CONF_VERIFY_TLS],
        )
        self.connected = False
        self.layout = ZoneLayout(hass, entry.entry_id, entry.title)
        self._listener = SnapDogListener(
            self.api,
            on_event=self._on_event,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
        )

    async def async_start(self) -> None:
        """Read the state once so that setup fails visibly, then start listening."""
        try:
            self.async_set_updated_data(await self._load())
        except SnapDogAuthError as err:
            raise ConfigEntryAuthFailed from err
        except SnapDogError as err:
            raise ConfigEntryNotReady(f"cannot read the SnapDog server: {err}") from err
        await self.layout.async_check(self.data.zones)
        self._listener.start()

    async def async_stop(self) -> None:
        """Close the socket."""
        await self._listener.stop()

    async def _load(self) -> SnapDogData:
        """Read the whole state, the way the web interface does after a connect."""
        api = self.api
        version = await api.version()
        zones = {zone.index: zone for zone in await api.zones()}
        clients = {client.index: client for client in await api.clients()}
        tracks = {index: await api.zone_track(index) for index in zones}
        playlists = await api.playlists()
        data = SnapDogData(
            version=version, zones=zones, tracks=tracks, clients=clients, playlists=playlists
        )
        await self._load_client_settings(data)
        return data

    async def _load_client_settings(self, data: SnapDogData) -> None:
        """Read what the events do not carry. A client that cannot answer stays without it."""
        for index, client in data.clients.items():
            await self._read_client_settings(data, index, client)
        await self._read_eq_catalogue(data)

    async def _read_client_settings(self, data: SnapDogData, index: int, client: Client) -> None:
        api = self.api
        try:
            data.client_latency[index] = await api.client_latency(index)
            if client.is_snapdog:
                data.client_eq[index] = await api.client_eq(index)
                data.client_speaker[index] = await api.client_speaker(index)
        except SnapDogAuthError:
            raise
        except SnapDogError as err:
            LOGGER.debug("Cannot read the settings of client %s: %s", index, err)

    async def _read_eq_catalogue(self, data: SnapDogData) -> None:
        if not any(client.is_snapdog for client in data.clients.values()):
            return
        try:
            data.eq_presets = [preset.name for preset in await self.api.eq_presets()]
            data.speakers = await self.api.speakers()
        except SnapDogAuthError:
            raise
        except SnapDogError as err:
            # The speaker profiles come from a catalogue outside the server and may be out of
            # reach. The selects then have no options and the rest keeps working.
            LOGGER.debug("Cannot read the EQ presets or the speaker profiles: %s", err)

    def client_by_mac(self, mac: str) -> Client | None:
        """Find a client by its MAC address, whatever its position in the configuration."""
        for client in self.data.clients.values():
            if dr.format_mac(client.mac) == mac:
                return client
        return None

    def update_client(self, index: int, **changes: Any) -> None:
        """Take a change of a client, that the server answered, into the state."""
        data = self.data
        data.clients[index] = dataclasses.replace(data.clients[index], **changes)
        self.async_set_updated_data(data)

    def set_client_latency(self, index: int, milliseconds: int) -> None:
        """Remember the latency the server stored."""
        self.data.client_latency[index] = milliseconds
        self.async_set_updated_data(self.data)

    def set_client_eq(self, index: int, config: EqConfig) -> None:
        """Remember the equalizer the server stored."""
        self.data.client_eq[index] = config
        self.async_set_updated_data(self.data)

    def set_client_speaker(self, index: int, config: EqConfig) -> None:
        """Remember the speaker correction the server stored, and the equalizer stays."""
        self.data.client_speaker[index] = config
        self.async_set_updated_data(self.data)

    async def _on_connected(self) -> None:
        self.async_set_updated_data(await self._load())
        await self.layout.async_check(self.data.zones)
        if not self.connected:
            LOGGER.info("Connected to the SnapDog server")
        self.connected = True
        self.async_update_listeners()

    async def _on_disconnected(self, reason: SnapDogError) -> None:
        if isinstance(reason, SnapDogAuthError):
            self.config_entry.async_start_reauth(self.hass)
        if self.connected:
            LOGGER.warning("Lost the SnapDog server: %s", reason)
        self.connected = False
        self.async_update_listeners()

    async def _on_event(self, event: Event) -> None:
        """Fold one event into the state. Events about unknown zones are ignored."""
        data = self.data
        match event:
            case ZoneChanged():
                if event.zone not in data.zones:
                    return
                data.zones[event.zone] = dataclasses.replace(
                    data.zones[event.zone],
                    playback=event.playback,
                    source=event.source,
                    shuffle=event.shuffle,
                    repeat=event.repeat,
                    volume=event.volume,
                    muted=event.muted,
                )
                data.tracks[event.zone] = dataclasses.replace(
                    data.tracks[event.zone],
                    title=event.title,
                    artist=event.artist,
                    album=event.album,
                    source=event.source,
                    duration_ms=event.duration_ms,
                    position_ms=event.position_ms,
                    seekable=event.seekable,
                    can_next=event.can_next,
                    can_prev=event.can_prev,
                    can_playlist_next=event.can_playlist_next,
                    can_playlist_prev=event.can_playlist_prev,
                    album_artist=event.album_artist,
                    genre=event.genre,
                    year=event.year,
                    track_number=event.track_number,
                    disc_number=event.disc_number,
                    cover_url=event.cover_url,
                    bitrate_kbps=event.bitrate_kbps,
                    content_type=event.content_type,
                    playlist_index=event.playlist_index,
                    playlist_id=event.playlist_id,
                    playlist_name=event.playlist_name,
                    playlist_total=event.playlist_total,
                    playlist_track_index=event.track_index,
                    playlist_track_count=event.track_count,
                )
            case ZoneVolumeChanged():
                if event.zone not in data.zones:
                    return
                data.zones[event.zone] = dataclasses.replace(
                    data.zones[event.zone], volume=event.volume, muted=event.muted
                )
            case ClientStateChanged():
                if event.client not in data.clients:
                    return
                data.clients[event.client] = dataclasses.replace(
                    data.clients[event.client],
                    volume=event.volume,
                    muted=event.muted,
                    connected=event.connected,
                    zone_index=event.zone,
                    is_snapdog=event.is_snapdog,
                )
                if event.is_snapdog and event.client not in data.client_eq:
                    # A SnapDog client that connected after the last read: its equalizer and
                    # speaker correction were not there to read before.
                    await self._read_client_settings(data, event.client, data.clients[event.client])
                    await self._read_eq_catalogue(data)
            case PlaylistsChanged():
                # An index, a name or the whole list may have changed. Keep the old list when
                # the server cannot be read right now, the next event or reconnect reloads it.
                try:
                    data.playlists = await self.api.playlists()
                except SnapDogError as err:
                    LOGGER.warning("Cannot reload the playlists: %s", err)
                    return
            case _:
                return
        self.async_set_updated_data(data)
