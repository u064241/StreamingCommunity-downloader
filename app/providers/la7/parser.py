"""LA7 metadata parsing, independent of HTTP and the portal.

Identity is the official page path, never a list position or legacy numeric
suffix (old migrated URLs can have a different suffix from the node id).
Only complete replicas belong to an episode list; clips are kept out.
"""
from html import unescape
from html.parser import HTMLParser
import re

from app.core import la7
from ..contracts import ContentRef, TitleDetails


def path_for_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 400:
        raise la7.LA7Error("Identificativo LA7 non valido")
    path = value if value.startswith("/") else "/" + value
    la7._validate_url(la7.BASE_URL + path)
    if not re.fullmatch(r"/[a-z0-9_-]+(?:/[a-z0-9_-]+)*", path):
        raise la7.LA7Error("Percorso LA7 non valido")
    return path


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def text(value):
    if isinstance(value, dict):
        value = value.get("plainValue") or value.get("value") or ""
    if not isinstance(value, str):
        return ""
    parser = _Text(); parser.feed(value)
    return " ".join(unescape(" ".join(parser.parts)).split())


def image(value):
    if isinstance(value, dict):
        value = value.get("url") or value.get("mediaUrl")
    if not isinstance(value, str):
        return None
    try:
        return la7._validate_url(value.split("|")[0], {"images.la7play.it"})
    except la7.LA7Error:
        return None


def number(value):
    if isinstance(value, dict):
        value = value.get("name")
    return int(value) if type(value) in (str, int) and re.fullmatch(r"[1-9][0-9]*", str(value)) else None


PROGRAM_KINDS = {"property", "TermProperty", "ResolvedProperty"}
VIDEO_KINDS = {"film", "replica", "video", "NodeReplica", "NodeEventoSingolo", "NodeVideo", "ResolvedVideo"}


def node_kind(node):
    kinds = (node.get("contentType"), node.get("__typename"))
    return next((kind for kind in kinds if isinstance(kind, str) and kind in PROGRAM_KINDS | VIDEO_KINDS), None)


def row(node):
    if not isinstance(node, dict):
        return None
    kind = node_kind(node)
    if kind is None:
        return None
    try:
        path = path_for_id(la7.node_path(node))
    except la7.LA7Error:
        return None
    title = text(node.get("titolo") or node.get("title"))
    if not title:
        return None
    video = node.get("uploadVideo") or {}
    if not isinstance(video, dict):
        video = {}
    # Search, carousel and route records can use different GraphQL typenames
    # for the same film. Its official /film/ path is authoritative as well.
    movie = kind in {"film", "NodeEventoSingolo"} or path.startswith("/film/")
    single = movie or kind not in PROGRAM_KINDS
    drm = la7.drm_state([node])
    return {"id": path.lstrip("/"), "source": "la7", "type": "movie" if movie else "tv",
            "name": title, "slug": path.lstrip("/"), "page_url": la7.BASE_URL + path,
            "poster": image(node.get("coverImage") or node.get("imageUrl") or node.get("image") or video.get("cf_thumbnail_cover")),
            "backdrop": image(node.get("scenaImage") or node.get("thumbnail") or node.get("imageUrl") or video.get("thumbnailDefault") or video.get("cf_thumbnail_url")),
            "description": text(node.get("description") or node.get("sinossibreve") or node.get("body") or node.get("excerpt")),
            "duration": video.get("cf_duration") or node.get("duration"),
            "season": number(node.get("numeroStagione") or video.get("stagione")),
            "number": number(node.get("numeroEpisodio") or video.get("episodio")),
            "drm": drm,
            # Lean catalogue cards need enrichment before being offered.
            "downloadable": drm is False if single else drm is not True, "content_type": kind,
            "download_scope": "single" if single else "series"}


def unique_rows(nodes):
    rows = {}
    for node in nodes:
        item = row(node)
        if item:
            old = rows.get(item["id"])
            if old is None:
                rows[item["id"]] = item
            else:
                if item["type"] == "movie":
                    old.update(type="movie", download_scope="single")
                drm = la7.drm_state([old, item])
                old.update(drm=drm, downloadable=(drm is False if old["download_scope"] == "single" else drm is not True))
    return list(rows.values())


def objects(page):
    # _objects walks in reverse order; preserve the website's editorial order.
    return [obj for value in la7.page_values(page) for obj in reversed(list(la7._objects(value)))]


def search_rows(payload):
    result = payload.get("siteSearch")
    if not isinstance(result, dict) or not isinstance(result.get("groups"), list):
        raise la7.LA7Error("Risultati ricerca LA7 non validi")
    rows = unique_rows(hit for group in result["groups"] if isinstance(group, dict)
                       for hit in group.get("hits", []) if isinstance(hit, dict))
    # Un film DRM non può essere scaricato dal portale: non mostrarlo proprio
    # nei risultati, evitando una card che conduce a un'azione inutilizzabile.
    return [item for item in rows if not (item["download_scope"] == "single" and item["drm"] is True)]


