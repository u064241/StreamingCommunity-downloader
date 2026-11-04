import logging
from pathlib import Path

from app import config, jobs
from app.routers import files
from tests.conftest import do_setup


def test_download_directory_changes_new_destinations_and_browser(client, admin_credentials, tmp_path):
    auth = do_setup(client, admin_credentials)
    destination = tmp_path / "downloads"
    response = client.put("/api/domain/download-directory", json={"path": str(destination)},
                          headers={"X-CSRF-Token": auth["csrf_token"]})
    assert response.status_code == 200
    assert config.download_dir() == destination
    assert Path(jobs._get_library_path("film")) == destination
    (destination / "download.mp4").write_bytes(b"media")
    assert files._safe_path("download.mp4") == destination / "download.mp4"
    assert client.get("/api/domain/download-directory").json()["path"] == str(destination)


def test_download_directory_rejects_relative_path(client, admin_credentials):
    auth = do_setup(client, admin_credentials)
    old = config.download_dir()
    response = client.put("/api/domain/download-directory", json={"path": "relative-folder"},
                          headers={"X-CSRF-Token": auth["csrf_token"]})
    assert response.status_code == 400
    assert config.download_dir() == old


def test_logs_are_available_to_settings_admin(client, admin_credentials):
    do_setup(client, admin_credentials)
    logging.getLogger("runtime-settings-check").warning("Diagnostic event")
    response = client.get("/api/domain/logs")
    assert response.status_code == 200
    assert any("Diagnostic event" in line for line in response.json()["lines"])
    client.cookies.clear()
    assert client.get("/api/domain/logs").status_code in (401, 403)



def test_native_folder_picker_fills_field_without_saving(client, admin_credentials, monkeypatch, tmp_path):
    from app import windows_folder_picker as picker
    auth = do_setup(client, admin_credentials)
    previous = config.download_dir()
    selected = str(tmp_path / "chosen")
    monkeypatch.setattr(picker, "available", lambda: True)
    calls = []
    def choose(initial):
        calls.append(initial)
        return selected
    monkeypatch.setattr(picker, "choose_folder", choose)
    response = client.post("/api/domain/download-directory/browse", json={"path": str(tmp_path)},
                           headers={"X-CSRF-Token": auth["csrf_token"]})
    assert response.status_code == 200
    assert response.json() == {"path": selected, "cancelled": False}
    assert calls == [str(tmp_path)]
    assert config.download_dir() == previous


def test_native_folder_picker_cancel_preserves_setting(client, admin_credentials, monkeypatch):
    from app import windows_folder_picker as picker
    auth = do_setup(client, admin_credentials)
    previous = config.download_dir()
    monkeypatch.setattr(picker, "available", lambda: True)
    monkeypatch.setattr(picker, "choose_folder", lambda initial: None)
    response = client.post("/api/domain/download-directory/browse", json={"path": ""},
                           headers={"X-CSRF-Token": auth["csrf_token"]})
    assert response.json() == {"path": None, "cancelled": True}
    assert config.download_dir() == previous


def test_native_folder_picker_reports_already_open(client, admin_credentials, monkeypatch):
    from app import windows_folder_picker as picker
    auth = do_setup(client, admin_credentials)
    monkeypatch.setattr(picker, "available", lambda: True)
    def busy(initial):
        raise RuntimeError("Una finestra di scelta cartella è già aperta.")
    monkeypatch.setattr(picker, "choose_folder", busy)
    response = client.post("/api/domain/download-directory/browse", json={"path": ""},
                           headers={"X-CSRF-Token": auth["csrf_token"]})
    assert response.status_code == 409
