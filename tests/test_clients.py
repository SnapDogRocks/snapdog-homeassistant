"""The devices and entities of the clients, which are identified by their MAC address."""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pysnapdog import ClientStateChanged, SnapDogConnectionError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.snapdog.const import CONF_SERVER_ID, DOMAIN

from .conftest import make_client, make_eq, make_zone
from .test_media_player import setup

MAC = "02:42:ac:11:00:10"
OTHER_MAC = "02:42:ac:11:00:11"
VOLUME = "number.kitchen_speaker_volume"
LATENCY = "number.kitchen_speaker_latency"
MUTE = "switch.kitchen_speaker_mute"
EQ = "switch.kitchen_speaker_equalizer"
ZONE = "select.kitchen_speaker_zone"
SPEAKER = "select.kitchen_speaker_speaker_correction"
PRESET = "select.kitchen_speaker_eq_preset"
CONNECTED = "binary_sensor.kitchen_speaker_connected"
CLIENT_ENTITIES = (VOLUME, LATENCY, MUTE, EQ, ZONE, SPEAKER, PRESET, CONNECTED)


@pytest.fixture
def one_client(api: MagicMock) -> MagicMock:
    api.clients.return_value = [make_client()]
    api.zones.return_value = [make_zone(), make_zone(index=2, name="Garden")]
    return api


def event(**changes: Any) -> ClientStateChanged:
    values: dict[str, Any] = {
        "client": 1,
        "volume": 30,
        "muted": False,
        "connected": True,
        "zone": 1,
        "is_snapdog": True,
    }
    values.update(changes)
    return ClientStateChanged(**values)


async def send(hass: HomeAssistant, listener: MagicMock, ev: ClientStateChanged) -> None:
    await listener.call_args.kwargs["on_event"](ev)
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, domain: str, service: str, entity: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity, **data}, blocking=True)


def device_of(hass: HomeAssistant, mac: str = MAC) -> dr.DeviceEntry | None:
    config = hass.config_entries.async_entries(DOMAIN)[0]
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{config.data[CONF_SERVER_ID]}_client_{mac}"), config.entry_id
    )


# The devices.


async def test_a_client_is_a_device_of_its_own_under_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    device = device_of(hass)
    assert device is not None
    assert device.name == "Kitchen speaker"
    assert device.model == "SnapDog client"
    assert (dr.CONNECTION_NETWORK_MAC, MAC) in device.connections
    server = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.data[CONF_SERVER_ID]), entry.entry_id
    )
    assert server is not None
    assert device.via_device_id == server.id