def home_shelves(payload):
    try:
        slots = payload["route"]["entity"]["contentSlots"]
        if not isinstance(slots, list):
            raise TypeError()
    except (KeyError, TypeError) as exc:
        raise la7.LA7Error("Home LA7 non valida") from exc
    shelves = []
    for slot in slots:
        if not isinstance(slot, dict) or slot.get("__typename") != "NodeContentCarousel":
            continue
        items = (slot.get("paginatedItems") or {}).get("items", [])
        rows = unique_rows(items)
        rows = [item for item in rows if not (item["download_scope"] == "single" and item["drm"] is True)]
        shelves.append({"key": "la7-" + str(slot.get("id")), "title": text(slot.get("titoloCarrello")),
                        "items": rows})
    return shelves


def episode_rows(page, program_path, season=None):
    nodes = []
    for obj in objects(page):
        path = obj.get("path", "")
        if not isinstance(path, str) or not path.startswith(program_path + "/"):
            continue
        # Replica listings also contain TG routes without /puntate/. Accept
        # their programme-owned video records, but never clips or trailers.
        if "/video/" in path or obj.get("__typename") in {"NodeVideo", "NodeLiveStreaming"}:
            continue
        if ("/puntate/" not in path and "/rivedila7/" not in path
                and obj.get("__typename") != "NodeReplica"
                and not (isinstance(obj.get("uploadVideo"), dict)
                         and "numeroEpisodio" in obj)):
            continue
        nodes.append({**obj, "__typename": "NodeReplica"})
    rows = unique_rows(nodes)
    for item in rows:
        # Replica lists omit DRM too. Do not disable a whole season merely
        # because its lean entries lack the flag: each selected page is
        # checked by details/the worker, and explicitly protected episodes
        # are still skipped by the batch router.
        item["downloadable"] = item["drm"] is not True
        item["season"] = item["season"] or 1
        # The portal and batch router use n, not the catalogue's number field.
        item["n"] = str(item["number"] or la7.validate_episode_url(item["page_url"]))
        item["still"] = item["backdrop"] or item["poster"]
        item["plot"] = item["description"]
        seconds = item["duration"]
        item["duration"] = (max(1, round(float(seconds) / 60))
                            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(seconds)) else None)
    return [item for item in rows if season is None or item["season"] == season]


def seasons(page, program_path=None):
    result = {}
    for obj in objects(page):
        for entry in obj.get("seasons", []) if isinstance(obj.get("seasons"), list) else []:
            n = number(entry)
            if n:
                result[n] = {"number": n, "name": f"Stagione {n}"}
    if not result:
        # Some programmes have complete episodes without a season selector.
        if program_path:
            for item in episode_rows(page, program_path):
                n = item["season"]
                result[n] = {"number": n, "name": f"Stagione {n}"}
    return [result[n] for n in sorted(result)]


class _Next(HTMLParser):
    def __init__(self):
        super().__init__(); self.href = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"link", "a"} and "next" in attrs.get("rel", "").split():
            self.href = attrs.get("href")


def next_page(page):
    parser = _Next(); parser.feed(page)
    return parser.href


def title_details(external_id, page, season_entries=()):
    path = path_for_id(external_id)
    matches = [obj for obj in objects(page)
               if (node_kind(obj) is not None or "drm" in obj or "uploadVideo" in obj)
               and la7.node_path(obj) == path]
    if not matches:
        raise la7.LA7Error("Dettagli LA7 non trovati")
    # Prefer the complete route entity over lightweight copies of the card.
    node = (la7.merge_video_nodes(matches) if any(node_kind(obj) in VIDEO_KINDS for obj in matches)
            else max(matches, key=len))
    item = row(node)
    if not item:
        raise la7.LA7Error("Dettagli LA7 non validi")
    drm = la7.drm_state(matches)
    protected = drm is True
    season_paths = [{**s, "path": path + "/puntate"} for s in season_entries]
    single = item["download_scope"] == "single"
    reason = None
    if protected:
        reason = "Contenuto LA7 protetto da DRM"
    elif single:
        if drm is not False:
            reason = "Stato DRM LA7 non verificato"
        else:
            try:
                if la7.video_id(node) is None:
                    raise la7.LA7Error("Identificativo video LA7 mancante")
                la7.validate_episode_url(item["page_url"])
                la7._manifest_url(node)
            except la7.LA7Error:
                reason = "Video LA7 in chiaro non disponibile"
    elif not season_paths:
        reason = "Nessuna puntata completa disponibile su LA7"
    downloadable = reason is None
    return TitleDetails(ContentRef("la7", item["id"], item["type"]), item["name"],
                        description=text(node.get("description") or node.get("sinossilunga")
                                         or node.get("sinossibreve") or node.get("body") or node.get("testoProgramma")),
                        year=str(node["annodiproduzione"]) if node.get("annodiproduzione") else None,
                        poster=item["poster"], backdrop=item["backdrop"], page_url=item["page_url"],
                        seasons=tuple(season_entries), duration=str(item["duration"]) if item["duration"] else None,
                        extra={"drm": drm,
                               "download_url": item["page_url"], "download_scope": item["download_scope"],
                               "season_paths": season_paths},
                        downloadable=downloadable,
                        download_disabled_reason=reason)
