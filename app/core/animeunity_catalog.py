"""Public AnimeUnity browsing, separate from episode/playback resolution.

Wire formats checked against the site's Inertia pages and public JSON APIs on
2026-11-18. Never follow paginator URLs supplied by the source.
"""
import copy
import math
import threading
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime

from app.core import animeunity as au
from app.core.hosts import safe_get

_cache = OrderedDict()
_lock = threading.RLock()
_TTL = 120
_MAX_CACHE = 128
# A filtered search walks the whole recent feed, one upstream request per page.
# Capped so one uncached query cannot turn into a long burst at the source.
_MAX_FEED_PAGES = 30
SECTIONS = {"latest", "archive", "latest_animes", "upcoming", "top_day", "top_week", "top_month"}
_WEEKDAYS = dict(zip(("lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"),
                     ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")))


def clear_cache():
    with _lock:
        _cache.clear()
        _key_locks.clear()


_key_locks: dict = {}


def _key_lock(key):
    with _lock:
        return _key_locks.setdefault(key, threading.Lock())


def _hit(key):
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            _cache.move_to_end(key)
            return True, copy.deepcopy(hit[1])
    return False, None


def _cached(key, fetch):
    key = (au.host(), *key)
    found, value = _hit(key)
    if found:
        return value
    # One lock per key: identical concurrent requests share one fetch, while
    # other keys (calendar, archive, search) are never blocked by a slow fetch.
    with _key_lock(key):
        found, value = _hit(key)
        if found:
            return value
        value = fetch()  # Errors are not cached as empty pages.
        with _lock:
            _cache[key] = (time.monotonic() + _TTL, copy.deepcopy(value))
            _cache.move_to_end(key)
            while len(_cache) > _MAX_CACHE:
                _cache.popitem(last=False)
        return value


def _json(path, **params):
    def fetch():
        response = safe_get(au._get_scraper().get, f"https://{au.host()}{path}",
                            params=params or None, headers={"Accept": "application/json"}, timeout=20)
        response.raise_for_status()
        return response.json()
    return _cached((path, tuple(sorted(params.items()))), fetch)


def _paginator(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
        raise RuntimeError("AnimeUnity: invalid catalogue response")
    try:
        total, size, last = int(raw["total"]), int(raw["per_page"]), int(raw["last_page"])
        current = int(raw["current_page"])
        if min(total, last) < 0 or not 1 <= size <= 500 or current < 1 or (total > 0 and last < 1):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise RuntimeError("AnimeUnity: invalid pagination") from None
    return total, size, last


def _result(items, page, size, total, **extra):
    last = max(1, math.ceil(total / size)) if total is not None else None
    return {"items": items, "page": page, "per_page": size, "total": total,
            "last_page": last, "has_previous": page > 1,
            "has_next": page < last if last is not None else False, **extra}


def _title(raw, language=None):
    item = au.normalize_title(raw, language=language)
    if item:
        item["source"] = "animeunity"
    return item


def _latest_item(raw):
    if not isinstance(raw, dict) or raw.get("is_hidden"):
        return None
    episode = raw.get("episode") or {}
    item = _title(episode.get("anime"), raw.get("language"))
    if item is not None:
        item.update(video_id=raw.get("id"), episode_number=episode.get("number"),
                    language=raw.get("language"), entry_key=str(raw.get("id")))
    return item


def _window(fetch, page, size, normalize):
    # Discover the actual upstream page size before calculating row offsets.
    first = fetch(1)
    total, upstream_size, last = _paginator(first)
    start = (page - 1) * size
    if start >= total:
        return _result([], page, size, total)
    items = []
    for number in range(start // upstream_size + 1, min(last, (start + size - 1) // upstream_size + 1) + 1):
        raw = first if number == 1 else fetch(number)
        _, actual_size, _ = _paginator(raw)
        if actual_size != upstream_size or int(raw["current_page"]) != number:
            raise RuntimeError("AnimeUnity: inconsistent pagination")
        offset = (number - 1) * upstream_size
        rows = raw["data"][max(0, start - offset):min(upstream_size, start + size - offset)]
        items.extend(item for row in rows if (item := normalize(row)) is not None)
    return _result(items, page, size, total)


def browse(*, section="archive", page=1, per_page=60, query="", media_type=None,
           dubbed=False, sort="title_az"):
    if section not in SECTIONS or not 1 <= page <= 10000 or per_page not in (20, 30, 60):
        raise ValueError("Invalid catalogue page")
    if media_type and media_type not in au.TYPES:
        raise ValueError("Invalid anime type")
    if sort not in {"title_az", "title_za", "latest", "score", "views"}:
        raise ValueError("Invalid catalogue order")
    if not au._modern_site():
        return _legacy_browse(section, page, per_page, query, media_type, dubbed)
    if section == "latest":
        params = {"per_page": 20}
        if dubbed:
            params["language"] = "dub"
        fetch = lambda n: _json("/api/home/latest-episodes", page=n, **params)
        if not media_type and not query:
            return _window(fetch, page, per_page, _latest_item)
        # This API ignores type/query. Filter the entire recent feed, not just
        # the visible page, so an empty page cannot hide later matching videos.
        first = fetch(1)
        _, _, last = _paginator(first)
        if last > _MAX_FEED_PAGES:
            raise RuntimeError("AnimeUnity: recent feed exceeds supported size")
        items, seen = [], set()
        for n in range(1, last + 1):
            raw = first if n == 1 else fetch(n)
            _paginator(raw)
            if int(raw["current_page"]) != n:
                raise RuntimeError("AnimeUnity: inconsistent pagination")
            for row in raw["data"]:
                item = _latest_item(row)
                if not item or item["entry_key"] in seen:
                    continue
                seen.add(item["entry_key"])
                if media_type and str(item["media_type"]).lower() != media_type:
                    continue
                if query and query.casefold() not in item["name"].casefold():
                    continue
                items.append(item)
        start = (page - 1) * per_page
        return _result(items[start:start + per_page], page, per_page, len(items))
    params = {"query": query, "sort_by": sort}
    if media_type:
        params["type"] = media_type
    if dubbed:
        params["language"] = "ita"
    path = "/api/archive"
    if section == "latest_animes":
        params["sort_by"] = "latest"
    elif section == "upcoming":
        params["status"] = "upcoming"
    elif section.startswith("top_"):
        path = "/api/top-anime"
        params.update(period=section[4:], sort_by="views")
    return _window(lambda n: _json(path, page=n, **params), page, per_page,
                   lambda row: _title(row, "ita" if dubbed else None))


def _legacy_browse(section, page, size, query, media_type, dubbed):
    if section not in {"latest", "archive"}:
        return _result([], page, size, 0, legacy=True, supported=False)
    if section == "latest":
        from app.core import home
        shelves = home.shelves("animeunity", au.host())
        items = next((s["items"] for s in shelves if s["key"] == "latest_episodes"), [])
        items = [dict(i, source="animeunity") for i in items
                 if (not media_type or str(i.get("media_type", "")).lower() == media_type)
                 and (not dubbed or i.get("dubbed")) and query.casefold() in i["name"].casefold()]
        start = (page - 1) * size
        return _result(items[start:start + size], page, size, len(items), legacy=True)
    # Legacy archive has offsets but no trustworthy total/last-page metadata.
    start, items, more = (page - 1) * size, [], True
    for n in range(start // au.ARCHIVE_PAGE + 1, (start + size - 1) // au.ARCHIVE_PAGE + 2):
        rows = _cached(("legacy", n, query, media_type, dubbed),
                       lambda: au.search(query, page=n, media_type=media_type, dubbed=dubbed))
        offset = (n - 1) * au.ARCHIVE_PAGE
        items.extend(dict(row, source="animeunity") for row in
                     rows[max(0, start - offset):min(au.ARCHIVE_PAGE, start + size - offset)])
        if len(rows) < au.ARCHIVE_PAGE:
            more = start + size < offset + len(rows)
            break
    return _result(items, page, size, None, legacy=True) | {"has_next": more}


def calendar(*, dubbed=False):
    if not au._modern_site():
        return {"days": [], "supported": False}
    entries = _json("/api/calendar")
    if not isinstance(entries, dict) or any(not isinstance(rows, list) for rows in entries.values()):
        raise RuntimeError("AnimeUnity: invalid calendar response")
    days = []
    for label, rows in entries.items():
        items = []
        for row in rows:
            if not isinstance(row, dict) or (dubbed and not row.get("isDub")):
                continue
            item = _title(row.get("anime"), "ita" if row.get("isDub") else None)
            if item is None:
                continue
            item.update(release_time=row.get("releaseTime"), next_episode=row.get("nextEpisodeNumber"),
                        aired=bool(row.get("isAired")), late=bool(row.get("isLate")),
                        paused_until=row.get("pausedUntil"), dubbed=bool(row.get("isDub")),
                        airing_day=row.get("airingDay"), aired_episodes=row.get("airedEpisodes") or [])
            items.append(item)
        # The source labels days in Italian. Return stable weekday/date fields
        # so the portal can localize them without changing the release timezone.
        name = unicodedata.normalize("NFKD", label.split(" - ")[0]).encode("ascii", "ignore").decode().lower()
        day = _WEEKDAYS.get(name)
        release = next((r.get("releaseTime") for r in rows if isinstance(r, dict) and r.get("releaseTime")), None)
        day_date = None
        if release:
            try:
                day_date = datetime.fromisoformat(release).date().isoformat()
            except (ValueError, TypeError):
                pass
        days.append({"key": str(label), "weekday": day, "date": day_date,
                     "display_date": label.split(" - ", 1)[1] if " - " in label and "Oggi" not in label else None,
                     "today": "Oggi" in label, "undetermined": label.startswith("Indeterminato"),
                     "items": items})
    return {"days": days, "supported": True, "timezone": "Europe/Rome"}
