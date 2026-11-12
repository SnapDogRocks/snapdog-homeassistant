"""Zone, speaker correction and equalizer preset of a client."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SERVER_ID, DOMAIN
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogClientEntity, async_setup_client_entities, request

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the zone select to every client, the other two to every SnapDog client."""
    async_setup_client_entities(
        entry, async_add_entities, lambda coordinator, mac: [ClientZone(coordinator, mac)]
    )
    async_setup_client_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [
            ClientSpeaker(coordinator, mac),
            ClientEqPreset(coordinator, mac),
        ],
        only_snapdog=True,
    )


def zone_options(coordinator: SnapDogCoordinator) -> dict[str, int]:
    """Map the label of every zone to its index. Two zones of one name get their number."""
    names = [zone.name for zone in coordinator.data.zones.values()]
    return {
        (zone.name if names.count(zone.name) == 1 else f"{zone.name} ({index})"): index
        for index, zone in sorted(coordinator.data.zones.items())
    }


class ClientZone(SnapDogClientEntity, SelectEntity):
    """The zone a client plays in. Choosing another one moves the client."""

    _attr_translation_key = "zone"

    def __init__(self, coordinator: SnapDogCoordinator, mac: str) -> None:
        """Set the identity."""
        super().__init__(coordinator, mac)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_client_{mac}_zone"

    @property
    def options(self) -> list[str]:
        """The zones of the server."""
        return list(zone_options(self.coordinator))

    @property
    def current_option(self) -> str | None:
        """The zone the server reports for the client."""
        client = self._client
        if client is None:
            return None
        for label, index in zone_options(self.coordinator).items():
            if index == client.zone_index:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        """Move the client. The server's event brings the state."""
        await self.async_move_client(zone_options(self.coordinator)[option])


class _SnapDogClientSelect(SnapDogClientEntity, SelectEntity):
    """A select that only a SnapDog client has, and only once the server told its settings."""

    _attr_entity_category = EntityCategory.CONFIG
    _suffix: str

    def __init__(self, coordinator: SnapDogCoordinator, mac: str) -> None:
        """Set the identity."""
        super().__init__(coordinator, mac)
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        self._attr_unique_id = f"{server_id}_client_{mac}_{self._suffix}"

    def _catalogue(self) -> list[str]:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Whether this is a SnapDog client with settings read and options to choose from."""
        client = self._client
        return (
            super().available
            and client is not None
            and client.is_snapdog
            and client.index in self.coordinator.data.client_eq
            and bool(self._catalogue())
        )

    @property
    def options(self) -> list[str]:
        """What the server offers."""
        return list(self._catalogue())

    def _check(self, option: str) -> None:
        if option not in self._catalogue():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_option",
                translation_placeholders={"option": option},
            )


class ClientSpeaker(_SnapDogClientSelect):
    """The speaker correction of a SnapDog client.

    The server answers with the bands in use and the name of the preset they came from.
    That name is taken as the speaker profile when it is one the server offers.
    """

    _attr_translation_key = "speaker"
    _suffix = "speaker"

    def _catalogue(self) -> list[str]:
        return self.coordinator.data.speakers

    @property
    def current_option(self) -> str | None:
        """The profile in use, ``None`` when the correction is custom or unknown."""
        client = self._client
        if client is None:
            return None
        config = self.coordinator.data.client_speaker.get(client.index)
        if config is None or config.preset not in self.coordinator.data.speakers:
            return None
        return config.preset

    async def async_select_option(self, option: str) -> None:
        """Apply the profile."""
        self._check(option)
        await self.apply_speaker(option)


class ClientEqPreset(_SnapDogClientSelect):
    """The equalizer preset of a SnapDog client."""

    _attr_translation_key = "eq_preset"
    _suffix = "eq_preset"

    def _catalogue(self) -> list[str]:
        return self.coordinator.data.eq_presets

    @property
    def current_option(self) -> str | None:
        """The preset the equalizer was loaded from, ``None`` when it was edited since."""
        client = self._client
        if client is None:
            return None
        config = self.coordinator.data.client_eq.get(client.index)
        if config is None or config.preset not in self.coordinator.data.eq_presets:
            return None
        return config.preset

    async def async_select_option(self, option: str) -> None:
        """Apply the preset."""
        self._check(option)
        client = self.client
        stored = await request(self.coordinator.api.apply_client_eq_preset(client.index, option))
        self.coordinator.set_client_eq(client.index, stored)
