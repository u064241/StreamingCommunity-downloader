"""Regression coverage for the AnimeUnity migration observed on 2026-11-18."""
import copy
import html
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

from app import config
from app.core import animeunity as au, home


def page(props):
    payload = json.dumps({"component": "Home", "props": props, "version": "fixture-v2"})
    return '<div id="app" data-page="' + html.escape(payload, quote=True) + '"></div>'


class Response:
    def __init__(self, text="", *, status=200, payload=None, url="", headers=None):
        self.text, self.status_code, self.payload, self.url = text, status, payload, url
        self.headers = headers or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


@pytest.fixture
def modern(monkeypatch, public_dns):
    raw = json.loads((Path(__file__).parent / 'fixtures/animeunity_2026_11_18.json').read_text())
    props = {"latestEpisodes": {"data": [raw["latest_episode"], raw["latest_episode"]]},
             "carouselAnimes": [raw["anime"]]}
    state = {"home": page(props), "raw": raw, "props": props, "calls": [], "routes": {}}

    def fake_get(url, **kwargs):
        state["calls"].append((url, kwargs))
        path = urlparse(url).path
        if path in state["routes"]:
            value = state["routes"][path]
            return value(url, kwargs) if callable(value) else value
        if path == "/":
            return Response(state["home"], url=url)
        raise AssertionError(f"Unexpected upstream request: {url}")

    class Session:
        get = staticmethod(fake_get)

        def post(self, *a, **kw):
            raise AssertionError("The modern site must never POST to the retired archive API")

    au.forget_csrf()
    home.clear_cache()
    monkeypatch.setattr(au, "_get_scraper", lambda: Session())
    yield state
    au.forget_csrf()
    home.clear_cache()


def test_real_inertia_home_preserves_source_artwork_and_deduplicates(modern):
    shelves = home.shelves("animeunity", au.host())
    assert [s["key"] for s in shelves] == ["latest_episodes", "featured"]
    assert len(shelves[0]["items"]) == 1
    item = shelves[0]["items"][0]
    assert item["id"] == "6467-super-cube"
    assert item["poster"] == modern["raw"]["latest_episode"]["episode"]["anime"]["images"]["poster"]
    assert item["backdrop"].endswith("39S0Y.png")
    assert item["language"] == "chn" and item["episodes_count"] == 12
    assert item["dubbed"] is True
    home.shelves("animeunity", au.host())
    assert len(modern["calls"]) == 1


def test_modern_metadata_uses_original_variant_count_studio_and_year(modern):
    item = au.normalize_title(modern["raw"]["anime"])
    assert (item["type"], item["media_type"], item["episodes_count"]) == ("anime", "tv", 170)
    assert item["release_date"] == "2017"
    assert item["studio"] == "Studio Pierrot"
    assert item["status"] == "completed"
    assert item["backdrop"].endswith("97940-1URQdQ4U1a0b.jpg")
    assert au.normalize_title(None) is None


@pytest.mark.parametrize("dubbed,kind", [(False, None), (True, "movie"), (True, "tv")])
def test_modern_search_uses_get_query_page_and_source_filters(modern, dubbed, kind):
    raw = modern["raw"]["anime"]
    modern["routes"]["/archive"] = Response(page({"animes": {"data": [raw, None, raw]}}))
    result = au.search("Black Clover", page=2, media_type=kind, dubbed=dubbed)
    assert len(result) == 1
    url, kwargs = modern["calls"][-1]
    assert url == "https://animeunity.cx/archive"
    assert kwargs["params"] == {"query": "Black Clover", "page": 2,
                                **({"type": kind} if kind else {}),
                                **({"language": "ita"} if dubbed else {})}
    assert result[0]["language"] == ("ita" if dubbed else "jpn")
    assert result[0]["episodes_count"] == (153 if dubbed else 170)
    assert len(modern["calls"]) == 2


def test_modern_empty_search_is_a_valid_answer(modern):
    modern["routes"]["/archive"] = Response(page({"animes": {"data": []}}))
    assert au.search("does not exist") == []


