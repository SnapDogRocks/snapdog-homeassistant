"""A zone that moved to another position in ``snapdog.toml`` raises a repair issue."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.snapdog.const import DOMAIN
from custom_components.snapdog.layout import MovedZone, moved_zones

from .conftest import make_zone


def zones(*names: str) -> list:
    """Zones named ``names``, numbered from 1 in that order."""
    return [make_zone(index=number, name=name) for number, name in enumerate(names, start=1)]


def issues(hass: HomeAssistant) -> list[ir.IssueEntry]:
    return [issue for (domain, _), issue in ir.async_get(hass).issues.items() if domain == DOMAIN]


# The comparison itself.


def test_swapped_zones_are_found_in_the_order_of_their_new_position() -> None:
    found = moved_zones({1: "Kitchen", 2: "Bath"}, {1: "Bath", 2: "Kitchen"})
    assert found == [MovedZone("Bath", 2, 1), MovedZone("Kitchen", 1, 2)]


def test_a_zone_inserted_at_the_front_moves_the_ones_behind_it() -> None:
    found = moved_zones({1: "A", 2: "B"}, {1: "New", 2: "A", 3: "B"})
    assert found == [MovedZone("A", 1, 2), MovedZone("B", 2, 3)]


def test_a_zone_deleted_at_the_front_moves_the_ones_behind_it() -> None:
    found = moved_zones({1: "A", 2: "B", 3: "C"}, {1: "B", 2: "C"})
    assert found == [MovedZone("B", 2, 1), MovedZone("C", 3, 2)]


def test_nothing_moved_when_the_layout_is_the_same() -> None:
    assert moved_zones({1: "A", 2: "B"}, {1: "A", 2: "B"}) == []


def test_a_zone_added_at_the_end_moves_nothing() -> None:
    assert moved_zones({1: "A", 2: "B"}, {1: "A", 2: "B", 3: "C"}) == []


def test_a_renamed_zone_is_not_a_move() -> None:
    assert moved_zones({1: "A", 2: "B"}, {1: "A", 2: "Renamed"}) == []


def test_a_name_that_occurs_twice_is_not_matched() -> None:
    assert moved_zones({1: "Zone", 2: "Zone"}, {1: "Zone", 2: "Zone"}) == []
    found = moved_zones({1: "A", 2: "A", 3: "B"}, {1: "B", 2: "A", 3: "A"})
    assert found == [MovedZone("B", 3, 1)]


def test_a_moved_zone_reads_as_name_and_positions() -> None:
    assert str(MovedZone("Kitchen", 2, 3)) == "Kitchen (2 → 3)"


# In Home Assistant.


async def test_the_first_start_records_the_zones_and_raises_nothing(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    hass_storage: dict,
) -> None:
    api.zones.return_value = zones("Kitchen", "Bath")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert issues(hass) == []
    stored = hass_storage[f"snapdog.layout.{entry.entry_id}"]["data"]
    assert stored["zones"] == {"1": "Kitchen", "2": "Bath"}


async def test_a_restart_with_the_same_layout_raises_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = zones("Kitchen", "Bath")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
    assert issues(hass) == []


async def test_reordered_zones_raise_one_issue_that_names_them(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = zones("Kitchen", "Bath")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    api.zones.return_value = zones("Bath", "Kitchen")
    await hass.config_entries.async_reload(entry.entry_id)

    (issue,) = issues(hass)
    assert issue.translation_key == "zone_order_changed"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_persistent
    assert not issue.is_fixable
    assert issue.translation_placeholders == {
        "server": entry.title,
        "zones": "Bath (2 → 1), Kitchen (1 → 2)",
    }


async def test_the_issue_stays_and_is_not_repeated_by_later_restarts(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = zones("Kitchen", "Bath")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    api.zones.return_value = zones("Bath", "Kitchen")
    await hass.config_entries.async_reload(entry.entry_id)
    (first,) = issues(hass)

    await hass.config_entries.async_reload(entry.entry_id)
    (again,) = issues(hass)
    assert again.issue_id == first.issue_id


async def test_a_second_move_replaces_the_first_issue(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = zones("A", "B", "C")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    api.zones.return_value = zones("B", "A", "C")
    await hass.config_entries.async_reload(entry.entry_id)
    (first,) = issues(hass)

    api.zones.return_value = zones("C", "B", "A")
    await hass.config_entries.async_reload(entry.entry_id)
    (second,) = issues(hass)
    assert second.issue_id != first.issue_id
    assert second.translation_placeholders is not None
    assert "C (3 → 1)" in second.translation_placeholders["zones"]


async def test_a_server_that_reconnects_with_moved_zones_raises_the_issue(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    api.zones.return_value = zones("Kitchen", "Bath")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert issues(hass) == []

    api.zones.return_value = zones("Bath", "Kitchen")
    await listener.call_args.kwargs["on_connected"]()
    await hass.async_block_till_done()
    assert len(issues(hass)) == 1


async def test_removing_the_entry_removes_the_issue_and_the_record(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    hass_storage: dict,
) -> None:
    api.zones.return_value = zones("Kitchen", "Bath")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    api.zones.return_value = zones("Bath", "Kitchen")
    await hass.config_entries.async_reload(entry.entry_id)
    assert len(issues(hass)) == 1

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issues(hass) == []
    assert f"snapdog.layout.{entry.entry_id}" not in hass_storage
