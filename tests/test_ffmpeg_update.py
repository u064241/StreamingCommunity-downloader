"""Offline update transactions and encoder regressions (including Pascal)."""
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config
from app.core import ffmpeg_update as updater, transcode


def rows(nvenc=True):
    return [{"codec": codec, "backend": backend, "encoder": f"{codec}_{backend}",
             "available": backend == "cpu" or nvenc}
            for codec in ("h264", "hevc") for backend in ("cpu", "nvenc")]


@pytest.mark.parametrize("codec,backend", [("copy", "auto"), ("h264", "cpu"), ("hevc", "auto")])
def test_preserves_both_nvenc_codecs_even_with_copy_or_cpu(monkeypatch, codec, backend):
    monkeypatch.setattr(config, "get_settings", lambda: {"video_conversion": codec, "video_encoder": backend})
    monkeypatch.setattr(transcode, "capabilities", lambda **kw: {"encoders": rows()})
    monkeypatch.setattr(transcode, "_detect", lambda exe: rows(nvenc=exe != "candidate"))
    with pytest.raises(updater.HardwareCompatibilityError, match="nvenc"):
        updater._test_hardware_compatibility(Path("candidate"))


def test_bundled_baseline_protects_already_broken_managed_binary(monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda: {"video_conversion": "copy"})
    monkeypatch.setattr(transcode, "capabilities", lambda **kw: {"encoders": rows(False)})
    monkeypatch.setattr(transcode, "_detect", lambda exe: rows(exe != "candidate"))
    with pytest.raises(updater.HardwareCompatibilityError, match="nvenc"):
        updater._test_hardware_compatibility(Path("candidate"))


@pytest.mark.parametrize("codec", ["copy", "h264", "hevc"])
def test_compatible_71_remains_valid(monkeypatch, codec):
    monkeypatch.setattr(config, "get_settings", lambda: {"video_conversion": codec})
    monkeypatch.setattr(transcode, "capabilities", lambda **kw: {"encoders": rows()})
    monkeypatch.setattr(transcode, "_detect", lambda exe: rows())
    assert updater._test_hardware_compatibility(Path("ffmpeg-7.1"))["codec"] == codec


def test_new_release_can_install_without_version_ceiling(monkeypatch):
    monkeypatch.setattr(updater, "_wheel_info", lambda: {"info": {"version": "9.0"}})
    monkeypatch.setattr(updater, "_install", lambda info: {"ok": True, "version": info["info"]["version"]})
    assert updater.update() == {"ok": True, "version": "9.0"}


def test_incompatible_latest_falls_back_once(monkeypatch):
    seen = []
    monkeypatch.setattr(updater, "_wheel_info", lambda version=None: {"info": {"version": version or "0.7.0"}})
    def install(info):
        version = info["info"]["version"]
        seen.append(version)
        if version != "0.6.0":
            raise updater.HardwareCompatibilityError("hevc/nvenc API")
        return {"ok": True, "version": version}
    monkeypatch.setattr(updater, "_install", install)
    assert updater.update()["fallback"] is True
    assert seen == ["0.7.0", "0.6.0"]


def test_integrity_errors_do_not_trigger_fallback(monkeypatch):
    seen = []
    monkeypatch.setattr(updater, "_wheel_info", lambda: {})
    def install(info):
        seen.append(info)
        raise RuntimeError("SHA-256")
    monkeypatch.setattr(updater, "_install", install)
    with pytest.raises(RuntimeError, match="SHA-256"):
        updater.update()
    assert len(seen) == 1


def test_no_wrong_architecture_or_yanked_wheel(monkeypatch):
    monkeypatch.setattr(updater.platform, "machine", lambda: "ARM64")
    with pytest.raises(RuntimeError, match="compatibile"):
        updater._select_wheel({"urls": [{"packagetype": "bdist_wheel", "filename": "x-win_amd64.whl"}]})
    monkeypatch.setattr(updater.platform, "machine", lambda: "AMD64")
    with pytest.raises(RuntimeError):
        updater._select_wheel({"urls": [{"packagetype": "bdist_wheel", "filename": "x-win_amd64.whl", "yanked": True}]})


@pytest.fixture
def package(monkeypatch, tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("imageio_ffmpeg/binaries/ffmpeg.exe", b"new-binary")
    payload = stream.getvalue()
    info = {"info": {"version": "0.6.0"}, "urls": [{"filename": "x-win_amd64.whl",
            "packagetype": "bdist_wheel", "url": "https://files.pythonhosted.org/x.whl",
            "digests": {"sha256": hashlib.sha256(payload).hexdigest()}}]}
    monkeypatch.setattr(updater.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(updater, "_root", lambda: tmp_path)
    monkeypatch.setattr(updater, "urlopen", lambda *a, **kw: io.BytesIO(payload))
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(updater, "_test_hardware_compatibility", lambda exe: {"ok": True})
    (tmp_path / "ffmpeg.exe").write_bytes(b"old-binary")
    (tmp_path / "version.json").write_text('{"version":"old"}', encoding="utf-8")
    return info, tmp_path


def test_verified_wheel_installs_with_matching_marker(package):
    info, root = package
    assert updater._install(info)["ok"]
    assert (root / "ffmpeg.exe").read_bytes() == b"new-binary"
    assert json.loads((root / "version.json").read_text())["version"] == "0.6.0"
    assert not (root / "ffmpeg.new.exe").exists()


def test_checksum_failure_preserves_existing_install(package):
    info, root = package
    info["urls"][0]["digests"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="SHA-256"):
        updater._install(info)
    assert (root / "ffmpeg.exe").read_bytes() == b"old-binary"


def test_marker_failure_rolls_back_binary_and_metadata(package, monkeypatch):
    info, root = package
    original = config.atomic_write_text
    def write(path, text):
        if '"0.6.0"' in text:
            raise OSError("disk failure")
        original(path, text)
    monkeypatch.setattr(config, "atomic_write_text", write)
    with pytest.raises(OSError, match="disk failure"):
        updater._install(info)
    assert (root / "ffmpeg.exe").read_bytes() == b"old-binary"
    assert json.loads((root / "version.json").read_text())["version"] == "old"


def test_rejects_parallel_update():
    with updater._update_lock:
        with pytest.raises(RuntimeError, match="già in corso"):
            updater.update()
