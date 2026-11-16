"""Catalogue page identities -> details -> episodes -> LA7 queue regressions.

Use the public site's actual field shapes, including migrated paths and TG
editions. HTTP/media I/O is replaced so these regressions remain reproducible.
"""
from copy import deepcopy
import json
from urllib.parse import quote

import pytest

from app import jobs
from app.core import la7
from app.providers import ProviderRegistry, get_registry
from app.providers.la7 import LA7Provider, parser
from tests.test_la7_resolver import MANIFEST, URL, Response, network, payload  # noqa: F401
from tests.test_la7_download_flow import engine, manager, run_next  # noqa: F401


TG_ID = "tgla7/edizione-ore-2000-del-16-11-2026-399613"


def page(value):
    return '<script type="application/json">' + json.dumps(value) + '</script>'


def video(path, *, node_id=None, kind="NodeReplica", season=1, episode=1, drm=False):
    return {"__typename": kind, "id": node_id or path.rsplit("-", 1)[-1],
            "path": path, "title": "Titolo " + path.rsplit("/", 1)[-1],
            "drm": drm, "numeroStagione": {"name": str(season)}, "numeroEpisodio": episode,
            "uploadVideo": {"cf_duration": "2252", "cf_remote_url": "/nk/content/test.mp4",
                            "thumbnailDefault": {"url": "https://images.la7play.it/test.webp"}}}


@pytest.fixture
def catalogue(client):
    class Catalogue:
        def __init__(self):
            self.calls = []
            self.programme = {"__typename": "TermProperty", "id": "42", "path": "/serie",
                              "titolo": "Serie", "vociSottomenu": [{"tipoDiContenuto": {"name": "Replica"}}]}
            self.tg = video("/" + TG_ID, season=2026, episode=16)
            self.movie = video("/film/film-di-prova-399460", kind="NodeEventoSingolo")
            self.episodes = [video("/serie/puntate/episodio-101", season=1, episode=1),
                             video("/serie/puntate/episodio-202", season=2, episode=3)]
            self.pages = {"/serie": page(self.programme), "/" + TG_ID: page(self.tg),
                          self.movie["path"]: page(self.movie),
                          "/serie/puntate": page({"seasons": [{"name": "1"}, {"name": "2"}],
                                                  "items": self.episodes})}
            for season in (1, 2):
                self.pages[f"/serie/puntate?stagione={season}"] = page({"items": self.episodes})

        def text(self, path):
            self.calls.append(path)
            assert path in self.pages, f"Unexpected LA7 page: {path}"
            return self.pages[path]

        def home(self):
            return {"route": {"entity": {"contentSlots": [{"__typename": "NodeContentCarousel",
                    "id": "1", "titoloCarrello": "LA7", "paginatedItems": {
                        "items": [self.programme, self.movie, self.tg]}}]}}}

    fake = Catalogue()
    provider = LA7Provider(client=fake)
    registry = ProviderRegistry()
    registry.register(provider)
    client.app.dependency_overrides[get_registry] = lambda: registry
    try:
        yield fake, provider
    finally:
        client.app.dependency_overrides.pop(get_registry, None)


@pytest.mark.parametrize("encoded", [False, True])
def test_reported_tg_path_reaches_details_without_404(client, catalogue, encoded):
    identity = quote(TG_ID, safe="") if encoded else TG_ID
    response = client.get("/api/titles/la7/" + identity)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["id"] == TG_ID
    assert card["page_url"] == la7.BASE_URL + "/" + TG_ID
    assert card["download_scope"] == "single" and card["downloadable"] is True
    assert card["download_disabled_reason"] is None


def test_home_id_is_a_page_path_not_the_video_node_id(client, catalogue):
    fake, provider = catalogue
    cards = provider.home().to_legacy()["shelves"][0]["items"]
    assert [c["id"] for c in cards] == ["serie", fake.movie["path"].lstrip("/"), TG_ID]
    for card in cards:
        response = client.get("/api/titles/la7/" + quote(card["id"], safe=""))
        assert response.status_code == 200, response.text
        assert response.json()["download_scope"] == card["download_scope"]


def test_series_contract_exposes_real_seasons_and_portal_episode_fields(client, catalogue):
    response = client.get("/api/titles/la7/serie")
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["downloadable"] is True
    assert card["seasons"] == [{"number": 1, "name": "Stagione 1"}, {"number": 2, "name": "Stagione 2"}]
    assert card["season_paths"] == [{**s, "path": "/serie/puntate"} for s in card["seasons"]]
    response = client.get("/api/titles/la7/serie/episodes", params={"season_number": 2})
    assert response.status_code == 200, response.text
    episode, = response.json()
    assert episode["n"] == "3" and episode["season"] == 2
    assert episode["page_url"] == la7.BASE_URL + "/serie/puntate/episodio-202"
    assert episode["still"] == "https://images.la7play.it/test.webp"
    assert episode["duration"] == 38  # source seconds -> portal minutes


