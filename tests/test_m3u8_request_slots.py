"""A retry sleep must not reserve one of the source's scarce connections."""
from types import SimpleNamespace

from app.core import m3u8


def test_request_gate_releases_during_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr(m3u8, "get_settings", lambda: {"max_segment_workers": 4, "source_max_connections": 2})
    gate = m3u8.SourceSlots(2)
    monkeypatch.setattr(m3u8, "_SOURCE_SLOTS", gate)
    monkeypatch.setattr(m3u8, "_segment_budget", None)
    monkeypatch.setattr(m3u8.source_budget, "report_pushback", lambda *a: None)
    seg = m3u8.M3U8_Segments("https://cdn.example/a.m3u8", temp_dir=str(tmp_path))
    calls = []
    waits = []
    def get(*a, **kw):
        assert gate.in_flight == 1
        calls.append(1)
        return SimpleNamespace(status_code=503 if len(calls) == 1 else 200,
                               content=b"segment", headers={})
    def wait(delay):
        waits.append(delay)
        assert gate.in_flight == 0
        # Another download can use every configured slot while this one waits.
        with gate, gate:
            assert gate.in_flight == 2
        return False
    monkeypatch.setattr(m3u8.hosts, "safe_get", get)
    monkeypatch.setattr(seg, "_wait", wait)
    try:
        assert seg.get_req_ts("https://cdn.example/0.ts") == b"segment"
        assert len(calls) == 2 and len(waits) == 1
        assert gate.in_flight == 0
    finally:
        seg._session.close()
