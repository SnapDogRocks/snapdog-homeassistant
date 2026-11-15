"""The entities of the server itself: version, counts, update and the KNX programming mode."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pysnapdog import (
    SnapDogAuthError,
    SnapDogBusyError,
    SnapDogConnectionError,
    SnapDogResponseError,
    SystemStatus,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.snapdog.const import DOMAIN
from custom_components.snapdog.coordinator import SERVER_POLL_INTERVAL

from .conftest import SERVER_ID, make_update
from .test_media_player import setup

VERSION = "sensor.snapdog_192_0_2_10_version"
ZONES = "sensor.snapdog_192_0_2_10_zones"
CLIENTS = "sensor.snapdog_192_0_2_10_clients"
RADIOS = "sensor.snapdog_192_0_2_10_radio_stations"
KNX = "switch.snapdog_192_0_2_10_knx_programming_mode"
UPDATE = "update.snapdog_192_0_2_10_server"


def state(hass: HomeAssistant, entity: str) -> str:
    found = hass.states.get(entity)
    assert found is not None, entity
    return found.state


async def poll(hass: HomeAssistant) -> None:
    """Let the minute pass that the server state is read again after."""
    async_fire_time_changed(hass, dt_util.utcnow() + SERVER_POLL_INTERVAL + timedelta(seconds=1))
    await hass.async_block_till_done()


async def test_a_plain_server_has_version_and_counts_only(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    registry = er.async_get(hass)
    assert state(hass, VERSION) == "0.31.0"
    version = hass.states.get(VERSION)
    assert version is not None
    assert version.attributes["rust_version"] == "1.90"
    expected = {
        VERSION: ("version", None),
        ZONES: ("zones_count", er.RegistryEntryDisabler.INTEGRATION),
        CLIENTS: ("clients_count", er.RegistryEntryDisabler.INTEGRATION),
        RADIOS: ("radios_count", er.RegistryEntryDisabler.INTEGRATION),
    }
    for entity, (suffix, disabled) in expected.items():
        found = registry.async_get(entity)
        assert found is not None, entity
        assert found.unique_id == f"{SERVER_ID}_{suffix}"
        assert found.entity_category == "diagnostic"
        assert found.disabled_by == disabled
    # No updater sidecar and no KNX device mode: nothing to show for either.
    assert registry.async_get(UPDATE) is None
    assert registry.async_get(KNX) is None


async def test_the_counts_follow_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for suffix in ("zones_count", "clients_count", "radios_count"):
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{SERVER_ID}_{suffix}",
            config_entry=entry,
            suggested_object_id=f"snapdog_192_0_2_10_{suffix.split('_')[0]}"
            if suffix != "radios_count"
            else "snapdog_192_0_2_10_radio_stations",
        )
    await setup(hass, entry, listener)
    assert state(hass, ZONES) == "1"
    assert state(hass, CLIENTS) == "0"
    assert state(hass, RADIOS) == "12"
    api.status.return_value = SystemStatus("0.31.0", 2, 3, 14)
    await poll(hass)
    assert state(hass, ZONES) == "2"
    assert state(hass, CLIENTS) == "3"
    assert state(hass, RADIOS) == "14"


async def test_a_failed_status_leaves_the_counts_unknown(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.status.side_effect = SnapDogConnectionError("down")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{SERVER_ID}_zones_count",
        config_entry=entry,
        suggested_object_id="snapdog_192_0_2_10_zones",
    )
    await setup(hass, entry, listener)
    assert state(hass, ZONES) == "unknown"
    assert state(hass, VERSION) == "0.31.0"


async def test_the_knx_switch_exists_for_a_knx_device(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.knx_programming_mode = AsyncMock(side_effect=None, return_value=False)
    await setup(hass, entry, listener)
    registry = er.async_get(hass)
    found = registry.async_get(KNX)
    assert found is not None
    assert found.unique_id == f"{SERVER_ID}_knx_programming_mode"
    assert found.entity_category == "config"
    assert state(hass, KNX) == STATE_OFF

    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: KNX}, blocking=True)
    api.set_knx_programming_mode.assert_awaited_with(True)
    assert state(hass, KNX) == STATE_ON
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: KNX}, blocking=True)
    api.set_knx_programming_mode.assert_awaited_with(False)
    assert state(hass, KNX) == STATE_OFF


async def test_the_knx_switch_reports_a_refusal_and_goes_when_the_mode_ends(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.knx_programming_mode = AsyncMock(side_effect=None, return_value=True)
    await setup(hass, entry, listener)
    assert state(hass, KNX) == STATE_ON
    api.set_knx_programming_mode.side_effect = SnapDogBusyError("KNX device", status=503)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: KNX}, blocking=True)
    assert state(hass, KNX) == STATE_ON

    api.knx_programming_mode.side_effect = SnapDogResponseError("not a KNX device", status=409)
    await poll(hass)
    assert state(hass, KNX) == STATE_UNAVAILABLE


async def test_the_update_entity_shows_a_release_and_installs_it(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.update_status.side_effect = None
    api.update_status.return_value = make_update(
        update_available=True, available_version="0.32.0", last_error="a check failed"
    )
    await setup(hass, entry, listener)
    registry = er.async_get(hass)
    found = registry.async_get(UPDATE)
    assert found is not None
    assert found.unique_id == f"{SERVER_ID}_update"
    assert state(hass, UPDATE) == STATE_ON
    attributes = hass.states.get(UPDATE).attributes  # type: ignore[union-attr]
    assert attributes["installed_version"] == "0.31.0"
    assert attributes["latest_version"] == "0.32.0"
    assert attributes["release_summary"] == "a check failed"
    assert attributes["in_progress"] is False

    await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: UPDATE}, blocking=True)
    api.apply_update.assert_awaited_once()
    attributes = hass.states.get(UPDATE).attributes  # type: ignore[union-attr]
    assert attributes["in_progress"] is True


async def test_a_current_server_has_nothing_to_install(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.update_status.side_effect = None
    api.update_status.return_value = make_update(current_version=None)
    await setup(hass, entry, listener)
    assert state(hass, UPDATE) == STATE_OFF
    attributes = hass.states.get(UPDATE).attributes  # type: ignore[union-attr]
    assert attributes["installed_version"] == "0.31.0"
    assert attributes["latest_version"] == "0.31.0"
    assert attributes["release_summary"] is None


async def test_an_install_that_fails_is_reported(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.update_status.side_effect = None
    api.update_status.return_value = make_update(update_available=True, available_version="0.32.0")
    api.apply_update.side_effect = SnapDogResponseError("busy", status=502)
    await setup(hass, entry, listener)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: UPDATE}, blocking=True)


async def test_the_updater_appears_later_and_goes_unavailable_when_it_leaves(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    registry = er.async_get(hass)
    assert registry.async_get(UPDATE) is None
    api.update_status.side_effect = None
    api.update_status.return_value = make_update(update_available=True, available_version="0.32.0")
    await poll(hass)
    assert state(hass, UPDATE) == STATE_ON
    api.update_status.side_effect = SnapDogBusyError("no updater", status=503)
    await poll(hass)
    assert state(hass, UPDATE) == STATE_UNAVAILABLE


async def test_a_connection_that_fails_keeps_the_last_answer(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.update_status.side_effect = None
    api.update_status.return_value = make_update(update_available=True, available_version="0.32.0")
    await setup(hass, entry, listener)
    api.update_status.side_effect = SnapDogConnectionError("down")
    api.status.side_effect = SnapDogConnectionError("down")
    api.knx_programming_mode.side_effect = SnapDogConnectionError("down")
    await poll(hass)
    assert state(hass, UPDATE) == STATE_ON


async def test_nothing_is_read_while_the_socket_is_down(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await listener.call_args.kwargs["on_disconnected"](SnapDogConnectionError("down"))
    api.status.reset_mock()
    await poll(hass)
    api.status.assert_not_awaited()
    assert coordinator.connected is False


async def test_a_rejected_key_during_the_poll_starts_reauthentication(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    api.status.side_effect = SnapDogAuthError("key")
    await poll(hass)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"]["source"] == "reauth" for flow in flows)
