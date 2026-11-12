import os
import logging
from urllib.parse import urljoin

import requests

from app.core import container
from app.core._shared import with_retry
from app.core.ffmpeg_path import ffmpeg_file_arg, get_ffmpeg_exe, run_command
from app.core.headers import get_headers
from app.core.hosts import redact, safe_get
from app.core.m3u8 import M3U8_Parser

logger = logging.getLogger(__name__)

LANG_MAP = {
    "ita": "it", "eng": "en", "fra": "fr", "spa": "es", "deu": "de",
    "por": "pt", "jpn": "ja", "zho": "zh", "ara": "ar", "rus": "ru",
    "kor": "ko", "nld": "nl", "pol": "pl", "swe": "sv", "nor": "no",
    "dan": "da", "fin": "fi", "tur": "tr", "ell": "el", "ces": "cs",
}

# Human-readable names for subtitle track titles, keyed on ISO 639-2 base code.
_LANG_NAME = {
    "ita": "Italian", "eng": "English", "fra": "French", "fre": "French",
    "spa": "Spanish", "deu": "German", "ger": "German", "por": "Portuguese",
    "jpn": "Japanese", "rus": "Russian", "ukr": "Ukrainian", "gre": "Greek",
    "ell": "Greek", "nld": "Dutch", "pol": "Polish", "ara": "Arabic",
    "zho": "Chinese", "kor": "Korean",
}


def _normalize_lang(raw: str) -> tuple[str, bool]:
    """Return (iso_639_2_code, is_forced) from a vixcloud language token.

    vixcloud uses ISO 639-2/B codes (eng, ita, ger, fre, gre, ukr...) plus a
    'forced-<code>' variant. MKV needs a valid 3-letter code, so 'forced-ita'
    must become language=ita + forced disposition, not a literal 'forced-ita'
    (which players show as 'Undefined').
    """
    forced = raw.startswith("forced-")
    code = raw[len("forced-"):] if forced else raw
    return code or "und", forced


# What FFmpeg writes into while the remux runs. The remux reads and writes the
# same container now — the join already produced the configured one — and
# FFmpeg cannot have one file as both input and output. It has to be a sibling
# in the library folder rather than something under tmp/: os.replace() cannot
# cross a filesystem, and the temp directory is routinely a different volume.
#
# The leading dot is what keeps a Jellyfin scan that runs mid-remux from
# indexing the half-written file as a second copy of the title.
STAGING_PREFIX = "."
STAGING_SUFFIX = ".remuxing"

# The same idea one step earlier. The whole download is assembled under the
# WORK name and renamed onto the destination as its last act, so a title that
# is already in the library stays intact until its replacement is complete —
# the join used to write straight onto it, and a failure or a cancel after that
# had already destroyed the good copy. JOIN is what FFmpeg writes into while
# the join itself runs.
WORK_SUFFIX = ".part"
JOIN_SUFFIX = ".joining"


def staging_path(video_path: str, suffix: str = STAGING_SUFFIX) -> str:
    """A hidden sibling of *video_path* that keeps its extension.

    The extension stays last because FFmpeg picks the muxer from it.
    """
    directory, name = os.path.split(video_path)
    stem, ext = os.path.splitext(name)
    return os.path.join(directory, STAGING_PREFIX + stem + suffix + ext)


