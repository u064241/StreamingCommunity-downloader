"""RaiPlay as a metadata-only source: offline, from anonymised local fixtures.

No test here reaches the network: every client gets a fake transport, and an
autouse guard makes any real ``requests`` call fail the test.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from app.providers import ProviderError, ProviderErrorCode, ProviderRegistry, create_default_registry, get_registry
from app.providers.contracts import NOT_DOWNLOADABLE_REASON, TitleDetails
from app.providers.raiplay import RaiPlayProvider
from app.providers.raiplay import client as rp_client
from app.providers.raiplay import parser
from app.providers.raiplay.client import RaiPlayClient
from tests.conftest import enable_open_mode

FIXTURES = Path(__file__).parent / "fixtures" / "raiplay"
STREAM_MARKERS = ("relinker", "mediapolis", "udp://", ".m3u8", ".mpd", "content_url", "video_url", "license")


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload=None, status_code=200, content=None):
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.content = content if content is not None else json.dumps(payload).encode("utf-8")


class FakeTransport:
    """Routes by path to a fixture; records every call."""

    def __init__(self, routes=None, failure=None):
        self.routes = routes or {
            "/index.json": "home.json",
            "/atomatic/raiplay-search-service/api/v1/msearch": "search.json",
            "/programmi/serieesempio.json": "program_series.json",
            "/programmi/filmesempio.json": "program_film.json",
        }
        self.failure = failure
        self.calls = []

    def __call__(self, method, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.failure is not None:
            if isinstance(self.failure, Exception):
                raise self.failure
            return self.failure
        path = url.removeprefix(rp_client.BASE_URL)
        if path not in self.routes:
            return FakeResponse({"status": 404}, status_code=404)
        return FakeResponse(fixture(self.routes[path]))


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("Real network access in a RaiPlay test")

    monkeypatch.setattr(requests, "request", refuse)
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def provider(transport):
    return RaiPlayProvider(client=RaiPlayClient(transport=transport, sleep=lambda s: None))


def assert_no_stream(value):
    text = json.dumps(value, ensure_ascii=False).lower()
    for marker in STREAM_MARKERS:
        assert marker not in text, marker


# ── capabilities ─────────────────────────────────────────────────────────────

def test_capabilities_are_metadata_only():
    caps = RaiPlayProvider.capabilities
    assert caps.search and caps.home and caps.details
    assert not caps.download and not caps.tracks and not caps.episodes and not caps.dubbed_filter


def test_stream_is_explicitly_refused(provider, transport):
    with pytest.raises(ProviderError) as caught:
        provider.stream("programmi:filmesempio")
    assert caught.value.code is ProviderErrorCode.UNSUPPORTED
    assert str(caught.value) == NOT_DOWNLOADABLE_REASON
    assert transport.calls == []


def test_raiplay_is_registered_by_default_and_never_downloadable():
    registry = create_default_registry()
    assert registry.sources() == ("streamingcommunity", "animeunity", "raiplay")
    assert get_registry().sources() == ("streamingcommunity", "animeunity", "raiplay")
    assert registry.is_downloadable("raiplay") is False
    assert registry.is_downloadable("streamingcommunity") is True
    assert registry.is_downloadable("animeunity") is True
    # Fail-closed for anything else.
    assert registry.is_downloadable("pilot") is False
    assert registry.is_downloadable(None) is False
    assert registry.is_downloadable(["streamingcommunity"]) is False
    # The explicit opt-out still exists for callers that want only engines.
    assert create_default_registry(include_metadata_sources=False).sources() == (
        "streamingcommunity", "animeunity")


def test_importing_the_provider_loads_no_http_library_and_makes_no_request():
    script = """
import sys
from app.providers import create_default_registry
r = create_default_registry(include_metadata_sources=True)
assert 'raiplay' in r.sources()
assert 'requests' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], check=True)


# ── search ───────────────────────────────────────────────────────────────────

def test_search_returns_minimal_cards_marked_not_downloadable(provider):
    rows = [r.to_legacy() for r in provider.search("esempio")]
    assert [r["id"] for r in rows] == ["programmi:serieesempio", "collezioni:raccoltaesempio"]
    first = rows[0]
    assert first["name"] == "Serie Esempio"
    assert first["source"] == "raiplay"
    assert first["type"] == "tv"
    assert first["poster"] == "https://www.raiplay.it/dl/img/2026/01/01/0000000000002_1536x2048_label.jpg"
    assert first["page_url"] == "https://www.raiplay.it/programmi/serieesempio"
    assert first["downloadable"] is False
    assert first["download_disabled_reason"] == "Contenuto non scaricabile dal pannello"
    assert rows[1]["name"] == "Raccolta Esempio"
    assert_no_stream(rows)


def test_search_request_shape_is_polite_and_paged(provider, transport):
    provider.search("  esempio ", page=3)
    (call,) = transport.calls
    assert call["method"] == "POST"
    assert call["url"] == "https://www.raiplay.it/atomatic/raiplay-search-service/api/v1/msearch"
    assert call["timeout"] == 10
    assert call["headers"]["User-Agent"] == rp_client.USER_AGENT
    assert call["json"]["params"]["param"] == "esempio"
    assert call["json"]["params"]["from"] == 60
    assert call["json"]["params"]["onlyProgramsQuery"] is True


