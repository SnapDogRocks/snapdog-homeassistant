"""Notice when zones of the server moved to another position.

Home Assistant identifies a zone by its position in ``snapdog.toml``. When a block is inserted,
deleted or reordered, the zones behind it get another position and the history, the areas and
the automations of the old entities end up on a different zone, without an error. The server
has no stable id for a zone, so the integration remembers which name stood at which position and
raises a repair issue when a name moves.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from pysnapdog import Zone

from .const import DOMAIN

STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True)
class MovedZone:
    """A zone that kept its name and changed its position."""

    name: str
    before: int
    after: int

    def __str__(self) -> str:
        """The form shown in the repair issue, for example ``Kitchen (2 → 3)``."""
        return f"{self.name} ({self.before} → {self.after})"


def moved_zones(before: Mapping[int, str], after: Mapping[int, str]) -> list[MovedZone]:
    """Find the zones whose position changed, matched by name.

    A name that occurs more than once on either side cannot be matched and is skipped. A zone
    that was renamed is not found either, because nothing tells it apart from a new one. Both
    cases stay silent instead of guessing.
    """
    once_before = _unique(before)
    once_after = _unique(after)
    moved = [
        MovedZone(name, once_before[name], index)
        for name, index in once_after.items()
        if name in once_before and once_before[name] != index
    ]
    return sorted(moved, key=lambda zone: zone.after)


def _unique(names: Mapping[int, str]) -> dict[str, int]:
    """Map every name that occurs exactly once to its position."""
    counts = Counter(names.values())
    return {name: index for index, name in names.items() if counts[name] == 1}


class ZoneLayout:
    """Remember the zone names of one server and raise a repair issue when they move."""

    def __init__(self, hass: HomeAssistant, entry_id: str, server: str) -> None:
        """Prepare the storage of this config entry. Nothing is read or written yet."""
        self._hass = hass
        self._entry_id = entry_id
        self._server = server
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.layout.{entry_id}"
        )

    async def async_check(self, zones: Mapping[int, Zone]) -> None:
        """Compare the zones with the ones seen last time, then remember them.

        The first check only records. A moved zone raises one issue that stays until the user
        dismisses it, so a restart does not hide it and does not repeat it.
        """
        current = {index: zone.name for index, zone in zones.items()}
        saved = await self._store.async_load() or {}
        previous = {int(index): name for index, name in saved.get("zones", {}).items()}
        issue: str | None = saved.get("issue")
        count: int = saved.get("count", 0)

        moved = moved_zones(previous, current) if previous else []
        if moved:
            if issue is not None:
                ir.async_delete_issue(self._hass, DOMAIN, issue)
            count += 1
            issue = f"zone_order_changed_{self._entry_id}_{count}"
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                issue,
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="zone_order_changed",
                translation_placeholders={
                    "server": self._server,
                    "zones": ", ".join(str(zone) for zone in moved),
                },
            )
        if moved or previous != current:
            await self._store.async_save(
                {
                    "zones": {str(index): name for index, name in current.items()},
                    "issue": issue,
                    "count": count,
                }
            )

    async def async_remove(self) -> None:
        """Forget the layout and its issue when the config entry is removed."""
        saved = await self._store.async_load() or {}
        if (issue := saved.get("issue")) is not None:
            ir.async_delete_issue(self._hass, DOMAIN, issue)
        await self._store.async_remove()
