"""The SnapDog integration: multi-room audio zones as media players."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_SERVER_ID, DOMAIN
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]


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