@pytest.mark.parametrize("scope,expected", [("series", 2), ("season", 1)])
def test_programme_url_does_not_fail_single_video_validation(client, catalogue, monkeypatch, scope, expected):
    queued = []
    monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: queued.append((a, kw)) or str(len(queued)))
    response = client.post("/api/download/la7", json={"source": "la7", "scope": scope,
        "external_id": "serie", "url": la7.BASE_URL + "/serie", "title": "Serie", "season_number": 2})
    assert response.status_code == 202, response.text
    assert response.json()["count"] == len(queued) == expected
    assert queued[-1][0][0].endswith("/serie/puntate/episodio-202")
    assert queued[-1][0][1] == "Serie S02E3"
    assert all(q[1]["batch_kind"] == scope for q in queued)


def test_catalogue_movie_details_single_queue_and_worker(client, catalogue, monkeypatch, manager, network, engine, payload):
    fake, _ = catalogue
    film_url = la7.BASE_URL + fake.movie["path"]
    payload["props"]["episode"].update(path=fake.movie["path"], __typename="NodeEventoSingolo")
    network.responses[film_url] = Response(network.page(payload))
    monkeypatch.setattr(jobs.job_manager, "submit_la7", manager.submit_la7)
    card = client.get("/api/titles/la7/" + quote(fake.movie["path"].lstrip("/"), safe="")).json()
    assert card["type"] == "movie" and card["downloadable"] is True
    response = client.post("/api/download/la7", json={"source": "la7", "scope": "single",
                                                     "url": card["page_url"], "title": card["name"]})
    assert response.status_code == 202, response.text
    run_next(manager)
    job = manager.get(response.json()["job_id"])
    assert job.status == "done" and job.output_created
    assert engine["extractions"] == [MANIFEST]


def test_whole_series_jobs_each_produce_their_episode_file(client, catalogue, monkeypatch, manager, network, engine, payload):
    fake, _ = catalogue
    for ep in fake.episodes:
        data = deepcopy(payload)
        data["props"]["episode"].update(id=ep["id"], path=ep["path"])
        network.responses[la7.BASE_URL + ep["path"]] = Response(page(data))
    monkeypatch.setattr(jobs.job_manager, "submit_la7", manager.submit_la7)
    response = client.post("/api/download/la7", json={"scope": "series", "external_id": "serie", "title": "Serie"})
    assert response.status_code == 202, response.text
    assert response.json()["count"] == 2
    run_next(manager)
    run_next(manager)
    results = [manager.get(job_id) for job_id in response.json()["job_ids"]]
    assert all(job.status == "done" and job.output_created for job in results)
    assert len({job.output_path for job in results}) == 2
    assert {job.external_id for job in results} == {"101", "202"}


def test_single_replica_is_not_accepted_as_a_series_batch(client, catalogue, monkeypatch):
    monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: pytest.fail("Invalid batch queued"))
    response = client.post("/api/download/la7", json={"scope": "series", "external_id": TG_ID, "title": "TG"})
    assert response.status_code == 422, response.text


def test_tg_year_season_and_direct_editions_are_supported(client, catalogue, monkeypatch):
    fake, _ = catalogue
    fake.pages["/tgla7"] = page({"__typename": "TermProperty", "path": "/tgla7", "titolo": "TG",
                                 "vociSottomenu": [{"tipoDiContenuto": {"name": "Replica"}}]})
    listing = page({"seasons": [{"name": "2026"}], "items": [fake.tg]})
    fake.pages["/tgla7/puntate"] = listing
    fake.pages["/tgla7/puntate?stagione=2026"] = listing
    response = client.get("/api/titles/la7/tgla7/episodes", params={"season_number": 2026})
    assert response.status_code == 200, response.text
    assert response.json()[0]["id"] == TG_ID
    queued = []
    monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: queued.append(a) or "job")
    response = client.post("/api/download/la7", json={"scope": "season", "external_id": "tgla7",
                                                     "season_number": 2026, "title": "TG"})
    assert response.status_code == 202, response.text
    assert queued[0][0] == la7.BASE_URL + "/" + TG_ID


def test_unseasoned_programmes_get_one_real_episode_season(catalogue):
    fake, provider = catalogue
    episode = video("/serie/puntate/episodio-101")
    del episode["numeroStagione"]
    fake.pages["/serie/puntate"] = page({"items": [episode]})
    assert provider.details("serie").seasons == ({"number": 1, "name": "Stagione 1"},)


def test_pagination_deduplicates_editions_and_ignores_extra_clips(catalogue):
    fake, provider = catalogue
    clip = video("/serie/video/trailer-303", kind="NodeVideo")
    first, second = fake.episodes
    fake.pages["/serie/puntate?stagione=1"] = page({"items": [first, clip]}) + '<a rel="next" href="?page=2&amp;stagione=1">Next</a>'
    fake.pages["/serie/puntate?page=2&stagione=1"] = page({"items": [first, {**second, "numeroStagione": {"name": "1"}}]})
    rows = provider.episodes("serie", season_number=1)
    assert [r["n"] for r in rows] == ["1", "3"]
    assert all("/puntate/" in r["page_url"] for r in rows)


