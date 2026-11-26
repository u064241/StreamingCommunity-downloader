"""aria2c managed executable and update gate (app/core/aria2_update.py): no network."""
import io
import json

import pytest

from app.core import aria2_update

GOOD_SHA = aria2_update.KNOWN_SHA256["1.37.0"]


class _Response(io.BytesIO):
    def geturl(self):
        return "https://api.github.com/repos/aria2/aria2/releases/latest"


def _release(assets):
    return json.dumps({"tag_name": "release-1.37.0", "assets": assets}).encode("utf-8")


def _asset(name="aria2-1.37.0-win-64bit-build1.zip", digest=None):
    return {"name": name, "size": 2475379, "digest": digest,
            "browser_download_url": f"https://github.com/aria2/aria2/releases/download/release-1.37.0/{name}"}


def test_latest_uses_github_digest_when_published(monkeypatch):
    digest = "sha256:" + "ab" * 32
    monkeypatch.setattr(aria2_update, "_open", lambda url, timeout: _Response(_release([_asset(digest=digest)])))
    release = aria2_update.latest_release()
    assert release["version"] == "1.37.0"
    assert release["sha256"] == "ab" * 32


def test_latest_falls_back_to_known_hash_only_for_known_version(monkeypatch):
    monkeypatch.setattr(aria2_update, "_open", lambda url, timeout: _Response(_release([_asset()])))
    assert aria2_update.latest_release()["sha256"] == GOOD_SHA


def test_newer_release_without_any_hash_is_refused(monkeypatch):
    name = "aria2-9.9.9-win-64bit-build1.zip"
    monkeypatch.setattr(aria2_update, "_open", lambda url, timeout: _Response(_release([_asset(name)])))
    with pytest.raises(aria2_update.Aria2UpdateError):
        aria2_update.latest_release()


def test_asset_outside_github_release_path_is_refused(monkeypatch):
    bad = _asset()
    bad["browser_download_url"] = "https://example.com/aria2-1.37.0-win-64bit-build1.zip"
    monkeypatch.setattr(aria2_update, "_open", lambda url, timeout: _Response(_release([bad])))
    with pytest.raises(aria2_update.Aria2UpdateError):
        aria2_update.latest_release()


def test_hosts_outside_the_allow_list_are_refused():
    with pytest.raises(aria2_update.Aria2UpdateError):
        aria2_update._check_url("https://evil.example/x.zip")
    with pytest.raises(aria2_update.Aria2UpdateError):
        aria2_update._check_url("http://github.com/x.zip")


def test_newer_compares_numerically():
    assert aria2_update._newer("1.38.0", "1.9.0")
    assert not aria2_update._newer("1.37.0", "1.37.0")
    assert not aria2_update._newer(None, "1.37.0")


def test_probe_version_reads_first_line(monkeypatch):
    class Done:
        returncode = 0
        stdout = b"aria2 version 1.37.0\nCopyright (C) 2006, 2019 Tatsuhiro Tsujikawa\n"

    monkeypatch.setattr(aria2_update.subprocess, "run", lambda *a, **k: Done())
    assert aria2_update.probe_version("aria2c.exe") == "1.37.0"


def test_update_refused_while_an_aria2_download_runs(monkeypatch):
    monkeypatch.setattr(aria2_update, "can_update", lambda: True)
    with aria2_update.running():
        with pytest.raises(aria2_update.Aria2BusyError):
            aria2_update.update()
    assert aria2_update.active_downloads() == 0


def test_running_is_refused_during_an_update(monkeypatch):
    monkeypatch.setattr(aria2_update, "_updating", True)
    with pytest.raises(aria2_update.Aria2BusyError):
        with aria2_update.running():
            pass
    monkeypatch.setattr(aria2_update, "_updating", False)
