"""Whether a client is connected."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogClientEntity, async_setup_client_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the connection state of every client."""
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