@pytest.mark.parametrize("kwargs", [{"media_type": "movie"}, {"dubbed": True}])
def test_unsupported_filters_are_refused_before_any_request(provider, transport, kwargs):
    with pytest.raises(ProviderError) as caught:
        provider.search("x", **kwargs)
    assert caught.value.code is ProviderErrorCode.UNSUPPORTED
    assert transport.calls == []


@pytest.mark.parametrize("query,page", [("", 1), ("   ", 1), ("x" * 201, 1), ("x", 0), ("x", 51)])
def test_invalid_search_is_refused(provider, transport, query, page):
    with pytest.raises(ProviderError) as caught:
        provider.search(query, page=page)
    assert caught.value.code is ProviderErrorCode.INVALID_REQUEST
    assert transport.calls == []


# ── home ─────────────────────────────────────────────────────────────────────

def test_home_keeps_only_catalogue_cards_and_safe_genres(provider):
    home = provider.home().to_legacy()
    assert home["source"] == "raiplay"
    assert [s["title"] for s in home["shelves"]] == ["Hero", "Film"]
    hero, film = (s["items"] for s in home["shelves"])
    assert [i["id"] for i in hero] == ["programmi:serieesempio"]
    assert [i["id"] for i in film] == ["programmi:filmesempio"]
    assert film[0]["type"] == "movie" and hero[0]["type"] == "tv"
    assert hero[0]["year"] == "2020"
    assert hero[0]["backdrop"].startswith("https://www.raiplay.it/dl/img/")
    assert all(i["downloadable"] is False for s in home["shelves"] for i in s["items"])
    assert home["genres"] == [{"id": "film", "name": "Film"}, {"id": "serieitaliane", "name": "Serie italiane"}]
    assert_no_stream(home)


# ── details ──────────────────────────────────────────────────────────────────

def test_series_details_have_the_minimum_card_fields(provider):
    details = provider.details("programmi:serieesempio")
    assert isinstance(details, TitleDetails)
    card = details.to_legacy()
    assert card["id"] == "programmi:serieesempio" and card["source"] == "raiplay" and card["type"] == "tv"
    assert card["name"] == "Serie Esempio"
    assert card["description"].startswith("Una dottoressa immaginaria")
    assert card["year"] == "2020"
    assert card["genres"] == ["Drammatico", "Fiction Italiana"]
    assert card["duration"] is None
    assert card["poster"] == "https://www.raiplay.it/dl/img/2026/01/01/0000000000002_1536x2048.jpg"
    assert card["backdrop"] == "https://www.raiplay.it/dl/img/2026/01/01/0000000000001_2048x1152.jpg"
    assert card["seasons"] == [{"name": "Stagione 2", "episodes": 12}, {"name": "Stagione 1", "episodes": 16}]
    assert card["typology"] == "Fiction" and card["subgenres"] == ["medical"]
    assert card["downloadable"] is False
    assert card["download_disabled_reason"] == "Contenuto non scaricabile dal pannello"
    assert_no_stream(card)


def test_film_details_carry_a_running_time(provider):
    card = provider.details("programmi:filmesempio").to_legacy()
    assert card["type"] == "movie"
    assert card["duration"] == "1h 33min"
    assert card["year"] == "2025" and card["genres"] == ["Drammatico"]
    assert card["seasons"] == []
    assert_no_stream(card)


@pytest.mark.parametrize("bad", ["", "programmi", "programmi:../x", "video:abc", "dirette:rai1",
                                 "https://evil.example/x", "programmi:ABC", "programmi:a/b", None, 7])
def test_unsafe_identifiers_never_reach_the_network(provider, transport, bad):
    with pytest.raises(ProviderError) as caught:
        provider.details(bad)
    assert caught.value.code is ProviderErrorCode.INVALID_REQUEST
    assert transport.calls == []


def test_client_refuses_paths_outside_the_catalogue(transport):
    client = RaiPlayClient(transport=transport, sleep=lambda s: None)
    for path in ("/video/2026/x.json", "/relinker/x", "//evil.example/x.json", "/programmi/../x.json"):
        with pytest.raises(ValueError):
            client.title(path)
    assert transport.calls == []


def test_details_registry_contract(transport):
    registry = ProviderRegistry()
    registry.register(RaiPlayProvider(client=RaiPlayClient(transport=transport, sleep=lambda s: None)))
    assert registry.details("raiplay", "programmi:filmesempio").title == "Film Esempio"
    with pytest.raises(ProviderError) as caught:
        registry.details("raiplay", "programmi:inesistente")
    assert caught.value.code is ProviderErrorCode.UPSTREAM


