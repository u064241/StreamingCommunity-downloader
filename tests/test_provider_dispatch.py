"""Offline coverage of registry dispatch, API compatibility and partial data."""
from unittest.mock import Mock

import pytest

from app.providers import (
    AnimeUnityProvider, HomeResult, ProviderCapabilities, ProviderError,
    ProviderErrorCode, ProviderRegistry, SearchResult, StreamingCommunityProvider,
    create_default_registry, get_registry,
)
from tests.conftest import enable_open_mode


def title(n=1, **extra):
    return {"id": n, "name": f"Title {n}", "type": "movie", "poster": "p.jpg",
            "backdrop": "b.jpg", "slug": "a-title", **extra}


class PilotProvider:
    source = "pilot"
    capabilities = ProviderCapabilities(home=True)

    def __init__(self):
        self.search_calls = []
        self.home_calls = 0
        self.failure = None

    def search(self, query, **filters):
        self.search_calls.append((query, filters))
        if self.failure:
            raise self.failure
        return [SearchResult.from_legacy(self.source, title())]

    def home(self):
        self.home_calls += 1
        if self.failure:
            raise self.failure
        return HomeResult.from_legacy(self.source, [
            {"key": "featured", "title": "Featured", "items": [title()]}
        ])


@pytest.fixture
def registry(client):
    enable_open_mode()
    registry = create_default_registry()
    client.app.dependency_overrides[get_registry] = lambda: registry
    yield registry
    client.app.dependency_overrides.pop(get_registry, None)


def test_registered_provider_is_reachable_through_both_routers(client, registry):
    pilot = PilotProvider()
    registry.register(pilot)
    response = client.get("/api/search?q=abc&source=pilot&page=3&media_type=movie")
    assert response.status_code == 200
    assert response.json() == [title(source="pilot")]
    assert pilot.search_calls == [("abc", {"page": 3, "media_type": "movie", "dubbed": False})]

    response = client.get("/api/home?source=pilot")
    assert response.status_code == 200
    assert response.json() == {"source": "pilot", "genres": [], "shelves": [
        {"key": "featured", "title": "Featured", "items": [title(source="pilot")]}
    ]}
    assert pilot.home_calls == 1


@pytest.mark.parametrize("source", ["missing", "AnimeUnity", "app.core.page", "https://example.test", "../pilot"])
@pytest.mark.parametrize("path", ["/api/search?q=abc&", "/api/home?"])
def test_unknown_source_never_calls_a_default_provider(client, registry, monkeypatch, source, path):
    for name in registry.sources():
        provider = registry.get(name)
        monkeypatch.setattr(provider, "search", Mock(side_effect=AssertionError("Unexpected search")))
        monkeypatch.setattr(provider, "home", Mock(side_effect=AssertionError("Unexpected home")))
    response = client.get(path, params={"q": "abc", "source": source})
    assert response.status_code == 422
    assert "Unknown provider" in response.json()["detail"]
    for name in registry.sources():
        registry.get(name).search.assert_not_called()
        registry.get(name).home.assert_not_called()


@pytest.mark.parametrize("path", ["/api/search?q=abc&source=pilot", "/api/home?source=pilot"])
def test_unexpected_provider_error_is_a_502_without_retry_or_fallback(client, registry, path):
    pilot = PilotProvider()
    pilot.failure = RuntimeError("private upstream details")
    registry.register(pilot)
    response = client.get(path)
    assert response.status_code == 502
    assert "private upstream details" not in response.text
    assert len(pilot.search_calls) + pilot.home_calls == 1


