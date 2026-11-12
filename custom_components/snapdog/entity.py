"""Shared base of the SnapDog entities."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pysnapdog import Client, EqBand, EqConfig, SnapDogError, SnapDogReadOnlyError

from .const import CONF_SERVER_ID, DOMAIN
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator

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


class SnapDogClientEntity(CoordinatorEntity[SnapDogCoordinator]):
    """An entity of one client (a speaker). Unavailable while the WebSocket is down.

    A client is identified by its MAC address, and not by its position in ``snapdog.toml``,
    so that history, areas and automations stay with the speaker when the ``[[client]]`` blocks
    are inserted, deleted or reordered. The position (``index``) is what the API paths use and
    is looked up again for every call.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: SnapDogCoordinator, mac: str) -> None:
        """Remember the MAC and describe the device of the client."""
        super().__init__(coordinator)
        self._mac = mac
        server_id = coordinator.config_entry.data[CONF_SERVER_ID]
        server = dr.async_get(coordinator.hass).async_get_device_by_identifier(
            (DOMAIN, server_id), coordinator.config_entry.entry_id
        )
        client = coordinator.client_by_mac(mac)
        assert client is not None
        info = DeviceInfo(
            identifiers={client_identifier(server_id, mac)},
            connections={(dr.CONNECTION_NETWORK_MAC, mac)},
            name=client.name,
            manufacturer="SnapDog",
            model="SnapDog client" if client.is_snapdog else "Snapcast client",
        )
        if server is not None:
            info["via_device_id"] = server.id
        self._attr_device_info = info

    @property
    def _client(self) -> Client | None:
        """The client as the server last reported it, ``None`` once the server dropped it."""
        return self.coordinator.client_by_mac(self._mac)

    @property
    def client(self) -> Client:
        """The client for a command: it must still exist, and its index is the current one."""
        client = self._client
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="client_gone",
                translation_placeholders={"mac": self._mac},
            )
        return client

    @property
    def available(self) -> bool:
        """Whether the server is reachable and still has this client."""
        return self.coordinator.connected and super().available and self._client is not None

    async def async_move_client(self, zone: int) -> None:
        """Put the client into the zone with this index."""
        if zone not in self.coordinator.data.zones:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_zone",
                translation_placeholders={"zone": str(zone)},
            )
        await request(self.coordinator.api.set_client_zone(self.client.index, zone))

    def _snapdog_client(self) -> Client:
        """The client, for something only a SnapDog client can do."""
        client = self.client
        if not client.is_snapdog:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_snapdog_client",
                translation_placeholders={"name": client.name},
            )
        return client

    async def apply_speaker(self, speaker: str) -> None:
        """Apply the correction of a named speaker, and remember what the server stored."""
        client = self._snapdog_client()
        stored = await request(
            self.coordinator.api.apply_client_speaker(client.index, speaker=speaker)
        )
        self.coordinator.set_client_speaker(client.index, stored)

    async def replace_eq(self, *, enabled: bool | None, bands: list[EqBand] | None) -> None:
        """Replace the bands of the equalizer, switch it, or both."""
        client = self._snapdog_client()
        if enabled is None and bands is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nothing_to_change"
            )
        current = self.coordinator.data.client_eq.get(client.index)
        if current is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="eq_unknown",
                translation_placeholders={"name": client.name},
            )
        config = EqConfig(
            enabled=current.enabled if enabled is None else enabled,
            bands=current.bands if bands is None else tuple(bands),
            preset=current.preset if bands is None else None,
        )
        stored = await request(self.coordinator.api.set_client_eq(client.index, config))
        self.coordinator.set_client_eq(client.index, stored)

    async def replace_band(self, band: int, value: EqBand) -> None:
        """Change one band of the equalizer. ``band`` counts from 1."""
        client = self._snapdog_client()
        current = self.coordinator.data.client_eq.get(client.index)
        if current is not None and band > len(current.bands):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_band",
                translation_placeholders={"band": str(band), "count": str(len(current.bands))},
            )
        stored = await request(
            self.coordinator.api.set_client_eq_band(client.index, band - 1, value)
        )
        self.coordinator.set_client_eq(client.index, stored)

    async def async_rename_client(self, name: str) -> None:
        """Give the client another name."""
        client = self.client
        stored = await request(self.coordinator.api.rename_client(client.index, name))
        self.coordinator.update_client(client.index, name=stored)


def client_identifier(server_id: str, mac: str) -> tuple[str, str]:
    """The device registry identifier of a client."""
    return (DOMAIN, f"{server_id}_client_{mac}")


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


type ClientEntityFactory = Callable[[SnapDogCoordinator, str], list[Entity]]


def async_setup_client_entities(
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: ClientEntityFactory,
    *,
    only_snapdog: bool = False,
) -> None:
    """Add the entities of every client the server has, and of clients that appear later.

    A client is identified by its MAC. ``only_snapdog`` leaves out the clients that are plain
    Snapcast clients: they have no equalizer and no speaker correction. A client that turns out
    to be a SnapDog client later gets its entities then.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_clients() -> None:
        new: list[Entity] = []
        for client in coordinator.data.clients.values():
            mac = dr.format_mac(client.mac)
            if mac in known or (only_snapdog and not client.is_snapdog):
                continue
            known.add(mac)
            new.extend(factory(coordinator, mac))
        if new:
            async_add_entities(new)

    add_new_clients()
    entry.async_on_unload(coordinator.async_add_listener(add_new_clients))
