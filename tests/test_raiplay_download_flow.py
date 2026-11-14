"""Catalogue -> preview API -> batch -> per-video engine, without real media.

The cases retain the different shapes observed in the public programme feeds:
Daily seasons, anthologies without numbers, and ordinary seasonal programmes.
"""
import inspect
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import downloads_notify
from app.jobs import JobManager
from app.providers import ProviderRegistry, get_registry
from app.providers.raiplay import parser
from app.providers.raiplay.client import BASE_URL, RaiPlayClient
from app.providers.raiplay.provider import RaiPlayProvider
from app.core import raiplay


def programme(slug, seasons, *, movie=False):
    root = f"/programmi/{slug}"
    sets = [{"name": name, "id": f"ContentSet-{number}", "episode_size": {"number": 2},
             "path_id": f"{root}/ContentSet-{number}.json"} for number, name in seasons]
    return {"name": slug, "first_item_path": "/video/2026/01/film.json",
            "program_info": {"name": slug, "layout": "single" if movie else "multi"},
            "blocks": [{"type": "RaiPlay Multimedia Block", "name": "Episodi", "id": "PublishingBlock-ep", "sets": sets},
                       {"type": "RaiPlay Multimedia Block", "name": "Anticipazioni", "id": "PublishingBlock-extra", "sets": sets},
                       {"type": "RaiPlay Multimedia Block", "name": "Extra", "id": "PublishingBlock-extra", "sets": sets}]}


def episode(number=1, season=6, **extra):
    return {"name": f"Episodio {number}", "episode_title": f"Episodio {number}",
            "episode": str(number), "season": str(season), "forma": "Integrale",
            "weblink": f"/video/2026/01/episodio-s{season}-e{number}.html",
            "images": {"landscape": "/dl/img/2026/01/still.jpg"},
            "duration_in_minutes": "52 min", **extra}


def fake_provider(slug, seasons, *, movie=False, editorial=True):
    root = f"/programmi/{slug}"
    catalogue = programme(slug, seasons, movie=movie)
    routes = {root + ".json": catalogue}
    for number, name in seasons:
        cards = [episode(2, number), episode(1, number)]
        if slug == "ilcommissariomontalbano":
            cards = [{**episode(i, number), "season": "", "episode": ""} for i in (1, 2)]
        feed = {"seasons": [{"label": "Episodi", "episodes": [{"label": name, "cards": cards}]}]}
        routes[f"{root}/PublishingBlock-ep/ContentSet-{number}/episodes.json"] = feed if editorial else {"seasons": []}
        routes[f"{root}/ContentSet-{number}.json"] = {"items": cards}
    calls = []

    def transport(method, url, **kwargs):
        path = url.removeprefix(BASE_URL)
        calls.append(path)
        data = routes.get(path)
        return SimpleNamespace(status_code=200 if data is not None else 404,
                               content=json.dumps(data or {}).encode())

    return RaiPlayProvider(client=RaiPlayClient(transport=transport, min_interval=0)), routes, calls


CASES = [
    ("lispettorecoliandro", [(1, "Stagione 1"), (6, "Stagione 6"), (7, "Stagione 7"), (8, "Stagione 8")]),
    ("doc-nelletuemani", [(1, "Stagione 1"), (4, "Stagione 4")]),
    ("sonicboom", [(1, "Stagione 1"), (2, "Stagione 2")]),
    ("ilparadisodellesignore", [(1, "Stagione 1"), (10, "Daily - Stagione 10"), (11, "Daily - Stagione 11")]),
    ("unpostoalsole", [(29, "Stagione 29"), (30, "Stagione 30")]),
    ("ilcommissariomontalbano", [(1, "TvMovie")]),
]


@pytest.fixture
def submit(monkeypatch):
    from app.jobs import job_manager
    calls = []
    signature = inspect.signature(job_manager.submit_raiplay)

    def record(*args, **kwargs):
        signature.bind(*args, **kwargs)
        calls.append((args, kwargs))
        return f"rp-{len(calls)}"

    monkeypatch.setattr(job_manager, "submit_raiplay", record)
    downloads_notify._batches.clear()
    yield calls
    downloads_notify._batches.clear()


