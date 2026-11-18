import copy
import json
from pathlib import Path

import pytest

from app.core import animeunity as au, animeunity_catalog as catalog

FIXTURE = json.loads((Path(__file__).parent / "fixtures/animeunity_browse_2026_11_18.json").read_text())


@pytest.fixture(autouse=True)
def reset_catalog(monkeypatch):
    catalog.clear_cache()
    monkeypatch.setattr(au, "_modern_site", lambda: True)
    yield
    catalog.clear_cache()


def title(n, kind="tv"):
    return {"id": n, "slug": f"anime-{n}", "title": f"Anime {n}", "type": kind,
            "variants": [{"language": "jpn", "episodes_count": 10}, {"language": "ita", "episodes_count": 2}]}


def paginator(rows, page, size):
    return {"data": rows[(page - 1) * size:page * size], "per_page": size,
            "current_page": page, "last_page": max(1, (len(rows) + size - 1) // size), "total": len(rows)}


@pytest.mark.parametrize("size,page", [(20, 2), (30, 1), (60, 2), (60, 4)])
def test_archive_exact_windows_no_missing_or_repeated_titles(monkeypatch, size, page):
    rows, calls = [title(n) for n in range(125)], []
    def upstream(path, **params):
        calls.append((path, params))
        return paginator(rows, params["page"], 30)
    monkeypatch.setattr(catalog, "_json", upstream)
    data = catalog.browse(page=page, per_page=size)
    start = (page - 1) * size
    assert [i["id"] for i in data["items"]] == [f"{n}-anime-{n}" for n in range(125)[start:start + size]]
    assert data["total"] == 125
    assert data["has_next"] == (page * size < 125)
    assert data["has_previous"] == (page > 1)
    assert all(path == "/api/archive" for path, _ in calls)


def test_archive_filters_applied_upstream_and_language_kept(monkeypatch):
    calls = []
    def upstream(path, **params):
        calls.append((path, params))
        return paginator([title(1, "ova")], 1, 30)
    monkeypatch.setattr(catalog, "_json", upstream)
    result = catalog.browse(query="Example", media_type="ova", dubbed=True, sort="score")
    assert calls == [("/api/archive", {"page": 1, "query": "Example", "type": "ova", "language": "ita", "sort_by": "score"})]
    assert result["items"][0]["language"] == "ita"
    assert result["items"][0]["source"] == "animeunity"


@pytest.mark.parametrize("section,path,extra", [
    ("latest_animes", "/api/archive", {"sort_by": "latest"}),
    ("upcoming", "/api/archive", {"status": "upcoming"}),
    ("top_week", "/api/top-anime", {"period": "week", "sort_by": "views"}),
])
def test_home_rails_expand_to_correct_complete_archive(monkeypatch, section, path, extra):
    def upstream(actual_path, **params):
        assert actual_path == path
        assert all(params[k] == v for k, v in extra.items())
        return paginator([], 1, 30)
    monkeypatch.setattr(catalog, "_json", upstream)
    assert catalog.browse(section=section)["items"] == []


def videos():
    return [{"id": n, "language": "ita", "episode": {"number": str(n), "anime": title(1, "movie" if n > 22 else "tv")}}
            for n in range(1, 46)]


def test_latest_keeps_multiple_episode_cards_and_actual_language(monkeypatch):
    monkeypatch.setattr(catalog, "_json", lambda path, **p: paginator(videos(), p["page"], 20))
    data = catalog.browse(section="latest", per_page=30, page=2)
    assert [i["video_id"] for i in data["items"]] == list(range(31, 46))
    assert len({i["id"] for i in data["items"]}) == 1
    assert all(i["language"] == "ita" for i in data["items"])
    assert not data["has_next"]


def test_latest_filter_searches_later_pages_and_uses_dub_not_ita(monkeypatch):
    calls = []
    def upstream(path, **params):
        assert params["language"] == "dub"
        assert "type" not in params
        calls.append(params["page"])
        return paginator(videos(), params["page"], 20)
    monkeypatch.setattr(catalog, "_json", upstream)
    data = catalog.browse(section="latest", per_page=20, media_type="movie", dubbed=True)
    assert data["total"] == 23 and data["last_page"] == 2
    assert [i["video_id"] for i in data["items"]] == list(range(23, 43))
    assert calls == [1, 2, 3]


def test_calendar_real_payload_preserves_schedule_and_dub_variant(monkeypatch):
    monkeypatch.setattr(catalog, "_json", lambda path: copy.deepcopy(FIXTURE["calendar"]))
    data = catalog.calendar()
    assert data["supported"] and data["timezone"] == "Europe/Rome"
    assert len(data["days"]) == 8
    today = data["days"][0]
    assert today["today"] and today["date"] == "2026-11-18"
    item = today["items"][0]
    assert item["language"] == "ita" and item["aired"]
    assert item["release_time"].endswith("+01:00")
    dubbed = catalog.calendar(dubbed=True)
    assert all(i["dubbed"] for day in dubbed["days"] for i in day["items"])
    assert data["days"][-1]["undetermined"]


@pytest.mark.parametrize("raw", [{}, {"data": []}, {"data": [], "total": -1, "per_page": 30, "last_page": 1, "current_page": 1}])
def test_malformed_catalogue_not_silently_empty(monkeypatch, raw):
    monkeypatch.setattr(catalog, "_json", lambda *a, **kw: raw)
    with pytest.raises(RuntimeError):
        catalog.browse()


def test_legacy_offset_archive_paginates_without_invented_total(monkeypatch):
    monkeypatch.setattr(au, "_modern_site", lambda: False)
    calls = []
    def search(q, *, page, **kw):
        calls.append(page)
        return [{"id": n} for n in range(65)][(page - 1) * 30:page * 30]
    monkeypatch.setattr(au, "search", search)
    data = catalog.browse(page=2, per_page=60)
    assert [i["id"] for i in data["items"]] == list(range(60, 65))
    assert data["total"] is None and data["last_page"] is None and not data["has_next"]
    assert calls == [3]
    assert catalog.calendar() == {"days": [], "supported": False}


def test_cache_is_bounded_host_scoped_and_invalidated(monkeypatch):
    monkeypatch.setattr(au, "host", lambda: "animeunity.cx")
    calls = []
    def fetch():
        calls.append(True)
        return {"rows": [1]}
    result = catalog._cached(("x",), fetch)
    result["rows"].append(2)
    assert catalog._cached(("x",), fetch) == {"rows": [1]}
    monkeypatch.setattr(au, "host", lambda: "animeunity.example")
    catalog._cached(("x",), fetch)
    assert len(calls) == 2
    au.forget_csrf()
    catalog._cached(("x",), fetch)
    assert len(calls) == 3
    for n in range(140): catalog._cached((n,), fetch)
    assert len(catalog._cache) == 128


def test_failed_fetch_not_cached_as_empty():
    def fail(): raise RuntimeError("unavailable")
    with pytest.raises(RuntimeError): catalog._cached(("bad",), fail)
    assert catalog._cached(("bad",), lambda: [1]) == [1]


@pytest.mark.parametrize("params", [{"per_page": 60}, {"page": 210, "per_page": 30}, {"media_type": "ona", "dubbed": True}])
def test_catalogue_api_accepts_real_query_strings(client, monkeypatch, params):
    monkeypatch.setattr(catalog, "browse", lambda **kw: {"items": [], "received": kw})
    response = client.get("/api/anime/catalog", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["received"]["per_page"] in (20, 30, 60)


@pytest.mark.parametrize("params", [{"page": 0}, {"per_page": 40}, {"section": "bad"}, {"media_type": "bad"}, {"sort": "bad"}])
def test_bad_query_rejected_before_network(client, monkeypatch, params):
    monkeypatch.setattr(catalog, "browse", lambda **kw: pytest.fail("invalid request reached provider"))
    assert client.get("/api/anime/catalog", params=params).status_code == 422


def test_calendar_api_error_is_not_a_successful_empty_calendar(client, monkeypatch):
    def fail(**kw): raise RuntimeError("changed upstream schema")
    monkeypatch.setattr(catalog, "calendar", fail)
    assert client.get("/api/anime/calendar").status_code == 502


def test_source_repeating_first_page_fails_instead_of_duplicating_cards(monkeypatch):
    monkeypatch.setattr(catalog, "_json", lambda *a, **kw: paginator([title(n) for n in range(100)], 1, 30))
    with pytest.raises(RuntimeError, match="inconsistent pagination"):
        catalog.browse(per_page=60)


def test_empty_calendar_day_keeps_translatable_day_and_date(monkeypatch):
    monkeypatch.setattr(catalog, "_json", lambda *a, **kw: {"Giovedì - 19/11": [], "Indeterminato": []})
    day = catalog.calendar()["days"][0]
    assert day["weekday"] == "thursday" and day["display_date"] == "19/11"
    assert day["items"] == []


def test_legacy_does_not_present_archive_as_top_chart(monkeypatch):
    monkeypatch.setattr(au, "_modern_site", lambda: False)
    result = catalog.browse(section="top_day")
    assert result["supported"] is False and result["items"] == []