async def test_a_snapcast_client_is_no_snapdog_client_and_has_no_equalizer(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.clients.return_value = [make_client(is_snapdog=False)]
    await setup(hass, entry, listener)
    device = device_of(hass)
    assert device is not None
    assert device.model == "Snapcast client"
    registry = er.async_get(hass)
    for gone in (EQ, SPEAKER, PRESET):
        assert registry.async_get(gone) is None
    for present in (VOLUME, LATENCY, MUTE, ZONE, CONNECTED):
        assert registry.async_get(present) is not None
    api.client_eq.assert_not_awaited()


async def test_every_client_entity_is_named_after_its_mac(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    registry = er.async_get(hass)
    for entity_id in CLIENT_ENTITIES:
        found = registry.async_get(entity_id)
        assert found is not None, entity_id
        assert found.unique_id.startswith(f"{entry.data[CONF_SERVER_ID]}_client_{MAC}_")


async def test_the_position_in_the_configuration_is_not_the_identity(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.clients.return_value = [
        make_client(),
        make_client(index=2, name="Bath", mac=OTHER_MAC, volume=55),
    ]
    await setup(hass, entry, listener)
    before = {
        e.entity_id: e.unique_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain != "media_player"
    }
    assert hass.states.get("number.bath_volume").state == "55"  # type: ignore[union-attr]

    api.clients.return_value = [
        make_client(index=1, name="Bath", mac=OTHER_MAC, volume=55),
        make_client(index=2),
    ]
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()

    after = {
        e.entity_id: e.unique_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain != "media_player"
    }
    assert after == before
    assert hass.states.get(VOLUME).state == "30"  # type: ignore[union-attr]
    # The command goes to the position the client has now.
    await call(hass, "number", "set_value", VOLUME, value=40)
    api.set_client_volume.assert_awaited_with(2, 40)


# The entities and what they ask the server.


async def test_the_states_come_from_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    assert hass.states.get(VOLUME).state == "30"  # type: ignore[union-attr]
    assert hass.states.get(VOLUME).attributes["max"] == 80  # type: ignore[union-attr]
    assert hass.states.get(LATENCY).state == "20"  # type: ignore[union-attr]
    assert hass.states.get(MUTE).state == STATE_OFF  # type: ignore[union-attr]
    assert hass.states.get(EQ).state == STATE_ON  # type: ignore[union-attr]
    assert hass.states.get(ZONE).state == "Ground floor"  # type: ignore[union-attr]
    assert hass.states.get(SPEAKER).state == "Genelec 8030C"  # type: ignore[union-attr]
    assert hass.states.get(PRESET).state == "Flat"  # type: ignore[union-attr]
    assert hass.states.get(CONNECTED).state == STATE_ON  # type: ignore[union-attr]


async def test_an_event_changes_volume_mute_zone_and_connection(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await send(hass, listener, event(volume=65, muted=True, zone=2, connected=False))
    assert hass.states.get(VOLUME).state == "65"  # type: ignore[union-attr]
    assert hass.states.get(MUTE).state == STATE_ON  # type: ignore[union-attr]
    assert hass.states.get(ZONE).state == "Garden"  # type: ignore[union-attr]
    assert hass.states.get(CONNECTED).state == STATE_OFF  # type: ignore[union-attr]


async def test_setting_the_volume_asks_the_server_and_waits_for_its_event(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "number", "set_value", VOLUME, value=50)
    one_client.set_client_volume.assert_awaited_once_with(1, 50)
    assert hass.states.get(VOLUME).state == "30"  # type: ignore[union-attr]


async def test_the_volume_cannot_go_beyond_the_maximum_of_the_client(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    with pytest.raises(ServiceValidationError):
        await call(hass, "number", "set_value", VOLUME, value=90)
    one_client.set_client_volume.assert_not_awaited()


async def test_mute_is_switched_on_the_server(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "switch", "turn_on", MUTE)
    one_client.set_client_mute.assert_awaited_with(1, True)
    await call(hass, "switch", "turn_off", MUTE)
    one_client.set_client_mute.assert_awaited_with(1, False)


async def test_the_latency_is_the_answer_of_the_server_because_no_event_tells_it(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "number", "set_value", LATENCY, value=-150)
    one_client.set_client_latency.assert_awaited_once_with(1, -150)
    assert hass.states.get(LATENCY).state == "-150"  # type: ignore[union-attr]


async def test_choosing_a_zone_moves_the_client(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    assert hass.states.get(ZONE).attributes["options"] == ["Ground floor", "Garden"]  # type: ignore[union-attr]
    await call(hass, "select", "select_option", ZONE, option="Garden")
    one_client.set_client_zone.assert_awaited_once_with(1, 2)


async def test_two_zones_of_one_name_are_told_apart_by_their_number(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.clients.return_value = [make_client()]
    api.zones.return_value = [make_zone(name="Room"), make_zone(index=2, name="Room")]
    await setup(hass, entry, listener)
    assert hass.states.get(ZONE).attributes["options"] == ["Room (1)", "Room (2)"]  # type: ignore[union-attr]
    await call(hass, "select", "select_option", ZONE, option="Room (2)")
    api.set_client_zone.assert_awaited_once_with(1, 2)


async def test_the_equalizer_is_switched_with_its_bands_unchanged(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await call(hass, "switch", "turn_off", EQ)
    sent = one_client.set_client_eq.await_args.args[1]
    assert sent == dataclasses.replace(make_eq(), enabled=False)
    assert hass.states.get(EQ).state == STATE_OFF  # type: ignore[union-attr]


async def test_choosing_a_preset_and_a_speaker_applies_them(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    assert hass.states.get(PRESET).attributes["options"] == ["Flat", "Bass"]  # type: ignore[union-attr]
    await call(hass, "select", "select_option", PRESET, option="Bass")
    one_client.apply_client_eq_preset.assert_awaited_once_with(1, "Bass")
    assert hass.states.get(PRESET).state == "Bass"  # type: ignore[union-attr]
    await call(hass, "select", "select_option", SPEAKER, option="KEF LS50")
    one_client.apply_client_speaker.assert_awaited_once_with(1, speaker="KEF LS50")
    assert hass.states.get(SPEAKER).state == "KEF LS50"  # type: ignore[union-attr]


async def test_an_unknown_speaker_correction_shows_no_option(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    one_client.client_speaker.return_value = make_eq(preset=None)
    await setup(hass, entry, listener)
    assert hass.states.get(SPEAKER).state == "unknown"  # type: ignore[union-attr]


async def test_a_failed_request_is_an_error_and_changes_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    one_client.set_client_mute.side_effect = SnapDogConnectionError("down")
    with pytest.raises(HomeAssistantError):
        await call(hass, "switch", "turn_on", MUTE)
    assert hass.states.get(MUTE).state == STATE_OFF  # type: ignore[union-attr]


async def test_the_entities_are_unavailable_while_the_socket_is_down(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await listener.call_args.kwargs["on_disconnected"](SnapDogConnectionError("lost"))
    await hass.async_block_till_done()
    for entity_id in CLIENT_ENTITIES:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id  # type: ignore[union-attr]


async def test_settings_the_server_does_not_answer_stay_unknown(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    one_client.client_latency.side_effect = SnapDogConnectionError("no")
    await setup(hass, entry, listener)
    assert hass.states.get(LATENCY).state == "unknown"  # type: ignore[union-attr]
    assert hass.states.get(VOLUME).state == "30"  # type: ignore[union-attr]


async def test_a_client_that_turns_out_to_be_snapdog_gets_its_equalizer_entities(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.clients.return_value = [make_client(is_snapdog=False)]
    await setup(hass, entry, listener)
    assert er.async_get(hass).async_get(EQ) is None

    await send(hass, listener, event(is_snapdog=True))
    assert hass.states.get(EQ).state == STATE_ON  # type: ignore[union-attr]
    assert hass.states.get(PRESET).state == "Flat"  # type: ignore[union-attr]


# The devices follow the server.


async def test_a_rename_reaches_the_device(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN, "rename_client", {ATTR_ENTITY_ID: VOLUME, "name": "Pantry"}, blocking=True
    )
    one_client.rename_client.assert_awaited_once_with(1, "Pantry")
    device = device_of(hass)
    assert device is not None
    assert device.name == "Pantry"


async def test_the_device_of_a_client_the_server_dropped_is_removed(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.clients.return_value = [make_client(), make_client(index=2, name="Bath", mac=OTHER_MAC)]
    await setup(hass, entry, listener)
    assert device_of(hass, OTHER_MAC) is not None

    api.clients.return_value = [make_client()]
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    assert device_of(hass, OTHER_MAC) is None
    assert device_of(hass) is not None


async def test_a_client_added_to_the_configuration_appears_after_the_next_read(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.clients.return_value = [make_client()]
    await setup(hass, entry, listener)
    assert hass.states.get("number.bath_volume") is None

    api.clients.return_value = [make_client(), make_client(index=2, name="Bath", mac=OTHER_MAC)]
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    assert hass.states.get("number.bath_volume") is not None


# The actions.


async def test_move_client_takes_the_number_of_the_zone(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN, "move_client", {ATTR_ENTITY_ID: VOLUME, "zone": 2}, blocking=True
    )
    one_client.set_client_zone.assert_awaited_once_with(1, 2)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "move_client", {ATTR_ENTITY_ID: VOLUME, "zone": 7}, blocking=True
        )


async def test_an_action_on_a_device_runs_once_for_the_client(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    device = device_of(hass)
    assert device is not None
    await hass.services.async_call(
        DOMAIN, "move_client", {"device_id": device.id, "zone": 2}, blocking=True
    )
    one_client.set_client_zone.assert_awaited_once_with(1, 2)


async def test_apply_speaker_profile_needs_a_snapdog_client(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.clients.return_value = [make_client(is_snapdog=False)]
    await setup(hass, entry, listener)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "apply_speaker_profile",
            {ATTR_ENTITY_ID: VOLUME, "speaker": "KEF LS50"},
            blocking=True,
        )
    api.apply_client_speaker.assert_not_awaited()


async def test_apply_speaker_profile(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN,
        "apply_speaker_profile",
        {ATTR_ENTITY_ID: VOLUME, "speaker": "KEF LS50"},
        blocking=True,
    )
    one_client.apply_client_speaker.assert_awaited_once_with(1, speaker="KEF LS50")


BAND = {"freq": 250, "gain": -2.5, "q": 1.2, "type": "peaking"}


async def test_set_client_eq_replaces_the_bands_and_keeps_the_switch(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN, "set_client_eq", {ATTR_ENTITY_ID: VOLUME, "bands": [BAND]}, blocking=True
    )
    sent = one_client.set_client_eq.await_args.args[1]
    assert sent.enabled is True
    assert [(b.freq, b.gain, b.q, str(b.type)) for b in sent.bands] == [
        (250.0, -2.5, 1.2, "peaking")
    ]
    assert sent.preset is None


async def test_set_client_eq_can_only_switch(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN, "set_client_eq", {ATTR_ENTITY_ID: VOLUME, "enabled": False}, blocking=True
    )
    assert one_client.set_client_eq.await_args.args[1] == dataclasses.replace(
        make_eq(), enabled=False
    )


async def test_set_client_eq_with_nothing_to_change_is_refused(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_client_eq", {ATTR_ENTITY_ID: VOLUME}, blocking=True
        )


async def test_set_client_eq_band_counts_from_one(
    hass: HomeAssistant, entry: MockConfigEntry, one_client: MagicMock, listener: MagicMock
) -> None:
    await setup(hass, entry, listener)
    await hass.services.async_call(
        DOMAIN,
        "set_client_eq_band",
        {ATTR_ENTITY_ID: VOLUME, "band": 3, **BAND},
        blocking=True,
    )
    index, band, value = one_client.set_client_eq_band.await_args.args
    assert (index, band) == (1, 2)
    assert (value.freq, value.gain) == (250.0, -2.5)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_client_eq_band",
            {ATTR_ENTITY_ID: VOLUME, "band": 11, **BAND},
            blocking=True,
        )
