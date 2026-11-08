"""Provider identity survives legacy defaults, queuing and library lookup."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import library
from app.core import metadata
from app.jobs import JobManager
from app.schedule import ScheduleStore
from tests.conftest import enable_open_mode


@pytest.fixture
def manager(monkeypatch):
    manager = JobManager()
    # Exercise submission and saved calls, without starting network workers.
    monkeypatch.setattr(manager, "_submit_worker", lambda *a, **kw: None)
    yield manager
    manager._executor.shutdown(wait=False)


@pytest.mark.parametrize("kind,source", [
    ("film", "streamingcommunity"), ("episode", "streamingcommunity"), ("anime", "animeunity"),
])
def test_job_identity_is_explicit_and_serialized(manager, kind, source):
    job = manager._make_job("Title", kind, external_id="42")
    public = manager._job_to_dict(job)
    assert (public["source"], public["external_id"]) == (source, "42")
    with pytest.raises(ValueError):
        manager._make_job("Title", kind, source="unknown")


def test_immediate_anime_and_retry_keep_source(manager):
    job_id = manager.submit_anime_episode("42-show", {"id": 5, "number": 1}, "Show")
    job = manager.get(job_id)
    assert (job.source, job.external_id) == ("animeunity", "42-show")
    assert job.call[2]["source"] == "animeunity"
    job.status = "error"
    retried = manager.get(manager.retry(job_id))
    assert (retried.source, retried.external_id) == (job.source, job.external_id)
    assert retried.call[2]["source"] == "animeunity"


def test_schedules_store_source_restore_legacy_and_leave_unknown_pending(manager, tmp_path):
    store = ScheduleStore(tmp_path / "schedule.json")
    manager.set_schedule_store(store)
    later = datetime(2999, 1, 1, tzinfo=timezone.utc)
    manager.schedule_film(42, "Film", "example.test", later)
    manager.schedule_anime_episode("42-show", {"id": 5, "number": 1}, "Anime", later)
    store.add("film", later, {"id": 43, "title": "Old", "domain": "example.test"})
    unknown = store.add("film", later, {"id": 44, "source": "unknown"})
    assert [e["params"].get("source") for e in store.list_all()[:2]] == [
        "streamingcommunity", "animeunity",
    ]
    manager._jobs.clear()
    manager.set_schedule_store(ScheduleStore(tmp_path / "schedule.json"))
    manager.load_scheduled_from_store()
    restored = {j.external_id: j for j in manager._jobs.values()}
    assert {key: j.source for key, j in restored.items()} == {
        "42": "streamingcommunity", "42-show": "animeunity", "43": "streamingcommunity",
    }
    for job in restored.values():
        entry = manager._schedule_store.get_by_schedule_id(job.schedule_id)
        assert manager._build_call(entry["type"], entry["params"], job)[2]["source"] == job.source
    assert manager._schedule_store.get_by_schedule_id(unknown)["fired"] is False
    assert len(manager._schedule_store.list_all()) == 4


@pytest.mark.parametrize("path,body,expected", [
    ("/api/download/film", {"id": 42, "title": "Film"}, "streamingcommunity"),
    ("/api/download/anime", {"anime_id": "42-show", "anime_name": "Anime",
                             "episode": {"id": 5, "number": 1}}, "animeunity"),
])
def test_existing_download_endpoints_propagate_defaults_and_reject_other_sources(
    client, source, stub_jobs, path, body, expected,
):
    enable_open_mode()
    assert client.post(path, json=body).status_code == 202
    assert stub_jobs[-1][2]["source"] == expected
    assert client.post(path, json={**body, "source": "unknown"}).status_code == 422
    assert len(stub_jobs) == 1


@pytest.mark.parametrize("path", [
    "/api/metadata/movie/42?source=animeunity",
    "/api/image/poster.jpg?source=animeunity",
    "/api/tv/42/token?source=animeunity",
    "/api/anime/42-show/episodes?source=streamingcommunity",
])
def test_detail_and_image_endpoints_refuse_wrong_provider_without_fetching(client, monkeypatch, path):
    from app.routers import anime, images, tv

    enable_open_mode()
    def unexpected(*a, **kw):
        pytest.fail("wrong provider reached a source")
    monkeypatch.setattr(metadata, "title_metadata", unexpected)
    monkeypatch.setattr(images, "_fetch_image", unexpected)
    monkeypatch.setattr(tv, "get_token", unexpected)
    monkeypatch.setattr(anime.animeunity, "get_episodes", unexpected)
    assert client.get(path).status_code == 422


def test_metadata_identity_is_not_legacy_provenance_and_does_not_leak_cache(client, monkeypatch):
    from app.core import tv

    enable_open_mode()
    metadata.clear_cache()
    monkeypatch.setattr(metadata, "configured_domain", lambda: "example.test")
    monkeypatch.setattr(tv, "get_title_props", lambda *a: {"plot": "Plot", "tmdb_id": 7})
    result = client.get("/api/metadata/movie/42?slug=film&source=streamingcommunity").json()
    assert (result["provider"], result["external_id"], result["source"]) == (
        "streamingcommunity", "42", "site",
    )
    assert metadata.cached_tmdb_id("movie", 42) == 7
    assert metadata.cached_tmdb_id("movie", 42, source="animeunity") is None
    with pytest.raises(ValueError):
        metadata.title_metadata("movie", 42, "film", "", source="animeunity")
    metadata.clear_cache()


def test_library_destination_uses_explicit_source_instead_of_kind(client, tmp_path):
    for source, folder in [("streamingcommunity", "SC"), ("other", "Other")]:
        (tmp_path / folder).mkdir()
        library.record(source, "film", "42", folder, library.MANUAL)
    sc = library.destination("film", "42", root=str(tmp_path), single_file=True)
    other = library.destination("film", "42", root=str(tmp_path), single_file=True, source="other")
    assert str(tmp_path / "SC") in sc
    assert str(tmp_path / "Other") in other


def test_finished_job_is_filed_under_its_explicit_source(client, tmp_path, monkeypatch):
    from app.requests import resolver

    folder = tmp_path / "Film"
    folder.mkdir()
    output = folder / "Film.mkv"
    output.touch()
    monkeypatch.setattr(resolver, "library_dir", lambda kind: str(tmp_path))
    library.file_download(SimpleNamespace(type="film", source="other", external_id="42",
                                        output_path=str(output), media_label="Film", year=None))
    assert library.folders("other", "film", "42") == ["Film"]
    assert library.folders("streamingcommunity", "film", "42") == []
