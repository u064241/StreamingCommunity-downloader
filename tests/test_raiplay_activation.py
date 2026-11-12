"""RaiPlay switched on as a metadata-only source, with the download gate closed.

RaiPlay is in the default registry (search, home, title cards) and is never
downloadable: every download and request entry point refuses it with 422
before a job or a request exists. No test reaches the network: RaiPlay gets a
fake transport, the other sources fake search functions.
"""
import asyncio

import pytest
import requests

from app import db
from app.jobs import job_manager
from app.providers import (
    AnimeUnityProvider, ProviderRegistry, StreamingCommunityProvider, get_registry,
)
from app.providers.contracts import NOT_DOWNLOADABLE_REASON
from app.providers.raiplay import RaiPlayProvider
from app.providers.raiplay.client import RaiPlayClient
from tests.test_raiplay_provider import FakeTransport, assert_no_stream


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("Real network access in a RaiPlay test")

    monkeypatch.setattr(requests, "request", refuse)
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)


@pytest.fixture
def no_jobs(monkeypatch):
    """Every way of creating a job fails the test if it is reached."""
    calls = []

    def refuse(name):
        def _refuse(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} reached for a non-downloadable source")
        return _refuse

    for name in ("submit_film", "submit_episode", "submit_anime_episode",
                 "schedule_film", "schedule_episode", "schedule_anime_episode"):
        monkeypatch.setattr(job_manager, name, refuse(name))
    before = set(job_manager._jobs)
    yield calls
    assert calls == []
    assert set(job_manager._jobs) == before


def _rp(transport=None):
    return RaiPlayProvider(client=RaiPlayClient(transport=transport or FakeTransport(), sleep=lambda s: None))


# ── registry ─────────────────────────────────────────────────────────────────

def test_raiplay_is_in_the_default_registry_and_not_downloadable():
    registry = get_registry()
    assert "raiplay" in registry.sources()
    assert registry.get("raiplay").capabilities.download is False
    assert registry.is_downloadable("raiplay") is False
    assert registry.is_downloadable("streamingcommunity") is True
    assert registry.is_downloadable("animeunity") is True


# ── download gate ────────────────────────────────────────────────────────────

FILM = {"id": 1, "title": "Film Esempio"}
DOWNLOAD_BODIES = [
    ("/api/download/film", FILM),
    ("/api/download/schedule/film", {**FILM, "scheduled_at": "2030-01-01T10:00:00"}),
    ("/api/download/episode", {"tv_id": 1, "eps": [{"id": 1, "n": "1"}], "ep_index": 0,
                               "token": "t", "tv_name": "Serie", "season": 1}),
    ("/api/download/season", {"tv_id": 1, "slug": "serie", "tv_name": "Serie", "season": 1}),
    ("/api/download/series", {"tv_id": 1, "slug": "serie", "tv_name": "Serie"}),
    ("/api/download/anime", {"anime_id": "1-x", "episode": {"id": 1, "number": "1"},
                             "anime_name": "Anime"}),
    ("/api/download/anime-all", {"anime_id": "1-x", "anime_name": "Anime"}),
]


@pytest.mark.parametrize("path,body", DOWNLOAD_BODIES)
def test_a_raiplay_download_is_refused_with_422_and_no_job(client, no_jobs, path, body):
    response = client.post(path, json={**body, "source": "raiplay"})
    assert response.status_code == 422, response.text
    assert NOT_DOWNLOADABLE_REASON in response.json()["detail"]


@pytest.mark.parametrize("source", ["pilot", "RaiPlay", "", None, 1, ["raiplay"], {"x": 1}])
def test_any_other_source_is_refused_too(client, no_jobs, source):
    response = client.post("/api/download/film", json={**FILM, "source": source})
    assert response.status_code == 422, response.text


def test_the_gate_refuses_even_when_the_registry_would_say_otherwise(client, no_jobs, monkeypatch):
    """The source check comes from the registry, not from the route's model:
    a downloadable flag missing on a source closes the gate for it."""
    monkeypatch.setattr(type(get_registry()), "is_downloadable", lambda self, source: False)
    response = client.post("/api/download/film", json={**FILM, "source": "streamingcommunity"})
    assert response.status_code == 422
    assert NOT_DOWNLOADABLE_REASON in response.json()["detail"]


def test_a_streamingcommunity_download_still_passes_the_gate(client, stub_jobs):
    response = client.post("/api/download/film", json={**FILM, "source": "streamingcommunity"})
    assert response.status_code == 202, response.text
    assert [name for name, *_ in stub_jobs] == ["submit_film"]


def _request_rows():
    return db.query_one("SELECT COUNT(*) AS n FROM jf_request")["n"]


@pytest.mark.parametrize("path,body", [
    ("/api/requests", {"media_type": "film", "external_id": "programmi:filmesempio",
                       "title": "Film Esempio"}),
    ("/api/requests/season", {"external_id": "programmi:serieesempio", "title": "Serie",
                              "slug": "serieesempio", "season": 1}),
])
def test_a_raiplay_request_is_refused_with_422_and_nothing_stored(client, no_jobs, path, body):
    before = _request_rows()
    response = client.post(path, json={**body, "source": "raiplay"})
    assert response.status_code == 422, response.text
    assert NOT_DOWNLOADABLE_REASON in response.json()["detail"]
    assert _request_rows() == before


