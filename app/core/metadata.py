"""Plot, genres, rating, artwork and a trailer for one title.

All of it comes from the title page's own props — the Inertia payload the panel
already fetches to find ``tmdb_id``. So metadata costs no request of its own,
needs no account anywhere, and works on a fresh install with nothing configured.

TMDB was tried as a second provider and removed. Measured against the props on
real titles it was not an upgrade: the site copies its synopses from TMDB, so
the text was usually identical, while the round trip lost a trailer on one
title, a logo on another, and 1100 characters of plot on a third. Paying an API
key to fetch a worse copy of data already in hand is a bad trade, and the
"three providers" this module started with are now honestly one.

``tmdb_id`` is still read and still exposed: it is free here, and it is the
identifier a fallback stream provider would resolve against (see
``app.core._shared.resolve_stream``).

Enrichment happens when a detail modal opens, one title at a time — never per
search card. Twenty-one titles per search is twenty-one requests for data
nineteen of which nobody will read.
"""

import logging
import time

from app.config import configured_domain
from app.core import source_budget

logger = logging.getLogger(__name__)

# Cache lifetimes, each stretched or shrunk by up to 20% (source_budget.JITTER).
# A hit is stable — a film's plot does not change, and a series' new season is
# a once-a-week event — so a title card lives a day. A new deploy of the site
# (its version changes) expires it early. A miss is usually a title the source
# has no metadata for, and re-asking on every modal open would make a broken
# title the most expensive one in the panel.
_TTL_HIT = 24 * 60 * 60
_TTL_MISS = 10 * 60
_MAX_ENTRIES = 512


def _now() -> float:
    """Wall clock, indirected so tests can expire an entry without sleeping."""
    return time.time()


# In memory, which is the same bet the rest of the panel makes: JobManager holds
# download state the same way and the process is single by design.
_cache = source_budget.SourceCache("metadata", _TTL_HIT, max_entries=_MAX_ENTRIES,
                                   clock=lambda: _now())


class _LookupFailed(Exception):
    """The title page could not be read (as opposed to: it has no metadata)."""


EMPTY: dict = {
    # `source` below is legacy metadata provenance (site/none). Provider identity
    # has its own field so existing consumers and cache TTLs keep working.
    "provider": "streamingcommunity",
    "external_id": None,
    "source": "none",
    "plot": None,
    "genres": [],
    "rating": None,
    "runtime": None,
    "backdrop": None,
    "logo": None,
    "trailer_url": None,
    "tmdb_id": None,
    "name": None,
    "poster": None,
    "type": None,
    "seasons_count": None,
    "season_numbers": [],
    "cast": [],
    "directors": [],
    "original_name": None,
    "original_language": None,
    "status": None,
    "quality": None,
    "age": None,
    "year": None,
}


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_key(media_type: str, title_id, source: str = "streamingcommunity") -> tuple:
    """Keyed on the host as well as the title.

    Artwork URLs and the plot itself come from whichever domain served them, so
    an entry outlives the domain it was fetched from only by accident. Without
    the host here, a rotation kept serving the old domain's images for six
    hours — long after every one of them had stopped resolving.
    """
    return (source, configured_domain(), media_type, str(title_id))


def _ttl(value: dict) -> float:
    return _TTL_HIT if value.get("source") != "none" else _TTL_MISS


def _cached(key: tuple) -> dict | None:
    """The cached value while fresh; None when absent or expired."""
    entry = _cache.entry(key)
    if entry is None or not _cache.is_fresh(entry, key[1]):
        return None
    return entry.value


def _store(key: tuple, value: dict) -> None:
    _cache.put(key, value, host=key[1], ttl=_ttl(value))


def clear_cache() -> None:
    _cache.clear()


def cached_tmdb_id(media_type: str, title_id, *, source: str = "streamingcommunity") -> int | None:
    """The TMDB id for this title if it is already known. Never does any I/O.

    A stream fallback would need an id at download time but must not pay for a
    lookup to get one: a download that is about to succeed should not spend a
    round trip discovering a fallback it will not use. In practice the detail
    modal has just filled this in; cold means None, which is exactly the
    behaviour there has always been.
    """
    if source != "streamingcommunity":
        return None
    hit = _cached(_cache_key(media_type, title_id, source))
    return hit.get("tmdb_id") if hit else None


# ── Normalisation ─────────────────────────────────────────────────────────────

def _source_image(images: list | None, kind: str) -> str | None:
    for image in images or []:
        if image.get("type") == kind and image.get("filename"):
            return f"/api/image/{image['filename']}"
    return None


# Enough to fill a cast row without turning the payload into a crew list. The
# source sends eight for a film; a long-running series can send many more.
_MAX_PEOPLE = 20


