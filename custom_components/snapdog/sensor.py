"""The server (version and counts) and what a zone plays from (source, bitrate, content type)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfDataRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogEntity, SnapDogServerEntity, async_setup_zone_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of the server and the diagnostic sensors of every zone."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ServerVersion(coordinator),
            ServerCount(coordinator, "zones"),
            ServerCount(coordinator, "clients"),
            ServerCount(coordinator, "radios"),
        ]
    )
    async_setup_zone_entities(
        entry,
        async_add_entities,
        lambda coordinator, zone: [
            ZoneSource(coordinator, zone),
            ZoneBitrate(coordinator, zone),
            ZoneContentType(coordinator, zone),
        ],
    )


class _ZoneDiagnostic(SnapDogEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _suffix: str

    def __init__(self, coordinator: SnapDogCoordinator, zone: int) -> None:
        """Set the identity."""
        super().__init__(coordinator, zone)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_zone_{zone}_{self._suffix}"


class ZoneSource(_ZoneDiagnostic):
    """The kind of source the zone plays, as the server names it (``radio``, ``airplay``, ...)."""

    _attr_translation_key = "source"
    _suffix = "source"

    @property
    def native_value(self) -> str:
        """The source the server reports."""
        return str(self.coordinator.data.zones[self._zone].source)


class ZoneBitrate(_ZoneDiagnostic):
    """The bitrate of the track, when the server knows it."""

    _attr_translation_key = "bitrate"
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_native_unit_of_measurement = UnitOfDataRate.KILOBITS_PER_SECOND
    _suffix = "bitrate"

    @property
    def native_value(self) -> int | None:
        """The bitrate in kbit/s, ``None`` when the source gives none."""
        return self.coordinator.data.tracks[self._zone].bitrate_kbps


class ZoneContentType(_ZoneDiagnostic):
    """The content type of the stream or file, when the server knows it."""

    _attr_translation_key = "content_type"
    _suffix = "content_type"

    @property
    def native_value(self) -> str | None:
        """The content type, ``None`` when the source gives none."""
        return self.coordinator.data.tracks[self._zone].content_type


class ServerVersion(SnapDogServerEntity, SensorEntity):
    """The version of the SnapDog server."""

    _attr_translation_key = "version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SnapDogCoordinator) -> None:
        """Set the identity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._server_id}_version"

    @property
    def native_value(self) -> str:
        """The version the server reports."""
        return self.coordinator.data.version.version

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """The Rust version the server was built with."""
        return {"rust_version": self.coordinator.data.version.rust_version}


class ServerCount(SnapDogServerEntity, SensorEntity):
    """How many zones, clients or radio stations the server has."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SnapDogCoordinator, what: str) -> None:
        """Set the identity. ``what`` is ``zones``, ``clients`` or ``radios``."""
        super().__init__(coordinator)
        self._what = what
        self._attr_translation_key = f"{what}_count"
        self._attr_unique_id = f"{self._server_id}_{what}_count"

    @property
    def native_value(self) -> int | None:
        """The count the server reports, ``None`` until it has been read."""
        status = self.coordinator.data.status
        return None if status is None else int(getattr(status, self._what))
