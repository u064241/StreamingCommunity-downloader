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


def _explicit_drm(value: Any) -> bool:
    """True only when RaiPlay explicitly declares a DRM/license requirement."""
    if not isinstance(value, dict):
        return False
    rights = value.get("rights_management")
    declared = rights.get("rights", rights) if isinstance(rights, dict) else {}
    drm = declared.get("drm") if isinstance(declared, dict) else None
    if drm not in (None, {}, [], "", False):
        return True
    return any(key in value and value.get(key) not in (None, "", False, {}, [])
               for key in ("drm", "has_drm", "license_url", "license_server", "widevine", "playready", "fairplay"))


def _card_extra(media_type_known: bool) -> dict:
    return {"downloadable": True, "download_disabled_reason": None,
            "metadata_only": False, "media_type_known": media_type_known,
            "download_note": "yt-dlp verifica automaticamente la disponibilità e il DRM"}


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
        if _explicit_drm(card):
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
    if _explicit_drm(item):
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

_EXTRA = re.compile(
    r"\b(?:extra|extras|highlights?|riassunt\w*|trailer|backstage|clip|"
    r"anticipazion\w*|promo|special\w*|dietro\s+le\s+quinte|making\s+of|"
    r"pillole|intervist\w*|gallery|webserie)\b", re.I)


def _extra_item(item: dict) -> bool:
    """Return true for bonus cards mixed into an episode ContentSet."""
    labels = (item.get("name"), item.get("episode_title"), item.get("toptitle"),
              item.get("content"), item.get("section"), item.get("sub_section"))
    if any(_EXTRA.search(str(value or "")) for value in labels):
        return True
    path = str(item.get("weblink") or item.get("path_id") or "").casefold()
    return any(f"/{part}/" in path for part in ("extra", "extras", "highlights", "clip", "trailer", "backstage"))


def season_number(label: Any) -> int | None:
    match = re.search(r"\bstagione\s+(\d+)\b", str(label or ""), re.I)
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


def block_season_number(label: Any) -> int | None:
    """Read legacy block labels such as ``Il Collegio 8``."""
    match = re.search(r"(?:stagione|serie|collegio)\s*(\d+)\s*$", str(label or ""), re.I)
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


def video_page(value: Any) -> str | None:
    """Public video page only; never a player, relinker or foreign address."""
    if not isinstance(value, str):
        return None
    if value.startswith(BASE_URL + "/"):
        value = value[len(BASE_URL):]
    value = value.removesuffix(".json") + ".html" if value.endswith(".json") else value
    if re.fullmatch(r"/video/[^\s\"'<>\\?#]+\.html", value) and "/../" not in value:
        return BASE_URL + value
    return None


def season_entries(blocks: Any) -> list[dict]:
    """Select real seasons across blocks, keeping Extra out of duplicate IDs."""
    if not isinstance(blocks, list):
        return []
    valid = [block for block in blocks if isinstance(block, dict)
             and block.get("type") == "RaiPlay Multimedia Block"
             and not _EXTRA.search(str(block.get("name") or ""))]
    # Explicit episode blocks take precedence over other collections.
    valid.sort(key=lambda block: str(block.get("name") or "").casefold() != "episodi")
    out = {}
    for block in valid:
        if not isinstance(block.get("sets"), list):
            continue
        block_number = block_season_number(block.get("name"))
        for row in block["sets"]:
            if not isinstance(row, dict):
                continue
            name = _text(row.get("name"), 120)
            if not name or _EXTRA.search(name):
                continue
            number = season_number(name)
            # RaiPlay's older catalogues (for example Il Collegio) store the
            # season number on the publishing block ("Il Collegio 8") and
            # call the actual set simply "Puntate". Do not discard those
            # sets or let their Clip/Backstories siblings become episodes.
            if number is None:
                number = block_number
                if number is not None and str(name).casefold() not in {
                        "episodi", "puntate", "tutti gli episodi"}:
                    continue
            # Anthologies such as Montalbano have one TvMovie collection and
            # no season/episode numbers. Preserve its real label in the UI.
            if number is None:
                if (str(block.get("name") or "").strip().casefold() not in
                        {"episodi", "puntate", "tvmovie", "film", "tutti gli episodi"}
                        or len(block["sets"]) != 1):
                    continue
                number = 1
            size = row.get("episode_size")
            count = size.get("number") if isinstance(size, dict) else None
            entry = {
                "name": name, "number": number,
                "episodes": count if type(count) is int and count >= 0 else None,
                "path": row.get("path_id"), "block_id": block.get("id"),
                "set_id": row.get("id"), "block_name": block.get("name"),
            }
            if number not in out:
                out[number] = entry
            else:
                out[number].setdefault("alternatives", []).append(entry)
    return [out[number] for number in sorted(out)]


