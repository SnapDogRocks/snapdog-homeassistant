"""The SnapDog integration: multi-room audio zones as media players."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CONF_SERVER_ID, DOMAIN
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .layout import ZoneLayout
from .services import async_register_services

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]

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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SnapDogConfigEntry) -> bool:
    """Unload the platforms. The listener stops through ``async_on_unload``."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SnapDogConfigEntry) -> None:
    """Forget the remembered zone positions and their repair issue with the entry."""
    await ZoneLayout(hass, entry.entry_id, entry.title).async_remove()
