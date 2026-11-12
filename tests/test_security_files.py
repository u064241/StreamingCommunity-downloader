"""The file manager and the settings that decide what it can reach."""

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.routers import files
from tests.conftest import do_setup


@pytest.fixture
def library(tmp_path, _configured_domain):
    """A download folder with a few entries in it, configured as the panel's."""
    root = tmp_path / "videos"
    (root / "Show").mkdir(parents=True)
    (root / "Show" / "episode.mkv").write_bytes(b"mkv")
    (root / "film.mp4").write_bytes(b"mp4")
    _configured_domain.write_text(
        json.dumps({"domain": "example.test", "download_dir": str(root)}), encoding="utf-8"
    )
    return root


@pytest.fixture
def admin(client, admin_credentials, library):
    return {"X-CSRF-Token": do_setup(client, admin_credentials)["csrf_token"]}


# ── 2. Where the download folder may point ─────────────────────────────────────

def _forbidden_folders(tmp_path) -> dict:
    import app

    system = os.environ.get("SystemRoot", r"C:\Windows") if os.name == "nt" else "/etc"
    return {
        "the folder holding panel.db and data.json": tmp_path,
        "the database itself": tmp_path / "test.db",
        "a drive root": Path(tmp_path.anchor),
        "a system folder": Path(system),
        "below a system folder": Path(system) / "Temp" / "videos",
        "the application folder": Path(app.__file__).parent,
        "inside the application folder": Path(app.__file__).parent / "static",
        "above the application folder": Path(app.__file__).parent.parent,
    }


@pytest.mark.parametrize("case", list(_forbidden_folders(Path("x"))))
def test_download_directory_cannot_expose_the_panels_own_files(client, admin, tmp_path, case):
    target = _forbidden_folders(tmp_path)[case]
    before = config.download_dir()

    response = client.put("/api/domain/download-directory", json={"path": str(target)},
                          headers=admin)

    assert response.status_code == 400, case
    assert config.download_dir() == before


def test_download_directory_check_is_case_insensitive_on_windows(client, admin):
    if os.name != "nt":
        pytest.skip("Windows paths")
    system = os.environ.get("SystemRoot", r"C:\Windows")
    response = client.put("/api/domain/download-directory",
                          json={"path": system.swapcase() + r"\Temp"}, headers=admin)
    assert response.status_code == 400


def test_an_ordinary_folder_is_still_accepted(client, admin, tmp_path):
    target = tmp_path / "elsewhere" / "downloads"
    response = client.put("/api/domain/download-directory", json={"path": str(target)},
                          headers=admin)
    assert response.status_code == 200
    assert config.download_dir() == target


# ── 11. settings/restore goes through the same validators ──────────────────────

def _restore(client, headers, data):
    return client.post("/api/domain/settings/restore", headers=headers,
                       json={"schema": 1, "data": data})


def test_restore_rejects_invalid_values_and_writes_nothing(client, admin, tmp_path):
    before = config.read_data()

    response = _restore(client, admin, {
        "domain": "good.test",                               # valid, must not be applied alone
        "download_dir": str(tmp_path),                       # holds panel.db
        "libraries": [{"type": "bogus", "path": "x"}],
        "excluded_folders": "not-a-list",
        "settings": {"max_concurrent_downloads": 9999},
    })

    assert response.status_code == 400
    detail = response.json()["detail"]
    for field in ("download_dir", "libraries", "excluded_folders", "settings"):
        assert field in detail
    assert "domain (" not in detail
    assert config.read_data() == before


@pytest.mark.parametrize("data", [
    {"domain": "http://user:pw@host.test"},
    {"domain": "ftp://host.test"},
    {"download_dir": "relative/folder"},
    {"libraries": [{"type": "film", "path": ""}]},
    {"settings": {"output_container": "exe"}},
    {"settings": {"naming_templates": {"film_file": "{no_such_token}"}}},
    {"settings": {"domain_check_interval_minutes": 0}},
    {"settings": {"an_unknown_key": True}},
    {"settings": ["not", "a", "dict"]},
    {"video_host": "10.0.0.1"},                            # hosts.normalize_host refuses an IP
])
def test_restore_applies_each_fields_own_validator(client, admin, data):
    before = config.read_data()
    response = _restore(client, admin, data)
    assert response.status_code == 400, response.text
    assert config.read_data() == before


def test_restore_verifies_hosts_like_put_hosts_does(client, admin, monkeypatch):
    from app.core import hosts

    def refuse(host):
        raise RuntimeError(f"{host} non serve la chiave video")

    monkeypatch.setattr(hosts, "verify_video_host", refuse)
    assert _restore(client, admin, {"video_host": "internal.example"}).status_code == 400
    assert config.configured_video_host() == ""

    monkeypatch.setattr(hosts, "verify_video_host", lambda host: None)
    assert _restore(client, admin, {"video_host": "HTTPS://Video.Example/"}).status_code == 200
    assert config.configured_video_host() == "video.example"


