"""The SnapDog integration: multi-room audio zones as media players."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CONF_SERVER_ID, DOMAIN
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import client_identifier
from .layout import ZoneLayout
from .services import async_register_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once, whether or not a server is set up yet."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SnapDogConfigEntry) -> bool:
    """Read the server once, start listening and set up the platforms."""
    coordinator = SnapDogCoordinator(hass, entry)
    await coordinator.async_start()
    entry.runtime_data = coordinator
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.data[CONF_SERVER_ID])},
        name=entry.title,
        manufacturer="SnapDog",
        model="Server",
        sw_version=coordinator.data.version.version,
    )
    entry.async_on_unload(coordinator.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _keep_client_devices_current(hass, entry, coordinator)
    return True


def _keep_client_devices_current(
    hass: HomeAssistant, entry: SnapDogConfigEntry, coordinator: SnapDogCoordinator
) -> None:
    """Follow a rename of a client, and remove the device of a client the server no longer has.

    A device keeps the name it was created with, so the registry is told when the server reports
    another one. A client is gone when its MAC is no longer in the configuration, which is what
    deleting a ``[[client]]`` block does. Moving the block leaves the MAC, and the device with it.
    """
    registry = dr.async_get(hass)
    server_id = entry.data[CONF_SERVER_ID]
    prefix = client_identifier(server_id, "")[1]
    names: dict[str, str] = {}

    @callback
    def sync() -> None:
        current = {
            dr.format_mac(client.mac): client for client in coordinator.data.clients.values()
        }
        for mac, client in current.items():
            if names.get(mac) == client.name:
                continue
            names[mac] = client.name
            device = registry.async_get_device_by_identifier(
                client_identifier(server_id, mac), entry.entry_id
            )
            if device is not None and device.name != client.name:
                registry.async_update_device(device.id, name=client.name)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            for domain, identifier in device.identifiers:
                if (
                    domain == DOMAIN
                    and identifier.startswith(prefix)
                    and identifier.removeprefix(prefix) not in current
                ):
                    registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    sync()
    entry.async_on_unload(coordinator.async_add_listener(sync))


async def async_unload_entry(hass: HomeAssistant, entry: SnapDogConfigEntry) -> bool:
    """Unload the platforms. The listener stops through ``async_on_unload``."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SnapDogConfigEntry) -> None:
    """Forget the remembered zone positions and their repair issue with the entry."""
    await ZoneLayout(hass, entry.entry_id, entry.title).async_remove()
