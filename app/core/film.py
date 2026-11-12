import logging
import os

import requests
from bs4 import BeautifulSoup

from app.core.headers import get_headers, sanitize_filename
from app.core.m3u8 import download_m3u8, fetch_master_languages, M3U8_Parser
from app.core.paths import film_path
from app.core._shared import (
    StreamSource,
    resolve_stream,
    MissingAudioTrackError,
    _fetch_vixcloud_embed,
    _parse_content,
    _get_m3u8_key,
    _get_m3u8_url,
    embed_referer,
    apply_video_host,
    with_retry,
)
from app.core.hosts import redact, safe_get

logger = logging.getLogger(__name__)


def iframe_src(html: str) -> str:
    """The embed URL a source's iframe page names, or a RuntimeError saying it has none.

    A page without the iframe — a removed title, a challenge, a login wall —
    used to surface as "'NoneType' object has no attribute 'get'".
    """
    iframe = BeautifulSoup(html, "lxml").find("iframe")
    src = iframe.get("src") if iframe is not None else None
    if not src:
        raise RuntimeError("Video non disponibile: la pagina della sorgente non contiene l'iframe del player")
    return src


def _get_iframe(id_title, domain):
    ua = get_headers()
    for path in (f"/iframe/{id_title}", f"/it/iframe/{id_title}"):
        url = f"https://{domain}{path}"
        req = with_retry(lambda: requests.get(url, headers={"User-agent": ua}, timeout=15),
                         what="iframe")
        if req.ok:
            break
    else:
        raise RuntimeError(f"Cannot fetch iframe: HTTP {req.status_code}")

    url_embed = apply_video_host(iframe_src(req.text))
    # The vixcloud /embed/ is behind Cloudflare — fetch via cloudscraper.
    script_text = _fetch_vixcloud_embed(url_embed, referer=f"https://{domain}/")
    # Its length and nothing else: the script carries the playback token.
    logger.info("Embed script received (%d characters)", len(script_text))
    return script_text, url_embed


_AUDIO_LANG_ALIASES = {
    "ita": ("it", "Italian", "Italiano"),
    "eng": ("en", "English"),
    "fra": ("fr", "French", "Français"),
    "spa": ("es", "Spanish", "Español"),
    "deu": ("de", "German", "Deutsch"),
    "por": ("pt", "Portuguese", "Português"),
    "jpn": ("ja", "Japanese", "日本語"),
}


def _get_audio_track_url(parser, lang_code: str) -> str | None:
    """Match audio track by ISO 639-2 code, ISO 639-1 code, or full name."""
    aliases = {lang_code} | set(_AUDIO_LANG_ALIASES.get(lang_code, ()))
    if parser.audio_ts:
        for obj_audio in parser.audio_ts:
            if obj_audio.get("language") in aliases or obj_audio.get("name") in aliases:
                return obj_audio.get("uri")
    return None


def _fetch_master(m3u8_url: str, referer: str, what: str):
    """The master playlist response, with the ``?b=1`` retry both collectors need."""
    headers = {"user-agent": get_headers(), "referer": referer}
    req = safe_get(requests.get, m3u8_url, headers=headers, timeout=15)
    if req.status_code == 403:
        b1_url = m3u8_url + ("&b=1" if "?" in m3u8_url else "?b=1")
        logger.warning("Master M3U8 returned 403, retrying with ?b=1 for %s track collection", what)
        req = safe_get(requests.get, b1_url, headers=headers, timeout=15)
    return req


def _collect_audio_tracks(m3u8_url: str, referer: str, audio_languages: list[str],
                          strict: bool = False, missing: list[str] | None = None) -> list[dict]:
    """Resolve the audio playlists for the requested languages.

    With ``strict`` set, a language that is not on offer raises instead of being
    skipped. The request flow uses that: a user who asked for Italian audio must
    not silently receive an English file.

    Without it the language is still skipped, but no longer silently: it is
    appended to *missing* (as ``audio:<lang>``) when the caller passes a list,
    which is how the finished job gets to say what it does not contain.
    """
    tracks = []

    def _report(languages):
        if missing is not None:
            missing.extend(f"audio:{lang}" for lang in languages
                           if f"audio:{lang}" not in missing)

    try:
        req = _fetch_master(m3u8_url, referer, "audio")
        if not req.ok:
            logger.warning("Could not fetch master M3U8 for audio tracks: HTTP %d", req.status_code)
            if strict:
                raise RuntimeError(f"Master M3U8 non raggiungibile: HTTP {req.status_code}")
            # Unknown, not absent — but the file will be written without them
            # either way, and that is what the job has to report.
            _report(audio_languages)
            return tracks
        parser = M3U8_Parser()
        parser.parse_data(req.text, base_uri=m3u8_url)

        # No separate audio renditions at all: this source muxes its single
        # soundtrack into the video rendition. There is nothing to choose
        # between and nothing to substitute, so strict has nothing to protect
        # against — the file that comes back carries the only audio there is.
        # Raising here rejected every such title at approval, after the request
        # had already been accepted: the create path (router.py) skips its own
        # check for exactly this case, so the two disagreed.
        if not parser.audio_ts:
            logger.info("No separate audio renditions offered; using the muxed track")
            return tracks

        for lang in audio_languages:
            url = _get_audio_track_url(parser, lang)
            if url:
                tracks.append({"url": url, "language": lang})
                logger.info("Audio track found for %s: %s", lang, redact(url)[:80])
                continue
            available = [t.get("language") or t.get("name") for t in parser.audio_ts]
            logger.warning("Audio track not found for language: %s (available: %s)", lang, available)
            if strict:
                raise MissingAudioTrackError(lang, [a for a in available if a])
            _report([lang])
    except MissingAudioTrackError:
        raise
    except Exception as e:
        if strict:
            raise
        logger.warning("Could not collect audio tracks: %s", redact(e))
        _report(lang for lang in audio_languages
                if lang not in {t["language"] for t in tracks})
    return tracks


