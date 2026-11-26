"""RaiPlay genre (typology) browsing: shelves only, validated path, no DRM check here."""
import asyncio
import json
from pathlib import Path

import pytest
import requests
from fastapi import HTTPException

from app.providers import ProviderError
from app.providers.raiplay import RaiPlayProvider
from app.providers.raiplay.client import RaiPlayClient
from app.providers.contracts import ProviderErrorCode
from app.routers import home as home_router
from tests.test_raiplay_provider import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures" / "raiplay"


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("Real network access in a RaiPlay test")

    monkeypatch.setattr(requests, "request", refuse)
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)


@pytest.fixture
def transport():
    routes = {"/tipologia/documentari/index.json": "genre_documentari.json"}
    return FakeTransport(routes=routes)


@pytest.fixture
def provider(transport):
    return RaiPlayProvider(client=RaiPlayClient(transport=transport, sleep=lambda s: None))


def test_genre_home_returns_its_shelves_and_no_genre_list(provider, transport):
    body = provider.home(genre="documentari").to_legacy()
    assert [shelf["title"] for shelf in body["shelves"]] == ["Storia"]
    assert len(body["shelves"][0]["items"]) == 3
    assert body.get("genres") == []
    assert transport.calls[-1]["url"].endswith("/tipologia/documentari/index.json")


@pytest.mark.parametrize("bad", ["", "Storia", "../home", "a/b", "x" * 81, "storia?x=1"])
def test_invalid_genre_is_refused_before_any_request(provider, transport, bad):
    with pytest.raises(ProviderError) as info:
        provider.home(genre=bad)
    assert info.value.code == ProviderErrorCode.INVALID_REQUEST
    assert transport.calls == []


def test_client_refuses_non_genre_paths(transport):
    client = RaiPlayClient(transport=transport, sleep=lambda s: None)
    for path in ("/programmi/x.json", "/tipologia/storia/index.json/../../x", "/video/x.json"):
        with pytest.raises(ValueError):
            client.genre(path)
    assert transport.calls == []


def test_genre_is_refused_for_other_sources():
    with pytest.raises(HTTPException) as info:
        asyncio.run(home_router.home(source="streamingcommunity", genre="storia", registry=None, response=None))
    assert info.value.status_code == 422
    with pytest.raises(HTTPException) as info:
        asyncio.run(home_router.home(source="all", genre="storia", registry=None, response=None))
    assert info.value.status_code == 422


def test_fixture_is_the_real_shape():
    payload = json.loads((FIXTURES / "genre_documentari.json").read_text(encoding="utf-8"))
    assert payload["contents"][0]["type"] == "RaiPlay Slider Block"
