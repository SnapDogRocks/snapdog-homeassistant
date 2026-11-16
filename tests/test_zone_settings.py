"""Presence and equalizer of a zone."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pysnapdog import SnapDogConnectionError, ZoneEqChanged, ZonePresenceChanged
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import make_eq, make_zone
from .test_media_player import setup

TIMER = "binary_sensor.ground_floor_presence_timer"
CONTROL = "switch.ground_floor_presence_control"
EQ = "switch.ground_floor_equalizer"
PRESET = "select.ground_floor_eq_preset"
TIMEOUT = "number.ground_floor_presence_timeout"
ENTITIES = (TIMER, CONTROL, EQ, PRESET, TIMEOUT)


async def call(hass: HomeAssistant, domain: str, service: str, entity: str, **data: object) -> None:
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity, **data}, blocking=True)


def state(hass: HomeAssistant, entity: str) -> str:
    found = hass.states.get(entity)
    assert found is not None, entity
    return found.state


async def test_a_zone_has_the_presence_and_equalizer_entities(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    registry = er.async_get(hass)
    for entity in ENTITIES:
        assert registry.async_get(entity) is not None, entity
    assert (
        registry.async_get("binary_sensor.ground_floor_presence") is None
    )  # SnapDog measures none
    assert registry.async_get(TIMER).entity_category == "diagnostic"  # type: ignore[union-attr]
    for entity in (CONTROL, EQ, PRESET, TIMEOUT):
        assert registry.async_get(entity).entity_category == "config", entity  # type: ignore[union-attr]


async def test_the_states_come_from_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = [
        make_zone(presence=True, presence_enabled=True, presence_timer_active=True)
    ]
    await setup(hass, entry, listener)
    assert state(hass, TIMER) == STATE_ON
    assert state(hass, CONTROL) == STATE_ON
    assert state(hass, EQ) == STATE_ON
    assert state(hass, PRESET) == "Flat"
    assert state(hass, TIMEOUT) == "900"
    assert hass.states.get(PRESET).attributes["options"] == ["Flat", "Bass"]  # type: ignore[union-attr]
    assert hass.states.get(TIMEOUT).attributes["unit_of_measurement"] == "s"  # type: ignore[union-attr]


async def test_presence_follows_the_event_of_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await listener.call_args.kwargs["on_event"](
        ZonePresenceChanged(zone=1, presence=True, enabled=True, timer_active=True)
    )
    await hass.async_block_till_done()
    assert state(hass, TIMER) == STATE_ON
    assert state(hass, CONTROL) == STATE_ON


async def test_a_presence_event_about_another_zone_is_ignored(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await listener.call_args.kwargs["on_event"](
        ZonePresenceChanged(zone=9, presence=True, enabled=True, timer_active=True)
    )
    await hass.async_block_till_done()
    assert state(hass, TIMER) == STATE_OFF


async def test_the_presence_automation_is_switched_on_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "switch", "turn_on", CONTROL)
    api.set_zone_presence_enabled.assert_awaited_once_with(1, True)
    # The event brings the state, the request alone changes nothing.
    assert state(hass, CONTROL) == STATE_OFF
    await call(hass, "switch", "turn_off", CONTROL)
    api.set_zone_presence_enabled.assert_awaited_with(1, False)


async def test_the_timeout_is_the_request_because_no_event_tells_it(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "number", "set_value", TIMEOUT, value=120)
    api.set_zone_presence_timeout.assert_awaited_once_with(1, 120)
    assert state(hass, TIMEOUT) == "120"


async def test_the_timeout_stays_in_the_range_of_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    with pytest.raises(ServiceValidationError):
        await call(hass, "number", "set_value", TIMEOUT, value=70_000)
    with pytest.raises(ServiceValidationError):
        await call(hass, "number", "set_value", TIMEOUT, value=-1)
    api.set_zone_presence_timeout.assert_not_called()


async def test_the_equalizer_is_switched_with_its_bands_unchanged(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "switch", "turn_off", EQ)
    sent = api.set_zone_eq.await_args.args[1]
    assert api.set_zone_eq.await_args.args[0] == 1
    assert sent == dataclasses.replace(make_eq(), enabled=False)
    assert state(hass, EQ) == STATE_OFF


async def test_choosing_a_preset_applies_it(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "select", "select_option", PRESET, option="Bass")
    api.apply_zone_eq_preset.assert_awaited_once_with(1, "Bass")
    assert state(hass, PRESET) == "Bass"


async def test_the_equalizer_follows_the_event_of_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    on_event = listener.call_args.kwargs["on_event"]
    await on_event(ZoneEqChanged(zone=1, config=make_eq(enabled=False, preset="Bass")))
    await hass.async_block_till_done()
    assert state(hass, EQ) == STATE_OFF
    assert state(hass, PRESET) == "Bass"
    await on_event(ZoneEqChanged(zone=9, config=make_eq(enabled=True, preset="Flat")))
    await hass.async_block_till_done()
    assert state(hass, EQ) == STATE_OFF


async def test_an_edited_equalizer_shows_no_preset(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await listener.call_args.kwargs["on_event"](ZoneEqChanged(zone=1, config=make_eq(preset=None)))
    await hass.async_block_till_done()
    assert state(hass, PRESET) == "unknown"


async def test_a_zone_that_does_not_answer_stays_unknown(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zone_eq.side_effect = SnapDogConnectionError("down")
    api.zone_presence_timeout.side_effect = SnapDogConnectionError("down")
    await setup(hass, entry, listener)
    assert state(hass, EQ) == STATE_UNAVAILABLE
    assert state(hass, PRESET) == STATE_UNAVAILABLE
    assert state(hass, TIMEOUT) == "unknown"
    assert state(hass, TIMER) == STATE_OFF


async def test_a_failed_request_is_an_error_and_changes_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    api.set_zone_eq.side_effect = SnapDogConnectionError("down")
    api.set_zone_presence_timeout.side_effect = SnapDogConnectionError("down")
    with pytest.raises(HomeAssistantError):
        await call(hass, "switch", "turn_off", EQ)
    with pytest.raises(HomeAssistantError):
        await call(hass, "number", "set_value", TIMEOUT, value=60)
    assert state(hass, EQ) == STATE_ON
    assert state(hass, TIMEOUT) == "900"


async def test_the_entities_are_unavailable_while_the_socket_is_down(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_disconnected(SnapDogConnectionError("down"))
    await hass.async_block_till_done()
    for entity in ENTITIES:
        assert state(hass, entity) == STATE_UNAVAILABLE, entity


async def test_a_zone_added_later_gets_its_entities(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    api.zones.return_value = [make_zone(), make_zone(index=2, name="Garden")]
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    assert state(hass, "binary_sensor.garden_presence_timer") == STATE_OFF
    assert state(hass, "switch.garden_equalizer") == STATE_ON
    assert state(hass, "number.garden_presence_timeout") == "900"
