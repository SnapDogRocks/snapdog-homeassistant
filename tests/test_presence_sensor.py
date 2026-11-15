"""A zone reacts to the presence sensor the user chose for it."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pysnapdog import SnapDogConnectionError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.snapdog.const import CONF_PRESENCE_SENSORS, DOMAIN

from .conftest import SERVER_ID, make_zone
from .test_media_player import setup

SENSOR = "binary_sensor.hall_motion"


def chosen(entry: MockConfigEntry, **zones: str) -> MockConfigEntry:
    """The same entry, with these sensors chosen: zone index as text to sensor."""
    return MockConfigEntry(
        domain=entry.domain,
        title=entry.title,
        unique_id=entry.unique_id,
        data=dict(entry.data),
        options={CONF_PRESENCE_SENSORS: zones},
    )


async def flip(hass: HomeAssistant, entity: str, value: str) -> None:
    hass.states.async_set(entity, value)
    await hass.async_block_till_done()


async def test_a_sensor_that_turns_on_is_reported_to_the_zone(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    hass.states.async_set(SENSOR, STATE_OFF)
    entry = chosen(entry, **{"1": SENSOR})
    await setup(hass, entry, listener)
    api.set_zone_presence.assert_not_called()  # the server says "absent" and so does the sensor
    await flip(hass, SENSOR, STATE_ON)
    api.set_zone_presence.assert_awaited_once_with(1, True)


async def test_a_sensor_that_turns_off_ends_the_presence(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = [make_zone(presence=True)]
    hass.states.async_set(SENSOR, STATE_ON)
    entry = chosen(entry, **{"1": SENSOR})
    await setup(hass, entry, listener)
    await flip(hass, SENSOR, STATE_OFF)
    api.set_zone_presence.assert_awaited_once_with(1, False)


async def test_a_sensor_that_is_unsure_reports_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    hass.states.async_set(SENSOR, STATE_OFF)
    entry = chosen(entry, **{"1": SENSOR})
    await setup(hass, entry, listener)
    await flip(hass, SENSOR, STATE_UNAVAILABLE)
    await flip(hass, SENSOR, "unknown")
    api.set_zone_presence.assert_not_called()


async def test_a_zone_without_a_sensor_reports_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await flip(hass, SENSOR, STATE_ON)
    api.set_zone_presence.assert_not_called()


async def test_the_server_is_told_again_after_it_came_back(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    """A restarted server has forgotten the presence, the sensor still says it."""
    hass.states.async_set(SENSOR, STATE_ON)
    entry = chosen(entry, **{"1": SENSOR})
    await setup(hass, entry, listener)
    api.set_zone_presence.assert_awaited_once_with(1, True)  # the first connect
    api.set_zone_presence.reset_mock()
    await listener.call_args.kwargs["on_disconnected"](SnapDogConnectionError("down"))
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    api.set_zone_presence.assert_awaited_once_with(1, True)


async def test_nothing_is_reported_while_the_server_is_down(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    hass.states.async_set(SENSOR, STATE_OFF)
    entry = chosen(entry, **{"1": SENSOR})
    await setup(hass, entry, listener)
    await listener.call_args.kwargs["on_disconnected"](SnapDogConnectionError("down"))
    await flip(hass, SENSOR, STATE_ON)
    api.set_zone_presence.assert_not_called()


async def test_a_refused_report_is_logged_and_does_not_break_the_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    caplog: object,
) -> None:
    api.set_zone_presence.side_effect = SnapDogConnectionError("down")
    hass.states.async_set(SENSOR, STATE_OFF)
    entry = chosen(entry, **{"1": SENSOR})
    await setup(hass, entry, listener)
    await flip(hass, SENSOR, STATE_ON)
    assert entry.state is ConfigEntryState.LOADED
    assert "Could not report presence" in caplog.text  # type: ignore[attr-defined]


async def test_one_sensor_can_serve_two_zones(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = [make_zone(), make_zone(index=2, name="Upper floor")]
    hass.states.async_set(SENSOR, STATE_OFF)
    entry = chosen(entry, **{"1": SENSOR, "2": SENSOR})
    await setup(hass, entry, listener)
    await flip(hass, SENSOR, STATE_ON)
    assert {call.args for call in api.set_zone_presence.await_args_list} == {(1, True), (2, True)}


async def test_the_old_presence_sensor_is_removed(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    """Versions before 0.6 mirrored the presence of the server as a sensor."""
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{SERVER_ID}_zone_1_presence",
        config_entry=entry,
        suggested_object_id="ground_floor_presence",
    )
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{SERVER_ID}_zone_1_presence_timer",
        config_entry=entry,
        suggested_object_id="ground_floor_presence_timer",
    )
    await setup(hass, entry, listener)
    assert registry.async_get("binary_sensor.ground_floor_presence") is None
    assert registry.async_get("binary_sensor.ground_floor_presence_timer") is not None


async def test_the_options_choose_a_sensor_per_zone(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = [make_zone(), make_zone(index=2, name="Upper floor")]
    await setup(hass, entry, listener)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zone"
    assert result["description_placeholders"] == {"zone": "Ground floor", "position": "1/2"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sensor": SENSOR}
    )
    assert result["description_placeholders"] == {"zone": "Upper floor", "position": "2/2"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PRESENCE_SENSORS] == {"1": SENSOR}


async def test_the_options_start_from_what_was_chosen(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    entry = chosen(entry, **{"1": SENSOR})
    await setup(hass, entry, listener)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    key = next(iter(result["data_schema"].schema))
    assert key.description == {"suggested_value": SENSOR}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PRESENCE_SENSORS] == {}


async def test_the_options_need_a_loaded_entry(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock
) -> None:
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


async def test_the_options_need_a_zone(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = []
    await setup(hass, entry, listener)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_zones"
