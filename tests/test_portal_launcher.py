"""Exercise the desktop entry point without opening a browser or real server."""
import os
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


def launch(monkeypatch, *, closing=False, delayed=False, restart=False, enabled=True):
    import uvicorn
    import webbrowser
    from app import distribution, windows_registration

    opened, pending = [], []
    server = SimpleNamespace(started=False, should_exit=False, force_exit=False)

    def run():
        server.started = True
        server.should_exit = closing
        if pending and not delayed:
            pending[0]()

    server.run = run
    monkeypatch.setattr(uvicorn, "Server", lambda config: server)
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **kw: None)
    monkeypatch.setattr(windows_registration, "register_portable", lambda: None)
    monkeypatch.setattr(distribution, "desktop_lifecycle", lambda host: True)
    fake_app = ModuleType("app.main")
    fake_app.app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "app.main", fake_app)
    monkeypatch.setenv("SC_PANEL_OPEN_BROWSER", "1" if enabled else "0")
    monkeypatch.setenv("SC_PANEL_RESTART", "1" if restart else "0")
    monkeypatch.setenv("SC_PANEL_PORTAL_TOKEN", "test-launcher")
    import threading
    monkeypatch.setattr(threading, "Thread", lambda *, target, **kw: SimpleNamespace(
        start=lambda: pending.append(target)))
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    import os
    monkeypatch.setattr(os, "startfile", lambda url: opened.append(url), raising=False)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "main.py"), run_name="__main__")
    assert result.value.code == 0
    if delayed and pending:
        pending[0]()
    return opened, server


def test_shutdown_before_browser_thread_runs_does_not_open_page(monkeypatch):
    opened, _ = launch(monkeypatch, closing=True)
    assert opened == []


def test_browser_thread_after_server_returns_does_not_open_page(monkeypatch):
    opened, _ = launch(monkeypatch, delayed=True)
    assert opened == []


def test_normal_start_opens_once(monkeypatch):
    opened, _ = launch(monkeypatch)
    assert len(opened) == 1
    assert opened[0].startswith("http://")


@pytest.mark.parametrize("options", [{"restart": True}, {"enabled": False}])
def test_restart_and_opt_out_do_not_open_browser(monkeypatch, options):
    opened, _ = launch(monkeypatch, **options)
    assert opened == []


@pytest.mark.skipif(os.name != "nt", reason="Windows URL association")
def test_windows_dispatch_cannot_wait_for_browser_then_open_fallback(monkeypatch):
    import main
    import threading
    import webbrowser

    calls = []
    # Real webbrowser dispatch would wait for the first browser, then fall
    # back on its nonzero exit. No real process or browser is launched here.
    command_browser = webbrowser.GenericBrowser(["browser", "%s"])
    fallback = SimpleNamespace(open=lambda *a: calls.append("fallback page") or True)
    monkeypatch.setattr(webbrowser, "_tryorder", ["command", "fallback"])
    monkeypatch.setattr(webbrowser, "_browsers", {
        "command": [None, command_browser], "fallback": [None, fallback]})
    process = SimpleNamespace(wait=lambda: calls.append("browser closed") or 1)
    monkeypatch.setattr(webbrowser.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(os, "startfile", lambda url: calls.append(url))
    main.open_panel_when_ready(
        SimpleNamespace(started=True, should_exit=False, force_exit=False), threading.Event())
    assert calls == [f"http://{main.HOST}:{main.PORT}/"]


@pytest.mark.parametrize("state", ["stopped", "should_exit", "force_exit"])
def test_cancelled_startup_never_dispatches(monkeypatch, state):
    import main
    import threading

    server = SimpleNamespace(started=False, should_exit=False, force_exit=False)
    stopped = threading.Event()
    if state == "stopped":
        stopped.set()
    else:
        setattr(server, state, True)
    dispatch = Mock()
    monkeypatch.setattr(os, "startfile", dispatch, raising=False)
    monkeypatch.setattr(main.webbrowser, "open", dispatch)
    main.open_panel_when_ready(server, stopped)
    dispatch.assert_not_called()


def test_waits_for_readiness_before_opening(monkeypatch):
    import main

    server = SimpleNamespace(started=False, should_exit=False, force_exit=False)
    waits, urls = [], []

    def tick(delay):
        waits.append(delay)
        server.started = True
        return False

    monkeypatch.setattr(main, "HOST", "::")
    monkeypatch.setattr(os, "startfile", urls.append, raising=False)
    monkeypatch.setattr(main.webbrowser, "open", urls.append)
    main.open_panel_when_ready(server, SimpleNamespace(wait=tick, is_set=lambda: False))
    assert waits == [0.1]
    assert urls == [f"http://[::1]:{main.PORT}/"]


@pytest.mark.skipif(os.name != "nt", reason="Windows URL association")
def test_failed_url_association_logs_without_browser_retry(monkeypatch, caplog):
    import main
    import threading

    def fail(url):
        raise OSError("No URL association")

    fallback = Mock()
    monkeypatch.setattr(os, "startfile", fail)
    monkeypatch.setattr(main.webbrowser, "open", fallback)
    main.open_panel_when_ready(
        SimpleNamespace(started=True, should_exit=False, force_exit=False), threading.Event())
    fallback.assert_not_called()
    assert "Cannot open portal browser" in caplog.text


def test_portable_restart_explicitly_disables_browser(monkeypatch, tmp_path):
    from app import portable_update

    spawn = Mock()
    monkeypatch.setattr(portable_update.subprocess, "Popen", spawn)
    monkeypatch.setenv("SC_PANEL_OPEN_BROWSER", "1")
    monkeypatch.setenv("SC_PANEL_RESTART", "0")
    exe = tmp_path / "App.exe"
    portable_update._restart(exe)
    spawn.assert_called_once()
    assert spawn.call_args.args == ([str(exe)],)
    assert spawn.call_args.kwargs["cwd"] == exe.parent
    assert spawn.call_args.kwargs["env"]["SC_PANEL_RESTART"] == "1"
    assert spawn.call_args.kwargs["env"]["SC_PANEL_OPEN_BROWSER"] == "0"
    assert os.environ["SC_PANEL_OPEN_BROWSER"] == "1"