def install(client, provider):
    registry = ProviderRegistry()
    registry.register(provider)
    client.app.dependency_overrides[get_registry] = lambda: registry
    return registry


@pytest.mark.parametrize("slug,seasons", CASES)
@pytest.mark.parametrize("editorial", [True, False], ids=["editorial", "contentset-fallback"])
def test_same_episodes_in_preview_season_and_series_download(client, submit, slug, seasons, editorial):
    provider, _, calls = fake_provider(slug, seasons, editorial=editorial)
    install(client, provider)
    try:
        details = client.get(f"/api/titles/raiplay/programmi:{slug}").json()
        assert [row["number"] for row in details["season_paths"]] == [n for n, _ in seasons]
        visible = []
        for number, _ in seasons:
            response = client.get(f"/api/titles/raiplay/programmi:{slug}/episodes", params={"season_number": number})
            assert response.status_code == 200, response.text
            rows = response.json()
            assert len(rows) == 2
            assert all(row["season"] == str(number) and row["still"] for row in rows)
            assert all(row["duration"] == "52" for row in rows)
            visible += rows
        body = {"url": f"{BASE_URL}/programmi/{slug}", "title": slug, "scope": "series"}
        response = client.post("/api/download/raiplay", json=body)
        assert response.status_code == 202, response.text
        assert response.json()["count"] == len(visible)
        assert [args[0] for args, _ in submit] == [row["page_url"] for row in visible]
        assert len({kw["batch_id"] for _, kw in submit}) == 1
        assert {kw["season"] for _, kw in submit} == {n for n, _ in seasons}
        submit.clear()
        response = client.post("/api/download/raiplay", json={**body, "scope": "season", "season_number": seasons[-1][0]})
        assert response.status_code == 202, response.text
        assert response.json()["count"] == 2
        assert {kw["season"] for _, kw in submit} == {seasons[-1][0]}
        assert not any("extra" in path for path in calls)
    finally:
        client.app.dependency_overrides.pop(get_registry, None)


def test_empty_primary_cards_continue_to_other_groups_and_sets():
    first = episode(1)
    wrong = episode(1, 7)
    payload = {"seasons": [
        {"label": "Extra", "episodes": [{"label": "Stagione 6", "cards": [first]}]},
        {"label": "Episodi", "episodes": [{"label": "Stagione 6", "cards": [wrong]}]},
        {"label": "Stagione 6", "episodes": [{"label": "Stagione 6", "cards": [first, first]}]},
    ]}
    assert len(parser.episode_rows(payload, 6)) == 1


def test_episode_without_numbers_or_weblink_uses_context_and_public_path_id():
    item = episode(season="", episode="", weblink=None, path_id="/video/2026/01/legacy.json")
    rows = parser.episode_rows({"items": [item]}, 6)
    assert rows[0]["season"] == "6" and rows[0]["n"] == "1"
    assert rows[0]["page_url"].endswith("legacy.html")


def test_drm_clips_and_foreign_video_urls_are_excluded():
    cards = [episode(1), episode(2, forma="Clip"), episode(3, has_drm=True),
             episode(4, rights_management={"rights": {"drm": {"widevine": True}}}),
             episode(5, weblink="https://evil.example/video/x.html")]
    assert [e["n"] for e in parser.episode_rows({"items": cards}, 6)] == ["1"]


def test_film_is_exactly_one_video_not_programme_playlist(client, submit):
    provider, _, _ = fake_provider("film", [], movie=True)
    install(client, provider)
    try:
        response = client.post("/api/download/raiplay", json={"url": BASE_URL + "/programmi/film", "title": "film", "scope": "film"})
        assert response.status_code == 202, response.text
        assert response.json()["count"] == 1
        assert submit[0][0][0] == BASE_URL + "/video/2026/01/film.html"
    finally:
        client.app.dependency_overrides.pop(get_registry, None)