def test_protected_film_has_specific_reason_and_never_resolves_media(client, catalogue, network):
    fake, _ = catalogue
    protected = {**fake.movie, "drm": True}
    fake.pages[protected["path"]] = page(protected)
    card = client.get("/api/titles/la7/" + protected["path"].lstrip("/")).json()
    assert card["downloadable"] is False and "DRM" in card["download_disabled_reason"]
    network.responses[card["page_url"]] = Response(page(protected))
    with pytest.raises(la7.LA7Error, match="DRM"):
        la7.resolve_episode(card["page_url"])
    assert network.calls == [card["page_url"]]


def test_protected_films_are_hidden_from_search_and_home():
    protected = video("/film/protetto-1", kind="NodeEventoSingolo", drm=True)
    available = video("/film/disponibile-2", kind="NodeEventoSingolo", drm=False)
    payload_data = {"siteSearch": {"groups": [{"hits": [protected, available]}]}}
    rows = parser.search_rows(payload_data)
    assert [row["id"] for row in rows] == ["film/disponibile-2"]
    home = {"route": {"entity": {"contentSlots": [{"__typename": "NodeContentCarousel",
        "id": "films", "titoloCarrello": "Film", "paginatedItems": {
            "items": [protected, available]}}]}}}
    assert [row["id"] for row in parser.home_shelves(home)[0]["items"]] == ["film/disponibile-2"]


def test_invalid_batch_plan_is_rejected_before_any_job(client, catalogue, monkeypatch):
    _, provider = catalogue
    monkeypatch.setattr(provider, "episodes", lambda *a, **kw: [
        {"n": "1", "season": 1, "page_url": URL},
        {"n": "2", "season": 1, "page_url": "https://evil.test/video-2"}])
    monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: pytest.fail("Partial batch created"))
    response = client.post("/api/download/la7", json={"scope": "series", "external_id": "serie", "title": "Serie"})
    assert response.status_code == 502, response.text


@pytest.mark.parametrize("identity", ["https://evil.test/serie", "serie/../film", "serie?path=film", "serie%2Fvideo"])
def test_invalid_catalogue_id_is_rejected_before_fetch_or_queue(client, catalogue, monkeypatch, identity):
    fake, _ = catalogue
    monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: pytest.fail("Invalid identity queued"))
    response = client.post("/api/download/la7", json={"scope": "series", "external_id": identity, "title": "Serie"})
    assert response.status_code == 422, response.text
    assert fake.calls == []


def test_migrated_url_matches_requested_path_not_legacy_numeric_suffix(network, payload):
    requested = "/miss-marple/rivedila7/miss-marple-19-10-2025-616335"
    actual = payload["props"]["episode"]
    actual.update(id="346857", path=requested)
    # A related node happens to have the old suffix: it must never be chosen.
    payload["props"]["related"].update(id="616335", path="/other/related-616335")
    url = la7.BASE_URL + requested
    network.responses[url] = Response(network.page(payload))
    result = la7.resolve_episode(url)
    assert result["id"] == "346857" and result["page_url"] == url
    assert result["manifest"] == MANIFEST


@pytest.mark.parametrize("prefix", ["/content/", "//content/"])
def test_migrated_clear_cdn_paths_resolve_without_relaxing_hosts(network, payload, prefix):
    remote = prefix + "entry/data/0/691/old.mp4"
    payload["props"]["episode"]["uploadVideo"]["cf_remote_url"] = remote
    network.set_page(payload)
    manifest = la7.CDN_HOST + "/local/hls/," + remote + ".urlset/master.m3u8"
    # A VOD media manifest suffices; never fetch segments during resolution.
    network.responses[manifest] = Response("#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:5,\nsegment.ts\n#EXT-X-ENDLIST\n")
    assert la7.resolve_episode(URL)["manifest"] == manifest
    assert network.calls == [URL, manifest]


def test_matching_path_cannot_borrow_clear_metadata_from_unrelated_node(network, payload):
    payload["props"]["episode"]["path"] = "/different/video-399460"
    network.set_page(payload)
    with pytest.raises(la7.LA7Error, match="non trovati"):
        la7.resolve_episode(URL)
    assert network.calls == [URL]


@pytest.mark.parametrize("kind", ["NodeVideo", "ResolvedVideo"])
def test_video_detail_types_match_catalogue_support(kind):
    node = video("/serie/video/clip-303", kind=kind)
    card = parser.title_details(node["path"].lstrip("/"), page(node)).to_legacy()
    assert card["download_scope"] == "single" and card["downloadable"] is True
