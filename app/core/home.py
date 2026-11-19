"""The shelves each source puts on its own front page.

What the panel shows before anything is typed: what is trending, what was added
lately, today's top ten, the latest anime episodes. None of it is curated here —
these are the source's own lists, read out of the page it serves to any visitor.

Items come back in exactly the shape ``/api/search`` returns for that source, so
the grid, the detail modal and the request flow need no new vocabulary.
"""

import json
import logging
import threading
import time

from bs4 import BeautifulSoup

from app.core import animeunity, page, source_budget

logger = logging.getLogger(__name__)

# The shelves change a few times a day. Past half an hour the start page still
# shows the copy it has at once, with its time, and refreshes it in background
# (stale-while-revalidate): nobody waits on the source to see the start page.
_TTL = 30 * 60

# Two shelf keys and two genre keys are live at once. The bound exists so a
# pathological run of domain rotations cannot grow the dict.
_MAX_ENTRIES = 16

# How many titles of a shelf are worth rendering. The source sends thirty.
_SHELF_CAP = 30


def _now() -> float:
    """Wall clock, indirected so tests can expire an entry without sleeping."""
    return time.time()


_cache = source_budget.SourceCache("home", _TTL, max_entries=_MAX_ENTRIES,
                                   clock=lambda: _now())

# Where the last answer for each source came from, for the router to tell the
# browser: {source: {"cached", "stale", "updated_at"}}.
_served: dict[str, dict] = {}
_served_lock = threading.Lock()


def _spawn(job) -> None:
    """Start a background refresh. Indirected so tests can run it inline."""
    source_budget.spawn_thread(job)


# The panel's own headings. The source's label is Italian copy it can re-word
# tomorrow, so the stable machine name is what the shelf is keyed on and this is
# what gets shown. A shelf nobody here has heard of still renders, under the
# source's own label — better a heading we did not choose than a vetrina that
# silently disappears the month the source adds one.
_LABELS = {
    ("streamingcommunity", "trending"): "Di tendenza",
    ("streamingcommunity", "latest"): "Aggiunti di recente",
    ("streamingcommunity", "top10"): "Top 10 di oggi",
    ("animeunity", "latest_episodes"): "Ultimi episodi",
    ("animeunity", "featured"): "In evidenza",
    ("animeunity", "latest_animes"): "Ultimi anime aggiunti",
    ("animeunity", "upcoming_animes"): "Anime in arrivo",
    ("animeunity", "top_day"): "Anime più visti oggi",
    ("animeunity", "top_week"): "Anime più visti della settimana",
    ("animeunity", "top_month"): "Anime più visti del mese",
}


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_key(source: str, host: str) -> tuple:
    """Keyed on the host, not just the source.

    This is the structural half of surviving a domain rotation: the old
    domain's shelves cannot be served because they cannot be reached. The
    explicit half is clear_cache(), called where the domain is written.
    """
    return (source, host)


def _cached(key: tuple) -> list | None:
    """The cached value, expired or not; None when nothing is cached.

    Expired is still worth showing here: the start page serves its last copy
    while a refresh runs, and the genres ride along with the shelves.
    """
    entry = _cache.entry(key)
    return entry.value if entry is not None else None


def _store(key: tuple, value: list) -> None:
    host = key[1] if len(key) > 1 and key[0] == "streamingcommunity" else None
    _cache.put(key, value, host=host)


def last_served(source: str) -> dict | None:
    """Where the most recent answer for *source* came from. No I/O."""
    with _served_lock:
        info = _served.get(source)
        return dict(info) if info else None


def clear_cache(source: str | None = None) -> None:
    """Drop cached shelves, for one source or all of them."""
    from app.core import catalog
    if source in (None, "streamingcommunity"):
        catalog.clear_cache()
    with _served_lock:
        if source is None:
            _served.clear()
        else:
            _served.pop(source, None)
    if source is None:
        _cache.clear()
        return
    _cache.clear(lambda k: k[0] == source)


# ── StreamingCommunity ────────────────────────────────────────────────────────

def _sc_shelves(domain: str) -> list[dict]:
    """The home page's own sliders: trending, recently added, today's top ten."""
    props = page.fetch_site_props(domain)
    source_budget.note_fields("home", props)
    _store(("streamingcommunity", domain, "genres"), [
        {"id": g["id"], "name": g["name"], "type": g.get("type", "all")}
        for g in (props.get("genres") or []) if isinstance(g, dict)
        and isinstance(g.get("id"), int) and g.get("name") and not g.get("hidden")])
    shelves = []
    for slider in props.get("sliders") or []:
        key = slider.get("name") or slider.get("label")
        if not key:
            continue
        items = [t for t in (page.normalize_title(raw)
                             for raw in (slider.get("titles") or [])[:_SHELF_CAP]) if t]
        if items:
            shelves.append(_shelf("streamingcommunity", key, items,
                                  fallback=slider.get("label")))
    return shelves


# ── AnimeUnity ────────────────────────────────────────────────────────────────

def _attr_json(soup, attribute: str, element: str | None = None):
    """One element attribute carrying escaped JSON, decoded.

    BeautifulSoup unescapes the attribute itself, so there is no
    ``html.unescape()`` here — the payload is escaped exactly once, and a second
    pass would corrupt any ``&amp;amp;`` inside it.

    Found by attribute rather than by tag name where it can be: these are custom
    elements whose names are the source's to change.
    """
    node = soup.find(element, attrs={attribute: True}) if element else None
    if node is None:
        node = soup.find(attrs={attribute: True})
    if node is None:
        return None
    try:
        return json.loads(node[attribute])
    except Exception as exc:
        logger.warning("AnimeUnity %s attribute could not be read: %s", attribute, exc)
        return None


