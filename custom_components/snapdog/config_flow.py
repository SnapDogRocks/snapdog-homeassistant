"""Set up a SnapDog server by hand, from zeroconf, and renew its API key."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pysnapdog import SnapDogAuthError, SnapDogClient, SnapDogError

from .const import (
    CONF_API_KEY,
    CONF_SERVER_ID,
    CONF_TLS,
    CONF_VERIFY_TLS,
    DEFAULT_PORT,
    DOMAIN,
    MINIMUM_SERVER_VERSION,
)


def _version_tuple(version: str) -> tuple[int, ...]:
    """The numeric part of a version such as ``0.30.0-dev``."""
    core = version.split("-", 1)[0].split("+", 1)[0]
    return tuple(int(part) for part in core.split(".") if part.isdigit())


class SnapDogConfigFlow(ConfigFlow, domain=DOMAIN):
    """One config entry per server."""

    VERSION = 1

    def __init__(self) -> None:
        """Nothing is known before the first step."""
        self._host: str | None = None
        self._port = DEFAULT_PORT
        self._tls = False
        self._server_id: str | None = None
        self._name = "SnapDog"

    async def _check(self, data: Mapping[str, Any]) -> tuple[str | None, str | None]:
        """Ask the server for its version. Returns the server name and an error key."""
        client = SnapDogClient(
            async_get_clientsession(self.hass, verify_ssl=data[CONF_VERIFY_TLS]),
            data[CONF_HOST],
            port=data[CONF_PORT],
            api_key=data.get(CONF_API_KEY) or None,
            tls=data[CONF_TLS],
            verify_tls=data[CONF_VERIFY_TLS],
        )
        try:
            info = await client.version()
            await client.zones()
        except SnapDogAuthError:
            return None, "invalid_auth"
        except SnapDogError:
            return None, "cannot_connect"
        if _version_tuple(info.version) < MINIMUM_SERVER_VERSION:
            return None, "unsupported_version"
        return info.name, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the address and, if the server wants one, the API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                **user_input,
                CONF_API_KEY: user_input.get(CONF_API_KEY) or None,
            }
            self._async_abort_entries_match(
                {CONF_HOST: data[CONF_HOST], CONF_PORT: data[CONF_PORT]}
            )
            _, error = await self._check(data)
            if error is None:
                server_id = self._server_id or uuid.uuid4().hex
                await self.async_set_unique_id(server_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: data[CONF_HOST]})
                return self.async_create_entry(
                    title=f"SnapDog {data[CONF_HOST]}",
                    data={**data, CONF_SERVER_ID: server_id},
                )
            errors["base"] = error
        schema = probatio.Schema(
            {
                probatio.Required(CONF_HOST, default=self._host or probatio.UNDEFINED): str,
                probatio.Required(CONF_PORT, default=self._port): int,
                probatio.Optional(CONF_API_KEY): str,
                probatio.Required(CONF_TLS, default=self._tls): bool,
                probatio.Required(CONF_VERIFY_TLS, default=True): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """A server announced itself as ``_snapdog._tcp``."""
        properties = discovery_info.properties
        server_id = properties.get("server_id")
        if not server_id:
            return self.async_abort(reason="no_server_id")
        host = str(discovery_info.ip_address)
        await self.async_set_unique_id(server_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: host, CONF_PORT: discovery_info.port}
        )
        self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: discovery_info.port})
        self._host = host
        self._port = discovery_info.port or DEFAULT_PORT
        self._tls = properties.get("tls") == "true"
        self._server_id = server_id
        self.context["title_placeholders"] = {"name": f"SnapDog {host}"}
        return await self.async_step_user()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The server no longer accepts the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_API_KEY: user_input[CONF_API_KEY]}
            _, error = await self._check(data)
            if error is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(CONF_API_KEY): str}),
            errors=errors,
        )
