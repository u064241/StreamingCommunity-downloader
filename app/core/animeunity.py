import json
import logging
import re
import threading
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app.core.headers import sanitize_filename

logger = logging.getLogger(__name__)

def host() -> str:
    """AnimeUnity's current host. Read per call: Settings can change it live."""
    from app.config import configured_animeunity_host
    configured = configured_animeunity_host()
    return _redirect_hosts.get(configured, configured)


BATCH_SIZE = 120

# What /archivio/get-animes answers per call. Not ours to choose — it is the
# source's page size, and what one "Carica altri" is worth.
ARCHIVE_PAGE = 30

# A ceiling on what one call may normalise, for the day the source answers with
# more than a page.
MAX_RESULTS = 60

# The panel's word for a kind, and the source's own. The wire vocabulary is
# lower case so there is one canonical form; AnimeUnity capitalises its own.
TYPES = {
    "movie": "Movie", "tv": "TV", "ova": "OVA", "ona": "ONA", "special": "Special",
}

# The CSRF token stays good for a while and costs a page load to get, so it is
# not refetched per search.
_CSRF_TTL = 10 * 60

_scraper = None
_csrf: tuple[float, str] | None = None
_csrf_host: str | None = None
_site_formats: dict[str, bool] = {}
_redirect_hosts: dict[str, str] = {}
_home_lock = threading.RLock()


def inertia_props(html: str) -> dict | None:
    """Decode the current site's public payload, without executing its scripts."""
    from app.core.page import _data_page
    try:
        payload = _data_page(html)
    except LookupError:
        return None
    props = payload.get("props")
    if not isinstance(props, dict):
        raise RuntimeError("AnimeUnity: formato pagina non riconosciuto")
    return props


