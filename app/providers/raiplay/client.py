"""Read-only HTTP client for RaiPlay's public *metadata* JSON.

Scope, on purpose narrow:
- only ``https://www.raiplay.it``; the host is a constant, never taken from a
  request or from a response;
- only page-description JSON (the ``.json`` twin of public pages) and the
  catalogue search the site's own search box calls without a login;
- no player, relinker, manifest, licence or DRM endpoint is ever contacted.

Every call has a timeout, a fixed User-Agent, a size cap, a small TTL cache and
a minimum spacing between upstream requests.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from html.parser import HTMLParser
from typing import Any, Callable

BASE_URL = "https://www.raiplay.it"
USER_AGENT = "StreamingCommunity-downloader/metadata (catalogue browsing; no media access)"
TIMEOUT_S = 10
MAX_BYTES = 4 * 1024 * 1024
MIN_INTERVAL_S = 1.0
CACHE_TTL_S = 600
CACHE_MAX_ENTRIES = 256

HOME_PATH = "/index.json"
SEARCH_PATH = "/atomatic/raiplay-search-service/api/v1/msearch"
# Public template identifiers the site's search component sends with every
# anonymous query (they select the result layout, not a user or a privilege).
SEARCH_TEMPLATE_IN = "6470a982e4e0301afe1f81f1"
SEARCH_TEMPLATE_OUT = "6516ac5d40da6c377b151642"

# Only these sections can be fetched for a title card.
_TITLE_PATH = re.compile(r"/(programmi|collezioni)/[A-Za-z0-9][A-Za-z0-9/-]{0,220}\.json")
_EPISODES_PATH = re.compile(r"/(programmi|collezioni)/[A-Za-z0-9][A-Za-z0-9/-]{0,300}/episodes\.json")
_EDITORIAL_PATH = re.compile(r"/(programmi|collezioni)/[a-z0-9][a-z0-9-]{0,150}(?:/[a-z0-9][a-z0-9-]{0,150}){2}")


class _EpisodeComponent(HTMLParser):
    def __init__(self):
        super().__init__()
        self.feeds = []

    def handle_starttag(self, tag, attrs):
        if tag != "rai-episodes":
            return
        attrs = dict(attrs)
        keys = ("base_path", "block", "set", "episode_path")
        if all(attrs.get(key) for key in keys):
            path = "/".join(attrs[key].strip("/") for key in keys)
            if _EPISODES_PATH.fullmatch("/" + path):
                self.feeds.append("/" + path)


class RaiPlayUnavailable(Exception):
    """Upstream unreachable, slow, refused, oversized or not JSON."""


class RaiPlayClient:
    def __init__(self, *, transport: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 min_interval: float = MIN_INTERVAL_S, ttl: float = CACHE_TTL_S):
        # ``transport(method, url, *, json, headers, timeout)`` returns an object
        # with ``status_code``, ``headers`` and ``content``; defaults to requests.
        self._transport = transport
        self._clock, self._sleep = clock, sleep
        self._min_interval, self._ttl = min_interval, ttl
        self._cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._last_request = None

    # ── public operations ────────────────────────────────────────────────────
    def home(self) -> dict:
        return self._get_json(HOME_PATH)

    def title(self, path: str) -> dict:
        if not isinstance(path, str) or not (_TITLE_PATH.fullmatch(path) or _EPISODES_PATH.fullmatch(path)):
            raise ValueError("Refusing a non-catalogue RaiPlay path")
        return self._get_json(path)

    def editorial_episodes(self, page_path: str) -> dict:
        """Resolve RaiPlay's web-component episode feed from an editorial page."""
        if not isinstance(page_path, str) or not _EDITORIAL_PATH.fullmatch(page_path):
            raise ValueError("Refusing a non-editorial RaiPlay path")
        # The UI historically invented /episodi/stagione-N URLs. Some programmes
        # use /stagione-N/stagione-N instead (and answer 559 on the invented URL).
        # Resolve the real block/set pair from the catalogue before fetching HTML.
        from .parser import season_entries

        root = "/" + "/".join(page_path.strip("/").split("/")[:2])
        match = re.fullmatch(r"stagione-(\d+)", page_path.rsplit("/", 1)[-1])
        if match:
            entries = season_entries(self.title(root + ".json").get("blocks"))
            for entry in entries:
                if entry["number"] == int(match.group(1)):
                    block_id, set_id = entry["block_id"], entry["set_id"]
                    feed = f"{root}/{block_id}/{set_id}/episodes.json"
                    if _EPISODES_PATH.fullmatch(feed) and block_id and set_id:
                        return self._get_json(feed)
                    return self.title(entry["path"])

        component = _EpisodeComponent()
        component.feed(self._request("GET", page_path, None, html=True))
        if not component.feeds:
            raise RaiPlayUnavailable(f"RaiPlay episode feed not found: {page_path}")
        return self._get_json(component.feeds[0])

    def season_episodes(self, title_path: str, number: int, season_path: str | None = None) -> list[dict]:
        """One resolver for previews and batches, using the catalogue's IDs.

        Editorial feeds can be empty even while their ContentSet is populated.
        The latter is also the public metadata used by yt-dlp's playlist
        extractor. Never fall back to the whole programme (which includes Extra).
        """
        from . import parser

        entries = parser.season_entries(self.title(title_path).get("blocks"))
        entry = next((row for row in entries if row["number"] == number), None)
        if entry is None:
            raise ValueError("Stagione non presente nel catalogo RaiPlay")
        root = title_path.removesuffix(".json")
        candidates = [entry, *entry.get("alternatives", [])]
        successful = False
        errors = []
        collected = {}
        for candidate in candidates:
            paths = []
            block_id, set_id = candidate.get("block_id"), candidate.get("set_id")
            if block_id and set_id:
                paths.append(f"{root}/{block_id}/{set_id}/episodes.json")
            paths.append(candidate.get("path"))
            for path in dict.fromkeys(paths):
                if not isinstance(path, str) or not path.startswith(root + "/"):
                    continue
                try:
                    payload = self.title(path)
                    successful = True
                    rows = parser.episode_rows(payload, number)
                    if rows:
                        collected.update((row["page_url"], row) for row in rows)
                        count = candidate.get("episodes")
                        if not count or len(collected) >= count:
                            return sorted(collected.values(), key=lambda row: int(row["n"]))
                except (RaiPlayUnavailable, ValueError) as exc:
                    errors.append(exc)
        # An actual editorial page can still expose a different, newer feed.
        # Only a caller-supplied validated page is tried; no guessed URL.
        if isinstance(season_path, str) and _EDITORIAL_PATH.fullmatch(season_path):
            try:
                component = _EpisodeComponent()
                component.feed(self._request("GET", season_path, None, html=True))
                for path in component.feeds:
                    if not path.startswith(root + "/"):
                        continue
                    payload = self.title(path)
                    successful = True
                    rows = parser.episode_rows(payload, number)
                    if rows:
                        collected.update((row["page_url"], row) for row in rows)
            except RaiPlayUnavailable as exc:
                errors.append(exc)
        if not successful and errors:
            raise RaiPlayUnavailable(f"Impossibile leggere la stagione {number}: {errors[-1]}") from errors[-1]
        return sorted(collected.values(), key=lambda row: int(row["n"]))

    def search(self, query: str, *, offset: int, size: int) -> dict:
        body = {
            "templateIn": SEARCH_TEMPLATE_IN, "templateOut": SEARCH_TEMPLATE_OUT,
            "params": {"param": query, "from": offset, "sort": "relevance", "size": size,
                       "additionalSize": size, "onlyVideoQuery": False, "onlyProgramsQuery": True},
        }
        return self._request("POST", SEARCH_PATH, body)

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _get_json(self, path: str) -> dict:
        return self._request("GET", path, None)

    def _request(self, method: str, path: str, body: dict | None, *, html=False) -> Any:
        key = (method, path, json.dumps(body, sort_keys=True) if body is not None else None, html)
        with self._lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < self._ttl:
                self._cache.move_to_end(key)
                return hit[1]
            self._throttle()
            payload = self._fetch(method, BASE_URL + path, body, html=html)
            self._cache[key] = (self._clock(), payload)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)
            return payload

    def _throttle(self):
        if self._last_request is not None:
            wait = self._min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _fetch(self, method: str, url: str, body: dict | None, *, html=False) -> Any:
        transport = self._transport or _requests_transport
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html" if html else "application/json"}
        try:
            response = transport(method, url, json=body, headers=headers, timeout=TIMEOUT_S)
        except Exception as exc:  # connection, DNS, timeout, TLS ...
            raise RaiPlayUnavailable(f"RaiPlay request failed: {type(exc).__name__}") from exc
        status = getattr(response, "status_code", None)
        if status != 200:
            raise RaiPlayUnavailable(f"RaiPlay answered HTTP {status}")
        content = getattr(response, "content", b"") or b""
        if len(content) > MAX_BYTES:
            raise RaiPlayUnavailable("RaiPlay response too large")
        try:
            text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
            if html:
                return text
            data = json.loads(text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RaiPlayUnavailable("RaiPlay response is not JSON") from exc
        if not isinstance(data, dict):
            raise RaiPlayUnavailable("RaiPlay response has an unexpected shape")
        return data


def _requests_transport(method, url, *, json, headers, timeout):
    import requests  # loaded on first real use only

    return requests.request(method, url, json=json, headers=headers, timeout=timeout,
                            allow_redirects=False)
