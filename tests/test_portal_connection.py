import asyncio
from types import SimpleNamespace

import pytest

from app import portal_lifecycle as portal
from app.core import domain_recovery


@pytest.fixture(autouse=True)
def clean_portal(monkeypatch):
    monkeypatch.setattr(portal, "_clients", {})
    monkeypatch.setattr(portal, "_closed_clients", {})
    monkeypatch.setattr(portal, "_last_heartbeat", 0.0)
    monkeypatch.setattr(portal, "_last_close", 0.0)


def test_background_timer_delay_does_not_stop_server(monkeypatch):
    monkeypatch.setattr(portal.time, "monotonic", lambda: 100.0)
    portal.heartbeat("tab")
    monkeypatch.setattr(portal.time, "monotonic", lambda: 7300.0)
    assert not portal.should_stop_idle()


def test_close_waits_for_other_tabs_and_survives_reload(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(portal.time, "monotonic", lambda: clock[0])
    portal.heartbeat("tab1")
    portal.heartbeat("tab2")
    portal.close("tab1")
    clock[0] += 10
    assert not portal.should_stop_idle()
    portal.close("tab2")
    clock[0] += 9
    assert portal.should_stop_idle()
    portal.heartbeat("new-document")
    assert not portal.should_stop_idle()


def test_public_channel_reads_latest_plain_domain_and_skips_struck_out(monkeypatch):
    html = """<div class="tgme_widget_message_text">streamingcommunityz.old</div>
    <div class="tgme_widget_message_text"><s>streamingcommunityz.build</s>
    Nuovo: <code>streamingcommunityz.day</code></div>"""
    response = SimpleNamespace(text=html, raise_for_status=lambda: None)
    monkeypatch.setattr(domain_recovery.requests, "get", lambda *a, **kw: response)
    assert domain_recovery.fetch_telegram_candidates() == ["streamingcommunityz.day"]


def test_download_keeps_event_loop_alive_when_portal_is_closed(monkeypatch):
    from app.jobs import job_manager
    monkeypatch.setattr(portal, "should_stop_idle", lambda *a: True)
    server = SimpleNamespace(should_exit=False)
    jobs = iter([True, False])
    monkeypatch.setattr(job_manager, "has_active_work", lambda: next(jobs))
    calls = []
    async def tick(*a):
        calls.append(server.should_exit)
    monkeypatch.setattr(asyncio, "sleep", tick)
    asyncio.run(portal.stop_server_when_idle(SimpleNamespace(state=SimpleNamespace(uvicorn_server=server))))
    assert calls == [False, False]
    assert server.should_exit


def test_close_beacon_requires_portal_token(client, monkeypatch):
    monkeypatch.setenv("SC_PANEL_PORTAL_TOKEN", "local-portal-token")
    portal.heartbeat("tab")
    assert client.post("/api/portal/close", json={"client_id": "tab", "token": "wrong"}).status_code == 404
    assert "tab" in portal._clients
    assert client.post("/api/portal/close", json={"client_id": "tab", "token": "local-portal-token"}).status_code == 204
    assert "tab" not in portal._clients


def test_late_heartbeat_cannot_resurrect_closed_tab(monkeypatch):
    clock=[100.0]
    monkeypatch.setattr(portal.time,"monotonic",lambda:clock[0])
    portal.heartbeat("closed")
    portal.close("closed")
    clock[0]+=2
    portal.heartbeat("closed")
    clock[0]+=7
    assert portal.should_stop_idle()


def test_close_before_initial_heartbeat_still_stops(monkeypatch):
    clock=[100.0]
    monkeypatch.setattr(portal.time,"monotonic",lambda:clock[0])
    portal.close("fast-close")
    portal.heartbeat("fast-close")
    clock[0]+=9
    assert portal.should_stop_idle()


def test_finished_job_worker_is_still_active_until_cleanup_returns():
    import threading
    from app.jobs import JobManager
    manager=JobManager()
    release=threading.Event()
    future=manager._submit_worker(lambda:release.wait(5))
    try:
        assert manager.has_active_work()
    finally:
        release.set()
        manager._executor.shutdown(wait=True)
    assert future.done()
    assert not manager.has_active_work()
