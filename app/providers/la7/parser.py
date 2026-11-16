"""LA7 metadata parsing, independent of HTTP and the portal.

Identity is the official page path, never a list position or legacy numeric
suffix (old migrated URLs can have a different suffix from the node id).
Only complete replicas belong to an episode list; clips are kept out.
"""
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

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


def row(node):
    if not isinstance(node, dict):
        return None
    kind = node.get("contentType") or node.get("__typename")
    if kind not in {"property", "film", "replica", "video", "TermProperty", "ResolvedProperty", "NodeReplica", "NodeEventoSingolo", "NodeVideo", "ResolvedVideo"}:
        return None
    try:
        raw_path = node.get("path") or node.get("url")
        if isinstance(raw_path, str) and raw_path.startswith("https://"):
            la7._validate_url(raw_path)
            raw_path = urlsplit(raw_path).path
        path = path_for_id(raw_path)
    except la7.LA7Error:
        return None
    title = text(node.get("titolo") or node.get("title"))
    if not title:
        return None
    video = node.get("uploadVideo") or {}
    if not isinstance(video, dict):
        video = {}
    movie = kind in {"film", "NodeEventoSingolo"}
    single = kind not in {"property", "TermProperty", "ResolvedProperty"}
    return {"id": path.lstrip("/"), "source": "la7", "type": "movie" if movie else "tv",
            "name": title, "slug": path.lstrip("/"), "page_url": la7.BASE_URL + path,
            "poster": image(node.get("coverImage") or node.get("imageUrl") or node.get("image") or video.get("cf_thumbnail_cover")),
            "backdrop": image(node.get("scenaImage") or node.get("thumbnail") or node.get("imageUrl") or video.get("thumbnailDefault") or video.get("cf_thumbnail_url")),
            "description": text(node.get("description") or node.get("sinossibreve") or node.get("body") or node.get("excerpt")),
            "duration": video.get("cf_duration") or node.get("duration"),
            "season": number(node.get("numeroStagione") or video.get("stagione")),
            "number": number(node.get("numeroEpisodio") or video.get("episodio")),
            "drm": node.get("drm") if type(node.get("drm")) is bool else None,
            # Missing DRM metadata is not proof of protection. The downloader
            # performs the definitive HLS/DRM check when the job starts.
            "downloadable": node.get("drm") is not True, "content_type": kind,
            "download_scope": "single" if single else "series"}


def unique_rows(nodes):
    rows = {}
    for node in nodes:
        item = row(node)
        if item:
            old = rows.get(item["id"])
            if old is None:
                rows[item["id"]] = item
            elif item["drm"] is True or old["drm"] is True:
                old.update(drm=True, downloadable=False)
            elif item["drm"] is False:
                old.update(drm=False, downloadable=True)
    return list(rows.values())


def objects(page):
    # _objects walks in reverse order; preserve the website's editorial order.
    return [obj for value in la7.page_values(page) for obj in reversed(list(la7._objects(value)))]


def search_rows(payload):
    result = payload.get("siteSearch")
    if not isinstance(result, dict) or not isinstance(result.get("groups"), list):
        raise la7.LA7Error("Risultati ricerca LA7 non validi")
    return unique_rows(hit for group in result["groups"] if isinstance(group, dict)
                       for hit in group.get("hits", []) if isinstance(hit, dict))


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
        shelves.append({"key": "la7-" + str(slot.get("id")), "title": text(slot.get("titoloCarrello")),
                        "items": unique_rows(items)})
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
    matches = [obj for obj in objects(page) if obj.get("path") == path
               and obj.get("__typename") in {"TermProperty", "ResolvedProperty", "NodeReplica",
                                              "NodeEventoSingolo", "ResolvedVideo", "NodeVideo"}]
    if not matches:
        raise la7.LA7Error("Dettagli LA7 non trovati")
    # Prefer the complete route entity over lightweight copies of the card.
    node = max(matches, key=len)
    item = row(node)
    if not item:
        raise la7.LA7Error("Dettagli LA7 non validi")
    protected = any(obj.get("drm") is True for obj in matches)
    season_paths = [{**s, "path": path + "/puntate"} for s in season_entries]
    downloadable = not protected and (item["download_scope"] == "single" or bool(season_paths))
    return TitleDetails(ContentRef("la7", item["id"], item["type"]), item["name"],
                        description=text(node.get("description") or node.get("sinossilunga")
                                         or node.get("sinossibreve") or node.get("body") or node.get("testoProgramma")),
                        year=str(node["annodiproduzione"]) if node.get("annodiproduzione") else None,
                        poster=item["poster"], backdrop=item["backdrop"], page_url=item["page_url"],
                        seasons=tuple(season_entries), duration=str(item["duration"]) if item["duration"] else None,
                        extra={"drm": True if protected else item["drm"],
                               "download_url": item["page_url"], "download_scope": item["download_scope"],
                               "season_paths": season_paths},
                        downloadable=downloadable,
                        download_disabled_reason=("Contenuto LA7 protetto da DRM" if protected
                                                  else "Nessuna puntata completa disponibile su LA7"))
