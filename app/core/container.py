"""Which container the downloader writes, and what FFmpeg needs to produce it.

The container used to be an accident rather than a choice. Every destination was
built as ``.mp4``, and the file turned into ``.mkv`` the moment there was a
second audio track or a subtitle to embed — so in practice almost everything
ended up Matroska, and the devices that cannot play it (older smart TVs,
Chromecast, consoles) had no way out. Issue #19.

A module of its own rather than part of ``app.core.format``, because
``format.py`` imports ``app.core.m3u8`` and ``m3u8.py`` imports
``app.core.paths``: putting the table there would make ``paths -> format`` a
cycle. Nothing here imports from ``app.core`` at all, and the one import of
``app.config`` is deferred, so this module stays safe to import from anywhere.

Nothing is re-encoded here unless it has to be. What comes out of the source's
HLS is H.264 video and AAC audio, both native to either container, so the join
and the remux are stream copies. Two exceptions, neither touching the picture:
WebVTT becomes ``mov_text`` in MP4, which has no WebVTT track type, and the
join re-encodes the *audio* to AAC as a fallback when FFmpeg refuses to copy
it. (The optional conversion in ``app.core.transcode`` is a separate, explicit
setting and runs after all of this.)
"""

import logging
import mimetypes
import os

logger = logging.getLogger(__name__)

DEFAULT = "mkv"

CONTAINERS: dict[str, dict] = {
    "mkv": {
        "extension": ".mkv",
        "label": "MKV",
        "mime": "video/x-matroska",
        # Matroska holds WebVTT natively, so the downloaded sidecars go in as-is.
        "subtitle_codec": "copy",
        # No muxer-private options. mp4's movflags is not merely ignored here,
        # it is fatal: the matroska muxer does not declare the option at all,
        # and FFmpeg refuses an option its muxer has never heard of.
        "mux_options": {},
    },
    "mp4": {
        "extension": ".mp4",
        "label": "MP4",
        "mime": "video/mp4",
        # MP4 has no WebVTT track type; 3GPP timed text is what players read.
        "subtitle_codec": "mov_text",
        "mux_options": {"movflags": "+faststart"},
    },
}

# The extensions a finished download can carry. The single list the library
# check, the failure cleanup and the file manager all read, instead of the
# ".mkv" literals that used to be spread across four modules.
KNOWN_EXTENSIONS: tuple[str, ...] = tuple(
    spec["extension"] for spec in CONTAINERS.values()
)

EMBED = "embed"
EXTERNAL = "external"
SUBTITLE_MODES = (EMBED, EXTERNAL)
DEFAULT_SUBTITLE_MODE = EMBED

# Applied to every join regardless of container: both streams are copied. The
# source's AAC is native to MKV and MP4 alike, so decoding and re-encoding it —
# which every join used to do — cost a generation of quality and most of the
# join's time for nothing. Only the muxer flags merged in on top differ.
JOIN_COMMON: dict[str, str] = {
    "c:v": "copy",
    "c:a": "copy",
}

# What the join falls back to when the copy is refused: audio that is not AAC,
# or an ADTS stream too damaged to rewrap. The video is still never touched.
JOIN_REENCODE_AUDIO: dict[str, str] = {
    "c:v": "copy",
    "c:a": "aac",
    "b:a": "192k",
    "af": "aresample=async=1000",
}

# AAC travels in MPEG-TS with an ADTS header on every frame; MP4 wants the
# configuration once, in the track header. Matroska's muxer does the rewrap by
# itself, MP4's is told to.
_COPY_AUDIO_BSF: dict[str, dict[str, str]] = {
    ".mp4": {"bsf:a": "aac_adtstoasc"},
}


def _setting(key: str, allowed, default: str) -> str:
    """A stored settings string, or *default* when it is not one of *allowed*.

    Falls back rather than raising, for the same reason ``naming.templates()``
    does: this is read inside a running download, where a malformed setting
    must not be what stops it.
    """
    from app.config import get_settings

    value = get_settings().get(key)
    if isinstance(value, str) and value in allowed:
        return value
    if value not in (None, ""):
        logger.warning(
            "%s is %r, which is not one of %s; using %r",
            key, value, sorted(allowed), default,
        )
    return default


def configured() -> str:
    """The container an administrator chose in Settings."""
    return _setting("output_container", CONTAINERS, DEFAULT)


def subtitle_mode() -> str:
    """Whether subtitles are muxed in or left beside the video as ``.vtt``."""
    return _setting("subtitle_mode", SUBTITLE_MODES, DEFAULT_SUBTITLE_MODE)


def spec(name: str | None = None) -> dict:
    """The table entry for *name*, or for the configured container."""
    if name is None:
        name = configured()
    if name not in CONTAINERS:
        logger.warning("Unknown container %r; using %r", name, DEFAULT)
        name = DEFAULT
    return CONTAINERS[name]


def extension(name: str | None = None) -> str:
    """The file extension for *name*, or for the configured container."""
    return spec(name)["extension"]


def candidate_extensions(name: str | None = None) -> tuple[str, ...]:
    """Every extension a finished download could carry, likeliest first.

    The configured container leads, so the library check stats the file it
    would write today before the one an earlier setting would have written.
    """
    first = extension(name)
    return (first, *(e for e in KNOWN_EXTENSIONS if e != first))


def _spec_for_path(path: str) -> dict:
    """The table entry matching a path's extension, falling back to configured.

    Everything downstream of ``paths.py`` knows its container only from the
    name it was handed, so the extension is the carrier.
    """
    ext = os.path.splitext(path or "")[1].lower()
    for entry in CONTAINERS.values():
        if entry["extension"] == ext:
            return entry
    return spec()


def join_options(output_path: str, reencode_audio: bool = False) -> dict:
    """FFmpeg output options for the join, for whatever *output_path* names.

    *reencode_audio* is the fallback for a copy FFmpeg refused; see
    JOIN_REENCODE_AUDIO.
    """
    entry = _spec_for_path(output_path)
    if reencode_audio:
        return {**JOIN_REENCODE_AUDIO, **entry["mux_options"]}
    return {**JOIN_COMMON, **_COPY_AUDIO_BSF.get(entry["extension"], {}),
            **entry["mux_options"]}


def subtitle_codec(output_path: str) -> str:
    """``-c:s`` for a remux writing *output_path*."""
    return _spec_for_path(output_path)["subtitle_codec"]


def mux_options(output_path: str) -> dict:
    """Muxer-private options for a remux writing *output_path*."""
    return dict(_spec_for_path(output_path)["mux_options"])


def mime_for(path: str) -> str:
    """The media type to serve *path* as.

    Every file used to be announced as ``video/mp4``, which was already wrong
    for the MKVs the downloader produced and would now be wrong half the time.
    """
    ext = os.path.splitext(path or "")[1].lower()
    for entry in CONTAINERS.values():
        if entry["extension"] == ext:
            return entry["mime"]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"