def _au_home_titles(raw, *, episodes: bool = False) -> list[dict]:
    """Normalize one rail, keeping source order and duplicates in other rails."""
    if isinstance(raw, dict):
        raw = raw.get("data")
    if not isinstance(raw, list):
        return []
    items, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        if episodes:
            episode = row.get("episode") if isinstance(row.get("episode"), dict) else {}
            row = row.get("anime") or episode.get("anime")
        if not isinstance(row, dict):
            continue
        identity = row.get("id")
        if (type(identity) not in (int, str) or not str(identity).isascii()
                or not str(identity).isdigit() or int(identity) <= 0
                or not isinstance(row.get("slug") or "", str)):
            continue
        identity = int(identity)
        if identity in seen:
            continue
        try:
            item = animeunity.normalize_title(row)
        except (AttributeError, TypeError, ValueError):
            logger.warning("Skipping malformed AnimeUnity home title %s", identity)
            continue
        if item:
            seen.add(identity)
            items.append(item)
            if len(items) >= _SHELF_CAP:
                break
    return items


def _au_shelves() -> list[dict]:
    """Read all public Inertia home rails, or the legacy Vue attributes."""
    html = animeunity.fetch_home_html()
    props = animeunity.inertia_props(html)
    if props is not None:
        top = props.get("topAnimes")
        top = top if isinstance(top, dict) else {}
        rows = [
            ("latest_episodes", props.get("latestEpisodes")),
            ("featured", props.get("carouselAnimes")),
            ("latest_animes", props.get("latestAnimes")),
            ("upcoming_animes", props.get("upcomingAnimes")),
            ("top_day", top.get("day")),
            ("top_week", top.get("week")),
            ("top_month", top.get("month")),
        ]
    else:
        soup = BeautifulSoup(html, "lxml")
        rows = [
            ("latest_episodes", _attr_json(soup, "items-json")),
            ("featured", _attr_json(soup, "animes", element="the-carousel")),
        ]

    shelves = []
    for key, raw in rows:
        items = _au_home_titles(raw, episodes=key == "latest_episodes")
        if items:
            shelves.append(_shelf("animeunity", key, items))
    if not shelves:
        # A changed/challenge page is a failure, never an empty catalogue
        # cached for half an hour and presented as a successful response.
        raise RuntimeError("AnimeUnity: catalogo non disponibile o formato pagina non riconosciuto")
    return shelves


# ── Assembly ──────────────────────────────────────────────────────────────────

def _shelf(source: str, key: str, items: list, fallback: str | None = None) -> dict:
    return {
        "key": key,
        "title": _LABELS.get((source, key)) or fallback or key,
        "items": items,
    }


# "Tutte le fonti": the sources whose front pages are stacked, in this order,
# one block each. RaiPlay is left out on purpose: no reusable front page of it
# has been verified, and guessing one would mean new requests to it.
ALL_SOURCES = ("streamingcommunity", "animeunity")
SOURCE_LABELS = {"streamingcommunity": "StreamingCommunity", "animeunity": "AnimeUnity"}


def combine_all(per_source: list[tuple[str, list[dict]]]) -> list[dict]:
    """Stack each source's shelves into one list, block by block, in the order
    given. A shelf keeps its ``key`` and gains ``source`` and ``base_title``
    (the unprefixed title, which the browser translates); ``title`` gets the
    source's name in front. Pure: no I/O."""
    combined = []
    for source, source_shelves in per_source:
        label = SOURCE_LABELS.get(source, source)
        for shelf in source_shelves or []:
            base = shelf.get("title") or shelf.get("key") or ""
            combined.append({**shelf, "source": source, "base_title": base,
                             "title": f"{label} · {base}"})
    return combined


def shelves(source: str, domain: str) -> list[dict]:
    """The front-page shelves for *source*, cached for half an hour (±20%).

    Past that the cached copy is still answered at once and a refresh runs in
    background, one per key however many tabs ask. While the source is in
    backoff or the hourly budget is spent, the copy is answered and nothing is
    sent (see ``source_budget``).

    A failed fetch is **not** cached, which is where this parts company with
    ``metadata``. That one caches a miss for ten minutes because a title with no
    plot usually still has none ten minutes later. A home page that failed is a
    flap, and remembering it would blank the start page for every user for the
    duration — so the answer is the last good copy if there is one, an empty
    list otherwise, and a fresh try on the next page load.
    """
    key = _cache_key(source, domain)

    def load():
        return _au_shelves() if source == "animeunity" else _sc_shelves(domain)

    try:
        result, info = source_budget.get_or_load(
            _cache, key, load, host=domain if source == "streamingcommunity" else None,
            label=f"home:{source}", swr=True, spawn=_spawn)
    except source_budget.SourceUnavailable as exc:
        logger.warning("Front page of %s not fetched: %s", source, exc)
        if source == "animeunity":
            raise
        return []
    except Exception:
        logger.exception("Cannot read the front page of %s", source)
        if source == "animeunity":
            raise
        return []

    with _served_lock:
        _served[source] = info
    return result
