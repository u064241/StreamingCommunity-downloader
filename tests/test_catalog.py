import pytest
import requests

from app.core import catalog, home, page
from tests.conftest import enable_open_mode, make_user, session_for


@pytest.fixture(autouse=True)
def clean_cache():
    catalog.clear_cache()
    yield
    catalog.clear_cache()


def title(n, **extra):
    return {"id": n, "name": f"Title {n}", "type": "movie", **extra}


def test_genre_mapping_paging_and_source_type(monkeypatch):
    calls = []
    def fetch(host, path, params=None):
        calls.append((host, path, params))
        if not path:
            return {"genres": [{"id": 4, "name": "Azione & avventura", "type": "movie"},
                               {"id": 5, "name": "Hidden", "hidden": 1}]}
        return {"label": "Azione", "titles": [title(n) for n in range(60)]}
    monkeypatch.setattr(page, "fetch_site_props", fetch)
    result = catalog.browse("source.test", "genre", genre_id=4, number=2, media_type="movie")
    assert calls[-1] == ("source.test", "/browse/genre", {"page": 2, "type": "movie", "g": "Azione & avventura"})
    assert result["has_more"] and result["page"] == 2
    assert len(catalog.genres("source.test")) == 1
    with pytest.raises(ValueError):
        catalog.browse("source.test", "genre", genre_id=5)


def test_has_more_uses_raw_count_before_normalization(monkeypatch):
    monkeypatch.setattr(page, "fetch_site_props", lambda *a, **k: {"titles": [title(1)] + [{}] * 59})
    result = catalog.browse("source.test", "latest")
    assert len(result["items"]) == 1 and result["has_more"]
    assert not catalog.browse("source.test", "latest", number=50)["has_more"]


def test_upcoming_flags_survive_normalization(monkeypatch):
    monkeypatch.setattr(page, "fetch_site_props", lambda *a, **k: {
        "label": "In arrivo", "titles": [title(1, coming_soon=True, upcoming_seasons=[{"number": 2}])]})
    result = catalog.browse("source.test", "upcoming")
    assert result["items"][0]["coming_soon"] is True
    assert result["items"][0]["upcoming_seasons"] == [{"number": 2}]
    assert not result["has_more"]


def test_cache_expiry_domain_and_home_invalidation(monkeypatch):
    calls = []
    clock = [0]
    monkeypatch.setattr(catalog.time, "monotonic", lambda: clock[0])
    def fetch(host, *a, **k):
        calls.append(host)
        return {"titles": []}
    monkeypatch.setattr(page, "fetch_site_props", fetch)
    catalog.browse("a.test", "upcoming")
    catalog.browse("a.test", "upcoming")
    catalog.browse("b.test", "upcoming")
    clock[0] = 901
    catalog.browse("a.test", "upcoming")
    home.clear_cache("streamingcommunity")
    catalog.browse("a.test", "upcoming")
    assert calls == ["a.test", "b.test", "a.test", "a.test"]


@pytest.mark.parametrize("failure", [requests.Timeout(), RuntimeError("bad payload")])
def test_failed_fetch_is_retryable(monkeypatch, failure):
    def fail(*a, **k):
        raise failure
    monkeypatch.setattr(page, "fetch_site_props", fail)
    with pytest.raises(type(failure)):
        catalog.browse("a.test", "upcoming")
    monkeypatch.setattr(page, "fetch_site_props", lambda *a, **k: {"titles": []})
    assert catalog.browse("a.test", "upcoming")["items"] == []


def test_malformed_payload_is_not_cached(monkeypatch):
    monkeypatch.setattr(page, "fetch_site_props", lambda *a, **k: {"titles": {"invalid": []}})
    with pytest.raises(RuntimeError):
        catalog.browse("a.test", "latest")
    monkeypatch.setattr(page, "fetch_site_props", lambda *a, **k: {"titles": []})
    assert catalog.browse("a.test", "latest")["items"] == []


@pytest.mark.parametrize("path", ["/api/browse", "/api/home/section"])
def test_catalog_endpoints_validate_and_use_configured_host(client, monkeypatch, path):
    enable_open_mode()
    from app.routers import home as router
    seen = []
    monkeypatch.setattr(router, "configured_domain", lambda: "trusted.test")
    monkeypatch.setattr(catalog, "browse", lambda host, *a, **k: seen.append(host) or {"items": []})
    assert client.get(path + "?section=upcoming&domain=evil.test").status_code == 200
    assert seen == ["trusted.test"]
    for query in ["section=invalid", "section=latest&page=0", "section=latest&page=51", "section=genre&genre_id=-1"]:
        assert client.get(path + "?" + query).status_code == 422
    monkeypatch.setattr(router, "configured_domain", lambda: "")
    assert client.get(path + "?section=upcoming").status_code == 409


def test_catalog_requires_search_privilege(client):
    from app.auth.models import SETTING_AUTH_MODE, set_setting
    set_setting(SETTING_AUTH_MODE, "jellyfin")
    user = make_user("no-access", "jf-denied", 0)
    session_for(client, user.id)
    for path in ["/api/browse?section=upcoming", "/api/home/section?section=latest"]:
        assert client.get(path).status_code == 403