@pytest.mark.parametrize("props", [{}, {"animes": []}, {"animes": {"total": 0}}])
def test_changed_archive_schema_is_not_silently_empty(modern, props):
    modern["routes"]["/archive"] = Response(page(props))
    with pytest.raises(RuntimeError, match="archivio"):
        au.search("Black Clover")


def test_new_domain_redirect_updates_all_subsequent_requests(modern):
    config.update_data({"animeunity_domain": "www.animeunity.co"})
    modern["routes"]["/"] = lambda url, kw: (
        Response(status=302, headers={"Location": "https://animeunity.cx/"})
        if "www.animeunity.co" in url else Response(modern["home"], url=url))
    modern["routes"]["/archive"] = Response(page({"animes": {"data": []}}))
    au.search("Black Clover")
    assert au.host() == "animeunity.cx"
    assert modern["calls"][-1][0] == "https://animeunity.cx/archive"
    assert config.configured_animeunity_host() == "www.animeunity.co"  # no settings write


def test_unreachable_host_discovers_only_an_explicit_animeunity_link(modern):
    config.update_data({"animeunity_domain": "www.animeunity.co", "domain": "streamingcommunityz.dad"})

    def root(url, kwargs):
        if "www.animeunity.co" in url:
            raise requests.ConnectionError("old host unavailable")
        if "streamingcommunityz.dad" in url:
            return Response('<a href="https://animeunity.cx/">AnimeUnity</a>', url=url)
        return Response(modern["home"], url=url)

    modern["routes"]["/"] = root
    assert "data-page" in au.fetch_home_html()
    assert au.host() == "animeunity.cx"
    assert len(modern["calls"]) == 3


def test_retired_404_host_uses_the_published_link_once(modern):
    config.update_data({"animeunity_domain": "www.animeunity.co", "domain": "streamingcommunityz.dad"})

    def root(url, kwargs):
        if "www.animeunity.co" in url:
            return Response(status=404)
        if "streamingcommunityz.dad" in url:
            return Response('<a href="https://animeunity.cx/">AnimeUnity</a>', url=url)
        return Response(modern["home"], url=url)

    modern["routes"]["/"] = root
    au.fetch_home_html()
    assert au.host() == "animeunity.cx" and len(modern["calls"]) == 3


def test_cloudflare_refusal_does_not_trigger_domain_hopping(modern):
    modern["routes"]["/"] = Response(status=403)
    with pytest.raises(requests.HTTPError):
        au.fetch_home_html()
    assert len(modern["calls"]) == 1


def test_simultaneous_initial_queries_share_host_detection(modern):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: au._modern_site(), range(8))) == [True] * 8
    assert len(modern["calls"]) == 1


def test_discovery_refuses_lookalike_domains_and_private_redirects(modern):
    modern["routes"]["/"] = Response('<a href="https://animeunity.attacker.test/">AnimeUnity</a>')
    assert au._discover_host() is None
    modern["routes"]["/"] = Response(status=302, headers={"Location": "https://127.0.0.1/"})
    with pytest.raises(RuntimeError, match="non pubblico"):
        au.fetch_home_html()
    assert len(modern["calls"]) == 2


def test_source_failure_is_visible_and_never_cached_as_a_success(client, modern):
    modern["home"] = '<html><title>AnimeUnity</title>Changed page</html>'
    response = client.get('/api/home?source=animeunity')
    assert response.status_code == 502
    assert home._cached(("animeunity", au.host())) is None
    modern["home"] = page(modern["props"])
    response = client.get('/api/home?source=animeunity')
    assert response.status_code == 200
    assert response.json()["shelves"][0]["items"][0]["source"] == "animeunity"


def test_anime_catalogue_remains_independent_of_streamingcommunity(client, modern):
    config.update_data({"domain": ""})
    response = client.get('/api/home?source=animeunity')
    assert response.status_code == 200
    assert response.json()["shelves"][1]["items"][0]["id"] == "112-black-clover"
    assert all(urlparse(url).hostname == "animeunity.cx" for url, _ in modern["calls"])