def _people(entries: list | None) -> list[str]:
    """Names out of the source's person records, in the order it sent them."""
    names = []
    for entry in entries or []:
        name = (entry or {}).get("name")
        if name and name not in names:
            names.append(name)
    return names[:_MAX_PEOPLE]


def _year(props: dict) -> str | None:
    """The year a title first came out, or None.

    ``release_date`` on the title page is the premiere: measured against 59
    series, it was present on every one and matched the first season. The
    search payload has no ``release_date`` at all, only ``last_air_date`` -
    the latest season's date - and that is what named 21 of those 59 folders
    after the wrong year (issue #21). So for a series there is no fallback:
    a folder with no year still matches in Jellyfin, one with the wrong year
    matches the wrong show. A film's two dates are the same day.
    """
    date = props.get("release_date")
    if not date and props.get("type") == "movie":
        date = props.get("last_air_date")
    date = str(date or "")[:4]
    return date if len(date) == 4 and date.isdigit() else None


def _from_props(props: dict) -> dict:
    """Everything the title page carries — which is nearly everything."""
    from app.core.tv import season_numbers

    score = props.get("score")
    trailers = props.get("trailers") or []
    youtube_id = trailers[0].get("youtube_id") if trailers else None
    return {
        "source": "site" if (props.get("plot") or props.get("images")) else "none",
        "plot": props.get("plot") or None,
        "genres": [g["name"] for g in props.get("genres") or [] if g.get("name")],
        "rating": round(float(score), 1) if score else None,
        "runtime": props.get("runtime"),
        "backdrop": (_source_image(props.get("images"), "background")
                     or _source_image(props.get("images"), "cover")),
        "logo": _source_image(props.get("images"), "logo"),
        "trailer_url": (
            f"https://www.youtube.com/watch?v={youtube_id}" if youtube_id else None
        ),
        "tmdb_id": props.get("tmdb_id"),
        # Measured against a real title before being added, as the rule above
        # requires: the page carries main_actors and main_directors as person
        # records, plus the original title, the release status, a quality
        # label and an age rating. All of it rides in the payload already
        # fetched for tmdb_id, so none of it costs a request.
        # The title's own identity. The detail view is a page with an
        # address now, so opening it from a pasted URL has no search result to
        # borrow a name and a poster from - it has to be able to rebuild
        # itself from the id alone.
        "name": props.get("name") or None,
        "poster": _source_image(props.get("images"), "poster"),
        "type": props.get("type") or None,
        "seasons_count": props.get("seasons_count"),
        "coming_soon": bool(props.get("coming_soon")),
        "upcoming_seasons": props.get("upcoming_seasons") or [],
        # The pills are drawn from these, not counted up to seasons_count:
        # numbering has holes (#40).
        "season_numbers": season_numbers(props) if props.get("type") == "tv" else [],
        "cast": _people(props.get("main_actors")),
        "directors": _people(props.get("main_directors")),
        "original_name": props.get("original_name") or None,
        "original_language": props.get("original_language") or None,
        "status": props.get("status") or None,
        # Only on the title page. The search payload has no quality field, so
        # this cannot be put on a grid card without a request per poster.
        "quality": props.get("quality") or None,
        "age": props.get("age"),
        "year": _year(props),
    }


# ── The orchestrator ──────────────────────────────────────────────────────────

def title_metadata(media_type: str, title_id, slug: str, version: str,
                   *, source: str = "streamingcommunity") -> dict:
    """Everything known about one title.

    Never raises for a missing answer: a detail modal with no plot is a modal
    with no plot, not an error page.
    """
    if source != "streamingcommunity":
        raise ValueError(f"Metadata non disponibili per la fonte {source!r}")
    key = _cache_key(media_type, title_id, source)
    identity = {"provider": source, "external_id": str(title_id)}

    def load():
        return {**_resolve(title_id, slug, version), **identity}

    try:
        result, _ = source_budget.get_or_load(
            _cache, key, load, host=key[1], label="metadata", ttl_for=_ttl)
    except source_budget.SourceUnavailable as exc:
        # Nothing was asked, so nothing is learnt: not cached as a miss.
        logger.info("Title %s not looked up: %s", title_id, exc)
        return {**EMPTY, **identity}
    except _LookupFailed:
        # The page could not be read and there is no older copy to fall back
        # on: remembered as a miss, as it always was.
        result = {**EMPTY, **identity}
        _store(key, result)
    return result


def _resolve(title_id, slug: str, version: str) -> dict:
    domain = configured_domain()
    if not domain or not slug:
        return dict(EMPTY)

    try:
        from app.core.tv import get_title_props

        props = get_title_props(title_id, slug, version or "", domain) or {}
    except source_budget.SourceUnavailable:
        raise
    except Exception as exc:
        logger.info("Cannot read title props for %s: %s", title_id, exc)
        raise _LookupFailed(str(exc)) from exc

    return _from_props(props) if props else dict(EMPTY)
