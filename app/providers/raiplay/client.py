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
_TITLE_PATH = re.compile(r"/(programmi|collezioni)/[a-z0-9][a-z0-9-]{0,150}\.json")


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
        if not isinstance(path, str) or not _TITLE_PATH.fullmatch(path):
            raise ValueError("Refusing a non-catalogue RaiPlay path")
        return self._get_json(path)

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

    def _request(self, method: str, path: str, body: dict | None) -> dict:
        key = (method, path, json.dumps(body, sort_keys=True) if body is not None else None)
        with self._lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < self._ttl:
                self._cache.move_to_end(key)
                return hit[1]
            self._throttle()
            payload = self._fetch(method, BASE_URL + path, body)
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

    def _fetch(self, method: str, url: str, body: dict | None) -> dict:
        transport = self._transport or _requests_transport
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
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
            data = json.loads(content.decode("utf-8-sig") if isinstance(content, bytes) else content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RaiPlayUnavailable("RaiPlay response is not JSON") from exc
        if not isinstance(data, dict):
            raise RaiPlayUnavailable("RaiPlay response has an unexpected shape")
        return data


def _requests_transport(method, url, *, json, headers, timeout):
    import requests  # loaded on first real use only

    return requests.request(method, url, json=json, headers=headers, timeout=timeout,
                            allow_redirects=False)