@pytest.mark.parametrize("code,status", [
    (ProviderErrorCode.NOT_CONFIGURED, 409),
    (ProviderErrorCode.INVALID_REQUEST, 422),
    (ProviderErrorCode.UNSUPPORTED, 422),
    (ProviderErrorCode.UPSTREAM, 502),
])
@pytest.mark.parametrize("path", ["/api/search?q=abc&source=pilot", "/api/home?source=pilot"])
def test_typed_errors_keep_their_http_meaning(client, registry, path, code, status):
    pilot = PilotProvider()
    pilot.failure = ProviderError("pilot", code, "Expected failure")
    registry.register(pilot)
    response = client.get(path)
    assert response.status_code == status
    assert response.json()["detail"] == "Expected failure"


def test_bad_provider_does_not_break_a_different_registered_source(client, registry):
    pilot = PilotProvider()
    pilot.failure = RuntimeError("down")
    registry.register(pilot)
    registry.get("animeunity")._search_fn = Mock(return_value=[title(type="anime")])
    assert client.get("/api/search?q=abc&source=pilot").status_code == 502
    response = client.get("/api/search?q=abc&source=animeunity")
    assert response.status_code == 200
    assert response.json()[0]["source"] == "animeunity"


def test_partial_search_preserves_valid_rows_and_legacy_identifiers(client, registry):
    rows = [title(0), {}, None, title("7-anime", type="anime"), title(3, type=None)]
    registry.get("animeunity")._search_fn = Mock(return_value=rows)
    response = client.get("/api/search?q=abc&source=animeunity")
    assert response.status_code == 200
    assert response.json() == [title(0, source="animeunity"), title("7-anime", type="anime", source="animeunity")]
    assert "source" not in rows[0]


def test_partial_home_preserves_valid_shelves_genres_and_source(client, registry, monkeypatch):
    from app.core import home

    shelves = [None, {"key": "broken", "items": None},
               {"key": "featured", "title": "Featured", "items": [title(), {}, None]}]
    genres = [{"id": 2, "name": "Drama"}]
    monkeypatch.setattr(home, "shelves", lambda source, host: shelves)
    monkeypatch.setattr(home, "_cached", lambda key: genres)
    response = client.get("/api/home")
    assert response.status_code == 200
    assert response.json() == {"source": "streamingcommunity", "genres": genres, "shelves": [
        {"key": "featured", "title": "Featured", "items": [title(source="streamingcommunity")]}
    ]}
    assert "source" not in shelves[2]["items"][0]


@pytest.mark.parametrize("source", ["streamingcommunity", "animeunity"])
def test_search_keeps_legacy_fields_and_tags_source(client, registry, source):
    row = title(source="spoofed", extra_field={"keep": True})
    registry.get(source)._search_fn = Mock(return_value=[row])
    response = client.get("/api/search", params={"q": "abc", "source": source})
    assert response.status_code == 200
    assert response.json() == [{**row, "source": source}]
    assert row["source"] == "spoofed"


def test_empty_search_and_home_are_successful(client, registry, monkeypatch):
    from app.core import home

    registry.get("animeunity")._search_fn = Mock(return_value=[])
    monkeypatch.setattr(home, "shelves", lambda *a: [])
    assert client.get("/api/search?q=abc&source=animeunity").json() == []
    assert client.get("/api/home?source=animeunity").json() == {
        "source": "animeunity", "shelves": [], "genres": []}


def test_registry_rejects_duplicate_without_replacing_original():
    registry = ProviderRegistry()
    original = PilotProvider()
    registry.register(original)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(PilotProvider())
    assert registry.get("pilot") is original


@pytest.mark.parametrize("source", ["", " ", "Pilot", "../pilot", "app.module", "https://host"])
def test_registry_rejects_invalid_identifiers(source):
    pilot = PilotProvider()
    pilot.source = source
    with pytest.raises(ValueError):
        ProviderRegistry().register(pilot)


def test_registry_rejects_nonproviders_and_false_contracts():
    with pytest.raises(TypeError):
        ProviderRegistry().register(object())
    pilot = PilotProvider()
    pilot.search = None
    with pytest.raises(TypeError):
        ProviderRegistry().register(pilot)
    pilot = PilotProvider()
    pilot.home = None
    with pytest.raises(TypeError):
        ProviderRegistry().register(pilot)


