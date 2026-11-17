"""Official LA7 catalogue and direct episode adapter."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from time import monotonic
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs

from app.core import la7
from ..contracts import HomeResult, ProviderCapabilities, ProviderError, ProviderErrorCode, search_results
from .client import LA7Client
from . import parser

logger = logging.getLogger(__name__)


class LA7Provider:
    source = "la7"
    capabilities = ProviderCapabilities(search=True, media_types=("tv", "movie"),
                                        details=True, episodes=True, home=True, download=True)

    def __init__(self, *, http_get=None, client=None):
        self._http_get = http_get
        self.client = client if client is not None else LA7Client(http_get=http_get)
        self._catalogue_cache = {}
        self._catalogue_lock = RLock()

    def _call(self, callback):
        try:
            return callback()
        except la7.LA7Error as exc:
            raise ProviderError(self.source, ProviderErrorCode.UPSTREAM, str(exc)) from exc

    def _path(self, external_id):
        try:
            return parser.path_for_id(external_id)
        except la7.LA7Error as exc:
            raise ProviderError(self.source, ProviderErrorCode.INVALID_REQUEST, str(exc)) from exc

    def resolve_url(self, url: str) -> dict:
        """Return checked metadata or raise LA7Error, without downloading media.

        Resolve again at job start: CDN URLs may expire.
        """
        from app.core import la7
        return {"source": self.source, **la7.resolve_episode(url, http_get=self._http_get)}

    def search(self, query, *, page=1, media_type=None, dubbed=False):
        if (not isinstance(query, str) or not 3 <= len(query.strip()) <= 200
                or type(page) is not int or not 1 <= page <= 50 or type(dubbed) is not bool):
            raise ProviderError(self.source, ProviderErrorCode.INVALID_REQUEST, "Ricerca LA7 non valida")
        if dubbed or media_type not in (None, "tv", "movie"):
            raise ProviderError(self.source, ProviderErrorCode.UNSUPPORTED, "Filtro LA7 non supportato")
        return self._call(lambda: search_results(self.source, self._catalogue_rows(parser.search_rows(
            self.client.search(query.strip(), page, media_type)))))

    def home(self):
        def load():
            shelves = parser.home_shelves(self.client.home())
            checked = self._check_catalogue([item for shelf in shelves for item in shelf["items"]])
            for shelf in shelves:
                shelf["items"] = self._catalogue_rows(shelf["items"], checked)
            return HomeResult.from_legacy(self.source, shelves)
        return self._call(load)

    def _check_catalogue(self, rows):
        identities = dict.fromkeys(item["id"] for item in rows
                                   if item["download_scope"] == "single" and item["drm"] is None)
        def check(identity):
            with self._catalogue_lock:
                cached = self._catalogue_cache.get(identity)
                if cached and monotonic() < cached[0]:
                    return cached[1]
            try:
                card = self.details(identity).to_legacy()
            except ProviderError:
                logger.warning("Skipping unverified LA7 video %s", identity)
                card = None
            # Cache small availability records separately from large HTML
            # pages, so a home with >64 videos does not churn the page cache.
            with self._catalogue_lock:
                if len(self._catalogue_cache) >= 256:
                    self._catalogue_cache.pop(next(iter(self._catalogue_cache)))
                self._catalogue_cache[identity] = (monotonic() + getattr(self.client, "cache_seconds", 120), card)
            return card
        if not identities:
            return {}
        # A cold home contains many lean cards. Bound metadata concurrency so
        # one slow page does not hold every other check in a serial queue.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="la7-catalogue") as pool:
            return dict(zip(identities, pool.map(check, identities)))

    def _catalogue_rows(self, rows, checked=None):
        """The public search/carousel queries omit DRM: read each video page.

        The client caches these bounded reads; repeated shelf entries are only
        checked once per response. Programmes keep their mixed episode lists.
        Unknown/unreachable standalone videos are not advertised as downloads.
        """
        checked = self._check_catalogue(rows) if checked is None else checked
        result = []
        for item in rows:
            if item["download_scope"] != "single":
                result.append(item)
                continue
            if item["drm"] is None:
                identity = item["id"]
                detail = checked[identity]
                if detail is None or detail.get("download_scope") != "single":
                    continue
                # Details determine availability; preserve card artwork when
                # the complete record has no cover of its own.
                item = {**item, **{key: value for key, value in detail.items() if value is not None}}
            if item["drm"] is False and item["downloadable"] is True:
                result.append(item)
        return result

    def details(self, external_id):
        path = self._path(external_id)
        def load():
            page = self.client.text(path)
            entries = ()
            if path.count("/") == 1:
                # Only programmes declaring a replica submenu have episodes.
                programme = [obj for obj in parser.objects(page)
                             if parser.node_kind(obj) in parser.PROGRAM_KINDS and la7.node_path(obj) == path]
                has_replicas = any(isinstance(obj.get("tipoDiContenuto"), dict)
                                   and obj["tipoDiContenuto"].get("name") == "Replica"
                                   for node in programme for obj in (node.get("vociSottomenu") or [])
                                   if isinstance(obj, dict))
                if has_replicas:
                    listing = self.client.text(path + "/puntate")
                    entries = parser.seasons(listing, path)
            return parser.title_details(external_id, page, entries)
        return self._call(load)

    def episodes(self, external_id, season_path=None, season_number=None):
        path = self._path(external_id)
        if (path.count("/") != 1 or (season_number is not None and
                (type(season_number) is not int or not 1 <= season_number <= 9999))
                or season_path not in (None, path + "/puntate")):
            raise ProviderError(self.source, ProviderErrorCode.INVALID_REQUEST, "Stagione LA7 non valida")
        return self._call(lambda: self._episodes(path, season_number))

    def _episodes(self, path, season):
        listing = path + "/puntate"
        query = {"stagione": season} if season else {}
        current = listing + ("?" + urlencode(query) if query else "")
        seen = set()
        rows = {}
        for _ in range(50):
            if current in seen:
                raise la7.LA7Error("Paginazione LA7 ciclica")
            seen.add(current)
            page = self.client.text(current)
            for item in parser.episode_rows(page, path, season):
                rows.setdefault(item["id"], item)
            following = parser.next_page(page)
            if not following:
                return list(rows.values())
            target = la7._validate_url(urljoin(la7.BASE_URL + current, following))
            parsed = urlsplit(target)
            params = parse_qs(parsed.query, keep_blank_values=True)
            if (parsed.path != listing or set(params) - {"page", "stagione", "sort"}
                    or params.get("stagione", []) != ([str(season)] if season else [])
                    or len(params.get("page", [])) != 1
                    or not params["page"][0].isdigit()):
                raise la7.LA7Error("Paginazione LA7 non valida")
            current = parsed.path + "?" + parsed.query
        raise la7.LA7Error("Troppe pagine LA7: elenco incompleto")

    def direct_download_metadata(self, url: str) -> dict:
        """Alias for consumers explicitly requesting download metadata."""
        return self.resolve_url(url)
