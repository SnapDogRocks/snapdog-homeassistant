"""Mute and equalizer of a client."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SERVER_ID
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogClientEntity, async_setup_client_entities, request

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a mute switch to every client and an equalizer switch to every SnapDog client."""
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
