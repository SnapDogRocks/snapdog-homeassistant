"""Whether a client is connected, whether the countdown of a zone runs."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import (
    SnapDogClientEntity,
    SnapDogEntity,
    async_setup_client_entities,
    async_setup_zone_entities,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the connection state of every client and the presence countdown of every zone."""
    async_setup_zone_entities(
        entry,
        async_add_entities,
        lambda coordinator, zone: [ZonePresenceTimer(coordinator, zone)],
    )
    async_setup_client_entities(
        entry, async_add_entities, lambda coordinator, mac: [ClientConnected(coordinator, mac)]
    )


class ClientConnected(SnapDogClientEntity, BinarySensorEntity):
    """Whether the speaker is connected to the server."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SnapDogCoordinator, mac: str) -> None:
        """Set the identity."""
        super().__init__(coordinator, mac)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_client_{mac}_connected"

    @property
    def is_on(self) -> bool | None:
        """Whether the server has the client."""
        client = self._client
        return client.connected if client is not None else None


class ZonePresenceTimer(SnapDogEntity, BinarySensorEntity):
    """Whether the countdown that switches the zone off after presence ended is running."""

    _attr_translation_key = "presence_timer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SnapDogCoordinator, zone: int) -> None:
        """Set the identity."""
        super().__init__(coordinator, zone)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_zone_{zone}_presence_timer"

    @property
    def is_on(self) -> bool:
        """Whether the auto-off timer runs."""
        return self.coordinator.data.zones[self._zone].presence_timer_active
