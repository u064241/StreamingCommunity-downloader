"""Opt-in yt-dlp engine for HLS playlists (app/core/hls_ytdlp.py).

No network: yt-dlp itself is replaced by a fake that writes a file.
"""
import contextlib
import os
from pathlib import Path

import pytest

from app.core import hls_ytdlp, m3u8, ytdlp_update


@contextlib.contextmanager
def _no_busy_lock():
    yield


@pytest.fixture
def fake_exe(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "in_use", _no_busy_lock)
    monkeypatch.setattr(ytdlp_update, "require_executable", lambda: "yt-dlp.exe")
    monkeypatch.setattr(hls_ytdlp, "_ffmpeg_dir", lambda: "ffmpeg")
    calls = {}

    def fake_run(argv, on_line, cancel_event=None, timeout=0):
        calls["argv"] = list(argv)
        template = argv[argv.index("-o") + 1]
        folder = os.path.dirname(template)
        produced = os.path.join(folder, "hls_stream.mkv")
        Path(produced).write_bytes(b"x" * 10)
        on_line(f"{hls_ytdlp._FILE_TAG}\t{produced}\n")
        return 0, b""

    monkeypatch.setattr(hls_ytdlp, "_run_download", fake_run)
    return calls


def test_argv_is_list_with_capped_connections_and_referer(tmp_path, fake_exe):
    out = tmp_path / "video.mkv"
    hls_ytdlp.download_playlist("https://cdn.example/index.m3u8", str(out), work_dir=str(tmp_path / "work"), ext="mkv",
                                referer="https://site.example/", connections=99)
    argv = fake_exe["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("-N") + 1] == str(hls_ytdlp._MAX_CONNECTIONS)
    assert "Referer:https://site.example/" in argv
    assert argv[-1] == "https://cdn.example/index.m3u8"
    assert "--no-playlist" in argv
    assert argv[argv.index("--remux-video") + 1] == "mkv"
    assert out.read_bytes() == b"x" * 10
    assert not (tmp_path / "work").exists()


def test_extension_with_dot_is_normalised(tmp_path, fake_exe):
    hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "a.mp4"), work_dir=str(tmp_path / "work"), ext=".mp4",
                                referer=None, connections=2)
    assert fake_exe["argv"][fake_exe["argv"].index("--remux-video") + 1] == "mp4"
    assert "Referer:" not in " ".join(fake_exe["argv"])


def test_failure_raises_engine_error_and_leaves_no_partials(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp_update, "in_use", _no_busy_lock)
    monkeypatch.setattr(ytdlp_update, "require_executable", lambda: "yt-dlp.exe")
    monkeypatch.setattr(hls_ytdlp, "_ffmpeg_dir", lambda: "ffmpeg")

    def failing(argv, on_line, cancel_event=None, timeout=0):
        folder = os.path.dirname(argv[argv.index("-o") + 1])
        Path(folder, "hls_stream.mkv.part").write_bytes(b"half")
        return 1, b"ERROR: HTTP Error 403"

    monkeypatch.setattr(hls_ytdlp, "_run_download", failing)
    with pytest.raises(hls_ytdlp.HlsEngineError, match="403"):
        hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "v.mkv"), work_dir=str(tmp_path / "w"),
                                    ext="mkv", referer=None, connections=2)
    assert not (tmp_path / "w").exists()
    assert not (tmp_path / "v.mkv").exists()


def tmp_path_factory_dir():
    import tempfile
    return tempfile.mkdtemp()


def _segments(monkeypatch, enabled, key=None):
    monkeypatch.setattr(m3u8, "get_settings", lambda: {"hls_use_ytdlp": enabled})
    obj = object.__new__(m3u8.M3U8_Downloader)
    obj.key = key
    obj.temp_dir = str(tmp_path_factory_dir())
    obj.referer = None
    obj.cancel_event = None
    obj.progress_factory = None
    return obj


