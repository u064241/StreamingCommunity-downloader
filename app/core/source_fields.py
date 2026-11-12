"""Which fields the source sends that the panel does not know about.

Read from pages already fetched - the start page, a search, a browse page, a
title card - never from a request of its own. Only the structures the parser
reads are walked (the title rows, their images, the shelves, the genres, the
title card), and each one is compared with the keys the panel knows for it
(the whitelists below, kept beside what ``page.normalize_title``,
``home._sc_shelves`` and ``metadata._from_props`` read).

A key that is not on the list is counted under its path, for instance
``titles[].images[].foo``: one count per page it was seen on. Names only, never
a value. The counts live in ``source_fields.json`` beside data.json (atomic
write), at most ``MAX_FIELDS`` paths, and are what ``GET /api/source/status``
reports as ``unknown_fields``. Nothing here changes what the panel shows.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "source_fields.json"
MAX_FIELDS = 200
_MAX_KEY_LEN = 64

# The keys of one title row (search, browse page, shelf), as normalize_title
# reads them.
TITLE_ROW_KEYS = frozenset({
    "id", "name", "type", "slug", "score", "release_date", "last_air_date",
    "age", "seasons_count", "images", "coming_soon", "upcoming_seasons",
})
IMAGE_KEYS = frozenset({"type", "filename"})
GENRE_KEYS = frozenset({"id", "name", "type", "hidden"})
SLIDER_KEYS = frozenset({"name", "label", "titles"})
# The title card (props.title), as metadata._from_props and tv read it.
TITLE_CARD_KEYS = TITLE_ROW_KEYS | frozenset({
    "plot", "genres", "runtime", "trailers", "tmdb_id", "seasons",
    "main_actors", "main_directors", "original_name", "original_language",
    "status", "quality",
})
TRAILER_KEYS = frozenset({"youtube_id"})
PERSON_KEYS = frozenset({"name"})
SEASON_KEYS = frozenset({"number"})

_lock = threading.Lock()
_counts: dict[str, int] = {}
_loaded_from: Path | None = None


def _path() -> Path:
    from app import config
    return Path(config.DATA_FILE).with_name(FILE_NAME)


def _load() -> None:
    """Under _lock. (Re)read the file when data.json has moved (or never read)."""
    global _counts, _loaded_from
    path = _path()
    if _loaded_from == path:
        return
    _loaded_from = path
    counts: dict[str, int] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for name, count in (raw.get("fields") or {}).items():
            if isinstance(name, str) and isinstance(count, int) and count > 0:
                counts[name[:400]] = count
                if len(counts) >= MAX_FIELDS:
                    break
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Source field counts unreadable (%s): starting over", exc)
    _counts = counts


def _save() -> None:
    """Under _lock. Best effort: a failed write is logged, never raised."""
    try:
        from app import config
        config.atomic_write_text(_path(), json.dumps({"fields": _counts}, sort_keys=True))
    except Exception as exc:
        logger.warning("Could not save the source field counts: %s", exc)


def _key_name(key) -> str | None:
    if not isinstance(key, str):
        return None
    key = key.strip()
    if not key or len(key) > _MAX_KEY_LEN or not key.isprintable():
        return None
    return key


def _unknown(obj, known: frozenset, path: str, found: set[str]) -> None:
    if not isinstance(obj, dict):
        return
    for key in obj:
        if key in known:
            continue
        name = _key_name(key)
        found.add(f"{path}.{name}" if name else f"{path}.<?>")


def _rows(rows, known: frozenset, path: str, found: set[str], children=()) -> None:
    for row in rows if isinstance(rows, list) else []:
        _unknown(row, known, path, found)
        if isinstance(row, dict):
            for key, child_known, grandchildren in children:
                _rows(row.get(key), child_known, f"{path}.{key}[]", found, grandchildren)


_IMAGES = (("images", IMAGE_KEYS, ()),)


def unknown_paths(kind: str, props) -> set[str]:
    """The paths in *props* the panel does not know. No I/O, never raises."""
    found: set[str] = set()
    if not isinstance(props, dict):
        return found
    try:
        if kind == "home":
            _rows(props.get("sliders"), SLIDER_KEYS, "sliders[]", found,
                  (("titles", TITLE_ROW_KEYS, _IMAGES),))
            _rows(props.get("genres"), GENRE_KEYS, "genres[]", found)
        elif kind in ("search", "browse"):
            _rows(props.get("titles"), TITLE_ROW_KEYS, "titles[]", found, _IMAGES)
        elif kind == "title":
            _unknown(props, TITLE_CARD_KEYS, "title", found)
            for key, known in (("images", IMAGE_KEYS), ("genres", GENRE_KEYS),
                               ("trailers", TRAILER_KEYS), ("main_actors", PERSON_KEYS),
                               ("main_directors", PERSON_KEYS), ("seasons", SEASON_KEYS)):
                _rows(props.get(key), known, f"title.{key}[]", found)
    except Exception as exc:   # a shape we did not foresee must not break a page
        logger.debug("Source field scan failed: %s", exc)
    return found


def record(kind: str, props) -> set[str]:
    """Count the unknown paths of one page already fetched. Never raises.

    Returns the paths found (counted or not: past ``MAX_FIELDS`` a new path is
    dropped, a known one still counts).
    """
    found = unknown_paths(kind, props)
    if not found:
        return found
    try:
        with _lock:
            _load()
            changed = False
            for name in sorted(found):
                if name in _counts:
                    _counts[name] += 1
                    changed = True
                elif len(_counts) < MAX_FIELDS:
                    _counts[name] = 1
                    changed = True
                    logger.info("Source sends a field the panel does not know: %s", name)
            if changed:
                _save()
    except Exception as exc:
        logger.warning("Could not record source fields: %s", exc)
    return found


def summary() -> dict[str, int]:
    """``{path: pages seen on}``, most frequent first. Names and counts only."""
    try:
        with _lock:
            _load()
            items = sorted(_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    except Exception as exc:
        logger.warning("Source field counts unavailable: %s", exc)
        return {}
    return dict(items)


def reset() -> None:
    """Forget the in-memory counts (the next call rereads the file)."""
    global _counts, _loaded_from
    with _lock:
        _counts = {}
        _loaded_from = None