def test_registry_rejects_details_that_claim_a_download():
    from app.providers.contracts import ContentRef, ProviderCapabilities

    class Liar:
        source = "liar"
        capabilities = ProviderCapabilities(details=True)
        def search(self, *a, **k): return []
        def details(self, ext): return TitleDetails(ContentRef("liar", ext, "movie"), "X", downloadable=True)

    registry = ProviderRegistry()
    registry.register(Liar())
    with pytest.raises(ProviderError) as caught:
        registry.details("liar", "1")
    assert caught.value.code is ProviderErrorCode.UPSTREAM


# ── network failures ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("failure", [
    requests.ConnectionError("dns down"),
    requests.Timeout("slow"),
    OSError("socket"),
    FakeResponse({"error": "x"}, status_code=500),
    FakeResponse({"error": "x"}, status_code=302),
    FakeResponse(content=b"<html>not json</html>"),
    FakeResponse(content=b"[1, 2, 3]"),
    FakeResponse(content=b"{" + b" " * (rp_client.MAX_BYTES + 1) + b"}"),
])
@pytest.mark.parametrize("operation", ["search", "home", "details"])
def test_network_errors_become_upstream_errors_without_crash(failure, operation):
    provider = RaiPlayProvider(client=RaiPlayClient(transport=FakeTransport(failure=failure), sleep=lambda s: None))
    call = {"search": lambda: provider.search("x"), "home": provider.home,
            "details": lambda: provider.details("programmi:filmesempio")}[operation]
    with pytest.raises(ProviderError) as caught:
        call()
    assert caught.value.code is ProviderErrorCode.UPSTREAM
    assert "dns down" not in str(caught.value)


@pytest.mark.parametrize("payload", [{}, {"agg": None}, {"agg": {"titoli": {"cards": "x"}}}])
def test_malformed_search_payload_is_upstream_not_empty(payload):
    transport = FakeTransport(failure=FakeResponse(payload))
    provider = RaiPlayProvider(client=RaiPlayClient(transport=transport, sleep=lambda s: None))
    with pytest.raises(ProviderError) as caught:
        provider.search("x")
    assert caught.value.code is ProviderErrorCode.UPSTREAM


def test_default_transport_disables_redirects_and_uses_timeout(monkeypatch):
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(kwargs, method=method, url=url)
        return FakeResponse(fixture("home.json"))

    monkeypatch.setattr(requests, "request", fake_request)
    RaiPlayClient(sleep=lambda s: None).home()
    assert seen["url"] == "https://www.raiplay.it/index.json"
    assert seen["allow_redirects"] is False and seen["timeout"] == 10


# ── cache and throttling ─────────────────────────────────────────────────────

def test_responses_are_cached_and_requests_spaced():
    now = [100.0]
    sleeps = []
    transport = FakeTransport()
    client = RaiPlayClient(transport=transport, clock=lambda: now[0], sleep=sleeps.append, min_interval=2.0, ttl=60)
    client.home(); client.home()
    assert len(transport.calls) == 1
    client.title("/programmi/filmesempio.json")
    assert len(transport.calls) == 2 and sleeps == [2.0]
    now[0] += 61
    client.home()
    assert len(transport.calls) == 3


# ── parser guard ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("leak", [
    {"video_url": "x"}, {"nested": [{"content_url": "x"}]},
    {"poster": "https://mediapolisvod.rai.it/relinker/relinkerServlet.htm?cont=1"},
    {"poster": "https://evil.example/img.jpg"},
])
def test_sanitize_refuses_any_stream_or_foreign_url(leak):
    with pytest.raises(ValueError):
        parser.sanitize(leak)


def test_text_fields_carrying_stream_addresses_are_dropped():
    payload = fixture("program_film.json")
    payload["program_info"]["description"] = "vedi https://mediapolisvod.rai.it/relinker/x"
    card = parser.title_details("programmi:filmesempio", payload).to_legacy()
    assert card["description"] == "Un film di esempio"
    assert_no_stream(card)


# ── through the HTTP routes (registry override, no network) ──────────────────

@pytest.fixture
def raiplay_registry(client, transport):
    enable_open_mode()
    registry = create_default_registry(include_metadata_sources=False)
    registry.register(RaiPlayProvider(client=RaiPlayClient(transport=transport, sleep=lambda s: None)))
    client.app.dependency_overrides[get_registry] = lambda: registry
    yield registry
    client.app.dependency_overrides.pop(get_registry, None)


def test_search_and_home_routes_serve_raiplay_cards(client, raiplay_registry):
    response = client.get("/api/search", params={"q": "esempio", "source": "raiplay"})
    assert response.status_code == 200
    rows = response.json()
    assert rows[0]["source"] == "raiplay" and rows[0]["downloadable"] is False
    assert_no_stream(rows)

    response = client.get("/api/home", params={"source": "raiplay"})
    assert response.status_code == 200
    assert_no_stream(response.json())


def test_route_maps_raiplay_outage_to_502(client, raiplay_registry):
    raiplay_registry.get("raiplay").client._transport = FakeTransport(failure=requests.ConnectionError("x"))
    assert client.get("/api/search", params={"q": "zz", "source": "raiplay"}).status_code == 502