def test_the_request_handler_refuses_on_its_own(no_jobs):
    """The MCP tools call the handler directly, past the HTTP dependencies."""
    from fastapi import HTTPException
    from app.requests import router as requests_router

    body = requests_router.CreateRequest.model_construct(
        source="raiplay", media_type="film", external_id="programmi:x", title="X",
        audio_languages=["ita"], subtitle_languages=[])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(requests_router.create_request(body, None))
    assert caught.value.status_code == 422


# ── title card ───────────────────────────────────────────────────────────────

@pytest.fixture
def registry(client):
    registry = ProviderRegistry()
    registry.register(StreamingCommunityProvider(search_fn=lambda q, d, **k: [], domain_getter=lambda: "s.test"))
    registry.register(AnimeUnityProvider(search_fn=lambda q, **k: []))
    transport = FakeTransport()
    registry.register(_rp(transport))
    registry.transport = transport
    client.app.dependency_overrides[get_registry] = lambda: registry
    yield registry
    client.app.dependency_overrides.pop(get_registry, None)


def test_a_raiplay_title_card_has_no_stream_and_says_why(client, registry, no_jobs):
    response = client.get("/api/titles/raiplay/programmi%3Aserieesempio")
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["source"] == "raiplay"
    assert card["id"] == "programmi:serieesempio"
    assert card["downloadable"] is False
    assert card["download_disabled_reason"] == NOT_DOWNLOADABLE_REASON
    assert_no_stream(card)
    # Only the public metadata JSON was read.
    assert [c["url"] for c in registry.transport.calls] == [
        "https://www.raiplay.it/programmi/serieesempio.json"]
    # Posters are absolute RaiPlay URLs, allowed by the CSP's img-src https:.
    if card.get("poster"):
        assert card["poster"].startswith("https://www.raiplay.it/")
    csp = response.headers.get("content-security-policy", "")
    assert "img-src" in csp and "https:" in csp.split("img-src", 1)[1].split(";", 1)[0]


@pytest.mark.parametrize("path,statuses", [
    ("/api/titles/raiplay/..%2F..%2Fetc", (404, 422)),   # never routed or refused
    ("/api/titles/raiplay/programmi%3ANope_Bad", (422,)),
    ("/api/titles/streamingcommunity/1", (422,)),        # no details adapter
    ("/api/titles/nope/1", (422,)),                      # unknown source
])
def test_invalid_title_requests_are_refused(client, registry, path, statuses):
    assert client.get(path).status_code in statuses
    assert registry.transport.calls == []


def test_a_raiplay_outage_on_a_title_card_is_502(client, registry):
    registry.get("raiplay").client._transport = FakeTransport(failure=requests.ConnectionError("x"))
    assert client.get("/api/titles/raiplay/programmi%3Aserieesempio").status_code == 502


# ── unified search ───────────────────────────────────────────────────────────

def _sc_row(**extra):
    return {"id": 7, "name": "Titolo SC", "type": "movie", "slug": "titolo-sc", **extra}


@pytest.fixture
def mixed(client):
    calls = {"sc": [], "au": []}

    def sc(q, domain, *, page, media_type):
        calls["sc"].append(media_type)
        return [_sc_row()]

    def au(q, *, page, media_type, dubbed):
        calls["au"].append(media_type)
        return []

    registry = ProviderRegistry()
    registry.register(StreamingCommunityProvider(search_fn=sc, domain_getter=lambda: "s.test"))
    registry.register(AnimeUnityProvider(search_fn=au))
    transport = FakeTransport()
    registry.register(_rp(transport))
    registry.transport = transport
    client.app.dependency_overrides[get_registry] = lambda: registry
    yield registry, calls
    client.app.dependency_overrides.pop(get_registry, None)


def test_all_sources_includes_raiplay_and_passes_the_type_only_where_supported(client, mixed):
    registry, calls = mixed
    response = client.get("/api/search", params={"q": "esempio", "source": "all", "media_type": "movie"})
    assert response.status_code == 200, response.text
    rows = response.json()
    sources = {row["source"] for row in rows}
    assert {"streamingcommunity", "raiplay"} <= sources
    assert calls["sc"] == ["movie"] and calls["au"] == ["movie"]
    # RaiPlay was asked, without a media type (it declares none).
    assert len(registry.transport.calls) == 1
    assert "X-Source-Errors" not in response.headers
    for row in rows:
        if row["source"] == "raiplay":
            assert row["downloadable"] is False
    assert_no_stream([r for r in rows if r["source"] == "raiplay"])


def test_a_failing_raiplay_does_not_break_the_mixed_search(client, mixed):
    registry, _ = mixed
    registry.get("raiplay").client._transport = FakeTransport(failure=requests.ConnectionError("x"))
    response = client.get("/api/search", params={"q": "esempio", "source": "all"})
    assert response.status_code == 200
    assert [row["source"] for row in response.json()] == ["streamingcommunity"]
    assert response.headers["X-Source-Errors"] == "raiplay=502"
    # Asked on its own, the same failure is a 502.
    assert client.get("/api/search", params={"q": "esempio", "source": "raiplay"}).status_code == 502


def test_a_raiplay_answer_in_bad_shape_is_502_for_raiplay_only(client, mixed):
    from tests.test_raiplay_provider import FakeResponse
    registry, _ = mixed
    registry.get("raiplay").client._transport = FakeTransport(failure=FakeResponse({"unexpected": True}))
    response = client.get("/api/search", params={"q": "esempio", "source": "all"})
    assert response.status_code == 200
    assert response.headers["X-Source-Errors"] == "raiplay=502"
    assert [row["source"] for row in response.json()] == ["streamingcommunity"]
