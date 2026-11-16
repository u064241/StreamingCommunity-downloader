"""Read-only official LA7 catalogue endpoints used by the public website."""
import base64
from copy import deepcopy
import json
from time import monotonic
from threading import RLock

from app.core import la7


class LA7Client:
    # Public persisted queries observed in the website's shipped JS bundle.
    QUERIES = {"home": "1546454f59a64bb6", "search": "4da1e384f8a63808"}

    def __init__(self, *, http_get=None, cache_seconds=120):
        self.http_get = http_get
        self.cache_seconds = cache_seconds
        self._cache = {}
        self._lock = RLock()

    def text(self, path):
        url = la7._validate_url(la7.BASE_URL + path)
        with self._lock:
            entry = self._cache.get(url)
            if entry and monotonic() < entry[0]:
                return entry[1]
        value = la7._fetch(url, la7.PAGE_HOSTS, self.http_get)
        with self._lock:
            if len(self._cache) >= 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[url] = (monotonic() + self.cache_seconds, value)
        return value

    def query(self, name, variables):
        raw = json.dumps(variables, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
        try:
            payload = json.loads(self.text(f"/api/gql/{self.QUERIES[name]}/{encoded}"))
            if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
                raise ValueError("Invalid GraphQL response")
            return deepcopy(payload["data"])
        except (ValueError, RecursionError) as exc:
            raise la7.LA7Error("Risposta catalogo LA7 non valida") from exc

    def home(self):
        return self.query("home", {"path": "/"})

    def search(self, query, page=1, media_type=None):
        types = {"tv": ["property"], "movie": ["film"]}
        return self.query("search", {"q": query, "page": page, "pageSize": 20,
                                     "filters": {"contentTypes": types[media_type]} if media_type else None})
