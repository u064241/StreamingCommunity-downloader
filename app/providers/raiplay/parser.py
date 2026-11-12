"""Turn RaiPlay public JSON into the panel's card shapes.

Whitelist only: each output field is picked explicitly from the upstream
payload. Fields that carry playback addresses upstream (``video``,
``video_url``, ``trailer``, ``content_url`` ...) are never read, and a final
guard drops any string that still looks like a media/licence address.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from ..contracts import NOT_DOWNLOADABLE_REASON, ContentRef, TitleDetails
from .client import BASE_URL

SOURCE = "raiplay"

_SECTIONS = ("programmi", "collezioni")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,150}")
_ID = re.compile(r"(programmi|collezioni):([a-z0-9][a-z0-9-]{0,150})")
_IMAGE_PATH = re.compile(r"/[^\s\"'<>\\]{1,400}\.(?:jpe?g|png|webp)", re.IGNORECASE)
_DURATION = re.compile(r"(\d{1,2}):([0-5]\d):([0-5]\d)")
_YEAR = re.compile(r"(19|20)\d\d")
# Anything resembling a playback or licence address must never leave the parser.
_STREAM_HINT = re.compile(r"relinker|mediapolis|\.m3u8|\.mpd|\.ism|widevine|playready|license|udp://|rtmp", re.I)

_CARD_TYPES = {"RaiPlay Programma Item", "RaiPlay Raccolta Item"}
_SHELF_TYPES = {"RaiPlay Hero Block", "RaiPlay Slider Block", "RaiPlay Slider Chart Block"}


# ── identifiers ──────────────────────────────────────────────────────────────

def external_id_from_path(path: Any) -> str | None:
    """``/programmi/nome.json`` or ``/programmi/nome`` -> ``programmi:nome``."""
    if not isinstance(path, str):
        return None
    parts = path.strip().removesuffix(".json").removesuffix(".html").strip("/").split("/")
    if len(parts) != 2 or parts[0] not in _SECTIONS or not _SLUG.fullmatch(parts[1]):
        return None
    return f"{parts[0]}:{parts[1]}"


def path_for_id(external_id: Any) -> str | None:
    """Inverse of :func:`external_id_from_path`, strictly validated."""
    if not isinstance(external_id, str):
        return None
    match = _ID.fullmatch(external_id)
    return f"/{match.group(1)}/{match.group(2)}.json" if match else None


# ── small field helpers ──────────────────────────────────────────────────────

def _text(value: Any, limit: int = 4000) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    if not value or _STREAM_HINT.search(value):
        return None
    return value[:limit]


def _image(value: Any) -> str | None:
    if not isinstance(value, str) or not _IMAGE_PATH.fullmatch(value.strip()):
        return None
    if _STREAM_HINT.search(value):
        return None
    return BASE_URL + value.strip()


def _first_image(images: Any, *keys: str) -> str | None:
    if not isinstance(images, dict):
        return None
    for key in keys:
        url = _image(images.get(key))
        if url:
            return url
    return None


def _poster(images: Any, fallback: Any = None) -> str | None:
    return _first_image(images, "portrait", "portrait43", "portrait_logo", "square") or _image(fallback)


def _backdrop(images: Any) -> str | None:
    return _first_image(images, "landscape", "landscape43", "landscape_logo")


def _year(*candidates: Any) -> str | None:
    for value in candidates:
        if isinstance(value, (int, str)) and _YEAR.fullmatch(str(value).strip()):
            return str(value).strip()
    return None


def _details_year(details: Any) -> str | None:
    if isinstance(details, list):
        for row in details:
            if isinstance(row, dict) and row.get("key") == "year":
                return _year(row.get("value"))
    return None


def _duration(value: Any) -> str | None:
    """``01:33:45`` -> ``1h 33min``; ``00:09:10`` -> ``9min``."""
    if not isinstance(value, str):
        return None
    match = _DURATION.fullmatch(value.strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours == minutes == 0:
        return None
    return f"{hours}h {minutes}min" if hours else f"{minutes}min"


def _names(rows: Any) -> tuple[str, ...]:
    out = []
    if isinstance(rows, list):
        for row in rows:
            name = _text(row.get("name") if isinstance(row, dict) else None, 80)
            if name and name not in out:
                out.append(name)
    return tuple(out)


def _media_type(typology: Any, layout: Any, section: str) -> str:
    if section == "programmi" and (typology == "Film" or layout == "single"):
        return "movie"
    return "tv"


def _card_extra(media_type_known: bool) -> dict:
    return {"downloadable": False, "download_disabled_reason": NOT_DOWNLOADABLE_REASON,
            "metadata_only": True, "media_type_known": media_type_known}


# ── search ───────────────────────────────────────────────────────────────────

def search_rows(payload: Any) -> list[dict]:
    """Rows in the legacy search shape from a catalogue search response."""
    if not isinstance(payload, dict):
        raise ValueError("Unexpected search payload")
    titles = (payload.get("agg") or {}).get("titoli") if isinstance(payload.get("agg"), dict) else None
    cards = titles.get("cards") if isinstance(titles, dict) else None
    if not isinstance(cards, list):
        raise ValueError("Unexpected search payload")
    rows = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        ext_id = external_id_from_path(card.get("path_id")) or external_id_from_path(card.get("url"))
        name = _text(card.get("titolo"), 300)
        if not ext_id or not name:
            continue
        rows.append({
            "id": ext_id, "name": name, "type": "tv", "slug": ext_id.split(":", 1)[1],
            "poster": _image(card.get("immagine")), "backdrop": None,
            "page_url": BASE_URL + "/" + ext_id.replace(":", "/"),
            "kind": _text(card.get("tipo"), 40),
            **_card_extra(media_type_known=False),
        })
    return sanitize(rows)


# ── home ─────────────────────────────────────────────────────────────────────

def _home_card(item: dict) -> dict | None:
    if item.get("type") not in _CARD_TYPES:
        return None
    ext_id = external_id_from_path(item.get("path_id")) or external_id_from_path(item.get("weblink"))
    name = _text(item.get("name"), 300)
    if not ext_id or not name:
        return None
    section = ext_id.split(":", 1)[0]
    known = item.get("layout") in ("single", "multi") and section == "programmi"
    return {
        "id": ext_id, "name": name, "type": _media_type(None, item.get("layout"), section),
        "slug": ext_id.split(":", 1)[1],
        "poster": _poster(item.get("images")), "backdrop": _backdrop(item.get("images")),
        "plot": _text(item.get("vanity") or item.get("description"), 600),
        "year": _details_year(item.get("details")),
        "page_url": BASE_URL + "/" + ext_id.replace(":", "/"),
        **_card_extra(media_type_known=known),
    }


def home_shelves(payload: Any) -> tuple[list[dict], list[dict]]:
    """``(shelves, genres)`` from the public home page JSON."""
    if not isinstance(payload, dict) or not isinstance(payload.get("contents"), list):
        raise ValueError("Unexpected home payload")
    shelves, genres = [], []
    for index, block in enumerate(payload["contents"]):
        if not isinstance(block, dict) or not isinstance(block.get("contents"), list):
            continue
        if block.get("type") == "RaiPlay Slider Generi Block":
            for item in block["contents"]:
                if not isinstance(item, dict):
                    continue
                name = _text(item.get("name"), 80)
                path = item.get("path_id")
                if name and isinstance(path, str) and re.fullmatch(r"/tipologia/[a-z0-9-]{1,80}/index\.json", path):
                    genres.append({"id": path.split("/")[2], "name": name})
            continue
        if block.get("type") not in _SHELF_TYPES:
            continue
        items = [card for card in (_home_card(i) for i in block["contents"] if isinstance(i, dict)) if card]
        title = _text(block.get("name"), 120)
        if items and title:
            shelves.append({"key": f"raiplay-{index}", "title": title, "items": items})
    return sanitize(shelves), sanitize(genres)


# ── details ──────────────────────────────────────────────────────────────────

def _seasons(blocks: Any) -> tuple[dict, ...]:
    """Season names and episode counts from the first multimedia block."""
    if not isinstance(blocks, list):
        return ()
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "RaiPlay Multimedia Block":
            continue
        out = []
        for row in block.get("sets") or []:
            if not isinstance(row, dict):
                continue
            name = _text(row.get("name"), 120)
            size = row.get("episode_size")
            count = size.get("number") if isinstance(size, dict) else None
            if name:
                out.append({"name": name, "episodes": count if type(count) is int and count >= 0 else None})
        return tuple(out)
    return ()


def title_details(external_id: str, payload: Any) -> TitleDetails:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected title payload")
    info = payload.get("program_info") if isinstance(payload.get("program_info"), dict) else {}
    section = external_id.split(":", 1)[0]
    name = _text(info.get("name"), 300) or _text(payload.get("name"), 300)
    if not name:
        raise ValueError("Title payload without a name")
    typology = _text(info.get("typology"), 60)
    media_type = _media_type(typology, info.get("layout"), section)
    extra = {
        "typology": typology,
        "plot": _text(info.get("vanity"), 600),
        "country": _text(info.get("country"), 120),
        "channel": _text(info.get("channel"), 120),
        "direction": _text(info.get("direction"), 600),
        "actors": _text(info.get("actors"), 1200),
        "subgenres": list(_names(info.get("subgenres"))),
        "metadata_only": True,
    }
    details = TitleDetails(
        ref=ContentRef(SOURCE, external_id, media_type),
        title=name,
        description=_text(info.get("description")) or _text(info.get("vanity")),
        year=_year(info.get("year")) or _details_year(info.get("details")),
        genres=_names(info.get("genres")) or ((typology,) if typology else ()),
        # Only a single-item programme (a film) has one meaningful running time.
        duration=_duration(payload.get("first_item_duration")) if media_type == "movie" else None,
        poster=_poster(info.get("images")),
        backdrop=_backdrop(info.get("images")),
        page_url=BASE_URL + "/" + external_id.replace(":", "/"),
        seasons=_seasons(payload.get("blocks")) if media_type == "tv" else (),
        extra={k: v for k, v in extra.items() if v not in (None, [], "")},
    )
    sanitize(details.to_legacy())
    return details


# ── last line of defence ─────────────────────────────────────────────────────

def sanitize(value: Any) -> Any:
    """Fail loudly if any string leaving the parser looks like a stream address."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if isinstance(key, str) and key.lower() in {"video", "video_url", "content_url", "trailer", "stream", "manifest"}:
                raise ValueError("Stream field leaked into provider output")
            sanitize(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            sanitize(inner)
    elif isinstance(value, str):
        if _STREAM_HINT.search(value):
            raise ValueError("Stream address leaked into provider output")
        if value.startswith(("http://", "https://")) and urlsplit(value).hostname != urlsplit(BASE_URL).hostname:
            raise ValueError("Foreign URL leaked into provider output")
    return value
