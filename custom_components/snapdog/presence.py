"""Pass the state of a Home Assistant sensor on to the zone it was chosen for.

SnapDog measures no presence. It reacts to a report from a detector, KNX or the API, and a report
makes the zone start playing. The user picks a binary sensor per zone in the options, and this
bridge reports its state to the server: on when the sensor turns on, off when it turns off.
"""

from __future__ import annotations

from collections import defaultdict

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from pysnapdog import SnapDogError

from .const import CONF_PRESENCE_SENSORS, LOGGER
from .coordinator import SnapDogConfigEntry, SnapDogCoordinator


class PresenceBridge:
    """Report the chosen sensors to their zones."""

    def __init__(
        self, hass: HomeAssistant, entry: SnapDogConfigEntry, coordinator: SnapDogCoordinator
    ) -> None:
        """Read the sensors chosen in the options."""
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._zones_of: dict[str, list[int]] = defaultdict(list)
        for zone, sensor in entry.options.get(CONF_PRESENCE_SENSORS, {}).items():
            self._zones_of[sensor].append(int(zone))
        self._was_connected = coordinator.connected

    @callback
    def async_start(self) -> None:
        """Follow the sensors, and report them again after every reconnect of the server."""
        if not self._zones_of:
            return
        self._entry.async_on_unload(
            async_track_state_change_event(self._hass, list(self._zones_of), self._on_state)
        )
        self._entry.async_on_unload(self._coordinator.async_add_listener(self._on_update))

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        for zone in self._zones_of[event.data["entity_id"]]:
            self._spawn(zone)

    @callback
    def _on_update(self) -> None:
        """A server that came back may have lost the presence it was told, so tell it again."""
        connected = self._coordinator.connected
        if connected and not self._was_connected:
            for zones in self._zones_of.values():
                for zone in zones:
                    self._spawn(zone)
        self._was_connected = connected

    def _spawn(self, zone: int) -> None:
        self._entry.async_create_background_task(
            self._hass, self._report(zone), f"snapdog presence zone {zone}"
        )

    async def _report(self, zone: int) -> None:
        """Tell the server what the sensor says, unless it knows already or the sensor is unsure."""
        sensor = next(entity for entity, zones in self._zones_of.items() if zone in zones)
        state = self._hass.states.get(sensor)
        if state is None or state.state not in (STATE_ON, STATE_OFF):
            return
        present = state.state == STATE_ON
        known = self._coordinator.data.zones.get(zone)
        if known is None or not self._coordinator.connected or known.presence == present:
            return
        try:
            await self._coordinator.api.set_zone_presence(zone, present)
        except SnapDogError as err:
            LOGGER.warning("Could not report presence of %s to zone %s: %s", sensor, zone, err)
