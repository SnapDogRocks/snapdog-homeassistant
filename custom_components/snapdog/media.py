"""Media ids and the browse tree of the SnapDog playlists.

A media id never carries a playlist index, because the index moves when a playlist is created
or removed, when radios change and during a Subsonic outage. It carries the ``playlist_id``
(``snapdog:radio`` or ``subsonic:<id>``), percent-encoded, and for a track its position in that
playlist:

* ``playlist/<playlist_id>``
* ``track/<playlist_id>/<track>``
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote, unquote

from homeassistant.components.media_player.browse_media import BrowseMedia
from homeassistant.components.media_player.const import MediaClass, MediaType
from pysnapdog import MediaTrack, Playlist

ROOT_TITLE: Final = "SnapDog"
ROOT_TYPE: Final = "library"

_PLAYLIST: Final = "playlist"
_TRACK: Final = "track"


@dataclass(frozen=True, slots=True)
class MediaRef:
    """What a media id points at: a playlist, or one track of it."""

    playlist_id: str
    track: int | None = None

    @property
    def media_type(self) -> MediaType:
        """The Home Assistant media type of the target."""
        return MediaType.PLAYLIST if self.track is None else MediaType.TRACK

    def __str__(self) -> str:
        """The media id."""
        encoded = quote(self.playlist_id, safe="")
        if self.track is None:
            return f"{_PLAYLIST}/{encoded}"
        return f"{_TRACK}/{encoded}/{self.track}"


def parse_media_id(media_id: str) -> MediaRef:
    """Read a media id. A ``ValueError`` says it is not one of ours."""
    kind, _, rest = media_id.partition("/")
    if kind == _PLAYLIST and rest and "/" not in rest:
        return MediaRef(unquote(rest))
    if kind == _TRACK:
        encoded, _, number = rest.partition("/")
        if encoded and number.isascii() and number.isdecimal():
            return MediaRef(unquote(encoded), int(number))
    msg = f"not a SnapDog media id: {media_id!r}"
    raise ValueError(msg)


def source_labels(playlists: Iterable[Playlist]) -> dict[str, str]:
    """Map the label of each addressable playlist to its ``playlist_id``.

    The label is the name. Two playlists of the same name get their ``playlist_id`` appended,
    so that the label picks exactly one. A playlist without ``playlist_id`` (an older server)
    cannot be addressed stably and is left out.
    """
    addressable = [
        (playlist.name, playlist.playlist_id)
        for playlist in playlists
        if playlist.playlist_id is not None
    ]
    names = Counter(name for name, _ in addressable)
    return {
        (name if names[name] == 1 else f"{name} ({playlist_id})"): playlist_id
        for name, playlist_id in addressable
    }


def browse_root(playlists: Iterable[Playlist]) -> BrowseMedia:
    """The playlists of the server."""
    children = [
        browse_playlist(playlist_id, name, expanded=False)
        for name, playlist_id in source_labels(playlists).items()
    ]
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id="",
        media_content_type=ROOT_TYPE,
        title=ROOT_TITLE,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=MediaClass.PLAYLIST,
    )


def browse_playlist(
    playlist_id: str,
    name: str,
    tracks: Iterable[MediaTrack] | None = None,
    *,
    expanded: bool,
) -> BrowseMedia:
    """A playlist, with its tracks as children when ``expanded``."""
    return BrowseMedia(
        media_class=MediaClass.PLAYLIST,
        media_content_id=str(MediaRef(playlist_id)),
        media_content_type=MediaType.PLAYLIST,
        title=name,
        can_play=True,
        can_expand=True,
        children=[browse_track(playlist_id, track) for track in tracks or ()] if expanded else None,
        children_media_class=MediaClass.TRACK if expanded else None,
    )


def browse_track(playlist_id: str, track: MediaTrack) -> BrowseMedia:
    """One track. It is played, not opened."""
    title = f"{track.artist} - {track.title}" if track.artist else track.title
    return BrowseMedia(
        media_class=MediaClass.TRACK,
        media_content_id=str(MediaRef(playlist_id, track.track)),
        media_content_type=MediaType.TRACK,
        title=title,
        can_play=True,
        can_expand=False,
    )