def test_unavailable_season_reported_without_hiding_other_seasons(client, submit):
    provider, routes, _ = fake_provider("serie", [(1, "Stagione 1"), (6, "Stagione 6")])
    for path in list(routes):
        if "ContentSet-6" in path:
            del routes[path]
    install(client, provider)
    try:
        response = client.post("/api/download/raiplay", json={"url": BASE_URL + "/programmi/serie", "title": "serie", "scope": "series"})
        assert response.status_code == 202, response.text
        assert response.json()["count"] == 2
        assert response.json()["unavailable_seasons"] == [6]
        response = client.get("/api/titles/raiplay/programmi:serie/episodes", params={"season_number": 6})
        assert response.status_code == 502
    finally:
        client.app.dependency_overrides.pop(get_registry, None)


@pytest.mark.parametrize("url", ["https://evil.example/programmi/serie", BASE_URL + "/programmi/../serie", BASE_URL + "/video/x.html?key=x"])
def test_invalid_download_urls_create_no_job(client, submit, url):
    response = client.post("/api/download/raiplay", json={"url": url, "title": "invalid"})
    assert response.status_code == 422
    assert not submit


def test_jobs_use_episode_kind_tv_library_and_season_metadata(monkeypatch, tmp_path):
    manager = JobManager.__new__(JobManager)
    manager._make_progress_factory = lambda job: None
    monkeypatch.setattr("app.jobs._get_library_path", lambda kind: str(tmp_path / kind))
    captured = []
    manager._submit_job = lambda job, fn, *args, **kwargs: captured.append((job, args, kwargs)) or job.job_id
    manager.submit_raiplay(BASE_URL + "/video/ep.html", "Serie", season=6, episode_number="2", external_id="programmi:serie")
    job, args, kwargs = captured[0]
    assert job.type == "episode" and job.source == "raiplay"
    assert job.season == 6 and job.episode_number == "2"
    assert args[2] == str(tmp_path / "tv")
    assert kwargs["season"] == 6


def fake_ydl(monkeypatch, info, tmp_path):
    state = {"extracts": 0, "processed": []}

    class FakeYDL:
        def __init__(self, opts): state["opts"] = self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, **kwargs):
            assert kwargs == {"download": False, "process": False}
            state["extracts"] += 1
            return info
        def process_ie_result(self, info, download):
            state["processed"].append(info)
            target = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4").replace("%%", "%"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fake media output")
            for fn in self.opts["post_hooks"]: fn(str(target))
            return {"filepath": str(target)}

    monkeypatch.setattr(raiplay, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr("app.core.ffmpeg_path.get_ffmpeg_exe", lambda: str(tmp_path / "ffmpeg.exe"))
    return state


@pytest.mark.parametrize("info", [{"has_drm": True}, {"drm": True}, {"formats": [{"has_drm": True, "url": "https://media/x"}]}, {"_type": "playlist", "entries": []}])
def test_engine_never_processes_drm_or_collection_results(monkeypatch, tmp_path, info):
    state = fake_ydl(monkeypatch, info, tmp_path)
    with pytest.raises(raiplay.RaiPlayDownloadError):
        raiplay.download(BASE_URL + "/video/test.html", "Test", str(tmp_path))
    assert not state["processed"]


def test_engine_downloads_checked_clear_formats_once_and_returns_actual_file(monkeypatch, tmp_path):
    state = fake_ydl(monkeypatch, {"formats": [
        {"url": "https://media/drm", "has_drm": True},
        {"url": "https://media/clear", "has_drm": False},
    ]}, tmp_path)
    (tmp_path / "unrelated.mp4").write_bytes(b"old")
    result = raiplay.download(BASE_URL + "/video/test.html", "Test", str(tmp_path), season=6, episode_number="2")
    assert state["extracts"] == 1 and state["opts"]["noplaylist"] is True
    assert state["opts"]["ffmpeg_location"] == str(tmp_path / "ffmpeg.exe")
    assert len(state["processed"][0]["formats"]) == 1
    assert Path(result).name == "Test S06E02.mp4" and "Season 06" in result


def test_cancellation_before_extraction(monkeypatch, tmp_path):
    state = fake_ydl(monkeypatch, {}, tmp_path)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(raiplay.DownloadCancelledError):
        raiplay.download(BASE_URL + "/video/test.html", "Test", str(tmp_path), cancel_event=cancelled)
    assert state["extracts"] == 0