def test_setting_off_never_calls_engine(monkeypatch):
    monkeypatch.setattr(hls_ytdlp, "available", lambda: True)
    called = []
    monkeypatch.setattr(hls_ytdlp, "download_playlist", lambda *a, **k: called.append(a))
    assert _segments(monkeypatch, False)._ytdlp_fetch("u", "o", "video", ".mkv") is False
    assert called == []


def test_external_key_goes_to_ytdlp(monkeypatch):
    # vixcloud/StreamingCommunity: yt-dlp reads the key from EXT-X-KEY itself
    # (checked on a real episode), so the external key no longer blocks it.
    monkeypatch.setattr(hls_ytdlp, "available", lambda: True)
    called = []
    monkeypatch.setattr(hls_ytdlp, "download_playlist", lambda *a, **k: called.append(a))
    assert _segments(monkeypatch, True, key=b"k" * 16)._ytdlp_fetch("u", "o", "video", ".mkv") is True
    assert len(called) == 1


def test_setting_on_without_ytdlp_logs_why_once(monkeypatch, caplog):
    monkeypatch.setattr(hls_ytdlp, "available", lambda: False)
    called = []
    monkeypatch.setattr(hls_ytdlp, "download_playlist", lambda *a, **k: called.append(a))
    obj = _segments(monkeypatch, True)
    with caplog.at_level("WARNING", logger=m3u8.logger.name):
        assert obj._ytdlp_fetch("u", "o", "video", ".mkv") is False
        assert obj._ytdlp_fetch("u2", "o2", "audio_it", "mp4") is False
    lines = [r.getMessage() for r in caplog.records if "yt-dlp HLS attivo" in r.getMessage()]
    assert len(lines) == 1
    assert "Controlla yt-dlp" in lines[0] and "downloader interno" in lines[0]
    assert called == []


def test_setting_off_logs_nothing(monkeypatch, caplog):
    monkeypatch.setattr(hls_ytdlp, "available", lambda: False)
    with caplog.at_level("INFO", logger=m3u8.logger.name):
        assert _segments(monkeypatch, False, key=b"k" * 16)._ytdlp_fetch("u", "o", "video", ".mkv") is False
    assert not any("yt-dlp" in r.getMessage() for r in caplog.records)


def test_engine_error_falls_back(monkeypatch):
    monkeypatch.setattr(hls_ytdlp, "available", lambda: True)

    def boom(*a, **k):
        raise hls_ytdlp.HlsEngineError("yt-dlp HLS fallito")

    monkeypatch.setattr(hls_ytdlp, "download_playlist", boom)
    assert _segments(monkeypatch, True)._ytdlp_fetch("u", "o", "video", ".mkv") is False


def test_success_returns_true_with_source_cap(monkeypatch):
    monkeypatch.setattr(hls_ytdlp, "available", lambda: True)
    seen = {}

    def ok(url, out, **kwargs):
        seen.update(kwargs, url=url, out=out)

    monkeypatch.setattr(hls_ytdlp, "download_playlist", ok)
    monkeypatch.setattr(m3u8, "_source_max_connections", lambda: 5)
    # The slot pool is sized at import from the settings file; pin it so the test
    # does not depend on whatever settings the machine has.
    monkeypatch.setattr(m3u8, "_SOURCE_SLOTS", m3u8.SourceSlots(5))
    assert _segments(monkeypatch, True)._ytdlp_fetch("u", "o", "audio_it", "mp4") is True
    assert seen["connections"] == 5 and seen["ext"] == "mp4" and seen["phase"] == "audio_it"
    assert seen["work_dir"].endswith("ytdlp_audio_it")


def test_default_setting_is_off():
    from app.config import SETTINGS_DEFAULTS
    assert SETTINGS_DEFAULTS["hls_use_ytdlp"] is False