def _remux_cmd(video_path: str, output_path: str,
               audio_tracks: list[dict], subtitle_tracks: list[dict]) -> list[str]:
    """The FFmpeg argv for the remux.

    Built by hand rather than through ffmpeg-python, and separated from the
    running of it so a test can read the command without a binary: per-stream
    -map/-metadata/-disposition are OUTPUT options and MUST come after all -i
    inputs but before the output file. (ffmpeg-python's .global_args put them
    after the output filename, where ffmpeg silently ignores them — hence
    subtitle language tags never applied.)

    file: on the library-side paths only. The temp inputs below are ours and
    cannot carry a colon, but video_path and output_path are built from a
    configured library root, where a stray colon would be read as a protocol.
    """
    cmd = [get_ffmpeg_exe(), "-y", "-i", ffmpeg_file_arg(video_path)]
    for track in audio_tracks:
        cmd += ["-i", track["path"]]
    for track in subtitle_tracks:
        cmd += ["-i", track["path"]]

    # Stream mapping
    cmd += ["-map", "0:v:0"]
    if audio_tracks:
        for i in range(len(audio_tracks)):
            cmd += ["-map", f"{i + 1}:a:0"]
    else:
        cmd += ["-map", "0:a?"]  # keep original audio if no external tracks
    for i in range(len(subtitle_tracks)):
        cmd += ["-map", f"{len(audio_tracks) + 1 + i}:0"]

    # Video and audio are always copied: the source is H.264 + AAC, native to
    # either container. Subtitles are the one thing that cannot be, because MP4
    # has no WebVTT track type — container.subtitle_codec picks "copy" for
    # Matroska and "mov_text" for MP4.
    cmd += ["-c:v", "copy", "-c:a", "copy"]
    if subtitle_tracks:
        cmd += ["-c:s", container.subtitle_codec(output_path)]
    for option, value in container.mux_options(output_path).items():
        cmd += [f"-{option}", value]

    # Per-stream metadata (output options, correctly positioned)
    for i, track in enumerate(audio_tracks):
        code, _ = _normalize_lang(track.get("language", "und"))
        cmd += [f"-metadata:s:a:{i}", f"language={code}"]

    for i, track in enumerate(subtitle_tracks):
        code, forced = _normalize_lang(track.get("language", "und"))
        name = _LANG_NAME.get(code, code.upper())
        if forced:
            name += " (Forced)"
        cmd += [f"-metadata:s:s:{i}", f"language={code}"]
        cmd += [f"-metadata:s:s:{i}", f"title={name}"]
        cmd += [f"-disposition:s:{i}", "forced" if forced else "0"]

    cmd += [ffmpeg_file_arg(output_path)]
    return cmd


def remux(video_path: str, audio_tracks: list[dict] = None,
          subtitle_tracks: list[dict] = None, cancel_event=None) -> str:
    """Mux the extra tracks into *video_path*, in the container it already names.

    The container is decided once, by the join that wrote *video_path*, and
    this keeps it: it used to be the other way round, with the destination
    always written as .mp4 and this function deciding — by renaming to .mkv —
    that anything with a second audio track or a subtitle was Matroska.

    *cancel_event* stops FFmpeg when the job is cancelled; either way it is
    stopped after ffmpeg_path.FFMPEG_TIMEOUT.

    Returns the path, which is now always the one it was given.
    """
    if audio_tracks is None:
        audio_tracks = []
    if subtitle_tracks is None:
        subtitle_tracks = []

    if not audio_tracks and not subtitle_tracks:
        return video_path

    staged = staging_path(video_path)
    cmd = _remux_cmd(video_path, staged, audio_tracks, subtitle_tracks)

    logger.info("Remuxing into %s: video + %d audio + %d subtitle tracks",
                os.path.splitext(video_path)[1] or "?", len(audio_tracks), len(subtitle_tracks))
    try:
        returncode, _, stderr_bytes = run_command(cmd, cancel_event=cancel_event)
    except BaseException:
        # Cancelled, timed out, or FFmpeg could not be started at all.
        _discard(staged)
        raise
    if returncode != 0:
        # Never leave the half-written sibling behind: it sits in the library
        # folder, where Jellyfin would index it as a second copy of the title.
        _discard(staged)
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else "(no stderr)"
        if len(stderr) > 500:
            stderr = f"...{stderr[-300:]}"
        raise RuntimeError(f"FFmpeg remux error: {stderr}")

    os.replace(staged, video_path)
    logger.info("Remux complete: %s", video_path)
    return video_path


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _get_text(url: str, referer: str | None, timeout: int, what: str):
    """One subtitle fetch: validated host, the download's headers, retried."""
    headers = {"user-agent": get_headers()}
    if referer:
        headers["referer"] = referer
    return with_retry(lambda: safe_get(requests.get, url, headers=headers, timeout=timeout),
                      what=what)