def _discover_host() -> str | None:
    """A bounded fallback to the AnimeUnity link published by the configured SC site."""
    from app.config import configured_domain
    from app.core.hosts import safe_get

    domain = configured_domain()
    if not domain:
        return None
    response = safe_get(_get_scraper().get, f"https://{domain}/", timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    for anchor in soup.select("a[href]"):
        parsed = urlparse(anchor["href"])
        if (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and re.fullmatch(r"(?:www\.)?animeunity\.[a-z]{2,24}", parsed.hostname or "")):
            return parsed.hostname
    return None


def _modern_site() -> bool:
    if host() not in _site_formats:
        with _home_lock:
            if host() not in _site_formats:
                fetch_home_html()
    return _site_formats.get(host(), False)


def _page(path: str, **params) -> dict:
    from app.core.hosts import safe_get
    response = safe_get(_get_scraper().get, f"https://{host()}{path}", params=params or None, timeout=20)
    response.raise_for_status()
    props = inertia_props(response.text)
    if props is None:
        raise RuntimeError("AnimeUnity: formato pagina non riconosciuto")
    return props


def _now() -> float:
    """Indirected so a test can move the clock without sleeping."""
    return time.time()


def _get_scraper():
    global _scraper
    if _scraper is None:
        try:
            import cloudscraper
            _scraper = cloudscraper.create_scraper(
                browser={"browser": "firefox", "platform": "darwin", "desktop": True}
            )
        except ImportError:
            logger.warning("cloudscraper not installed, falling back to requests.Session")
            _scraper = requests.Session()
    return _scraper


def fetch_home_html() -> str:
    """The AnimeUnity home page, refreshing the cached CSRF token on the way.

    Shared with the start page, which reads its shelves out of this same HTML.
    """
    global _csrf, _csrf_host
    from app.core.hosts import safe_get
    with _home_lock:
        original = host()
        try:
            r = safe_get(_get_scraper().get, f"https://{original}/", timeout=15)
            r.raise_for_status()
        except requests.RequestException as exc:
            if (not isinstance(exc, (requests.ConnectionError, requests.Timeout))
                    and getattr(exc.response, "status_code", None) not in (404, 410)):
                raise
            # An unreachable old host may no longer redirect. Use only the
            # source's explicit AnimeUnity link, once, then verify its payload.
            candidate = _discover_host()
            if not candidate or candidate == original:
                raise
            r = safe_get(_get_scraper().get, f"https://{candidate}/", timeout=15)
        r.raise_for_status()
        props = inertia_props(r.text)
        modern = props is not None and any(k in props for k in
                    ("latestEpisodes", "carouselAnimes", "animes", "anime"))
        final = urlparse(getattr(r, "url", "") or f"https://{original}/").hostname or original
        if final != original:
            if not modern or not re.fullmatch(r"(?:www\.)?animeunity\.[a-z]{2,24}", final):
                raise RuntimeError("AnimeUnity: reindirizzamento non riconosciuto")
            from app.config import configured_animeunity_host
            _redirect_hosts[configured_animeunity_host()] = final
        _site_formats[host()] = modern
        meta = BeautifulSoup(r.text, "lxml").find("meta", {"name": "csrf-token"})
        _csrf = (_now() + _CSRF_TTL, meta["content"]) if meta and meta.get("content") else None
        _csrf_host = host()
        return r.text


def forget_csrf() -> None:
    """Reset host-bound discovery, format and CSRF state after a settings change."""
    global _csrf, _csrf_host
    with _home_lock:
        _csrf = None
        _csrf_host = None
        _site_formats.clear()
        _redirect_hosts.clear()
    from app.core.animeunity_catalog import clear_cache
    clear_cache()


def _csrf_token() -> str | None:
    """The cached token, fetching the home page when there is none or it is old."""
    global _csrf
    if _csrf and _csrf_host == host() and _csrf[0] > _now():
        return _csrf[1]
    _csrf = None
    fetch_home_html()
    return _csrf[1] if _csrf else None


def _ajax_headers(token: str | None) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{host()}/archivio",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    if token:
        headers["X-CSRF-TOKEN"] = token
    return headers


def _genres(value) -> list[str]:
    """Genre names, whichever shape Laravel cast them into this time.

    Rows arrive as dicts with pivot tables, sometimes as plain strings, and
    occasionally as a JSON string. The panel wants names, and the alternative to
    tolerating all three is a TypeError raised in the middle of a search.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    names = []
    for g in (value or []):
        name = g.get("name") if isinstance(g, dict) else g
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def normalize_title(t: dict, *, language: str | None = None) -> dict | None:
    """One raw record into the shape the panel renders, or None if unusable."""
    if not isinstance(t, dict):
        return None
    title_str = (t.get("title_eng") or t.get("title") or t.get("name") or "").strip()
    id_num = t.get("id")
    if not title_str or id_num is None:
        return None
    slug = t.get("slug", "") or ""
    images = t.get("images") if isinstance(t.get("images"), dict) else {}
    variants = [v for v in (t.get("variants") or []) if isinstance(v, dict)]
    # Variants now belong to one title instead of separate dubbed records.
    # Keep original audio by default, matching the source's player.
    variant = next((v for v in variants if v.get("language") == language), None) if language else None
    variant = variant or next((v for v in variants if v.get("language") != "ita"), None)
    variant = variant or next(iter(variants), {})
    studios = [s.get("name") for s in (t.get("studios") or []) if isinstance(s, dict) and s.get("name")]
    return {
        "id": f"{id_num}-{slug}" if slug else str(id_num),
        "name": title_str,
        "type": "anime",
        # The source's own classification — Movie, TV, OVA, ONA, Special. Kept
        # beside `type` rather than in it: `type` is "anime" for every record and
        # the whole anime flow keys off that, from the detail modal to the
        # episodes endpoint to the job kind. Without this the card had nothing to
        # go on, so it labelled every film a TV series.
        "media_type": t.get("type") or "",
        "slug": slug,
        # An absolute URL, unlike StreamingCommunity's bare filename: the
        # frontend branches on that and sends only the latter through /api/image.
        "poster": (t.get("imageurl") or t.get("cover") or
                   t.get("poster") or t.get("image") or images.get("poster") or ""),
        "episodes_count": t.get("episodes_count", variant.get("episodes_count", 0)),
        # The archive endpoint sends these with the search, so the detail panel
        # costs no request of its own.
        "plot": t.get("plot") or "",
        "genres": _genres(t.get("genres")),
        # Old records had separate dubbed ids; new records expose variants.
        "dubbed": bool(t.get("dub")) or any(v.get("language") == "ita" for v in variants),
        "language": variant.get("language"),
        "score": t.get("score") or t.get("vote"),
        "release_date": t.get("date") or t.get("release_date") or str(t.get("year") or ""),
        # The archive record carries thirty-four fields and the panel was
        # keeping eleven. These cost nothing - the search already paid for
        # them - and they are what let an anime's page look like a film's
        # instead of a bare list of episodes.
        #
        # imageurl is the poster; imageurl_cover is the wide banner, which is
        # what the detail hero wants behind the title. Both are absolute
        # AniList URLs, so neither goes through /api/image.
        "backdrop": t.get("imageurl_cover") or images.get("cover") or images.get("banner") or "",
        "studio": t.get("studio") or ", ".join(studios),
        "status": t.get("status") or variant.get("status") or t.get("default_status") or "",
        "season": t.get("season") or "",
        # The romaji or Japanese title, when it differs from the one shown.
        "original_name": t.get("title_ja") or ((t.get("title") or "") if (t.get("title") or "") != title_str else ""),
    }


def _normalize_titles(titles: list, *, language: str | None = None) -> list[dict]:
    """Convert raw AnimeUnity records to the normalized format the frontend renders."""
    results, seen = [], set()
    for t in titles:
        item = normalize_title(t, language=language)
        if item is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        results.append(item)
        if len(results) >= MAX_RESULTS:
            break
    return results


def _post_archive(body: dict):
    """POST the archive query, renewing the CSRF token once if it has gone stale.

    A 419 is Laravel saying the token expired. Fetching a new one and sending the
    request again is **re-authentication, not a retry** — a different request
    carrying a new credential — which is why it does not go through
    ``_shared.with_retry`` and must not be folded into it.
    """
    global _csrf
    scraper = _get_scraper()
    url = f"https://{host()}/archivio/get-animes"

    r = scraper.post(url, json=body, headers=_ajax_headers(_csrf_token()), timeout=20)
    if r.status_code == 419:
        logger.info("AnimeUnity refused the CSRF token (HTTP %d), renewing it",
                    r.status_code)
        _csrf = None
        r = scraper.post(url, json=body, headers=_ajax_headers(_csrf_token()), timeout=20)
    return r


def search(query: str, *, page: int = 1, media_type: str | None = None,
           dubbed: bool = False) -> list[dict]:
    """Search the public Inertia archive, retaining the legacy POST adapter.

    Not ``/livesearch``, which this used to call: the source caps that at
    **eight records** for every query, with no way to ask for more and no
    filters — measured, not guessed. The archive endpoint answers thirty at a
    time, takes a row offset, and applies both filters itself.

    Both filters go to the source rather than to what comes back: filtering here
    could only ever narrow one page, and would answer "no Italian dub" for a show
    whose dub sat on page two. ``page`` is 1-based and becomes the row offset.
    """
    if _modern_site():
        params = {"query": query, "page": max(page, 1)}
        if media_type:
            params["type"] = media_type.lower()
        if dubbed:
            params["language"] = "ita"
        raw = _page("/archive", **params).get("animes")
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
            raise RuntimeError("AnimeUnity: formato archivio non riconosciuto")
        return _normalize_titles(raw["data"], language="ita" if dubbed else None)

    body = {"title": query, "offset": (max(page, 1) - 1) * ARCHIVE_PAGE}
    if dubbed:
        body["dubbed"] = 1
    if media_type:
        body["type"] = TYPES.get(media_type.lower(), media_type)

    r = _post_archive(body)
    if not r.ok:
        logger.error("Archive search failed with status %d", r.status_code)
        raise RuntimeError(f"AnimeUnity search failed: HTTP {r.status_code}")

    try:
        payload = r.json()
    except Exception as e:
        logger.error("Error parsing archive response: %s", e)
        raise
    records = payload if isinstance(payload, list) else payload.get("records", [])

    # No rows is an answer, not a failure — the same one StreamingCommunity
    # gives. This used to raise, which reached the panel as a red error box
    # saying the search had broken when the query simply matched nothing.
    return _normalize_titles(records)


def get_episodes(anime_id: str, *, language: str | None = None) -> list[dict]:
    """
    Fetch every batch from the public episode API (or legacy info_api).
    Episode ids in the returned records identify playable language variants.
    """
    if _modern_site():
        return _modern_episodes(anime_id, language=language)
    scraper = _get_scraper()
    au_host = host()

    r = scraper.get(f"https://{au_host}/info_api/{anime_id}", timeout=15)
    r.raise_for_status()
    episodes_count = r.json().get("episodes_count", 0)

    if not episodes_count:
        return []

    episodes = []
    for start in range(1, episodes_count + 1, BATCH_SIZE):
        end = min(start + BATCH_SIZE - 1, episodes_count)
        batch_r = scraper.get(
            f"https://{au_host}/info_api/{anime_id}/0",
            params={"start_range": start, "end_range": end},
            timeout=15,
        )
        if batch_r.ok:
            payload = batch_r.json()
            rows = payload.get("episodes", []) if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                number = row.get("number") or row.get("episode") or row.get("n")
                if number is None:
                    continue
                item = dict(row)
                item["number"] = number
                item["name"] = (row.get("name") or row.get("title") or
                                 row.get("episode_name") or f"Episodio {number}")
                images = row.get("images") if isinstance(row.get("images"), dict) else {}
                item["image"] = (row.get("image") or row.get("imageurl") or
                                  row.get("thumbnail") or row.get("still") or
                                  row.get("image_url") or images.get("cover") or
                                  images.get("still") or images.get("thumbnail"))
                episodes.append(item)

    return episodes


def _modern_episodes(anime_id: str, *, language: str | None = None) -> list[dict]:
    if not re.fullmatch(r"\d+(?:-[a-zA-Z0-9-]+)?", anime_id):
        raise ValueError("AnimeUnity: identificativo non valido")
    props = _page(f"/anime/{anime_id}")
    loaded = props.get("loadedEpisodes")
    if not isinstance(loaded, dict) or not isinstance(loaded.get("episodes"), list):
        raise RuntimeError("AnimeUnity: formato episodi non riconosciuto")
    anime = props.get("anime") or {}
    # Choose one language for the entire list; do not mix dubbed and subtitled
    # episodes when an Italian variant is incomplete.
    language = language or (props.get("video") or {}).get("language")
    rows = list(loaded["episodes"])
    size, total = int(loaded.get("tab_size") or BATCH_SIZE), int(loaded.get("total") or 0)
    offset, first = int(loaded.get("offset") or 1), int(loaded.get("start") or 1)
    if not 1 <= size <= 1000 or not 0 <= total <= 10000:
        raise RuntimeError("AnimeUnity: paginazione episodi non valida")
    for start in range(offset, offset + total, size):
        if start == first:
            continue
        from app.core.hosts import safe_get
        response = safe_get(_get_scraper().get, f"https://{host()}/api/anime/{anime['id']}/episodes",
                            params={"start": start}, timeout=20)
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, dict) or not isinstance(batch.get("episodes"), list):
            raise RuntimeError("AnimeUnity: risposta episodi non valida")
        rows.extend(batch["episodes"])
    result, seen = [], set()
    for row in rows:
        videos = [v for v in row.get("videos", []) if not v.get("is_hidden")]
        video = next((v for v in videos if v.get("language") == language), None) if language else next(iter(videos), None)
        if not video or video.get("id") in seen:
            continue
        seen.add(video["id"])
        result.append({"id": video["id"], "number": row["number"],
                       "name": row.get("title") or f"Episodio {row['number']}",
                       "language": video.get("language"), "image": row.get("image")})
    return sorted(result, key=lambda e: float(e["number"]) if str(e["number"]).replace(".", "", 1).isdigit() else float("inf"))


def _get_embed_content(episode_id, *, anime_id: str | None = None) -> tuple[str, str]:
    """
    Fetch the vixcloud embed page for an episode.
    Returns (script_text, embed_url) — same shape as film._get_iframe().
    """
    scraper = _get_scraper()
    au_host = host()

    # AnimeUnity returns the vixcloud embed URL as plain text
    if anime_id is not None and _modern_site():
        if not re.fullmatch(r"\d+(?:-[a-zA-Z0-9-]+)?", anime_id) or not str(episode_id).isdigit():
            raise ValueError("AnimeUnity: identificativo non valido")
        embed_url = _page(f"/anime/{anime_id}/{episode_id}").get("embedUrl") or ""
        if not embed_url:
            raise RuntimeError("AnimeUnity: video non disponibile")
        au_host = host()
    else:
        r = scraper.get(f"https://{au_host}/embed-url/{episode_id}", timeout=15)
        r.raise_for_status()
        embed_url = r.text.strip()

    # The body is not quoted: it is a third party's response, and when it is
    # not a URL it is usually a whole error page.
    if not isinstance(embed_url, str) or not embed_url.startswith("https://"):
        raise RuntimeError(
            "Unexpected embed-url response" +
            (f" (HTTP {r.status_code}, {len(r.text)} characters)" if anime_id is None else ""))

    from app.core._shared import apply_video_host, with_retry
    from app.core.hosts import redact, safe_get
    embed_url = apply_video_host(embed_url)
    logger.info("Episode %s embed URL: %s", episode_id, redact(embed_url)[:80])

    # Fetch the vixcloud embed page. vixcloud now serves this /embed/ HTML
    # page behind Cloudflare, so plain requests.get returns a 403 challenge page.
    # Use the cloudscraper session to clear the challenge. (The downstream
    # /playlist, /storage/enc.key and CDN segment URLs are NOT Cloudflare-gated
    # and still work with plain requests.)
    #
    # The URL is whatever the source answered with, so it — and any redirect
    # after it — must be https to a public host before the panel goes there.
    req_embed = with_retry(
        lambda: safe_get(scraper.get, embed_url,
                         headers={"Referer": f"https://{au_host}/"}, timeout=15),
        what="embed",
    )
    req_embed.raise_for_status()

    soup = BeautifulSoup(req_embed.text, "lxml")
    body = soup.find("body")
    if not body:
        raise RuntimeError("Empty embed page body")
    script = body.find("script")
    if not script:
        raise RuntimeError("No script tag found in vixcloud embed page")

    # Its length and nothing else: the script carries the playback token.
    logger.info("Embed script received (%d characters)", len(script.text))
    return script.text, embed_url


def get_episode_languages(episode_id, *, anime_id: str | None = None) -> dict:
    """Audio and subtitle languages available on one anime episode."""
    from app.core._shared import _parse_content, _get_m3u8_url
    from app.core.m3u8 import fetch_master_languages

    embed_content, embed_url = _get_embed_content(episode_id, anime_id=anime_id)
    json_win_video, json_win_param = _parse_content(embed_content, embed_url)
    return fetch_master_languages(_get_m3u8_url(json_win_video, json_win_param), embed_url)


def download_anime_episode(
    anime_id: str,
    episode: dict,
    anime_name: str,
    anime_type: str = "tv",
    output_dir: str = "videos",
    temp_dir: str = None,
    progress_factory=None,
    cancel_event=None,
    year: str = None,
    audio_languages: list[str] = None,
    subtitle_languages: list[str] = None,
    strict_audio: bool = False,
    *, source: str = "animeunity",
) -> str:
    """
    Full download pipeline for a single anime episode.
    - Movies (anime_type="movie" or "film", any case): videos/AnimeName (YYYY)/AnimeName.mkv
    - Anything else (TV, ONA, OVA, Special, …): videos/AnimeName/Season 01/AnimeName S01E01.mkv

    The extension is whatever app.core.container is configured to write.
    """
    from app.source_identity import download_source

    source = download_source("anime", source)
    audio_languages = audio_languages or ["ita"]
    subtitle_languages = subtitle_languages or []

    from app.core.film import _collect_audio_tracks, _collect_subtitle_tracks
    from app.core._shared import _parse_content, _get_m3u8_url, _get_m3u8_key
    from app.core.m3u8 import download_m3u8
    from app.core.paths import anime_path

    episode_id = episode["id"]
    episode_number = str(episode.get("number", "0"))

    embed_content, embed_url = _get_embed_content(episode_id, anime_id=anime_id)
    json_win_video, json_win_param = _parse_content(embed_content, embed_url)

    logger.info(
        "Anime episode %s — video_id=%s token=<redacted>",
        episode_number, json_win_video.get("id"),
    )

    m3u8_url = _get_m3u8_url(json_win_video, json_win_param)
    m3u8_key = _get_m3u8_key(json_win_video, json_win_param, embed_url)

    missing_tracks: list[str] = []
    audio_track_urls = _collect_audio_tracks(
        m3u8_url, embed_url, audio_languages, strict=strict_audio,
        missing=missing_tracks,
    )
    subtitle_track_urls = _collect_subtitle_tracks(m3u8_url, embed_url, subtitle_languages)

    # A folder a person associated with this anime wins — which is how the
    # parts of one series, separate titles at the source, land in one folder
    # at the season and episode their offsets say (app/library.py).
    from app.core.paths import is_anime_series
    from app.library import destination

    dest_path = destination("anime", anime_id, root=output_dir, episode=episode_number,
                            year=year, single_file=not is_anime_series(anime_type), source=source)
    if not dest_path and is_anime_series(anime_type):
        # The series' folder already on disk, whatever year it carries (#24).
        from app.requests.resolver import ANIME, series_episode_path

        dest_path = series_episode_path(ANIME, anime_id, output_dir, anime_name, 1,
                                        episode_number, year, anime_type=anime_type, source=source)
    dest_path = dest_path or anime_path(output_dir, anime_name, episode_number, anime_type, year)

    final_path = download_m3u8(
        m3u8_index=m3u8_url,
        key=m3u8_key,
        output_filename=dest_path,
        temp_dir=temp_dir,
        progress_factory=progress_factory,
        referer=embed_url,
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
