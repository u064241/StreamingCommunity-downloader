"""Optional, verified FFmpeg fallback update from the imageio-ffmpeg wheel."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
import subprocess
import platform
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from app import config

PACKAGE = "imageio-ffmpeg"
PYPI_URL = "https://pypi.org/pypi/imageio-ffmpeg/json"
_update_lock = threading.Lock()


class HardwareCompatibilityError(RuntimeError):
    """A valid candidate that would remove working encoder support."""


def _root() -> Path:
    root = config.DATA_FILE.with_name("ffmpeg")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _wheel_info(version: str | None = None) -> dict:
    url = PYPI_URL if version is None else f"https://pypi.org/pypi/{PACKAGE}/{version}/json"
    req = Request(url, headers={"User-Agent": "StreamingCommunityDownloader/ffmpeg-update"})
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _select_wheel(info: dict) -> dict:
    machine = platform.machine().lower()
    arch = {"amd64": "win_amd64", "x86_64": "win_amd64", "arm64": "win_arm64",
            "aarch64": "win_arm64", "x86": "win32", "i386": "win32"}.get(machine)
    candidates = [f for f in info["urls"] if f["packagetype"] == "bdist_wheel"
                  and not f.get("yanked") and arch and f["filename"].endswith(f"-{arch}.whl")]
    if not candidates:
        raise RuntimeError("PyPI non offre un wheel FFmpeg compatibile con Windows")
    return candidates[0]


def status() -> dict:
    current = None
    marker = _root() / "version.json"
    if marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            current = None
    return {"managed": current, "path": str(_root() / "ffmpeg.exe")}


def _test_hardware_compatibility(executable: Path) -> dict:
    """Run the same encoder probes used by Rileva GPU against the candidate."""
    from app.core import transcode
    settings = config.get_settings()
    current_rows = transcode.capabilities(refresh=True)["encoders"]
    # A managed binary may already be broken. Also protect the working bundled
    # baseline, so a CPU fallback cannot hide loss of NVENC on Pascal/R580.
    import imageio_ffmpeg
    baseline = transcode._detect(imageio_ffmpeg.get_ffmpeg_exe())
    rows = transcode._detect(str(executable))
    available = {(r["codec"], r["backend"]) for r in rows if r["available"]}
    required = {(r["codec"], r["backend"]) for r in current_rows + baseline if r["available"]}
    required.update({("h264", "cpu"), ("hevc", "cpu")})
    missing = required - available
    if missing:
        names = ", ".join(f"{c}/{b}" for c, b in sorted(missing))
        raise HardwareCompatibilityError(f"Il nuovo FFmpeg perde encoder funzionanti: {names}")
    codec = settings.get("video_conversion", "copy")
    requested = settings.get("video_encoder", "auto")
    gpu_rows = [r for r in rows if r["codec"] == codec and r["backend"] in ("nvenc", "qsv", "amf") and r["available"]]
    active_backend = requested
    if requested == "auto":
        active_backend = next((b for b in ("nvenc", "qsv", "amf")
                               if any(r["backend"] == b and r["codec"] == codec and r["available"] for r in current_rows)), "cpu")
    if codec == "copy":
        ok = True  # Both codecs and all working backends were checked above.
    elif active_backend != "cpu":
        ok = any(r["backend"] == active_backend for r in gpu_rows)
    else:
        # Auto may legitimately fall back to CPU; require the selected GPU
        # only when the current hardware profile explicitly has one available.
        ok = bool(gpu_rows) or any(r["codec"] == codec and r["backend"] == "cpu" and r["available"] for r in rows)
    if not ok:
        raise HardwareCompatibilityError(f"Il nuovo FFmpeg non supera il test encoder {codec}/{requested}")
    return {"codec": codec, "requested": requested, "active_backend": active_backend,
            "encoders": [r["encoder"] for r in gpu_rows]}


def update() -> dict:
    if not _update_lock.acquire(blocking=False):
        raise RuntimeError("Aggiornamento FFmpeg già in corso")
    try:
        info = _wheel_info()
        try:
            return _install(info)
        except HardwareCompatibilityError as exc:
            # Only hardware rejection triggers fallback. Integrity/network
            # failures stay visible; never silently accept an unverified wheel.
            if info["info"]["version"] == config.FFMPEG_COMPATIBLE_FALLBACK:
                raise
            result = _install(_wheel_info(config.FFMPEG_COMPATIBLE_FALLBACK))
            return {**result, "fallback": True, "latest_version": info["info"]["version"],
                    "rejected_reason": str(exc)}
    finally:
        _update_lock.release()


def _install(info: dict) -> dict:
    wheel = _select_wheel(info)
    target = _root() / "ffmpeg.exe"
    with tempfile.TemporaryDirectory(prefix="sc-ffmpeg-") as tmp:
        archive = Path(tmp) / wheel["filename"]
        # The URL comes from PyPI's JSON. Only PyPI's own file host is trusted.
        from urllib.parse import urlsplit
        url = urlsplit(str(wheel.get("url") or ""))
        if url.scheme != "https" or url.hostname != "files.pythonhosted.org":
            raise RuntimeError("URL del wheel FFmpeg non attendibile")
        req = Request(wheel["url"], headers={"User-Agent": "StreamingCommunityDownloader/ffmpeg-update"})
        with urlopen(req, timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest.lower() != wheel["digests"]["sha256"].lower():
            raise RuntimeError("Verifica SHA-256 del wheel FFmpeg fallita")
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in zf.namelist()
                     if n.lower().endswith(".exe") and "ffmpeg" in Path(n).name.lower()]
            if not names:
                raise RuntimeError("Il wheel non contiene ffmpeg.exe")
            extracted = Path(tmp) / "ffmpeg.exe"
            extracted.write_bytes(zf.read(names[0]))
        probe = subprocess.run([str(extracted), "-version"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=15, check=False,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if probe.returncode != 0:
            raise RuntimeError("Il nuovo eseguibile FFmpeg non supera il controllo -version")
        compatibility = _test_hardware_compatibility(extracted)
        # TemporaryDirectory may live on another volume (for example R:\TEMP
        # while the app data is on C:). Copy into the destination volume first;
        # Path.replace() is a rename and Windows rejects cross-volume renames.
        staged = target.with_name("ffmpeg.new.exe")
        if staged.exists():
            staged.unlink()
        shutil.copy2(extracted, staged)
        previous = _root() / "ffmpeg.previous.exe"
        marker_path = _root() / "version.json"
        old_marker = marker_path.read_text(encoding="utf-8") if marker_path.exists() else None
        marker = {"version": info["info"]["version"], "wheel": wheel["filename"], "sha256": wheel["digests"]["sha256"]}
        moved_previous = False
        installed = False
        try:
            if target.exists():
                if previous.exists():
                    previous.unlink()
                target.replace(previous)
                moved_previous = True
            staged.replace(target)
            installed = True
            config.atomic_write_text(marker_path, json.dumps(marker, indent=2))
        except Exception:
            if installed:
                target.unlink()
            if moved_previous:
                previous.replace(target)
            if installed:
                if old_marker is None:
                    marker_path.unlink(missing_ok=True)
                else:
                    config.atomic_write_text(marker_path, old_marker)
            raise
        finally:
            if staged.exists():
                staged.unlink()
        # The previous binary is only a rollback aid for this transaction.
        if previous.exists():
            previous.unlink()
    from app.core.ffmpeg_path import get_ffmpeg_exe, get_ffprobe_exe
    from app.core import transcode
    get_ffmpeg_exe.cache_clear()
    get_ffprobe_exe.cache_clear()
    transcode._detect.cache_clear()
    return {"ok": True, **marker, "path": str(target), "compatibility": compatibility}
