"""StreamingCommunity browse pages, using the same public HTML as search."""
import threading
import time

from app.core import page

SECTIONS = ("trending", "latest", "top10", "upcoming", "genre")
_cache = {}
_lock = threading.Lock()
TTL = 900


def clear_cache():
    with _lock:
        _cache.clear()


def _fetch(domain, path, params=None):
    key = (domain, path, tuple(sorted((params or {}).items())))
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    props = page.fetch_site_props(domain, path, params=params)
    if not isinstance(props, dict) or (path and not isinstance(props.get("titles"), list)):
        raise RuntimeError("Formato catalogo sorgente non riconosciuto")
    with _lock:
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic() + TTL, props)
    return props


def genres(domain):
    raw = _fetch(domain, "").get("genres") or []
    return [{"id": g["id"], "name": g["name"], "type": g.get("type", "all")}
            for g in raw if isinstance(g, dict) and isinstance(g.get("id"), int)
            and g.get("name") and not g.get("hidden")]


def browse(domain, section, *, genre_id=None, number=1, media_type=None):
    if section not in SECTIONS:
        raise ValueError("Sezione non valida")
    params = {"page": number}
    if media_type:
        params["type"] = media_type
    genre = None
    if section == "genre":
        genre = next((g for g in genres(domain) if g["id"] == genre_id), None)
        if genre is None:
            raise ValueError("Genere non disponibile")
        params["g"] = genre["name"]
    elif genre_id is not None:
        raise ValueError("Il genere richiede la sezione genre")
    props = _fetch(domain, "/browse/" + section, params)
    raw = props.get("titles")
    if not isinstance(raw, list):
        raise RuntimeError("Formato catalogo sorgente non riconosciuto")
    items = [item for row in raw if isinstance(row, dict)
             if (item := page.normalize_title(row)) is not None]
    return {"source": "streamingcommunity", "section": section,
            "genre_id": genre_id, "title": props.get("label") or (genre or {}).get("name") or section,
            "items": items, "page": number, "has_more": len(raw) >= 60 and number < 50}
