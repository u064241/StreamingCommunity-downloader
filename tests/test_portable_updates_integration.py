"""Portable orchestration: no installed app, live network or real data."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import distribution, portable_updates as service
from app.auth.permissions import Permission
from app.routers import updates as routes
from app.update_download import UpdateDownloadError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    exe = tmp_path / service.EXE
    helper = tmp_path / service.HELPER
    exe.write_bytes(b"old executable")
    helper.write_bytes(b"helper")
    monkeypatch.setattr(service, "_runtime", lambda: (exe, helper))
    monkeypatch.setattr(service, "_idle", lambda: None)
    monkeypatch.setattr(service, "_prepared", None)
    monkeypatch.setattr(service, "_running", None)
    yield exe
    state = service._prepared or (service._running[1] if service._running else None)
    if state:
        state["folder"].cleanup()


@pytest.fixture
def release(runtime, tmp_path, monkeypatch):
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(service.EXE, b"new executable")
        z.writestr(service.HELPER, b"new helper")
        z.writestr(distribution.MARKER, json.dumps({
            "schema": 1, "repository": distribution.PRODUCT, "mode": "portable"}))
    name = "StreamingCommunityDownloader-9.0.0-Portable.zip"
    prefix = f"https://github.com/{distribution.PRODUCT}/releases/download/v9.0.0/"
    record = dict(name=name, size=archive.stat().st_size,
                  sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    manifest = dict(schema=1, variant="u064241", channel="stable", version="9.0.0",
                    repository=distribution.PRODUCT, assets=[record])
    data = dict(status="update_available", tag="v9.0.0", version="9.0.0", assets=[
        dict(name="update-manifest.json", url=prefix + "update-manifest.json", size=300),
        dict(name=name, url=prefix + name, size=record["size"])])
    monkeypatch.setattr(service.updates, "check", lambda **kw: data)

    @contextmanager
    def response(url):
        yield SimpleNamespace(iter_content=lambda size: [json.dumps(manifest).encode()])

    monkeypatch.setattr(service, "github_response", response)
    def download(url, digest, folder):
        assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
        target = Path(folder) / name
        target.write_bytes(archive.read_bytes())
        return target
    monkeypatch.setattr(service, "download_verified", download)
    return data, manifest, archive


def test_prepare_pins_and_verifies_release_without_touching_app(runtime, release):
    result = service.prepare(17)
    assert len(result["token"]) > 20
    assert service._prepared["asset"].read_bytes() == b"new executable"
    assert service._prepared["sha256"] == hashlib.sha256(b"new executable").hexdigest()
    assert runtime.read_bytes() == b"old executable"


@pytest.mark.parametrize("key,value", [("repository", "other/repo"), ("version", "8.0.0"),
    ("schema", 2), ("channel", "beta"), ("variant", "other")])
def test_foreign_manifest_rejected(runtime, release, key, value):
    release[1][key] = value
    with pytest.raises(UpdateDownloadError):
        service.prepare(1)
    assert service._prepared is None
    assert runtime.read_bytes() == b"old executable"


def test_asset_from_different_release_rejected(runtime, release):
    release[0]["assets"][1]["url"] = release[0]["assets"][1]["url"].replace("v9.0.0", "v8.0.0")
    with pytest.raises(UpdateDownloadError, match="estraneo"):
        service.prepare(1)


@pytest.mark.parametrize("field,value", [("size", -1), ("sha256", "bad")])
def test_invalid_asset_metadata_rejected(runtime, release, field, value):
    release[1]["assets"][0][field] = value
    with pytest.raises(UpdateDownloadError):
        service.prepare(1)


def test_checksum_failure_never_prepares(runtime, release, monkeypatch):
    def fail(*args):
        raise UpdateDownloadError("Checksum SHA-256 non corrispondente")
    monkeypatch.setattr(service, "download_verified", fail)
    with pytest.raises(UpdateDownloadError, match="Checksum"):
        service.prepare(1)
    assert service._prepared is None


def test_zip_traversal_never_extracted(runtime, release):
    with zipfile.ZipFile(release[2], "a") as z:
        z.writestr("../evil.exe", b"evil")
    record = release[1]["assets"][0]
    record["size"] = release[2].stat().st_size
    record["sha256"] = hashlib.sha256(release[2].read_bytes()).hexdigest()
    release[0]["assets"][1]["size"] = record["size"]
    with pytest.raises(UpdateDownloadError, match="Contenuto"):
        service.prepare(1)
    assert not (runtime.parent / "evil.exe").exists()


@pytest.mark.parametrize("change", ["owner", "token", "expired"])
def test_apply_requires_same_owner_valid_token_and_fresh_preparation(runtime, release, change):
    result = service.prepare(17)
    if change == "expired":
        service._prepared["expires"] = 0
    with pytest.raises(UpdateDownloadError):
        service.apply(18 if change == "owner" else 17,
                      "invalid" if change == "token" else result["token"])


def test_apply_waits_for_helper_ready_and_is_single_use(runtime, release, monkeypatch):
    prepared = service.prepare(17)
    def launch(args, **kwargs):
        assert kwargs["cwd"] == runtime.parent
        assert "shell" not in kwargs
        assert args[0] == str(runtime.parent / service.HELPER)
        Path(args[args.index("--ready-file") + 1]).write_text("ready")
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(service.subprocess, "Popen", launch)
    result = service.apply(17, prepared["token"])
    assert result["status"] == "waiting_for_exit"
    assert runtime.read_bytes() == b"old executable"
    with pytest.raises(UpdateDownloadError):
        service.apply(17, prepared["token"])


def test_failed_helper_leaves_app_running_and_retryable(runtime, release, monkeypatch):
    prepared = service.prepare(17)
    monkeypatch.setattr(service.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(poll=lambda: 1))
    with pytest.raises(UpdateDownloadError, match="non pronto"):
        service.apply(17, prepared["token"])
    assert service._prepared
    assert runtime.read_bytes() == b"old executable"


@pytest.mark.parametrize("status", ["running", "queued", "paused", "scheduled"])
def test_pending_work_blocks_update(monkeypatch, status):
    from app.jobs import job_manager
    # Restore the actual read-only guard rather than modifying job state.
    monkeypatch.setattr(job_manager, "has_active_work", lambda: False)
    monkeypatch.setattr(job_manager, "list_jobs", lambda: [{"status": status}])
    with pytest.raises(UpdateDownloadError, match="lavori"):
        service._idle()


def test_installed_source_unknown_refused(monkeypatch):
    for mode in ("installed", "source", "unknown"):
        monkeypatch.setattr(distribution, "mode", lambda: mode)
        with pytest.raises(UpdateDownloadError, match="solo"):
            service._runtime()


def test_explicit_marker_only(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / service.EXE))
    assert distribution.mode() == "unknown"
    marker = tmp_path / distribution.MARKER
    marker.write_text('{"schema":1,"repository":"wrong","mode":"portable"}')
    assert distribution.mode() == "unknown"
    for mode in ("portable", "installed"):
        marker.write_text(json.dumps(dict(schema=1, repository=distribution.PRODUCT, mode=mode)))
        assert distribution.mode() == mode
    marker.write_text("broken")
    assert distribution.mode() == "unknown"


@pytest.fixture
def local_api(monkeypatch):
    app = FastAPI()
    user = SimpleNamespace(id=17, has=lambda *a, **kw: Permission.MANAGE_SETTINGS in a,
                           username="test")
    @app.middleware("http")
    async def identify(request, call_next):
        request.state.user = user
        return await call_next(request)
    app.include_router(routes.router)
    monkeypatch.setattr(distribution, "mode", lambda: "portable")
    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)), user


def test_local_api_prepare_apply_confirmation_and_owner(local_api, monkeypatch):
    client, _ = local_api
    token = client.get("/api/updates/portable").json()["token"]
    headers = {"Origin": "http://127.0.0.1:8000", "X-Portable-Update-Token": token}
    prepare = Mock(return_value={"token": "a" * 32})
    apply = Mock(return_value={"status": "waiting_for_exit"})
    monkeypatch.setattr(service, "prepare", prepare)
    monkeypatch.setattr(service, "apply", apply)
    assert client.post("/api/updates/portable/prepare", headers=headers).status_code == 200
    prepare.assert_called_once_with(17)
    assert client.post("/api/updates/portable/apply", headers=headers,
                       json={"token": "a" * 32}).status_code == 400
    apply.assert_not_called()
    assert client.post("/api/updates/portable/apply", headers=headers,
                       json={"token": "a" * 32, "confirmed": True}).status_code == 200
    apply.assert_called_once_with(17, "a" * 32)


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.test"},
    {"Origin": "null"}, {"Origin": "http://127.0.0.1:8000", "X-Portable-Update-Token": "wrong"}])
def test_open_mode_still_requires_origin_and_token(local_api, monkeypatch, headers):
    client, _ = local_api
    operation = Mock()
    monkeypatch.setattr(service, "prepare", operation)
    assert client.post("/api/updates/portable/prepare", headers=headers).status_code == 403
    operation.assert_not_called()


def test_manage_settings_checked_server_side(local_api):
    client, user = local_api
    user.has = lambda *a, **kw: False
    for path in ("/portable", "/portable/prepare", "/portable/apply"):
        response = client.request("GET" if path == "/portable" else "POST", "/api/updates" + path)
        assert response.status_code == 403


def test_remote_proxy_and_dns_rebinding_refused(local_api):
    client, _ = local_api
    assert client.get("/api/updates/portable", headers={"Host": "evil.test"}).status_code == 403
    assert client.get("/api/updates/portable", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    remote = TestClient(client.app, base_url="http://127.0.0.1:8000", client=("192.0.2.1", 5555))
    assert remote.get("/api/updates/portable").status_code == 403


def test_redirect_destination_validated_before_request(monkeypatch):
    from app import update_download
    response = Mock(status_code=302, headers={"Location": "http://127.0.0.1/private"})
    get = Mock(return_value=response)
    monkeypatch.setattr(update_download.requests, "get", get)
    with pytest.raises(UpdateDownloadError):
        with update_download.github_response("https://github.com/a/b"):
            pytest.fail("Unsafe redirect accepted")
    assert get.call_count == 1
    response.close.assert_called_once()


@pytest.mark.parametrize("correct", [True, False])
def test_streamed_download_checks_sha256_and_cleans_partial(tmp_path, monkeypatch, correct):
    from app import update_download
    tmp_path = tmp_path / "update-staging"
    tmp_path.mkdir()
    response = Mock(status_code=200)
    response.iter_content.return_value = [b"downloaded", b" executable"]
    monkeypatch.setattr(update_download.requests, "get", Mock(return_value=response))
    digest = hashlib.sha256(b"downloaded executable").hexdigest() if correct else "0" * 64
    if correct:
        path = update_download.download_verified("https://github.com/a/b/new.zip", digest, tmp_path)
        assert path.read_bytes() == b"downloaded executable"
    else:
        with pytest.raises(UpdateDownloadError, match="Checksum"):
            update_download.download_verified("https://github.com/a/b/new.zip", digest, tmp_path)
        assert list(tmp_path.iterdir()) == []


def test_helper_ready_only_after_staging_before_process_exit(tmp_path, monkeypatch):
    from app import portable_update as helper
    exe, asset, backup = (tmp_path / name for name in ("app.exe", "new.exe", "old.exe"))
    exe.write_bytes(b"old")
    asset.write_bytes(b"new")
    ready = tmp_path / "ready"
    @contextmanager
    def process(pid, target):
        def wait(timeout):
            assert ready.read_text() == "ready"
            assert target.read_bytes() == b"old"
            assert list(tmp_path.glob(".portable-update-*.exe"))
        yield wait
    monkeypatch.setattr(helper, "_process", process)
    helper.replace_portable(pid=123, exe=exe, asset=asset, backup=backup,
        sha256=hashlib.sha256(b"new").hexdigest(), ready_file=ready)
    assert exe.read_bytes() == b"new"
    assert backup.read_bytes() == b"old"
