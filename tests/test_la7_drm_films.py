"""Lean official cards must be checked before exposing a LA7 film download."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from urllib.parse import quote

import pytest

from app import jobs
from app.core import la7
from app.providers import ProviderRegistry, get_registry
from app.providers.la7 import LA7Provider, parser
from app.providers.la7.client import LA7Client
from tests.test_la7_catalogue_flow import page
from tests.test_la7_download_flow import engine, manager, run_next  # noqa: F401
from tests.test_la7_resolver import MANIFEST, Response, network, payload  # noqa: F401


KINDS = ["film", "NodeEventoSingolo", "NodeReplica", "NodeVideo", "ResolvedVideo"]
FIXTURE = Path(__file__).parent / "fixtures" / "la7" / "catalogue-drm.json"


@pytest.fixture
def records():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def search_payload(items):
    return {"siteSearch": {"groups": [{"contentType": "film", "hits": items}]}}


def home_payload(items):
    return {"route": {"entity": {"contentSlots": [
        {"__typename": "NodeContentCarousel", "id": "films", "titoloCarrello": "Film",
         "paginatedItems": {"items": items}},
        {"__typename": "NodeContentCarousel", "id": "recent", "titoloCarrello": "Recenti",
         "paginatedItems": {"items": items}},
    ]}}}


@pytest.fixture
def catalogue(records, client):
    class Catalogue(LA7Client):
        def __init__(self):
            self.calls = []
            self.items = [records["searchFilm"], records["clearFilm"]]
            self.pages = {records["detailFilm"]["path"]: page(records["detailFilm"]),
                          records["clearFilm"]["path"]: page({"entity": records["clearFilm"],
                                                               "config": records["siteConfig"]})}

        def text(self, path):
            self.calls.append(path)
            if path not in self.pages:
                raise la7.LA7Error("LA7 Play non raggiungibile")
            return self.pages[path]

        def home(self):
            return home_payload(self.items)

        def search(self, *_):
            return search_payload(self.items)

    fake = Catalogue()
    provider = LA7Provider(client=fake)
    registry = ProviderRegistry()
    registry.register(provider)
    client.app.dependency_overrides[get_registry] = lambda: registry
    try:
        yield fake, provider
    finally:
        client.app.dependency_overrides.pop(get_registry, None)


@pytest.mark.parametrize("kind", KINDS)
def test_all_film_node_shapes_have_single_movie_identity(records, kind):
    node = {**records["clearFilm"], "__typename": kind}
    if kind == "film":
        node.update(contentType=kind, url=node.pop("path"))
    else:
        # An unrelated contentType must not mask the supported GraphQL type.
        node["contentType"] = "evento_singolo"
    item = parser.row(node)
    assert item["type"] == "movie" and item["download_scope"] == "single"
    card = parser.title_details(item["id"], page(node)).to_legacy()
    assert card["downloadable"] and card["download_scope"] == "single"
    assert card["download_disabled_reason"] is None


@pytest.mark.parametrize("kind", KINDS)
def test_protected_film_disguised_as_replica_or_video_is_hidden(records, kind):
    node = {**records["detailFilm"], "__typename": kind}
    assert parser.search_rows(search_payload([node])) == []
    assert all(not s["items"] for s in parser.home_shelves(home_payload([node])))


@pytest.mark.parametrize("entry", ["searchFilm", "homeFilm"])
@pytest.mark.parametrize("route", ["/api/search?source=la7&q=film", "/api/home?source=la7"])
def test_real_cards_without_drm_are_enriched_before_api_response(client, catalogue, records, entry, route):
    fake, _ = catalogue
    fake.items[0] = records[entry]
    response = client.get(route)
    assert response.status_code == 200, response.text
    groups = [response.json()] if "/search" in route else [s["items"] for s in response.json()["shelves"]]
    for rows in groups:
        assert [row["id"] for row in rows] == [records["clearFilm"]["path"].lstrip("/")]
        assert rows[0]["drm"] is False and rows[0]["downloadable"]
    # A video repeated in two shelves needs only one details read.
    assert fake.calls == [records["detailFilm"]["path"]]


@pytest.mark.parametrize("drm", [None, "false", 0])
def test_unverified_video_is_hidden_without_borrowing_global_or_related_flags(catalogue, records, drm):
    fake, provider = catalogue
    node = {**records["detailFilm"], "drm": drm}
    fake.pages[node["path"]] = page({"entity": node, "related": records["clearFilm"],
                                    "config": {"drm": False, "abilitaDrm": False}})
    assert [r.ref.external_id for r in provider.search("film")] == [records["clearFilm"]["path"].lstrip("/")]


def test_unreachable_film_does_not_break_other_cards_or_repeat_failed_read(catalogue, records):
    fake, provider = catalogue
    del fake.pages[records["detailFilm"]["path"]]
    shelves = provider.home().shelves
    assert all(len(s["items"]) == 1 for s in shelves)
    assert fake.calls == [records["detailFilm"]["path"]]


@pytest.mark.parametrize("kind", ["property", "TermProperty", "ResolvedProperty"])
def test_programmes_are_not_hidden_or_eagerly_loaded(catalogue, kind):
    fake, provider = catalogue
    programme = {"__typename": kind, "path": "/serie", "titolo": "Serie"}
    fake.items = [programme]
    assert provider.search("serie")[0].ref.media_type == "tv"
    assert provider.home().shelves[0]["items"][0]["download_scope"] == "series"
    assert fake.calls == []


def test_clear_film_lightweight_and_complete_flight_copies_merge(records, network):
    full = records["clearFilm"]
    lean = {k: v for k, v in full.items() if k != "drm"}
    lean["uploadVideo"] = {"cf_duration": "5072"}
    body = page({"card": lean, "entity": full, "settings": records["siteConfig"]})
    card = parser.title_details(full["path"].lstrip("/"), body).to_legacy()
    assert card["downloadable"] is True and card["drm"] is False
    url = card["page_url"]
    network.responses[url] = Response(body)
    assert la7.resolve_episode(url)["manifest"] == MANIFEST


@pytest.mark.parametrize("conflict", ["drm", "remote", "id"])
def test_conflicting_video_records_never_become_a_download(records, network, conflict):
    full = records["clearFilm"]
    other = deepcopy(full)
    if conflict == "drm":
        other["drm"] = True
    elif conflict == "remote":
        other["uploadVideo"]["cf_remote_url"] = "/nk/different.mp4"
    else:
        other["id"] = "123"
    url = la7.BASE_URL + full["path"]
    network.responses[url] = Response(page([full, other]))
    with pytest.raises(la7.LA7Error, match="DRM|ambigui"):
        la7.resolve_episode(url)
    assert network.calls == [url]


@pytest.mark.parametrize("remote", [None, "", "/F100366_20261113230324"])
def test_missing_or_unsupported_clear_media_is_not_offered_as_download(catalogue, records, remote):
    fake, provider = catalogue
    full = deepcopy(records["clearFilm"])
    full["uploadVideo"]["cf_remote_url"] = remote
    fake.items = [{k: v for k, v in full.items() if k != "drm"}]
    fake.pages[full["path"]] = page(full)
    assert not provider.details(full["path"].lstrip("/")).downloadable
    assert provider.search("film") == []


def test_catalogue_download_rechecks_protection_before_queue(client, catalogue, records, monkeypatch):
    monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: pytest.fail("DRM video queued"))
    node = records["detailFilm"]
    response = client.post("/api/download/la7", json={"title": "Fury", "scope": "single",
        "url": la7.BASE_URL + node["path"], "external_id": node["path"].lstrip("/")})
    assert response.status_code == 409 and "DRM" in response.json()["detail"]


def test_single_download_cannot_use_a_different_title_url(client, catalogue, records, monkeypatch):
    fake, _ = catalogue
    monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: pytest.fail("Mismatched video queued"))
    response = client.post("/api/download/la7", json={"title": "Film", "url": la7.BASE_URL + records["clearFilm"]["path"],
        "external_id": records["detailFilm"]["path"].lstrip("/")})
    assert response.status_code == 422 and fake.calls == []


@pytest.mark.parametrize("kind", KINDS)
def test_clear_film_search_details_queue_and_worker_create_file(client, catalogue, records, monkeypatch,
                                                                network, engine, manager, kind):
    fake, _ = catalogue
    node = {**records["clearFilm"], "__typename": kind}
    fake.items = [{k: v for k, v in node.items() if k != "drm"}]
    body = page({"card": fake.items[0], "entity": node, "config": records["siteConfig"]})
    fake.pages[node["path"]] = body
    network.responses[la7.BASE_URL + node["path"]] = Response(body)
    monkeypatch.setattr(jobs.job_manager, "submit_la7", manager.submit_la7)
    rows = client.get("/api/search?source=la7&q=film").json()
    assert len(rows) == 1 and rows[0]["type"] == "movie"
    card = client.get("/api/titles/la7/" + quote(rows[0]["id"], safe="")).json()
    assert card["downloadable"] and card["download_scope"] == "single"
    response = client.post("/api/download/la7", json={"source": "la7", "scope": "single",
        "url": card["page_url"], "external_id": card["id"], "title": card["name"]})
    assert response.status_code == 202, response.text
    run_next(manager)
    job = manager.get(response.json()["job_id"])
    assert job.status == "done" and Path(job.output_path).read_bytes() == b"test-media"
    assert engine["extractions"] == [MANIFEST]


def test_detail_and_resolver_accept_absolute_official_video_url(records, network):
    node = records["clearFilm"]
    url = la7.BASE_URL + node.pop("path")
    node["url"] = url
    network.responses[url] = Response(page(node))
    identity = url.split(la7.BASE_URL + "/", 1)[1]
    assert parser.title_details(identity, page(node)).downloadable
    assert la7.resolve_episode(url)["manifest"] == MANIFEST


def test_lean_public_cards_use_client_cache_across_search_and_home(records, network):
    full = records["detailFilm"]
    url = la7.BASE_URL + full["path"]
    network.responses[url] = Response(page(full))
    client = LA7Client(http_get=network.get)
    client.search = lambda *_: search_payload([records["searchFilm"]])
    client.home = lambda: home_payload([records["homeFilm"]])
    provider = LA7Provider(client=client)
    assert provider.search("film") == []
    assert all(not shelf["items"] for shelf in provider.home().shelves)
    assert network.calls == [url]


def test_search_entity_id_and_numeric_route_id_describe_the_same_video(records, network):
    full = records["clearFilm"]
    lean = {**full, "id": "entity:node/399460:it", "contentType": "film"}
    del lean["drm"]
    body = page([lean, full])
    url = la7.BASE_URL + full["path"]
    network.responses[url] = Response(body)
    assert parser.title_details(full["path"].lstrip("/"), body).downloadable
    assert la7.resolve_episode(url)["id"] == "399460"


def test_programme_images_and_related_submenus_do_not_change_the_title_kind(catalogue):
    fake, provider = catalogue
    fake.pages["/serie"] = page({"__typename": "TermProperty", "path": "/serie", "titolo": "Serie",
        "coverImage": {"url": "https://images.la7play.it/cover.webp"},
        "related": {"__typename": "TermProperty", "path": "/other", "titolo": "Other",
                    "vociSottomenu": [{"tipoDiContenuto": {"name": "Replica"}}]}})
    # Root-owned programme data includes related records. A submenu belonging
    # to another path must not trigger /serie/puntate.
    card = provider.details("serie")
    assert card.ref.media_type == "tv"
    assert fake.calls == ["/serie"]


def test_metadata_checks_are_bounded_deduplicated_and_preserve_card_order(records, monkeypatch):
    provider = LA7Provider()
    original = records["clearFilm"]
    rows = [parser.row({**original, "path": f"/film/film-{n}", "drm": None}) for n in range(8)]
    entered = threading.Barrier(4)
    lock = threading.Lock()
    active = 0
    maximum = 0
    calls = []

    def details(identity):
        nonlocal active, maximum
        with lock:
            calls.append(identity)
            active += 1
            maximum = max(maximum, active)
        entered.wait(timeout=5)
        with lock:
            active -= 1
        node = {**original, "path": "/" + identity}
        return parser.title_details(identity, page(node))

    monkeypatch.setattr(provider, "details", details)
    result = provider._catalogue_rows(rows + rows[:1])
    assert len(calls) == 8 and maximum == 4
    assert [item["id"] for item in result] == [row["id"] for row in rows + rows[:1]]


def test_availability_cache_covers_large_home_and_expires(catalogue, records, monkeypatch):
    from app.providers.la7 import provider as module
    fake, provider = catalogue
    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    original = records["clearFilm"]
    fake.items = []
    for n in range(70):
        path = f"/film/control-{n}"
        node = {**original, "path": path}
        fake.pages[path] = page(node)
        fake.items.append({k: v for k, v in node.items() if k != "drm"})
    assert len(provider.home().shelves[0]["items"]) == 70
    assert len(fake.calls) == 70
    provider.home()
    assert len(fake.calls) == 70
    # A rights change must be detected when the short availability cache expires.
    now[0] += 121
    node = {**original, "path": fake.items[0]["path"], "drm": True}
    fake.pages[node["path"]] = page(node)
    assert len(provider.home().shelves[0]["items"]) == 69
    assert len(fake.calls) == 140


def test_unreachable_availability_is_cached_briefly_and_rechecked(catalogue, records, monkeypatch):
    from app.providers.la7 import provider as module
    fake, provider = catalogue
    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    fake.pages.clear()
    provider.home()
    provider.home()
    assert len(fake.calls) == 1
    now[0] += 121
    provider.home()
    assert len(fake.calls) == 2


def test_replica_listing_without_drm_does_not_disable_clear_series_download(client, catalogue, records,
                                                                          monkeypatch, network, engine, manager):
    fake, _ = catalogue
    node = {**records["clearFilm"], "__typename": "NodeReplica", "path": "/serie/puntate/episodio-399460",
            "numeroEpisodio": 1, "numeroStagione": {"name": "1"}}
    lean = {k: v for k, v in node.items() if k != "drm"}
    fake.pages["/serie"] = page({"__typename": "TermProperty", "id": "42", "path": "/serie", "titolo": "Serie",
        "vociSottomenu": [{"tipoDiContenuto": {"name": "Replica"}}]})
    listing = page({"seasons": [{"name": "1"}], "items": [lean]})
    fake.pages["/serie/puntate"] = fake.pages["/serie/puntate?stagione=1"] = listing
    fake.pages[node["path"]] = page(node)
    network.responses[la7.BASE_URL + node["path"]] = Response(page(node))
    monkeypatch.setattr(jobs.job_manager, "submit_la7", manager.submit_la7)
    rows = client.get("/api/titles/la7/serie/episodes", params={"season_number": 1}).json()
    assert rows[0]["drm"] is None and rows[0]["downloadable"] is True
    response = client.post("/api/download/la7", json={"scope": "series", "external_id": "serie", "title": "Serie"})
    assert response.status_code == 202 and response.json()["count"] == 1, response.text
    run_next(manager)
    assert manager.get(response.json()["job_ids"][0]).status == "done"


@pytest.mark.parametrize("with_id", [False, True])
def test_untyped_matching_protection_record_cannot_be_ignored(catalogue, records, network, with_id):
    fake, provider = catalogue
    full = records["clearFilm"]
    protection = {"path": full["path"], "drm": True}
    if with_id:
        protection["id"] = full["id"]
    body = page([full, protection])
    fake.items = [{k: v for k, v in full.items() if k != "drm"}]
    fake.pages[full["path"]] = body
    card = provider.details(full["path"].lstrip("/"))
    assert not card.downloadable and card.extra["drm"] is True
    assert provider.search("film") == []
    url = la7.BASE_URL + full["path"]
    network.responses[url] = Response(body)
    with pytest.raises(la7.LA7Error, match="DRM"):
        la7.resolve_episode(url)
    assert network.calls == [url]


def test_typed_card_and_untyped_complete_record_resolve_together(records, network):
    full = records["clearFilm"]
    lean = {k: full[k] for k in ("__typename", "id", "path", "title")}
    del full["__typename"]
    body = page([lean, full])
    assert parser.title_details(full["path"].lstrip("/"), body).downloadable
    url = la7.BASE_URL + full["path"]
    network.responses[url] = Response(body)
    assert la7.resolve_episode(url)["manifest"] == MANIFEST