def test_a_valid_backup_still_restores(client, admin, tmp_path):
    response = _restore(client, admin, {
        "domain": "https://Restored.Test/",
        "download_dir": str(tmp_path / "other"),
        "libraries": [{"type": "film", "path": str(tmp_path / "film")}],
        "excluded_folders": ["extras"],
        "settings": {"max_concurrent_downloads": 2, "output_container": "mp4"},
    })
    assert response.status_code == 200, response.text
    assert config.configured_domain() == "restored.test"
    assert config.get_settings()["max_concurrent_downloads"] == 2
    assert config.download_dir() == tmp_path / "other"


# ── 7. Only media is opened in place ───────────────────────────────────────────

@pytest.mark.parametrize("name", ["page.html", "image.svg", "script.js", "notes.txt", "noextension"])
def test_non_media_files_are_downloaded_never_rendered(client, admin, library, name):
    (library / name).write_text("<script>alert(document.domain)</script>", encoding="utf-8")

    response = client.get(f"/api/files/stream/{name}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name, mime", [
    ("film.mp4", "video/mp4"), ("Show/episode.mkv", "video/x-matroska"),
])
def test_media_still_streams_inline(client, admin, library, name, mime):
    response = client.get(f"/api/files/stream/{name}")
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["content-type"] == mime


def test_the_download_header_is_built_by_the_framework(client, admin, library):
    (library / "città è.mp4").write_bytes(b"x")
    response = client.get("/api/files/download/città è.mp4")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "filename*=utf-8''citt%C3%A0%20%C3%A8.mp4" in disposition

    (library / "page.html").write_text("<b>", encoding="utf-8")
    assert client.get("/api/files/download/page.html").headers["content-type"] == (
        "application/octet-stream"
    )


# ── 8. The root is not an entry ────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["", ".", "./", "Show/..", "Show/../."])
def test_the_root_cannot_be_deleted_renamed_or_moved(client, admin, library, path):
    deleted = client.post("/api/files/delete-batch", json={"paths": [path]}, headers=admin)
    assert deleted.status_code == 200
    assert deleted.json()["results"] == [{"path": path, "ok": False, "error": "Invalid path"}]

    renamed = client.post("/api/files/rename", json={"path": path, "new_name": "gone"},
                          headers=admin)
    assert renamed.status_code == 400

    moved = client.post("/api/files/move-batch", json={"paths": [path], "dest_dir_path": "Show"},
                        headers=admin)
    assert moved.json()["results"][0]["ok"] is False

    assert library.is_dir()
    assert (library / "film.mp4").exists() and (library / "Show" / "episode.mkv").exists()


def test_delete_route_refuses_the_root(client, admin, library):
    assert client.delete("/api/files/delete/%2E", headers=admin).status_code in (400, 404, 405)
    with pytest.raises(ValueError):
        files._safe_path_strict("")
    assert (library / "film.mp4").exists()


def test_entries_can_still_be_deleted(client, admin, library):
    response = client.post("/api/files/delete-batch", json={"paths": ["film.mp4", "Show"]},
                           headers=admin)
    assert [r["ok"] for r in response.json()["results"]] == [True, True]
    assert list(library.iterdir()) == []


# ── 9. /open launches a program ────────────────────────────────────────────────

@pytest.fixture
def opened(monkeypatch):
    launched = []
    monkeypatch.setattr(files.os, "startfile", lambda path: launched.append(path), raising=False)
    monkeypatch.setattr(files.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd[-1]))
    return launched


def _local_client():
    """A client whose peer is the machine itself, as the browser on the PC is."""
    from app.main import app

    local = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    return local, {}


def test_open_is_refused_from_another_machine(client, admin, library, opened):
    # The default test client's peer is "testclient": not loopback.
    assert client.post("/api/files/open", json={"path": "film.mp4"}, headers=admin).status_code == 403

    local, headers = _local_client()
    for extra in ({"X-Forwarded-For": "8.8.8.8"}, {"Origin": "http://attacker.example"},
                  {"Host": "attacker.example"}):
        response = local.post("/api/files/open", json={"path": "film.mp4"},
                              headers={**headers, **extra})
        assert response.status_code == 403, extra
    assert opened == []


def test_open_works_for_a_file_manager_on_the_machine_itself(client, admin, library, opened):
    local, headers = _local_client()
    response = local.post("/api/files/open", json={"path": "film.mp4"},
                          headers={**headers, "Origin": "http://127.0.0.1:8000"})
    assert response.status_code == 200
    assert opened == [str(library / "film.mp4")]


