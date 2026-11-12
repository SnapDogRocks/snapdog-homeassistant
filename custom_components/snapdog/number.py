"""Volume and latency of a client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pysnapdog import EqBand, FilterType

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogClientEntity, async_setup_client_entities, request

PARALLEL_UPDATES = 1

LATENCY_LIMIT = 10_000

ATTR_ZONE = "zone"
ATTR_NAME = "name"
ATTR_SPEAKER = "speaker"
ATTR_ENABLED = "enabled"
ATTR_BANDS = "bands"
ATTR_BAND = "band"

SERVICE_MOVE_CLIENT = "move_client"
SERVICE_RENAME_CLIENT = "rename_client"
SERVICE_APPLY_SPEAKER_PROFILE = "apply_speaker_profile"
SERVICE_SET_CLIENT_EQ = "set_client_eq"
SERVICE_SET_CLIENT_EQ_BAND = "set_client_eq_band"

_BAND: dict[Any, Any] = {
    probatio.Required("freq"): probatio.All(probatio.Coerce(float), probatio.Range(min=1)),
    probatio.Required("gain"): probatio.Coerce(float),
    probatio.Required("q"): probatio.All(probatio.Coerce(float), probatio.Range(min=0.01)),
    probatio.Required("type"): probatio.In([str(kind) for kind in FilterType]),
}


def _band(value: Mapping[str, Any]) -> EqBand:
    return EqBand(
        freq=value["freq"], gain=value["gain"], q=value["q"], type=FilterType(value["type"])
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a volume and a latency for every client, and register the actions of the clients.

    The actions are registered here, and not with the others in ``__init__``, because the volume
    is the one entity of a client that a device target reaches exactly once: a target by device
    leaves out the entities of the ``config`` and ``diagnostic`` categories, and a platform with
    two entities per client would run the action twice.
    """
    async_setup_client_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [ClientVolume(coordinator, mac), ClientLatency(coordinator, mac)],
    )
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_MOVE_CLIENT,
        {probatio.Required(ATTR_ZONE): cv.positive_int},
        "async_move_client",
    )
    platform.async_register_entity_service(
        SERVICE_RENAME_CLIENT,
        {probatio.Required(ATTR_NAME): probatio.All(cv.string, probatio.Length(min=1))},
        "async_rename_client",
    )
    platform.async_register_entity_service(
        SERVICE_APPLY_SPEAKER_PROFILE,
        {probatio.Required(ATTR_SPEAKER): probatio.All(cv.string, probatio.Length(min=1))},
        "async_apply_speaker_profile",
    )
    platform.async_register_entity_service(
        SERVICE_SET_CLIENT_EQ,
        {
            probatio.Optional(ATTR_ENABLED): cv.boolean,
            probatio.Optional(ATTR_BANDS): probatio.All(
                cv.ensure_list, [_BAND], probatio.Length(min=1)
            ),
        },
        "async_set_eq",
    )
    platform.async_register_entity_service(
        SERVICE_SET_CLIENT_EQ_BAND,
        {
            probatio.Required(ATTR_BAND): probatio.All(cv.positive_int, probatio.Range(min=1)),
            **_BAND,
        },
        "async_set_eq_band",
    )


class ClientVolume(SnapDogClientEntity, NumberEntity):
    """The volume of a client. It ends at the maximum the server allows for this client."""

    _attr_translation_key = "volume"
    _attr_native_min_value = 0
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: SnapDogCoordinator, mac: str) -> None:
        """Set the identity."""
        super().__init__(coordinator, mac)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_client_{mac}_volume"

    @property
    def native_max_value(self) -> float:
        """The server's limit for this client."""
        client = self._client
        return client.max_volume if client is not None else 100

    @property
    def native_value(self) -> float | None:
        """The volume the server reports."""
        client = self._client
        return client.volume if client is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Ask for a volume. The server's event brings the state."""
        await request(self.coordinator.api.set_client_volume(self.client.index, round(value)))

    async def async_apply_speaker_profile(self, speaker: str) -> None:
        """Apply the correction of a named speaker."""
        await self.apply_speaker(speaker)

    async def async_set_eq(
        self, enabled: bool | None = None, bands: list[Mapping[str, Any]] | None = None
    ) -> None:
        """Replace the bands of the equalizer, switch it, or both."""
        parsed = None if bands is None else [_band(band) for band in bands]
        await self.replace_eq(enabled=enabled, bands=parsed)

    async def async_set_eq_band(self, band: int, **fields: Any) -> None:
        """Change one band of the equalizer, counted from 1."""
        await self.replace_band(band, _band(fields))


class ClientLatency(SnapDogClientEntity, NumberEntity):
    """The manual latency correction of a client, to line its sound up with the others."""

    _attr_translation_key = "latency"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = -LATENCY_LIMIT
    _attr_native_max_value = LATENCY_LIMIT
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SnapDogCoordinator, mac: str) -> None:
        """Set the identity."""
        super().__init__(coordinator, mac)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_client_{mac}_latency"

    @property
    def native_value(self) -> float | None:
        """The latency read from the server. ``None`` when the server did not answer."""
        client = self._client
        if client is None:
            return None
        return self.coordinator.data.client_latency.get(client.index)

    async def async_set_native_value(self, value: float) -> None:
        """Set the latency. The server sends no event for it, so the answer is the state."""
        client = self.client
        stored = await request(self.coordinator.api.set_client_latency(client.index, round(value)))
        self.coordinator.set_client_latency(client.index, stored)