def setup_episodes(modern, total=170, loaded_start=1):
    rows = []
    for number in range(1, total + 1):
        row = copy.deepcopy(modern["raw"]["episode"])
        row.update(id=number + 3000, number=str(number), seq_number_start=number)
        for video in row["videos"]:
            video["id"] += number - 1
        rows.append(row)
    loaded = {"episodes": rows[loaded_start - 1:loaded_start + 119], "start": loaded_start,
              "tab_size": 120, "total": total, "offset": 1}
    props = {"anime": modern["raw"]["anime"], "loadedEpisodes": loaded, "video": {"language": "jpn"}}
    modern["routes"]["/anime/112-black-clover"] = Response(page(props))
    modern["routes"]["/api/anime/112/episodes"] = lambda url, kw: Response(
        payload={"episodes": rows[kw["params"]["start"] - 1:kw["params"]["start"] + 119]})
    return rows, loaded


@pytest.mark.parametrize("start", [1, 121])
def test_modern_episode_api_enumerates_every_batch_once_in_order(modern, start):
    setup_episodes(modern, loaded_start=start)
    rows = au.get_episodes("112-black-clover")
    assert [r["number"] for r in rows] == [str(n) for n in range(1, 171)]
    assert rows[0]["id"] == 10242  # video id, not episode entity id 3767
    assert len(modern["calls"]) == 3


def test_episode_language_and_hidden_videos_are_not_mixed(client, modern):
    rows, _ = setup_episodes(modern, total=3)
    rows[1]["videos"][0]["is_hidden"] = True
    # The fake response is rendered now, after marking the Italian video hidden.
    modern["routes"]["/anime/112-black-clover"] = Response(page({
        "anime": modern["raw"]["anime"], "video": {"language": "jpn"},
        "loadedEpisodes": {"episodes": rows, "total": 3, "start": 1, "offset": 1, "tab_size": 120}}))
    response = client.get('/api/anime/112-black-clover/episodes?source=animeunity&language=ita')
    assert response.status_code == 200
    assert [r["number"] for r in response.json()] == ["1", "3"]
    assert {r["language"] for r in response.json()} == {"ita"}
    assert client.get('/api/anime/112-black-clover/episodes?language=../../bad').status_code == 422


def test_an_episode_batch_failure_is_not_a_partial_success(modern):
    setup_episodes(modern)
    modern["routes"]["/api/anime/112/episodes"] = Response(status=503)
    with pytest.raises(requests.HTTPError):
        au.get_episodes("112-black-clover")


def test_whole_anime_download_uses_the_same_variant_as_its_card(client, modern, stub_jobs):
    rows, _ = setup_episodes(modern, total=3)
    response = client.post('/api/download/anime-all', json={
        "source": "animeunity", "anime_id": "112-black-clover", "anime_name": "Black Clover",
        "language": "ita", "anime_type": "tv"})
    assert response.status_code == 202, response.text
    assert response.json()["count"] == 3
    assert [args[1]["id"] for _, args, _ in stub_jobs] == [r["videos"][0]["id"] for r in rows]


def test_modern_embed_is_resolved_fresh_from_the_selected_video(modern):
    modern["routes"]["/anime/112-black-clover/10242"] = Response(page({
        "embedUrl": "https://vixcloud.example.test/embed/218897?token=fixture"}))
    modern["routes"]["/embed/218897"] = Response('<body><script>window.video={};</script></body>')
    script, url = au._get_embed_content(10242, anime_id="112-black-clover")
    assert script == "window.video={};"
    assert url.startswith("https://vixcloud.example.test/embed/218897")
    assert not any('/embed-url/' in url for url, _ in modern["calls"])


def test_a_legacy_403_is_not_retried_as_expired_csrf(modern):
    modern["home"] = '<meta name="csrf-token" content="fixture">'
    calls = []

    class Session:
        def get(self, url, **kwargs):
            return Response(modern["home"])

        def post(self, url, **kwargs):
            calls.append(url)
            result = Response(status=403)
            result.ok = False
            return result

    # Reuse the fixture's DNS guard, but exercise the retired site's protocol.
    from unittest.mock import patch
    with patch.object(au, '_get_scraper', return_value=Session()):
        with pytest.raises(RuntimeError, match="403"):
            au.search("Black Clover")
    assert len(calls) == 1
