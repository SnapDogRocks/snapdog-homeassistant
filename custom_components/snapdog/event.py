"""Playback errors of a zone."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pysnapdog import PlaybackError

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogEntity, async_setup_zone_entities

PARALLEL_UPDATES = 0

EVENT_PLAYBACK_ERROR = "playback_error"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the playback error event of every zone."""
    async_setup_zone_entities(
        entry, async_add_entities, lambda coordinator, zone: [ZonePlaybackError(coordinator, zone)]
    )


class ZonePlaybackError(SnapDogEntity, EventEntity):
    """Fires when the zone could not play: a dead stream, a decoder or a cache failure.

    The event carries the ``message`` for people, the ``details`` the server has (or ``None``)
    and whether the error is ``recoverable``, so that an automation can tell a station that is
    gone from a hiccup of the network.
    """

    _attr_translation_key = "playback_error"

    def __init__(self, coordinator: SnapDogCoordinator, zone: int) -> None:
        """Set the identity."""
        super().__init__(coordinator, zone)
        self._attr_event_types = [EVENT_PLAYBACK_ERROR]
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_zone_{zone}_playback_error"

    async def async_added_to_hass(self) -> None:
        """Listen for the errors of the server, for as long as the entity exists."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_playback_error_listener(self._handle_playback_error)
        )

    @callback
    def _handle_playback_error(self, error: PlaybackError) -> None:
        if error.zone != self._zone:
            return
        self._trigger_event(
            EVENT_PLAYBACK_ERROR,
            {
                "message": error.message,
                "details": error.details,
                "recoverable": error.recoverable,
            },
        )
        self.async_write_ha_state()
