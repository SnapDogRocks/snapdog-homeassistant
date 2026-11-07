"""The cover of the track a zone plays, as the entity picture."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pysnapdog import SnapDogBusyError, SnapDogNotFoundError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .test_media_player import PLAYER, setup, zone_changed

COVER = b"\x89PNG cover"
URL = "http://snapdog.local/api/v1/zones/1/cover"


def picture(hass: HomeAssistant) -> str | None:
    state = hass.states.get(PLAYER)
    assert state is not None
    return state.attributes.get("entity_picture")


async def test_a_track_without_a_cover_has_no_picture(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    """The server answers with a placeholder icon for such a track, which is not a cover."""
    await setup(hass, entry, listener)
    assert picture(hass) is None


async def test_a_track_with_a_cover_has_a_picture_that_the_player_serves(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(cover_url=URL))
    await hass.async_block_till_done()
    url = picture(hass)
    assert url is not None
    assert url.startswith(f"/api/media_player_proxy/{PLAYER}?token=")
    response = await (await hass_client_no_auth()).get(url)
    assert (response.status, await response.read(), response.content_type) == (
        200,
        COVER,
        "image/png",
    )
    api.zone_cover.assert_awaited_once_with(1)


async def test_the_picture_changes_with_the_track_although_the_cover_address_stays(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(cover_url=URL, title="So What"))
    await hass.async_block_till_done()
    first = picture(hass)
    await coordinator._on_event(zone_changed(cover_url=URL, title="So What"))
    await hass.async_block_till_done()
    assert picture(hass) == first
    await coordinator._on_event(zone_changed(cover_url=URL, title="Blue in Green"))
    await hass.async_block_till_done()
    assert picture(hass) not in (None, first)


async def test_the_picture_goes_when_the_cover_does(
    hass: HomeAssistant, entry: MockConfigEntry, api: MagicMock, listener: MagicMock
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(cover_url=URL))
    await hass.async_block_till_done()
    assert picture(hass) is not None
    await coordinator._on_event(zone_changed(cover_url=None))
    await hass.async_block_till_done()
    assert picture(hass) is None


@pytest.mark.parametrize(
    "error", [SnapDogNotFoundError("none", status=404), SnapDogBusyError("busy", status=503)]
)
async def test_a_cover_the_server_does_not_deliver_is_a_missing_image(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
    error: Exception,
) -> None:
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(cover_url=URL))
    await hass.async_block_till_done()
    url = picture(hass)
    assert url is not None
    api.zone_cover.side_effect = error
    assert (await (await hass_client_no_auth()).get(url)).status == 404


async def test_a_cover_is_not_fetched_for_a_track_without_one(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    api: MagicMock,
    listener: MagicMock,
) -> None:
    """A stale address of the proxy must not make the server send its placeholder."""
    coordinator = await setup(hass, entry, listener)
    await coordinator._on_event(zone_changed(cover_url=URL))
    await hass.async_block_till_done()
    url = picture(hass)
    assert url is not None
    await coordinator._on_event(zone_changed(cover_url=None))
    await hass.async_block_till_done()
    assert (await (await hass_client_no_auth()).get(url)).status == 404
    api.zone_cover.assert_not_called()
