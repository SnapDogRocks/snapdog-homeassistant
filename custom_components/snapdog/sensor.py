"""What a zone plays from: source, bitrate and content type. Diagnostics, off by default."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfDataRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogEntity, async_setup_zone_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the diagnostic sensors of every zone."""
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
