"""Media ids and the browse tree of the SnapDog playlists.

A media id never carries a playlist index, because the index moves when a playlist is created
or removed, when radios change and during a Subsonic outage. It carries the ``playlist_id``
(``snapdog:radio`` or ``subsonic:<id>``), percent-encoded, and for a track its position in that
playlist:

* ``playlist/<playlist_id>``
* ``track/<playlist_id>/<track>``

A search also finds what is not in a playlist: a track or an album of the Subsonic server. They
are played by their own id, and the ids are percent-encoded the same way:

* ``song/<track_id>``
* ``album/<album_id>``
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote, unquote

from homeassistant.components.media_player.browse_media import BrowseMedia
from homeassistant.components.media_player.const import MediaClass, MediaType
from pysnapdog import MediaTrack, Playlist, SearchItem, SearchKind

ROOT_TITLE: Final = "SnapDog"
ROOT_TYPE: Final = "library"

_PLAYLIST: Final = "playlist"
_TRACK: Final = "track"
_SONG: Final = "song"
_ALBUM: Final = "album"


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


@dataclass(frozen=True, slots=True)
class SongRef:
    """A track of the Subsonic server that is in no playlist, found by search."""

    track_id: str

    @property
    def media_type(self) -> MediaType:
        """The Home Assistant media type of the target."""
        return MediaType.TRACK

    def __str__(self) -> str:
        """The media id."""
        return f"{_SONG}/{quote(self.track_id, safe='')}"


@dataclass(frozen=True, slots=True)
class AlbumRef:
    """An album of the Subsonic server, found by search."""

    album_id: str

    @property
    def media_type(self) -> MediaType:
        """The Home Assistant media type of the target."""
        return MediaType.ALBUM

    def __str__(self) -> str:
        """The media id."""
        return f"{_ALBUM}/{quote(self.album_id, safe='')}"


type MediaTarget = MediaRef | SongRef | AlbumRef


def parse_media_id(media_id: str) -> MediaTarget:
    """Read a media id. A ``ValueError`` says it is not one of ours."""
    kind, _, rest = media_id.partition("/")
    if kind == _PLAYLIST and rest and "/" not in rest:
        return MediaRef(unquote(rest))
    if kind == _TRACK:
        encoded, _, number = rest.partition("/")
        if encoded and number.isascii() and number.isdecimal():
            return MediaRef(unquote(encoded), int(number))
    if kind == _SONG and rest and "/" not in rest:
        return SongRef(unquote(rest))
    if kind == _ALBUM and rest and "/" not in rest:
        return AlbumRef(unquote(rest))
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


_KIND_CLASSES: Final = {
    SearchKind.TRACK: MediaClass.TRACK,
    SearchKind.ALBUM: MediaClass.ALBUM,
    SearchKind.PLAYLIST: MediaClass.PLAYLIST,
    SearchKind.RADIO: MediaClass.TRACK,
}


def search_kinds(classes: Iterable[MediaClass | str] | None) -> list[SearchKind]:
    """The kinds of result that fit the media classes a search asks for.

    No filter, which Home Assistant sends as ``None`` or as an empty list, means every kind. A
    radio station is a track of the radio playlist in the browser, so it is found under the track
    class, too. Classes the server has no kind for match nothing.
    """
    wanted = set(classes or ())
    if not wanted:
        return list(SearchKind)
    return [kind for kind, media_class in _KIND_CLASSES.items() if media_class in wanted]


def browse_search_item(item: SearchItem) -> BrowseMedia | None:
    """One search result, or ``None`` when it has no id to play it by.

    A result is played, not opened: an album and a playlist list their tracks in the browser only
    through the playlist route, which a search result does not use.
    """
    target: MediaTarget
    match item.kind:
        case SearchKind.TRACK if item.track_id is not None:
            target = SongRef(item.track_id)
        case SearchKind.ALBUM if item.album_id is not None:
            target = AlbumRef(item.album_id)
        case SearchKind.PLAYLIST if item.playlist_id is not None:
            target = MediaRef(item.playlist_id)
        case SearchKind.RADIO if item.playlist_id is not None and item.track is not None:
            target = MediaRef(item.playlist_id, item.track)
        case _:
            return None
    title = f"{item.subtitle} - {item.title}" if item.subtitle else item.title
    return BrowseMedia(
        media_class=_KIND_CLASSES[item.kind],
        media_content_id=str(target),
        media_content_type=target.media_type,
        title=title,
        can_play=True,
        can_expand=False,
    )
