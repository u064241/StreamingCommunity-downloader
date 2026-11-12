from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import deps
from app.core import source_budget
from app.routers import images


def _client(monkeypatch):
    monkeypatch.setattr(deps, "LOCAL_USER", SimpleNamespace(id=1, username="t", has=lambda *a, **k: True))
    app = FastAPI()
    app.include_router(images.router)
    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))


def test_refresh_empties_the_cache_and_bumps_the_generation(monkeypatch):
    client = _client(monkeypatch)
    images._cache.clear()
    images._cache_put(("d", "a.webp"), (b"x", "image/webp"))
    before = images.generation()

    response = client.post("/api/image/refresh", headers={"Origin": "http://127.0.0.1:8000"})

    assert response.json() == {"cleared": 1, "generation": before + 1}
    assert images._cache_get(("d", "a.webp")) is None
    assert images.generation() == before + 1


def test_artwork_does_not_spend_the_page_budget(monkeypatch):
    monkeypatch.setattr(images, "configured_domain", lambda: "streamingcommunityz.nexus")
    def refuse(what=""):
        raise source_budget.SourceUnavailable("budget", None)
    monkeypatch.setattr(source_budget, "acquire", refuse)
    monkeypatch.setattr(source_budget, "backoff_remaining", lambda: 0.0)
    res = Mock(ok=True, status_code=200, headers={"content-type": "image/webp"}, content=b"x", url="u")
    get = Mock(return_value=res)
    monkeypatch.setattr(images.requests, "get", get)
    monkeypatch.setattr(images, "_image_sent", images.deque())
    images._cache.clear()

    response = _client(monkeypatch).get("/api/image/fresh-one.webp")

    assert response.status_code == 200
    get.assert_called()


def test_artwork_during_a_pause_is_refused_without_a_request(monkeypatch):
    monkeypatch.setattr(images, "configured_domain", lambda: "streamingcommunityz.nexus")
    monkeypatch.setattr(source_budget, "backoff_remaining", lambda: 300.0)
    get = Mock()
    monkeypatch.setattr(images.requests, "get", get)
    images._cache.clear()

    response = _client(monkeypatch).get("/api/image/paused-one.webp")

    assert response.status_code == 503
    get.assert_not_called()


def test_the_artwork_hourly_allowance_caps_misses(monkeypatch):
    monkeypatch.setattr(images, "_IMAGE_MAX_PER_HOUR", 2)
    monkeypatch.setattr(images, "_image_sent", images.deque())
    assert images._image_allowance() and images._image_allowance()
    assert not images._image_allowance()


def test_refresh_also_forgets_the_listings_that_name_the_posters(monkeypatch):
    from app.core import catalog, home, metadata, page
    cleared = []
    monkeypatch.setattr(home, "clear_cache", lambda *a, **k: cleared.append("home"))
    monkeypatch.setattr(metadata, "clear_cache", lambda *a, **k: cleared.append("metadata"))
    monkeypatch.setattr(catalog, "clear_cache", lambda *a, **k: cleared.append("catalog"))
    monkeypatch.setattr(page, "clear_search_cache", lambda *a, **k: cleared.append("search"))

    _client(monkeypatch).post("/api/image/refresh", headers={"Origin": "http://127.0.0.1:8000"})

    assert set(cleared) == {"home", "metadata", "catalog", "search"}