def test_argv_aborts_on_holes_and_ignores_user_config(tmp_path, fake_exe):
    hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "v.mkv"),
                                work_dir=str(tmp_path / "w"), ext="mkv", referer=None, connections=2)
    argv = fake_exe["argv"]
    assert "--abort-on-unavailable-fragments" in argv
    assert "--ignore-config" in argv


def test_os_replace_failure_becomes_engine_error(tmp_path, fake_exe, monkeypatch):
    def refuse(src, dst):
        raise PermissionError("bloccato")

    monkeypatch.setattr(hls_ytdlp.os, "replace", refuse)
    with pytest.raises(hls_ytdlp.HlsEngineError, match="spostamento"):
        hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "v.mkv"),
                                    work_dir=str(tmp_path / "w"), ext="mkv", referer=None, connections=2)
    assert not (tmp_path / "w").exists()


def test_pushback_in_output_reports_to_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp_update, "in_use", _no_busy_lock)
    monkeypatch.setattr(ytdlp_update, "require_executable", lambda: "yt-dlp.exe")
    monkeypatch.setattr(hls_ytdlp, "_ffmpeg_dir", lambda: "ffmpeg")
    monkeypatch.setattr(hls_ytdlp.source_budget, "blocked", lambda: None)
    reported = []
    monkeypatch.setattr(hls_ytdlp.source_budget, "report_pushback", lambda reason="": reported.append(reason))

    def refused(argv, on_line, cancel_event=None, timeout=0):
        return 1, b"ERROR: unable to download fragment: HTTP Error 503: Service Unavailable"

    monkeypatch.setattr(hls_ytdlp, "_run_download", refused)
    with pytest.raises(hls_ytdlp.HlsEngineError):
        hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "v.mkv"),
                                    work_dir=str(tmp_path / "w"), ext="mkv", referer=None, connections=2)
    assert reported == ["yt-dlp HLS"]


def test_paused_source_never_starts_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(hls_ytdlp.source_budget, "blocked", lambda: "backoff")
    started = []
    monkeypatch.setattr(hls_ytdlp, "_run", lambda *a, **k: started.append(a))
    with pytest.raises(hls_ytdlp.HlsEngineError, match="pausa"):
        hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "v.mkv"),
                                    work_dir=str(tmp_path / "w"), ext="mkv", referer=None, connections=2)
    assert started == []


def test_hold_blocks_other_jobs_until_released():
    from app.core.m3u8 import SourceSlots
    slots = SourceSlots(4)
    with slots.hold(3) as held:
        assert held == 3 and slots.in_flight == 3
        assert slots.in_flight + 2 > slots.maximum  # a second job of 2 must wait
    assert slots.in_flight == 0


def test_hold_never_asks_more_than_the_cap():
    from app.core.m3u8 import SourceSlots
    slots = SourceSlots(2)
    with slots.hold(8) as held:
        assert held == 2
    assert slots.in_flight == 0


def test_video_only_format_selection(tmp_path, fake_exe):
    hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "v.mkv"),
                                work_dir=str(tmp_path / "w"), ext="mkv", referer=None, connections=2)
    argv = fake_exe["argv"]
    assert argv[argv.index("-f") + 1] == "bv*/b"


def test_stats_line_logged_even_when_engine_raises(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ytdlp_update, "in_use", _no_busy_lock)
    monkeypatch.setattr(ytdlp_update, "require_executable", lambda: "yt-dlp.exe")
    monkeypatch.setattr(hls_ytdlp, "_ffmpeg_dir", lambda: "ffmpeg")

    def cancelled(argv, on_line, cancel_event=None, timeout=0):
        raise hls_ytdlp.DownloadCancelledError()

    monkeypatch.setattr(hls_ytdlp, "_run_download", cancelled)
    with pytest.raises(hls_ytdlp.DownloadCancelledError):
        hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "v.mkv"),
                                    work_dir=str(tmp_path / "w"), ext="mkv", referer=None, connections=2)
