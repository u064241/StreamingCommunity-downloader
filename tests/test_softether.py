"""Local launcher boundaries; no VPN process, service or network is started."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import deps, session
from app.routers import softether


@pytest.fixture
def panel(monkeypatch):
    app = FastAPI()
    app.add_middleware(deps.AuthMiddleware)
    app.include_router(softether.router)
    user = SimpleNamespace(id=1, username="tester", has=lambda *a, **k: True)
    monkeypatch.setattr(deps.models, "runtime_open_mode", lambda: False)
    monkeypatch.setattr(session, "resolve_session", lambda token: (user, "csrf"))
    launch = Mock()
    monkeypatch.setattr(softether.subprocess, "Popen", launch)
    manager = Path("C:/Program Files/SoftEther VPN Client/vpncmgr_x64.exe")
    monkeypatch.setattr(softether, "_find_manager", lambda: manager)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    client.cookies.set(session.SESSION_COOKIE, "test-session")
    return client, user, launch, manager


def headers():
    return {"Origin": "http://127.0.0.1:8000", session.CSRF_HEADER: "csrf",
            "X-SoftEther-Token": softether._TOKEN}


def test_authentication_and_permission(panel):
    client, user, launch, _ = panel
    client.cookies.clear()
    assert client.get("/api/softether/status").status_code == 401
    assert client.post("/api/softether/open-manager", headers=headers()).status_code == 401
    client.cookies.set(session.SESSION_COOKIE, "test-session")
    user.has = lambda *a, **k: False
    assert client.get("/api/softether/status").status_code == 403
    assert client.post("/api/softether/open-manager", headers=headers()).status_code == 403
    launch.assert_not_called()


@pytest.mark.parametrize("extra", [
    {"Origin": "https://other.example"}, {"Origin": "http://["},
    {"X-SoftEther-Token": "wrong"}, {session.CSRF_HEADER: "wrong"},
    {"X-Forwarded-For": "127.0.0.1"}, {"Forwarded": "for=127.0.0.1"},
    {"Host": "other.example"},
])
def test_reject_unsafe_launch(panel, extra):
    client, _, launch, _ = panel
    assert client.post("/api/softether/open-manager", headers=headers() | extra).status_code == 403
    launch.assert_not_called()


def test_remote_client_rejected(panel):
    client, _, launch, _ = panel
    remote = TestClient(client.app, base_url="http://127.0.0.1:8000", client=("192.0.2.1", 50000))
    remote.cookies.set(session.SESSION_COOKIE, "test-session")
    assert remote.get("/api/softether/status").status_code == 403
    assert remote.post("/api/softether/open-manager", headers=headers()).status_code == 403
    launch.assert_not_called()


def test_open_mode_still_requires_local_origin_and_token(panel, monkeypatch):
    client, _, launch, _ = panel
    monkeypatch.setattr(deps.models, "runtime_open_mode", lambda: True)
    client.cookies.clear()
    assert client.get("/api/softether/status").status_code == 200
    assert client.post("/api/softether/open-manager").status_code == 403
    assert client.post("/api/softether/open-manager", headers={"Origin": headers()["Origin"]}).status_code == 403
    launch.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_status_and_exact_manager_launch(panel):
    client, _, launch, manager = panel
    response = client.get("/api/softether/status")
    assert response.json()["installed"] is True
    assert response.headers["cache-control"] == "no-store"
    launch.assert_not_called()
    assert client.post("/api/softether/open-manager", headers=headers()).json() == {"opened": True}
    launch.assert_called_once_with([str(manager)], executable=str(manager),
                                  cwd=str(manager.parent), shell=False, close_fds=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_recheck_and_launch_errors(panel, monkeypatch):
    client, _, launch, _ = panel
    assert client.get("/api/softether/status").json()["installed"]
    monkeypatch.setattr(softether, "_find_manager", lambda: None)
    assert client.post("/api/softether/open-manager", headers=headers()).status_code == 404
    launch.assert_not_called()
    monkeypatch.setattr(softether, "_find_manager", lambda: Path("C:/Program Files/SoftEther VPN Client/vpncmgr.exe"))
    launch.side_effect = OSError("cannot start")
    assert client.post("/api/softether/open-manager", headers=headers()).status_code == 503


@pytest.mark.parametrize("query,body", [("?path=C:/evil.exe", None), ("", '{"path":"C:/evil.exe"}')])
def test_no_caller_parameters(panel, query, body):
    client, _, launch, _ = panel
    assert client.post("/api/softether/open-manager" + query, headers=headers(), content=body).status_code == 400
    launch.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows discovery")
def test_discovery_only_manager_and_no_reparse(tmp_path, monkeypatch):
    monkeypatch.setattr(softether, "_program_files", lambda: [tmp_path])
    folder = tmp_path / "SoftEther VPN Client"
    folder.mkdir()
    (folder / "vpnclient_x64.exe").write_bytes(b"test")
    assert softether._find_manager() is None
    manager = folder / "vpncmgr.exe"
    manager.write_bytes(b"test")
    assert softether._find_manager() == manager
    manager64 = folder / "vpncmgr_x64.exe"
    manager64.write_bytes(b"test")
    assert softether._find_manager() == manager64
    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_file_attributes=0x400)
                        if path == folder else real_lstat(path))
    assert softether._find_manager() is None
