"""Mute and equalizer of a client, presence control and equalizer of a zone, KNX programming."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import (
    SnapDogClientEntity,
    SnapDogEntity,
    SnapDogServerEntity,
    async_setup_client_entities,
    async_setup_optional_server_entity,
    async_setup_zone_entities,
    request,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a mute switch to every client and an equalizer switch to every SnapDog client.

    Every zone gets the switch of its presence automation and of its equalizer.
    """
    async_setup_zone_entities(
        entry,
        async_add_entities,
        lambda coordinator, zone: [
            ZonePresenceControl(coordinator, zone),
            ZoneEq(coordinator, zone),
        ],
    )
    async_setup_optional_server_entity(
        entry,
        async_add_entities,
        lambda data: data.knx_programming_mode is not None,
        KnxProgrammingMode,
    )
    async_setup_client_entities(
        entry, async_add_entities, lambda coordinator, mac: [ClientMute(coordinator, mac)]
    )
    async_setup_client_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [ClientEq(coordinator, mac)],
        only_snapdog=True,
    )


class ClientMute(SnapDogClientEntity, SwitchEntity):
    """Whether a client is muted."""

    _attr_translation_key = "mute"

    def __init__(self, coordinator: SnapDogCoordinator, mac: str) -> None:
        """Set the identity."""
        super().__init__(coordinator, mac)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_client_{mac}_mute"

    @property
    def is_on(self) -> bool | None:
        """The mute the server reports."""
        client = self._client
        return client.muted if client is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Mute the client."""
        await request(self.coordinator.api.set_client_mute(self.client.index, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unmute the client."""
        await request(self.coordinator.api.set_client_mute(self.client.index, False))


class ClientEq(SnapDogClientEntity, SwitchEntity):
    """Whether the equalizer of a SnapDog client is on. The bands stay as they are."""

    _attr_translation_key = "eq"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SnapDogCoordinator, mac: str) -> None:
        """Set the identity."""
        super().__init__(coordinator, mac)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_client_{mac}_eq"

    @property
    def available(self) -> bool:
        """Only a SnapDog client has an equalizer, and only once the server has told it."""
        client = self._client
        return (
            super().available
            and client is not None
            and client.is_snapdog
            and client.index in self.coordinator.data.client_eq
        )

    @property
    def is_on(self) -> bool | None:
        """Whether the equalizer is enabled."""
        client = self._client
        if client is None or client.index not in self.coordinator.data.client_eq:
            return None
        return self.coordinator.data.client_eq[client.index].enabled

    async def _enable(self, enabled: bool) -> None:
        client = self.client
        config = self.coordinator.data.client_eq[client.index]
        stored = await request(
            self.coordinator.api.set_client_eq(
                client.index, dataclasses.replace(config, enabled=enabled)
            )
        )
        self.coordinator.set_client_eq(client.index, stored)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the equalizer on."""
        await self._enable(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the equalizer off."""
        await self._enable(False)


class ZonePresenceControl(SnapDogEntity, SwitchEntity):
    """Whether the zone follows presence at all. Off, the server ignores what a detector says."""

    _attr_translation_key = "presence_control"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SnapDogCoordinator, zone: int) -> None:
        """Set the identity."""
        super().__init__(coordinator, zone)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_zone_{zone}_presence_control"

    @property
    def is_on(self) -> bool:
        """The automation the server reports."""
        return self.coordinator.data.zones[self._zone].presence_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Let the zone follow presence."""
        await request(self.coordinator.api.set_zone_presence_enabled(self._zone, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the zone from following presence."""
        await request(self.coordinator.api.set_zone_presence_enabled(self._zone, False))


class ZoneEq(SnapDogEntity, SwitchEntity):
    """Whether the equalizer of the zone is on. The bands stay as they are."""

    _attr_translation_key = "eq"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SnapDogCoordinator, zone: int) -> None:
        """Set the identity."""
        super().__init__(coordinator, zone)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_zone_{zone}_eq"

    @property
    def available(self) -> bool:
        """Only once the server has told the equalizer."""
        return super().available and self._zone in self.coordinator.data.zone_eq

    @property
    def is_on(self) -> bool | None:
        """Whether the equalizer is enabled."""
        config = self.coordinator.data.zone_eq.get(self._zone)
        return config.enabled if config is not None else None

    async def _enable(self, enabled: bool) -> None:
        config = self.coordinator.data.zone_eq[self._zone]
        stored = await request(
            self.coordinator.api.set_zone_eq(
                self._zone, dataclasses.replace(config, enabled=enabled)
            )
        )
        self.coordinator.set_zone_eq(self._zone, stored)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the equalizer on."""
        await self._enable(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the equalizer off."""
        await self._enable(False)


class KnxProgrammingMode(SnapDogServerEntity, SwitchEntity):
    """The KNX programming mode of the server, which ETS needs to give it a physical address.

    The server has this only when it runs as a KNX device. Left on, it stays on until it is
    switched off, as on any KNX device with a programming button.
    """

    _attr_translation_key = "knx_programming_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SnapDogCoordinator) -> None:
        """Set the identity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._server_id}_knx_programming_mode"

    @property
    def available(self) -> bool:
        """Whether the server is reachable and still runs as a KNX device."""
        return super().available and self.coordinator.data.knx_programming_mode is not None

    @property
    def is_on(self) -> bool | None:
        """The programming mode the server reports."""
        return self.coordinator.data.knx_programming_mode

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the programming mode on."""
        self.coordinator.set_knx_programming_mode(
            await request(self.coordinator.api.set_knx_programming_mode(True))
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the programming mode off."""
        self.coordinator.set_knx_programming_mode(
            await request(self.coordinator.api.set_knx_programming_mode(False))
        )
