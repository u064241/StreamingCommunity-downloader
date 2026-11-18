"""yt-dlp version check: read-only, offline-safe, never installs anything."""
import pytest

from app.core import ytdlp_update


def test_newer_pypi_version_is_flagged(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "installed_version", lambda: "2025.9.1")
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2025.10.22")
    result = ytdlp_update.status()
    assert result["update_available"] is True
    assert result["installed"] == "2025.9.1" and result["latest"] == "2025.10.22"
    assert result["error"] is None


@pytest.mark.parametrize("installed, latest", [("2025.10.22", "2025.10.22"), ("2025.11.01", "2025.10.22")])
def test_same_or_newer_local_is_not_flagged(monkeypatch, installed, latest):
    monkeypatch.setattr(ytdlp_update, "installed_version", lambda: installed)
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: latest)
    assert ytdlp_update.status()["update_available"] is False


def test_pypi_unreachable_reports_error_and_no_update(monkeypatch):
    def down():
        raise OSError("offline")
    monkeypatch.setattr(ytdlp_update, "installed_version", lambda: "2025.10.22")
    monkeypatch.setattr(ytdlp_update, "latest_version", down)
    result = ytdlp_update.status()
    assert result["latest"] is None
    assert result["update_available"] is False
    assert "PyPI" in result["error"]


def test_missing_install_is_not_flagged(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "installed_version", lambda: None)
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2025.10.22")
    assert ytdlp_update.status()["update_available"] is False
