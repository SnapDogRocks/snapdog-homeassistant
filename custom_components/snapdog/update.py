"""The update of the SnapDog server, where the Docker updater sidecar runs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SnapDogConfigEntry, SnapDogCoordinator
from .entity import SnapDogServerEntity, async_setup_optional_server_entity, request

PARALLEL_UPDATES = 1

# The state of the sidecar while it replaces the server.
UPDATING_STATE = "updating"
RELEASES_URL = "https://github.com/SnapDogRocks/snapdog/releases"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnapDogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the update entity when the server has an updater sidecar."""
    async_setup_optional_server_entity(
        entry, async_add_entities, lambda data: data.update is not None, ServerUpdate
    )


class ServerUpdate(SnapDogServerEntity, UpdateEntity):
    """The release of the server, and the button that installs a newer one.

    The sidecar pulls the image, verifies it and restarts the server, so the connection drops
    while this runs and the entity is unavailable until the server is back.
    """

    _attr_translation_key = "server"
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    _attr_release_url = RELEASES_URL

    def __init__(self, coordinator: SnapDogCoordinator) -> None:
        """Set the identity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._server_id}_update"

    @property
    def available(self) -> bool:
        """Whether the server is reachable and still has the updater."""
        return super().available and self.coordinator.data.update is not None

    @property
    def installed_version(self) -> str | None:
        """The version that runs, as the updater and else the server itself report it."""
        update = self.coordinator.data.update
        if update is not None and update.current_version:
            return update.current_version
        return self.coordinator.data.version.version

    @property
    def latest_version(self) -> str | None:
        """The newest release the last check found, the installed one when there is none."""
        update = self.coordinator.data.update
        if update is not None and update.update_available and update.available_version:
            return update.available_version
        return self.installed_version

    @property
    def in_progress(self) -> bool:
        """Whether the sidecar is replacing the server."""
        update = self.coordinator.data.update
        return update is not None and update.state == UPDATING_STATE

    @property
    def release_summary(self) -> str | None:
        """The error of the last check or update, when it failed."""
        update = self.coordinator.data.update
        return None if update is None else update.last_error

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Start the update. The sidecar installs the release it found, not an older one."""
        self.coordinator.set_update(await request(self.coordinator.api.apply_update()))