def _collect_subtitle_tracks(m3u8_url: str, referer: str, subtitle_languages: list[str]) -> list[dict]:
    """The subtitle playlists on offer for the requested languages.

    Informational: the subtitles themselves are fetched by download_m3u8, which
    is also what reports the ones that did not arrive.
    """
    tracks = []
    try:
        req = _fetch_master(m3u8_url, referer, "subtitle")
        if not req.ok:
            logger.warning("Could not fetch master M3U8 for subtitle tracks: HTTP %d",
                           req.status_code)
            return tracks
        parser = M3U8_Parser()
        parser.parse_data(req.text, base_uri=m3u8_url)
        for lang in subtitle_languages:
            for sub in parser.subtitle_playlist:
                if sub.get("language") == lang:
                    tracks.append({"uri": sub.get("uri"), "language": lang})
                    logger.info("Subtitle track found for %s: %s", lang,
                                redact(sub.get("uri") or "")[:80])
                    break
    except Exception as e:
        logger.warning("Could not collect subtitle tracks: %s", redact(e))
    return tracks


def get_film_languages(id_film: int, domain: str) -> dict:
    from urllib.parse import urlparse, parse_qs
    embed_content, url_embed = _get_iframe(id_film, domain)
    json_win_video, json_win_param = _parse_content(embed_content, url_embed)
    m3u8_url = _get_m3u8_url(json_win_video, json_win_param)
    referer = embed_referer(json_win_video, json_win_param)
    langs = fetch_master_languages(m3u8_url, referer)
    explicit_lang = parse_qs(urlparse(url_embed).query).get("lang", [None])[0]
    langs["lang"] = explicit_lang
    return langs


def download_film(id_film: int, title_name: str, domain: str,
                  output_dir: str = "videos",
                  temp_dir: str = None,
                  progress_factory=None,
                  year: str = None,
                  cancel_event=None,
                  audio_languages: list[str] = None,
                  subtitle_languages: list[str] = None,
                  strict_audio: bool = False,
                  tmdb_id: int = None, *, source: str = "streamingcommunity"):
    from app.source_identity import download_source

    source = download_source("film", source)
    audio_languages = audio_languages or ["ita"]
    subtitle_languages = subtitle_languages or []

    def _primary() -> StreamSource:
        embed_content, embed_referer = _get_iframe(id_film, domain)
        json_win_video, json_win_param = _parse_content(embed_content, embed_referer)
        logger.info("Video ID: %s token: <redacted> embed_url: %s", json_win_video['id'], redact(embed_referer)[:80])
        logger.info("Audio language: %s", json_win_video.get("lang", "it"))
        return StreamSource(
            m3u8_url=_get_m3u8_url(json_win_video, json_win_param),
            key_hex=_get_m3u8_key(json_win_video, json_win_param, embed_referer),
            referer=embed_referer,
            provider="vixcloud",
        )

    stream = resolve_stream(_primary, tmdb_id=tmdb_id, media_type="movie")
    m3u8_url, m3u8_key, embed_referer = stream.m3u8_url, stream.key_hex, stream.referer
    if stream.provider != "vixcloud":
        logger.info("Resolved %s through the %s fallback", title_name, stream.provider)

    # Runs against whichever playlist answered, so a missing language is still a
    # MissingAudioTrackError rather than a quiet substitution.
    missing_tracks: list[str] = []
    audio_track_urls = _collect_audio_tracks(
        m3u8_url, embed_referer, audio_languages, strict=strict_audio,
        missing=missing_tracks,
    )
    subtitle_track_urls = _collect_subtitle_tracks(m3u8_url, embed_referer, subtitle_languages)

    # A folder a person associated with this film wins over the name the
    # templates would give it (app/library.py); None means there is none.
    from app.library import destination

    dest_path = (destination("film", id_film, root=output_dir, year=year, single_file=True,
                             source=source)
                 or film_path(output_dir, title_name, year))

    final_path = download_m3u8(
        m3u8_index=m3u8_url,
        key=m3u8_key,
        output_filename=dest_path,
        temp_dir=temp_dir,
        progress_factory=progress_factory,
        referer=embed_referer,
        cancel_event=cancel_event,
        audio_languages=audio_languages,
        subtitle_languages=subtitle_languages,
        audio_track_urls=audio_track_urls,
        subtitle_track_urls=subtitle_track_urls,
        missing_tracks=missing_tracks,
    )

    # download_m3u8 returns the real output path: the destination is built in
    # whatever container is configured, and the remux keeps it.
    return final_path or dest_path
