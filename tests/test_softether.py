"""Local launcher boundaries; no VPN process, service or network is started."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import deps
from app.routers import softether


@pytest.fixture
def panel(monkeypatch):
    app = FastAPI()
    app.add_middleware(deps.AuthMiddleware)
    app.include_router(softether.router)
    user = SimpleNamespace(id=1, username="tester", has=lambda *a, **k: True)
    # No login: every request is the local user. Swapped for a stand-in so a
    # test can take a permission away.
    monkeypatch.setattr(deps, "LOCAL_USER", user)
    launch = Mock()
    monkeypatch.setattr(softether.subprocess, "Popen", launch)
    manager = Path("C:/Program Files/SoftEther VPN Client/vpncmgr_x64.exe")
    monkeypatch.setattr(softether, "_find_manager", lambda: manager)
    monkeypatch.setattr(softether, "_manager_running", lambda _manager: False)
    monkeypatch.setattr(softether, "_manager_state", lambda _manager: "stopped")
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    return client, user, launch, manager


def headers():
    return {"Origin": "http://127.0.0.1:8000",
            "X-SoftEther-Token": softether._TOKEN}


def test_permission(panel):
    client, user, launch, _ = panel
    user.has = lambda *a, **k: False
    assert client.get("/api/softether/status").status_code == 403
    assert client.post("/api/softether/open-manager", headers=headers()).status_code == 403
    launch.assert_not_called()


@pytest.mark.parametrize("extra", [
    {"Origin": "https://other.example"}, {"Origin": "http://["},
    {"X-SoftEther-Token": "wrong"},
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
    assert remote.get("/api/softether/status").status_code == 403
    assert remote.post("/api/softether/open-manager", headers=headers()).status_code == 403
    launch.assert_not_called()


def test_launch_still_requires_local_origin_and_token(panel):
    client, _, launch, _ = panel
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
    assert launch.call_args.args == ([str(manager)],)
    assert launch.call_args.kwargs["cwd"] == str(manager.parent)
    assert launch.call_args.kwargs["shell"] is False
    assert launch.call_args.kwargs["close_fds"] is True
    assert launch.call_args.kwargs["startupinfo"].wShowWindow == 6


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


def _tasklist(monkeypatch, stdout, visible=()):
    class Result:
        returncode = 0
        def __init__(self, text):
            self.stdout = text
    monkeypatch.setattr(softether.subprocess, "run", lambda *a, **k: Result(stdout))
    monkeypatch.setattr(softether, "_visible_window_pids", lambda: set(visible))


RUNNING_ROW = ('"vpncmgr_x64.exe","4242","Console","1","52,324 K","Running",'
               '"PC\\me","0:00:01","SoftEther VPN Client Manager"\n')


def test_manager_state_tells_stopped_tray_and_visible_apart(monkeypatch):
    manager = Path("C:/Program Files/SoftEther VPN Client/vpncmgr_x64.exe")
    monkeypatch.setattr(softether.os, "name", "nt")

    _tasklist(monkeypatch, "INFO: No tasks are running which match the specified criteria.\n")
    assert softether._manager_state(manager) == "stopped"

    # Running, hidden in the tray: its window exists but is not visible.
    _tasklist(monkeypatch, RUNNING_ROW, visible=[9999])
    assert softether._manager_state(manager) == "tray"

    # Running with its window on screen.
    _tasklist(monkeypatch, RUNNING_ROW, visible=[4242])
    assert softether._manager_state(manager) == "visible"


def test_a_title_alone_does_not_make_the_manager_visible(monkeypatch):
    # Regression: a manager sitting in the tray keeps its window title, and the
    # title used to be read as "window open", so "Chiudi" stayed enabled.
    manager = Path("C:/Program Files/SoftEther VPN Client/vpncmgr_x64.exe")
    monkeypatch.setattr(softether.os, "name", "nt")
    _tasklist(monkeypatch, RUNNING_ROW, visible=[])
    assert softether._manager_state(manager) == "tray"


def test_a_tray_manager_is_asked_for_its_window(monkeypatch, tmp_path):
    client_module = softether
    manager = Path("C:/Program Files/SoftEther VPN Client/vpncmgr_x64.exe")
    launch = Mock()
    monkeypatch.setattr(softether.subprocess, "Popen", launch)
    monkeypatch.setattr(softether, "_find_manager", lambda: manager)
    monkeypatch.setattr(softether, "_manager_state", lambda _manager: "tray")
    monkeypatch.setattr(softether, "_ensure_client_service", lambda: None)
    monkeypatch.setattr(softether.subprocess, "STARTUPINFO", lambda: SimpleNamespace(dwFlags=0, wShowWindow=0), raising=False)
    user = SimpleNamespace(id=1, username="tester", has=lambda *a, **k: True)
    monkeypatch.setattr(deps, "LOCAL_USER", user)
    app = FastAPI()
    app.include_router(client_module.router)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    response = client.post("/api/softether/open-manager", headers=headers())

    assert response.json() == {"opened": True}
    assert launch.call_args.kwargs["startupinfo"].wShowWindow == 1  # SW_SHOWNORMAL, not minimized


def test_a_visible_manager_is_not_launched_again(monkeypatch):
    manager = Path("C:/Program Files/SoftEther VPN Client/vpncmgr_x64.exe")
    launch = Mock()
    monkeypatch.setattr(softether.subprocess, "Popen", launch)
    monkeypatch.setattr(softether, "_find_manager", lambda: manager)
    monkeypatch.setattr(softether, "_manager_state", lambda _manager: "visible")
    user = SimpleNamespace(id=1, username="tester", has=lambda *a, **k: True)
    monkeypatch.setattr(deps, "LOCAL_USER", user)
    app = FastAPI()
    app.include_router(softether.router)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    response = client.post("/api/softether/open-manager", headers=headers())

    assert response.json() == {"opened": True, "already_running": True}
    assert not any(call.args and call.args[0] == [str(manager)] for call in launch.call_args_list)


def test_hide_only_acts_on_a_visible_manager(monkeypatch):
    manager = Path("C:/Program Files/SoftEther VPN Client/vpncmgr_x64.exe")
    hide = Mock(return_value=2)
    monkeypatch.setattr(softether, "_find_manager", lambda: manager)
    monkeypatch.setattr(softether, "_hide_manager_windows", hide)
    user = SimpleNamespace(id=1, username="tester", has=lambda *a, **k: True)
    monkeypatch.setattr(deps, "LOCAL_USER", user)
    monkeypatch.setattr(softether.os, "name", "nt")
    app = FastAPI()
    app.include_router(softether.router)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    monkeypatch.setattr(softether, "_manager_state", lambda _m: "tray")
    assert client.post("/api/softether/hide-manager", headers=headers()).json() == {"hidden": False, "reason": "not_visible"}
    hide.assert_not_called()

    monkeypatch.setattr(softether, "_manager_state", lambda _m: "visible")
    assert client.post("/api/softether/hide-manager", headers=headers()).json() == {"hidden": True}
    hide.assert_called_once()


def test_close_really_ends_the_manager_process(monkeypatch):
    # Without /F taskkill sends a close request, which the Manager answers by
    # hiding to the tray; "Chiudi" must end the process instead.
    calls = []
    class Result:
        returncode = 0
    monkeypatch.setattr(softether.subprocess, "run", lambda args, **k: calls.append(args) or Result())
    monkeypatch.setattr(softether, "_find_manager", lambda: Path("C:/x/vpncmgr_x64.exe"))
    user = SimpleNamespace(id=1, username="tester", has=lambda *a, **k: True)
    monkeypatch.setattr(deps, "LOCAL_USER", user)
    app = FastAPI()
    app.include_router(softether.router)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    assert client.post("/api/softether/close-manager", headers=headers()).json() == {"closed": True}
    assert calls and "/F" in calls[0] and "vpncmgr_x64.exe" in calls[0]


def test_public_ip_asks_only_the_ip_service(panel, monkeypatch):
    """The "Controlla IP pubblico" button reaches api.ipify.org, never the source."""
    client, *_ = panel
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ip": "203.0.113.7"})

    monkeypatch.setattr(softether.requests, "get", fake_get)
    monkeypatch.setattr(softether, "_original_public_ip", None)
    response = client.get("/api/softether/public-ip")
    assert response.status_code == 200
    assert response.json()["current"] == "203.0.113.7"
    assert calls == ["https://api.ipify.org"]
    assert not any("streamingcommunity" in url for url in calls)


def test_public_ip_unavailable_is_a_503(panel, monkeypatch):
    client, *_ = panel

    def offline(url, **kwargs):
        raise softether.requests.ConnectionError("offline")

    monkeypatch.setattr(softether.requests, "get", offline)
    assert client.get("/api/softether/public-ip").status_code == 503