def test_registries_are_independent_and_default_capabilities_are_honest():
    first, second = create_default_registry(), create_default_registry()
    first.register(PilotProvider())
    assert "pilot" not in second.sources()
    assert first.get("animeunity") is not second.get("animeunity")
    assert first.get("animeunity").capabilities.home
    assert not first.get("animeunity").capabilities.download


def test_search_only_provider_has_no_home(client, registry):
    pilot = PilotProvider()
    pilot.capabilities = ProviderCapabilities()
    registry.register(pilot)
    assert client.get("/api/search?q=x&source=pilot").status_code == 200
    assert client.get("/api/home?source=pilot").status_code == 422
    assert pilot.home_calls == 0


def test_disabled_search_is_refused_before_calling_provider(client, registry):
    pilot = PilotProvider()
    pilot.capabilities = ProviderCapabilities(search=False, home=True)
    registry.register(pilot)
    assert client.get("/api/search?q=x&source=pilot").status_code == 422
    assert pilot.search_calls == []
    assert client.get("/api/home?source=pilot").status_code == 200


@pytest.mark.parametrize("operation,payload", [
    ("search", None),
    ("search", [title()]),
    ("search", [SearchResult.from_legacy("other", title())]),
    ("home", {"shelves": []}),
    ("home", HomeResult("other", [])),
])
def test_bad_contract_output_is_reported_as_upstream_error(operation, payload):
    pilot = PilotProvider()
    setattr(pilot, operation, Mock(return_value=payload))
    registry = ProviderRegistry()
    registry.register(pilot)
    with pytest.raises(ProviderError) as caught:
        if operation == "search":
            registry.search("pilot", "x")
        else:
            registry.home("pilot")
    assert caught.value.code is ProviderErrorCode.UPSTREAM
    getattr(pilot, operation).assert_called_once()


def test_home_adapter_reports_thrown_errors_without_retry(monkeypatch):
    from app.core import home

    backend = Mock(side_effect=RuntimeError("upstream failed"))
    monkeypatch.setattr(home, "shelves", backend)
    with pytest.raises(ProviderError) as caught:
        StreamingCommunityProvider(domain_getter=lambda: "source.test").home()
    assert caught.value.code is ProviderErrorCode.UPSTREAM
    backend.assert_called_once_with("streamingcommunity", "source.test")


def test_registry_loading_does_not_import_backends_or_start_source_requests():
    import subprocess
    import sys
    from pathlib import Path

    script = """
import sys
from app.providers import get_registry
assert get_registry().sources() == ('streamingcommunity', 'animeunity')
assert not {'app.config', 'app.core.page', 'app.core.animeunity', 'app.core.home', 'requests'} & sys.modules.keys()
"""
    subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], check=True)


@pytest.mark.parametrize("payload", [None, {"items": []}, "broken"])
def test_invalid_search_payload_is_reported_not_misread_as_no_results(payload):
    backend = Mock(return_value=payload)
    with pytest.raises(ProviderError) as caught:
        AnimeUnityProvider(search_fn=backend).search("abc")
    assert caught.value.code is ProviderErrorCode.UPSTREAM
    backend.assert_called_once()


def test_adapters_keep_legacy_call_signatures_and_resolve_domain_on_each_use():
    backend = Mock(return_value=[title()])
    domains = iter(["first.test", "second.test"])
    provider = StreamingCommunityProvider(search_fn=backend, domain_getter=lambda: next(domains))
    assert provider.search("abc", page=3, media_type="movie")[0].to_legacy() == title(source="streamingcommunity")
    provider.search("abc")
    assert backend.call_args_list[0].args == ("abc", "first.test")
    assert backend.call_args_list[0].kwargs == {"page": 3, "media_type": "movie"}
    assert backend.call_args.args == ("abc", "second.test")
