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
    hls_ytdlp.download_playlist("https://cdn.example/index.m3u8", str(out), ext="mkv",
                                referer="https://site.example/", connections=99)
    argv = fake_exe["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("-N") + 1] == str(hls_ytdlp._MAX_CONNECTIONS)
    assert "Referer:https://site.example/" in argv
    assert argv[-1] == "https://cdn.example/index.m3u8"
    assert "--no-playlist" in argv
    assert argv[argv.index("--remux-video") + 1] == "mkv"
    assert out.read_bytes() == b"x" * 10
    assert not (tmp_path / "video.ytdlp").exists()


def test_extension_with_dot_is_normalised(tmp_path, fake_exe):
    hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "a.mp4"), ext=".mp4",
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
        hls_ytdlp.download_playlist("https://cdn.example/a.m3u8", str(tmp_path / "v.mkv"),
                                    ext="mkv", referer=None, connections=2)
    assert not list(tmp_path.glob("v.ytdlp/*"))
    assert not (tmp_path / "v.mkv").exists()


def _segments(monkeypatch, enabled, key=None):
    monkeypatch.setattr(m3u8, "get_settings", lambda: {"hls_use_ytdlp": enabled})
    obj = object.__new__(m3u8.M3U8_Downloader)
    obj.key = key
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


def test_external_key_stays_on_builtin(monkeypatch):
    monkeypatch.setattr(hls_ytdlp, "available", lambda: True)
    called = []
    monkeypatch.setattr(hls_ytdlp, "download_playlist", lambda *a, **k: called.append(a))
    assert _segments(monkeypatch, True, key=b"k" * 16)._ytdlp_fetch("u", "o", "video", ".mkv") is False
    assert called == []


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
    assert _segments(monkeypatch, True)._ytdlp_fetch("u", "o", "audio_it", "mp4") is True
    assert seen["connections"] == 5 and seen["ext"] == "mp4" and seen["phase"] == "audio_it"


def test_default_setting_is_off():
    from app.config import SETTINGS_DEFAULTS
    assert SETTINGS_DEFAULTS["hls_use_ytdlp"] is False