# ── 15. Subtitle conversion ────────────────────────────────────────────────────

class _FakeFFmpeg:
    def __init__(self, command, **kwargs):
        self.kwargs = kwargs
        self.stdout = io.BytesIO(b"WEBVTT\n\n00:00.000 --> 00:01.000\nciao\n")
        self.killed = False
        _FakeFFmpeg.last = self

    def poll(self):
        return 0

    def wait(self):
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture
def ffmpeg(monkeypatch):
    monkeypatch.setattr(files, "get_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(files.subprocess, "Popen", _FakeFFmpeg)
    return _FakeFFmpeg


def _free_slots() -> int:
    return files._subtitle_slots._value


def test_subtitle_stderr_is_not_an_unread_pipe_and_the_slot_is_returned(client, admin, library, ffmpeg):
    response = client.get("/api/files/subtitle/2/Show/episode.mkv")

    assert response.status_code == 200
    assert response.text.startswith("WEBVTT")
    assert ffmpeg.last.kwargs["stderr"] == subprocess.DEVNULL
    assert ffmpeg.last.kwargs["stdin"] == subprocess.DEVNULL
    assert _free_slots() == files._SUBTITLE_CONCURRENCY


def test_subtitle_conversions_are_capped(client, admin, library, ffmpeg, monkeypatch):
    monkeypatch.setattr(files, "_SUBTITLE_WAIT", 0.01)
    ffmpeg.last = None
    for _ in range(files._SUBTITLE_CONCURRENCY):
        assert files._subtitle_slots.acquire(blocking=False)
    try:
        response = client.get("/api/files/subtitle/2/Show/episode.mkv")
    finally:
        for _ in range(files._SUBTITLE_CONCURRENCY):
            files._subtitle_slots.release()

    assert response.status_code == 503
    assert response.headers["retry-after"]
    assert ffmpeg.last is None                              # no process was started


def test_a_failed_start_gives_the_slot_back(client, admin, library, monkeypatch):
    def cannot_start(command, **kwargs):
        raise OSError("no ffmpeg")

    monkeypatch.setattr(files, "get_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(files.subprocess, "Popen", cannot_start)
    assert client.get("/api/files/subtitle/2/Show/episode.mkv").status_code == 503
    assert _free_slots() == files._SUBTITLE_CONCURRENCY


# ── 16. Server paths and exception text ────────────────────────────────────────

@pytest.fixture
def libraries(library, monkeypatch):
    monkeypatch.setattr(files, "_read_data", lambda: {
        "libraries": [{"type": "film", "path": str(library)}],
    })
    files._disk_usage_cache["data"] = None
    yield library
    files._disk_usage_cache["data"] = None


def test_someone_who_manages_settings_still_does(client, admin, libraries):
    usage = client.get("/api/files/disk-usage").json()
    (film,) = client.get("/api/files/library-tree").json()

    assert usage["volumes"][0]["paths"] == [str(libraries)]
    assert film["abs_path"] == str(libraries.resolve())
    assert film["children"][0]["abs_path"] == str((libraries / "Show").resolve())


def test_failures_do_not_echo_the_exception(client, admin, library, monkeypatch):
    def denied(*args, **kwargs):
        raise OSError(13, "Permission denied", r"\\nas\secret-share\film.mp4")

    monkeypatch.setattr(files.shutil, "move", denied)
    moved = client.post("/api/files/move", json={"path": "Show/episode.mkv", "dest_dir_path": ""},
                        headers=admin)
    batch = client.post("/api/files/move-batch",
                        json={"paths": ["Show/episode.mkv"], "dest_dir_path": ""}, headers=admin)
    monkeypatch.setattr(Path, "rename", denied)
    renamed = client.post("/api/files/rename", json={"path": "film.mp4", "new_name": "new.mp4"},
                          headers=admin)
    monkeypatch.setattr(files, "_delete_sync", denied)
    deleted = client.post("/api/files/delete-batch", json={"paths": ["film.mp4"]}, headers=admin)

    assert (moved.status_code, renamed.status_code) == (500, 500)
    for response in (moved, batch, renamed, deleted):
        assert "secret-share" not in response.text
        assert "Permission denied" not in response.text
    assert moved.json()["detail"] == "Spostamento fallito"
    assert renamed.json()["detail"] == "Rinomina fallita"


def test_move_reports_the_destination_to_the_local_user(client, admin, library):
    """The local user also manages settings, so — like the library tree — the
    answer carries the real path rather than one relative to the library."""
    response = client.post("/api/files/move", json={"path": "film.mp4", "dest_dir_path": "Show"},
                           headers=admin)
    assert response.status_code == 200
    assert response.json() == {"moved_to": str(library / "Show" / "film.mp4")}
    assert (library / "Show" / "film.mp4").exists()
