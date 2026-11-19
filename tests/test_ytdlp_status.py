"""yt-dlp status: read-only, offline-safe, never installs anything."""
import pytest

from app.core import ytdlp_update


@pytest.fixture
def present(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: str(tmp_path / "yt-dlp.exe"))


def test_newer_github_version_is_flagged(monkeypatch, present):
    monkeypatch.setattr(ytdlp_update, "probe_version", lambda exe, timeout=None: "2025.9.1")
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2025.10.22")
    result = ytdlp_update.status()
    assert result["update_available"] is True and result["present"] is True
    assert result["installed"] == "2025.9.1" and result["latest"] == "2025.10.22"
    assert result["error"] is None


@pytest.mark.parametrize("installed, latest", [("2025.10.22", "2025.10.22"), ("2025.11.01", "2025.10.22")])
def test_same_or_newer_local_is_not_flagged(monkeypatch, present, installed, latest):
    monkeypatch.setattr(ytdlp_update, "probe_version", lambda exe, timeout=None: installed)
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: latest)
    assert ytdlp_update.status()["update_available"] is False


def test_github_unreachable_reports_error_and_no_update(monkeypatch, present):
    def down():
        raise OSError("offline")
    monkeypatch.setattr(ytdlp_update, "probe_version", lambda exe, timeout=None: "2025.10.22")
    monkeypatch.setattr(ytdlp_update, "latest_version", down)
    result = ytdlp_update.status()
    assert result["latest"] is None
    assert result["update_available"] is False
    assert "GitHub" in result["error"]


def test_missing_install_is_reported_not_flagged(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: None)
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2025.10.22")
    result = ytdlp_update.status()
    assert result["present"] is False and result["installed"] is None
    assert result["update_available"] is False


def test_status_route_never_downloads(client, monkeypatch):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: None)
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2025.10.22")
    monkeypatch.setattr(ytdlp_update, "update", lambda **k: pytest.fail("status must not update"))
    response = client.get("/api/domain/ytdlp/status")
    assert response.status_code == 200 and response.json()["present"] is False


def test_version_probe_reads_the_executable(monkeypatch, tmp_path):
    from types import SimpleNamespace
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b"2025.10.22\n", stderr=b"")

    monkeypatch.setattr(ytdlp_update.subprocess, "run", run)
    assert ytdlp_update.probe_version(tmp_path / "yt-dlp.exe") == "2025.10.22"
    assert seen["argv"][1] == "--version" and seen["shell"] is False and seen["timeout"]
    monkeypatch.setattr(ytdlp_update.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"garbage", stderr=b""))
    assert ytdlp_update.probe_version(tmp_path / "yt-dlp.exe") is None
