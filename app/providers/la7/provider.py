"""Official LA7 catalogue and direct episode adapter."""
from __future__ import annotations

from urllib.parse import urlencode, urljoin, urlsplit, parse_qs

from app.core import la7
from ..contracts import HomeResult, ProviderCapabilities, ProviderError, ProviderErrorCode, search_results
from .client import LA7Client
from . import parser


class LA7Provider:
    source = "la7"
    capabilities = ProviderCapabilities(search=True, media_types=("tv", "movie"),
                                        details=True, episodes=True, home=True, download=True)

    def __init__(self, *, http_get=None, client=None):
        self._http_get = http_get
        self.client = client if client is not None else LA7Client(http_get=http_get)

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
        return self._call(lambda: search_results(self.source, parser.search_rows(
            self.client.search(query.strip(), page, media_type))))

    def home(self):
        return self._call(lambda: HomeResult.from_legacy(self.source, parser.home_shelves(self.client.home())))

    def details(self, external_id):
        path = self._path(external_id)
        def load():
            page = self.client.text(path)
            entries = ()
            if path.count("/") == 1:
                # Only programmes declaring a replica submenu have episodes.
                has_replicas = any(isinstance(obj.get("tipoDiContenuto"), dict)
                                   and obj["tipoDiContenuto"].get("name") == "Replica"
                                   for obj in parser.objects(page))
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