def _join_vtt(parts: list[bytes]) -> bytes:
    """Concatenate WebVTT segments into one file.

    Each segment is a complete WebVTT document, so every one after the first
    loses its header block — ``WEBVTT`` and whatever follows it up to the first
    blank line, which is where HLS puts X-TIMESTAMP-MAP — and only its cues are
    appended. The cue times are left as they are: X-TIMESTAMP-MAP offsets are
    not applied, which is right for a playlist whose cues are already timed
    against the programme rather than against each segment.
    """
    joined = []
    for index, raw in enumerate(parts):
        text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
        if index and text.lstrip().startswith("WEBVTT"):
            _, _, text = text.lstrip().partition("\n\n")
        text = text.strip("\n")
        if text:
            joined.append(text)
    return ("\n\n".join(joined) + "\n").encode("utf-8")


def download_subtitle_tracks(parser, allowed_languages: list[str], output_dir: str,
                             video_stem: str, temp_dir: str = None,
                             referer: str = None, base_url: str = None) -> list[dict]:
    """Download the requested subtitle languages; returns the ones that arrived.

    A language that is absent from the result did not make it — not offered, or
    failed — and the caller reports it as a missing track rather than letting
    the job finish as though it were complete. Every segment of the subtitle
    playlist is fetched and joined: only the first used to be, which is the
    whole film on a source that publishes one segment and the opening minutes
    on one that does not.
    """
    downloaded = []

    if parser is None or not parser.subtitle_playlist:
        return downloaded

    save_dir = temp_dir if temp_dir else output_dir
    os.makedirs(save_dir, exist_ok=True)

    for sub_info in parser.subtitle_playlist:
        lang_code = sub_info.get("language", "")
        if lang_code not in allowed_languages:
            continue
        if any(done["language"] == lang_code for done in downloaded):
            continue

        lang_short = LANG_MAP.get(lang_code, lang_code)
        out_path = os.path.join(save_dir, f"{video_stem}.{lang_short}.vtt")

        logger.info("Downloading subtitle: %s -> %s", lang_code, out_path)

        try:
            playlist_url = urljoin(base_url or "", sub_info.get("uri") or "")
            req_sub_content = _get_text(playlist_url, referer, 15, "subtitle playlist")
            if not req_sub_content.ok:
                logger.warning("Failed to fetch subtitle playlist for %s: HTTP %s",
                               lang_code, req_sub_content.status_code)
                continue

            sub_parse = M3U8_Parser()
            sub_parse.parse_data(req_sub_content.text, base_uri=playlist_url)
            if not sub_parse.subtitle:
                logger.warning("Subtitle playlist for %s lists no WebVTT segment", lang_code)
                continue

            parts = []
            for segment_url in sub_parse.subtitle:
                content_resp = _get_text(segment_url, referer, 30, "subtitle")
                if not content_resp.ok or not content_resp.content:
                    raise RuntimeError(
                        f"segmento {len(parts) + 1}/{len(sub_parse.subtitle)}: "
                        f"HTTP {content_resp.status_code}")
                parts.append(content_resp.content)

            # Staged and renamed, so a failure part-way never leaves a file
            # that reads as a finished subtitle.
            staged = out_path + ".part"
            with open(staged, "wb") as f:
                f.write(_join_vtt(parts))
            os.replace(staged, out_path)
            downloaded.append({"path": out_path, "language": lang_code})
            logger.info("Subtitle downloaded: %s (%d segments)", out_path, len(parts))
        except Exception as e:
            _discard(out_path + ".part")
            logger.warning("Failed to download subtitle %s: %s", lang_code, redact(e))

    return downloaded


def get_audio_track_url(parser, language_name: str) -> str | None:
    if parser.audio_ts:
        for obj_audio in parser.audio_ts:
            if obj_audio.get("name") == language_name:
                return obj_audio.get("uri")
    return None
