"""Next and previous playlist of a zone."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogEntity, async_setup_zone_entities, request

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the playlist buttons of every zone."""
    async_setup_zone_entities(
        entry,
        async_add_entities,
        lambda coordinator, zone: [
            ZoneNextPlaylist(coordinator, zone),
            ZonePreviousPlaylist(coordinator, zone),
        ],
    )


class _ZonePlaylistButton(SnapDogEntity, ButtonEntity):
    """Move the zone to the next or the previous playlist, the way a KNX wall switch does."""

    _suffix: str
    _flag: str

    def __init__(self, coordinator: SnapDogCoordinator, zone: int) -> None:
        """Set the identity."""
        super().__init__(coordinator, zone)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_zone_{zone}_{self._suffix}"

    @property
    def available(self) -> bool:
        """Whether there is another playlist to go to, as the server reports it."""
        return super().available and bool(
            getattr(self.coordinator.data.tracks[self._zone], self._flag)
        )


class ZoneNextPlaylist(_ZonePlaylistButton):
    """The next playlist."""

    _attr_translation_key = "next_playlist"
    _suffix = "next_playlist"
    _flag = "can_playlist_next"

    async def async_press(self) -> None:
        """Go to the next playlist. The server's event brings the state."""
        await request(self.coordinator.api.next_playlist(self._zone))


class ZonePreviousPlaylist(_ZonePlaylistButton):
    """The previous playlist."""

    _attr_translation_key = "previous_playlist"
    _suffix = "previous_playlist"
    _flag = "can_playlist_prev"

    async def async_press(self) -> None:
        """Go to the previous playlist. The server's event brings the state."""
        await request(self.coordinator.api.previous_playlist(self._zone))
