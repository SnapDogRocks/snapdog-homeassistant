"""Actions that manage the playlists of the media server behind SnapDog.

They belong to the server and not to a zone, so they are registered for the integration and name the
server by its config entry. An entity action would run once for every zone a call targets, which
would create a playlist twice or add the same tracks twice. The server must allow it
(``[subsonic].playlist_write``), and the radio list can never be changed.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Final

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import SnapDogConfigEntry
from .entity import request
from .media import SongRef, parse_media_id

ATTR_CONFIG_ENTRY: Final = "config_entry_id"
ATTR_NAME: Final = "name"
ATTR_PLAYLIST_ID: Final = "playlist_id"
ATTR_TRACK_IDS: Final = "track_ids"
ATTR_POSITIONS: Final = "positions"

SERVICE_CREATE: Final = "create_playlist"
SERVICE_RENAME: Final = "rename_playlist"
SERVICE_ADD: Final = "add_tracks_to_playlist"
SERVICE_REMOVE: Final = "remove_tracks_from_playlist"
SERVICE_REPLACE: Final = "replace_playlist_tracks"
SERVICE_DELETE: Final = "delete_playlist"


def _track_id(value: Any) -> str:
    """A track id of the media server, or the ``song/<id>`` media id a search returns for it."""
    text = cv.string(value)
    try:
        ref = parse_media_id(text)
    except ValueError:
        return text
    if isinstance(ref, SongRef):
        return ref.track_id
    msg = "only tracks can be put into a playlist"
    raise probatio.Invalid(msg)


_NAME = probatio.All(cv.string, probatio.Length(min=1))
_PLAYLIST_ID = probatio.All(cv.string, probatio.Length(min=1))
_TRACK_IDS = probatio.All(cv.ensure_list, [_track_id])
_NEED_TRACKS = probatio.All(_TRACK_IDS, probatio.Length(min=1))
_POSITIONS = probatio.All(cv.ensure_list, [cv.positive_int], probatio.Length(min=1))
_ENTRY = {probatio.Optional(ATTR_CONFIG_ENTRY): cv.string}


def _schema(fields: dict[Any, Any]) -> probatio.Schema:
    return probatio.Schema({**_ENTRY, **fields})


def _entry(call: ServiceCall) -> SnapDogConfigEntry:
    """The server a call is meant for: the one it names, or the only one there is."""
    hass = call.hass
    if (entry_id := call.data.get(ATTR_CONFIG_ENTRY)) is not None:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_server",
                translation_placeholders={"entry": entry_id},
            )
    else:
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if len(loaded) != 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="server_required",
                translation_placeholders={"count": str(len(loaded))},
            )
        entry = loaded[0]
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="server_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    return entry


async def _create(call: ServiceCall) -> ServiceResponse:
    api = _entry(call).runtime_data.api
    playlist = await request(api.create_playlist(call.data[ATTR_NAME], call.data[ATTR_TRACK_IDS]))
    return {"playlist_id": playlist.playlist_id, "name": playlist.name, "index": playlist.index}


async def _rename(call: ServiceCall) -> None:
    api = _entry(call).runtime_data.api
    await request(api.update_playlist(call.data[ATTR_PLAYLIST_ID], name=call.data[ATTR_NAME]))


async def _add(call: ServiceCall) -> None:
    api = _entry(call).runtime_data.api
    playlist_id = call.data[ATTR_PLAYLIST_ID]
    await request(api.update_playlist(playlist_id, add_track_ids=call.data[ATTR_TRACK_IDS]))


async def _remove(call: ServiceCall) -> None:
    api = _entry(call).runtime_data.api
    playlist_id = call.data[ATTR_PLAYLIST_ID]
    await request(api.update_playlist(playlist_id, remove_positions=call.data[ATTR_POSITIONS]))


async def _replace(call: ServiceCall) -> None:
    api = _entry(call).runtime_data.api
    playlist_id = call.data[ATTR_PLAYLIST_ID]
    await request(api.replace_playlist_tracks(playlist_id, call.data[ATTR_TRACK_IDS]))


async def _delete(call: ServiceCall) -> None:
    api = _entry(call).runtime_data.api
    await request(api.delete_playlist(call.data[ATTR_PLAYLIST_ID]))


_SERVICES: Final[
    dict[str, tuple[probatio.Schema, Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]]]]
] = {
    SERVICE_CREATE: (
        _schema(
            {
                probatio.Required(ATTR_NAME): _NAME,
                probatio.Optional(ATTR_TRACK_IDS, default=list): _TRACK_IDS,
            }
        ),
        _create,
    ),
    SERVICE_RENAME: (
        _schema(
            {probatio.Required(ATTR_PLAYLIST_ID): _PLAYLIST_ID, probatio.Required(ATTR_NAME): _NAME}
        ),
        _rename,
    ),
    SERVICE_ADD: (
        _schema(
            {
                probatio.Required(ATTR_PLAYLIST_ID): _PLAYLIST_ID,
                probatio.Required(ATTR_TRACK_IDS): _NEED_TRACKS,
            }
        ),
        _add,
    ),
    SERVICE_REMOVE: (
        _schema(
            {
                probatio.Required(ATTR_PLAYLIST_ID): _PLAYLIST_ID,
                probatio.Required(ATTR_POSITIONS): _POSITIONS,
            }
        ),
        _remove,
    ),
    SERVICE_REPLACE: (
        _schema(
            {
                probatio.Required(ATTR_PLAYLIST_ID): _PLAYLIST_ID,
                probatio.Required(ATTR_TRACK_IDS): _TRACK_IDS,
            }
        ),
        _replace,
    ),
    SERVICE_DELETE: (_schema({probatio.Required(ATTR_PLAYLIST_ID): _PLAYLIST_ID}), _delete),
}


def async_register_services(hass: HomeAssistant) -> None:
    """Register the playlist actions. Only creating a playlist answers with something."""
    for name, (schema, handler) in _SERVICES.items():
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=(
                SupportsResponse.OPTIONAL if name == SERVICE_CREATE else SupportsResponse.NONE
            ),
        )
