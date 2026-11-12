"""The source's /archive: one page per user action, through the budget and the cache.

No network: ``page.requests.get`` (or ``page.fetch_site_props``) is faked.
"""

import html
import json

import pytest
import requests

from app.core import catalog, page, source_budget
from tests.conftest import enable_open_mode


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(source_budget.ENV_REQUESTS_PER_HOUR, raising=False)
    catalog.clear_cache()
    yield
    catalog.clear_cache()


class _Response:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code
        self.ok = status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _title(n, **extra):
    return {"id": n, "name": f"Title {n}", "slug": f"t-{n}", "type": "tv", "score": "7.5",
            "sub_ita": 0, "seasons_count": 2, "translations": [],
            "images": [{"type": "poster", "filename": f"p{n}.webp"}], **extra}


def _props(titles, total=600):
    return {"titles": titles, "totalCount": total, "browseMoreApiRoute": "/api/archive",
            "sortBy": "", "genres": [{"id": 4, "name": "Azione", "type": "movie", "hidden": 0},
                                     {"id": 9, "name": "Hidden", "hidden": 1}],
            "countries": [{"id": 1, "code": "IT", "name": "Italia"}, {"code": "XX"}]}


@pytest.fixture
def source(monkeypatch):
    state = {"calls": [], "status": 200, "props": _props([_title(n) for n in range(60)])}

    def fake_get(url, *args, **kwargs):
        state["calls"].append((url, kwargs.get("params")))
        payload = {"props": state["props"], "version": "v1"}
        body = ('<html><body><div id="app" data-page="'
                + html.escape(json.dumps(payload), quote=True) + '"></div></body></html>')
        return _Response(body, state["status"])

    monkeypatch.setattr(page.requests, "get", fake_get)
    return state


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(source_budget, "_now", lambda: now[0])
    monkeypatch.setattr(catalog.time, "monotonic", lambda: now[0])
    return now


def test_first_page_reads_archive_and_normalizes(source, clock):
    result, info = catalog.archive("s.test")
    assert len(source["calls"]) == 1
    url, params = source["calls"][0]
    assert url.endswith("/archive") and not params
    item = result["items"][0]
    assert item["poster"] == "p0.webp" and item["type"] == "tv" and "images" not in item
    assert result["total_count"] == 600 and result["has_more"] and result["page"] == 1
    assert result["genres"] == [{"id": 4, "name": "Azione", "type": "movie"}]
    assert result["countries"] == [{"id": 1, "code": "IT", "name": "Italia"}]
    assert result["filters"] == {"genre": False, "country": False, "sort": False}
    assert info["cached"] is False


def test_one_request_per_page_and_cache_reused(source, clock):
    catalog.archive("s.test")
    catalog.archive("s.test", number=2)
    catalog.archive("s.test", number=2)
    catalog.archive("s.test")
    assert [p for _, p in source["calls"]] == [None, {"page": 2}]
    _, info = catalog.archive("s.test", number=2)
    assert info["cached"] is True and len(source["calls"]) == 2


def test_type_is_its_own_page(source, clock):
    catalog.archive("s.test", media_type="movie")
    assert source["calls"][0][1] == {"type": "movie"}


def test_backoff_sends_nothing_and_serves_the_stale_copy(source, clock):
    catalog.archive("s.test")
    clock[0] += catalog.TTL * 2
    source_budget.report_pushback("test")
    result, info = catalog.archive("s.test")
    assert len(source["calls"]) == 1
    assert info["stale"] == "backoff" and len(result["items"]) == 60
    with pytest.raises(source_budget.SourceUnavailable):
        catalog.archive("s.test", number=2)
    assert len(source["calls"]) == 1


def test_spent_budget_sends_nothing(source, clock, monkeypatch):
    monkeypatch.setenv(source_budget.ENV_REQUESTS_PER_HOUR, "1")
    catalog.archive("s.test")
    with pytest.raises(source_budget.SourceUnavailable) as exc:
        catalog.archive("s.test", number=2)
    assert exc.value.reason == "budget" and len(source["calls"]) == 1


def test_unknown_filters_are_refused_before_any_request(source, clock):
    for kwargs in ({"genre_id": 4}, {"country_id": 1}, {"sort": "name"},
                   {"number": 0}, {"number": 51}, {"media_type": "anime"}):
        with pytest.raises(ValueError):
            catalog.archive("s.test", **kwargs)
    assert source["calls"] == []


def test_configured_filters_are_validated_against_the_source_lists(source, clock, monkeypatch):
    monkeypatch.setitem(catalog.ARCHIVE_PARAMS, "genre", "genre_param")
    with pytest.raises(ValueError):
        catalog.archive("s.test", genre_id=9)   # hidden genre
    catalog.archive("s.test", genre_id=4)
    assert [p for _, p in source["calls"]] == [None, {"genre_param": 4}]


def test_has_more_follows_total_count(source, clock):
    source["props"] = _props([_title(n) for n in range(60)], total=120)
    assert not catalog.archive("s.test", number=2)[0]["has_more"]
    source["props"] = _props([_title(1)], total=None)
    assert not catalog.archive("s.test", number=3)[0]["has_more"]


def test_malformed_payload_is_not_cached(monkeypatch):
    monkeypatch.setattr(page, "fetch_site_props", lambda *a, **k: {"titles": {"x": 1}})
    with pytest.raises(RuntimeError):
        catalog.archive("s.test")
    monkeypatch.setattr(page, "fetch_site_props", lambda *a, **k: {"titles": []})
    assert catalog.archive("s.test")[0]["items"] == []


def test_endpoint_validates_and_uses_configured_host(client, monkeypatch):
    enable_open_mode()
    from app.routers import home as router
    seen = []
    monkeypatch.setattr(router, "configured_domain", lambda: "trusted.test")
    monkeypatch.setattr(catalog, "archive", lambda host, **k: seen.append((host, k)) or (
        {"items": [], "page": k["number"]}, {"cached": True, "stale": "budget", "updated_at": 1234.5}))
    response = client.get("/api/archive?page=2&media_type=tv&domain=evil.test")
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "HIT" and response.headers["X-Stale"] == "budget"
    assert response.headers["X-Cache-Updated"] == "1234"
    assert seen == [("trusted.test", {"number": 2, "genre_id": None, "country_id": None, "media_type": "tv"})]
    for query in ["page=0", "page=51", "media_type=anime", "genre_id=0", "country_id=-1", "page=x"]:
        assert client.get("/api/archive?" + query).status_code == 422, query
    assert len(seen) == 1
    monkeypatch.setattr(router, "configured_domain", lambda: "")
    assert client.get("/api/archive").status_code == 409


def test_endpoint_maps_refusals(client, monkeypatch):
    enable_open_mode()
    from app.routers import home as router
    monkeypatch.setattr(router, "configured_domain", lambda: "trusted.test")

    def unavailable(*a, **k):
        raise source_budget.SourceUnavailable("backoff")
    monkeypatch.setattr(catalog, "archive", unavailable)
    assert client.get("/api/archive").status_code == 503

    def unsupported(*a, **k):
        raise ValueError("Filtro 'genre' non ancora supportato")
    monkeypatch.setattr(catalog, "archive", unsupported)
    assert client.get("/api/archive?genre_id=4").status_code == 422
