"""First-run banner for yt-dlp and FFmpeg: local presence check only."""
import re
from pathlib import Path

import pytest

from app.core import ytdlp_update
from app.core import ffmpeg_path
from app.routers import domain

ROOT = Path(__file__).resolve().parents[1]


def test_presence_reports_both_tools_present(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: "C:/data/yt-dlp/yt-dlp.exe")
    monkeypatch.setattr(ffmpeg_path, "get_ffmpeg_exe", lambda: "C:/data/ffmpeg.exe")
    assert domain.tools_presence() == {"ytdlp": True, "ffmpeg": True}


def test_presence_reports_missing_tools_without_installing(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: None)

    def no_ffmpeg():
        raise RuntimeError("ffmpeg executable not found")

    monkeypatch.setattr(ffmpeg_path, "get_ffmpeg_exe", no_ffmpeg)
    installs = []
    monkeypatch.setattr(ytdlp_update, "update", lambda **kw: installs.append(kw))
    assert domain.tools_presence() == {"ytdlp": False, "ffmpeg": False}
    assert installs == []


def test_presence_route_is_gated_by_download_permission():
    src = (ROOT / "app" / "routers" / "domain.py").read_text(encoding="utf-8")
    block = src[src.index('"/tools/presence"'):]
    block = block[:block.index("\n\n\n")]
    assert "Permission.DOWNLOAD" in block


def test_banner_is_wired_to_registered_actions():
    html = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="tools-banner"' in html
    for action in ("tools:dismiss", "tools:settings"):
        assert f'data-action="{action}"' in html
        assert re.search(rf"'{re.escape(action)}'\s*:", js), action