def _seasons(blocks: Any) -> tuple[dict, ...]:
    return tuple({"name": row["name"], "episodes": row["episodes"]}
                 for row in season_entries(blocks))


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
    drm = _explicit_drm(payload) or _explicit_drm(info)
    season_paths = []
    for row in season_entries(payload.get("blocks")):
        if isinstance(row["path"], str):
            root = "/" + external_id.replace(":", "/")
            feed = (f"{root}/{row['block_id']}/{row['set_id']}/episodes.json"
                    if row["block_id"] and row["set_id"] else row["path"])
            season_paths.append({"name": row["name"], "number": row["number"],
                                 "path": row["path"], "episodes_path": feed})
    extra = {
        "typology": typology,
        "plot": _text(info.get("vanity"), 600),
        "country": _text(info.get("country"), 120),
        "channel": _text(info.get("channel"), 120),
        "direction": _text(info.get("direction"), 600),
        "actors": _text(info.get("actors"), 1200),
        "subgenres": list(_names(info.get("subgenres"))),
        "metadata_only": False,
        "season_paths": season_paths,
        "video_page_url": video_page(payload.get("first_item_path")) if media_type == "movie" else None,
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
        downloadable=not drm,
        download_disabled_reason="Il contenuto RaiPlay è protetto da DRM" if drm else None,
        extra={k: v for k, v in extra.items() if v not in (None, [], "")},
    )
    sanitize(details.to_legacy())
    return details


def episode_rows(payload: Any, expected_season: int | None = None) -> list[dict]:
    """Convert a public ContentSet JSON into safe preview/download cards."""
    rows = []
    items = (payload.get("items") if isinstance(payload, dict) else []) or []
    if not isinstance(items, list):
        items = []
    groups = [(items, expected_season)] if items else []
    if isinstance(payload, dict) and isinstance(payload.get("seasons"), list):
        for block in payload["seasons"]:
            if not isinstance(block, dict) or _EXTRA.search(str(block.get("label") or "")):
                continue
            for season in (block.get("episodes") or []) if isinstance(block, dict) else []:
                if not isinstance(season, dict):
                    continue
                label = str(season.get("label") or "")
                if _EXTRA.search(label):
                    continue
                number = season_number(label)
                if number is None and label.strip().casefold() in {"tvmovie", "episodi", "puntate", "tutti gli episodi"}:
                    number = 1
                if expected_season is None or number == expected_season:
                    cards = season.get("cards")
                    if isinstance(cards, list) and cards:
                        groups.append((cards, number))
    seen = set()
    for item, context_season, index in ((item, number, i) for cards, number in groups
                                       for i, item in enumerate(cards, 1)):
        if not isinstance(item, dict) or _explicit_drm(item) or _extra_item(item):
            continue
        page = video_page(item.get("weblink")) or video_page(item.get("path_id"))
        if not page or page in seen:
            continue
        # Numeric metadata also identifies episodes whose titles contain no
        # SxEy token (e.g. Mare fuori #confessioni).
        episode_name = str(item.get("name") or item.get("episode_title") or item.get("toptitle") or "")
        match = re.search(r"\bS\s*(\d+)\s*E\s*(\d+)\b", episode_name, re.I)
        raw_season = str(item.get("season") or "")
        # Some legacy programmes expose the broadcast year (e.g. 2023) in
        # ``season``. When the ContentSet already supplies the authoritative
        # catalogue season, keep that context instead of dropping the card.
        if match:
            season = match.group(1)
        elif context_season is not None and expected_season is not None and raw_season != str(expected_season):
            season = str(context_season)
        else:
            season = raw_season or str(context_season or "")
        episode = str(item.get("episode") or (match.group(2) if match else index))
        if not episode.isdigit() or not season.isdigit():
            continue
        if expected_season is not None and int(season) != expected_season:
            continue
        if str(item.get("forma") or "integrale").casefold() != "integrale":
            continue
        seen.add(page)
        image = _image((item.get("images") or {}).get("landscape")) if isinstance(item.get("images"), dict) else None
        rows.append({"n": episode,
                     "name": _text(item.get("episode_title") or item.get("toptitle") or item.get("name"), 200),
                     "plot": _text(item.get("description"), 600),
                     "still": image, "duration": _text(str(item.get("duration_in_minutes") or "").removesuffix(" min"), 20),
                     "season": season,
                     "page_url": page})
    return sanitize(sorted(rows, key=lambda row: (int(row["season"]), int(row["n"]))))


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
