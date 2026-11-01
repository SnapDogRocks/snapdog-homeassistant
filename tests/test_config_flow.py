"""Setting up, discovering and renewing the key of a server."""

from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pysnapdog import SnapDogAuthError, SnapDogConnectionError, VersionInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.snapdog.const import (
    CONF_API_KEY,
    CONF_SERVER_ID,
    CONF_TLS,
    CONF_VERIFY_TLS,
    DOMAIN,
)

from .conftest import SERVER_ID

USER_INPUT = {
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 5555,
    CONF_API_KEY: "secret",
    CONF_TLS: False,
    CONF_VERIFY_TLS: True,
}


@pytest.fixture
def flow_api() -> MagicMock:
    """The client the flow builds. A server of the minimum version."""
    with (
        patch("custom_components.snapdog.config_flow.SnapDogClient") as cls,
        patch("custom_components.snapdog.async_setup_entry", return_value=True),
    ):
        client = cls.return_value
        client.version = AsyncMock(return_value=VersionInfo("SnapDog", "0.30.0", "1.90"))
        client.zones = AsyncMock(return_value=[])
        yield client


def discovery(**properties: str) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("192.0.2.20"),
        ip_addresses=[ip_address("192.0.2.20")],
        port=5555,
        hostname="snapdog.local.",
        type="_snapdog._tcp.local.",
        name="SnapDog._snapdog._tcp.local.",
        properties={"server_id": SERVER_ID, **properties},
    )


async def test_a_manual_setup_creates_an_entry(hass: HomeAssistant, flow_api: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SnapDog 192.0.2.10"
    assert result["data"][CONF_API_KEY] == "secret"
    assert result["data"][CONF_SERVER_ID] == result["result"].unique_id


async def test_an_empty_key_is_stored_as_none(hass: HomeAssistant, flow_api: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_API_KEY: ""}
    )
    assert result["data"][CONF_API_KEY] is None


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (SnapDogAuthError("no"), "invalid_auth"),
        (SnapDogConnectionError("no"), "cannot_connect"),
    ],
)
async def test_a_failed_check_shows_its_error_and_can_be_retried(
    hass: HomeAssistant, flow_api: MagicMock, error: Exception, key: str
) -> None:
    flow_api.version.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}
    flow_api.version.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_an_old_server_is_refused(hass: HomeAssistant, flow_api: MagicMock) -> None:
    flow_api.version.return_value = VersionInfo("SnapDog", "0.29.0", "1.90")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "unsupported_version"}


async def test_a_development_version_counts_by_its_number(
    hass: HomeAssistant, flow_api: MagicMock
) -> None:
    flow_api.version.return_value = VersionInfo("SnapDog", "0.30.0-dev+abc", "1.90")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_same_address_is_not_added_twice(
    hass: HomeAssistant, flow_api: MagicMock, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_prefills_the_form_and_keeps_the_server_id(
    hass: HomeAssistant, flow_api: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=discovery(tls="true")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: "192.0.2.20", CONF_TLS: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERVER_ID] == SERVER_ID
    assert result["result"].unique_id == SERVER_ID


async def test_zeroconf_updates_the_address_of_a_known_server(
    hass: HomeAssistant, flow_api: MagicMock, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=discovery()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.20"


async def test_zeroconf_without_a_server_id_is_refused(hass: HomeAssistant) -> None:
    info = discovery()
    info.properties.pop("server_id")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_server_id"


async def test_reauth_stores_the_new_key(
    hass: HomeAssistant, flow_api: MagicMock, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    flow_api.version.side_effect = SnapDogAuthError("no")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    flow_api.version.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "fresh"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "fresh"
