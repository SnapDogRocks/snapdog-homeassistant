"""Shared base of the SnapDog entities."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pysnapdog import SnapDogError, SnapDogReadOnlyError

from .const import CONF_SERVER_ID, DOMAIN
from .coordinator import SnapDogCoordinator

_T = TypeVar("_T")


class SnapDogEntity(CoordinatorEntity[SnapDogCoordinator]):
    """An entity of one zone. Unavailable while the WebSocket is down."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SnapDogCoordinator, zone: int) -> None:
        """Remember the zone and describe its device."""
        super().__init__(coordinator)
        self._zone = zone
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        server = dr.async_get(coordinator.hass).async_get_device_by_identifier(
            (DOMAIN, server_id), coordinator.config_entry.entry_id
        )
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{server_id}_zone_{zone}")},
            name=coordinator.data.zones[zone].name,
            manufacturer="SnapDog",
            model="Zone",
        )
        if server is not None:
            info["via_device_id"] = server.id
        self._attr_device_info = info

    @property
    def available(self) -> bool:
        """Whether the server is reachable and still has this zone."""
        return (
            self.coordinator.connected
            and super().available
            and self._zone in self.coordinator.data.zones
        )


async def request(call: Awaitable[_T]) -> _T:
    """Await a call to the server and turn its failure into a Home Assistant error."""
    try:
        return await call
    except SnapDogReadOnlyError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="read_only",
            translation_placeholders={"error": str(err)},
        ) from err
    except SnapDogError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="request_failed",
            translation_placeholders={"error": str(err)},
        ) from err
