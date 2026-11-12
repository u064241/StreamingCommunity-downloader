"""StreamingCommunity browse pages, using the same public HTML as search.

Cached like the start page they extend: half an hour (±20%), expired early by a
new site version, and served expired while the source is in backoff or out of
budget (see ``source_budget``).
"""
import time

from app.core import page, source_budget

SECTIONS = ("trending", "latest", "top10", "upcoming", "genre")
TTL = 30 * 60
_cache = source_budget.SourceCache("catalog", TTL, max_entries=128,
                                   clock=lambda: time.monotonic())


def clear_cache():
    _cache.clear()


def _fetch_with_info(domain, path, params=None):
    key = (domain, path, tuple(sorted((params or {}).items())))

    def load():
        props = page.fetch_site_props(domain, path, params=params)
        source_budget.note_fields("browse" if path else "home", props)
        if not isinstance(props, dict) or (path and not isinstance(props.get("titles"), list)):
            raise RuntimeError("Formato catalogo sorgente non riconosciuto")
        return props

    return source_budget.get_or_load(_cache, key, load, host=domain, label="catalog")


def _fetch(domain, path, params=None):
    return _fetch_with_info(domain, path, params)[0]


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


# ── Archive (/archive) ────────────────────────────────────────────────────────
#
# The source's full catalogue, sixty titles a page. Read through the same cache
# and budget as the browse pages: one request per page actually asked for, the
# expired copy while the source is in backoff or out of budget, nothing else.
#
# What is NOT known yet, on purpose not guessed:
# * ``props.browseMoreApiRoute`` is the route the site's own JS calls for the
#   next pages. It has never been read, so it is not used: later pages ask
#   ``/archive?page=N`` like the browse pages do. If the source ignores
#   ``page`` there, the panel's dedupe stops "load more" after one wasted page.
# * The query names for genre, country and sort on /archive. Until someone reads
#   them off the site, they stay None here and the endpoint refuses those
#   filters (422) instead of sending a parameter the source may not know.
#   ``props.sortBy`` is passed through as read, for the same reason.
ARCHIVE_PATH = "/archive"
ARCHIVE_PAGE_SIZE = 60
ARCHIVE_MAX_PAGE = 50
ARCHIVE_PARAMS = {"page": "page", "type": "type", "genre": None, "country": None, "sort": None}


def _archive_lists(props):
    genres = [{"id": g["id"], "name": g["name"], "type": g.get("type", "all")}
              for g in (props.get("genres") or []) if isinstance(g, dict)
              and isinstance(g.get("id"), int) and g.get("name") and not g.get("hidden")]
    countries = [{"id": c["id"], "code": c.get("code"), "name": c["name"]}
                 for c in (props.get("countries") or []) if isinstance(c, dict)
                 and isinstance(c.get("id"), int) and c.get("name")]
    return genres, countries


def archive(domain, *, number=1, genre_id=None, country_id=None, media_type=None, sort=None):
    """One page of /archive. Returns ``(result, cache_info)``.

    Raises ValueError for a filter the source is not known to accept (nothing
    is sent), SourceUnavailable when nothing is cached and nothing may be sent.
    """
    if not isinstance(number, int) or not 1 <= number <= ARCHIVE_MAX_PAGE:
        raise ValueError("Pagina non valida")
    if media_type not in (None, "movie", "tv"):
        raise ValueError("Tipo non valido")
    for name, value in (("genre", genre_id), ("country", country_id), ("sort", sort)):
        if value is not None and not ARCHIVE_PARAMS[name]:
            raise ValueError(f"Filtro '{name}' non ancora supportato per l'archivio")
    params = {}
    if number > 1:
        params[ARCHIVE_PARAMS["page"]] = number
    if media_type:
        params[ARCHIVE_PARAMS["type"]] = media_type
    if genre_id is not None or country_id is not None:
        genres, countries = _archive_lists(_fetch(domain, ARCHIVE_PATH))
        if genre_id is not None:
            if not any(g["id"] == genre_id for g in genres):
                raise ValueError("Genere non disponibile")
            params[ARCHIVE_PARAMS["genre"]] = genre_id
        if country_id is not None:
            if not any(c["id"] == country_id for c in countries):
                raise ValueError("Paese non disponibile")
            params[ARCHIVE_PARAMS["country"]] = country_id
    if sort is not None:
        params[ARCHIVE_PARAMS["sort"]] = sort
    props, info = _fetch_with_info(domain, ARCHIVE_PATH, params or None)
    raw = props.get("titles")
    if not isinstance(raw, list):
        raise RuntimeError("Formato archivio sorgente non riconosciuto")
    items = [item for row in raw if isinstance(row, dict)
             if (item := page.normalize_title(row)) is not None]
    total = props.get("totalCount")
    total = total if isinstance(total, int) and not isinstance(total, bool) else None
    more = (number * ARCHIVE_PAGE_SIZE < total) if total is not None else len(raw) >= ARCHIVE_PAGE_SIZE
    genres, countries = _archive_lists(props)
    sort_by = props.get("sortBy")
    if info and info.get("updated_at") is not None:
        # This cache runs on a monotonic clock; the browser wants epoch seconds.
        info = {**info, "updated_at": time.time() - (_cache.now() - info["updated_at"])}
    return {
        "source": "streamingcommunity", "items": items, "page": number,
        "total_count": total, "has_more": bool(raw) and more and number < ARCHIVE_MAX_PAGE,
        "genres": genres, "countries": countries,
        "sort_by": sort_by if isinstance(sort_by, str) else None,
        "filters": {name: bool(ARCHIVE_PARAMS[name]) for name in ("genre", "country", "sort")},
    }, info
