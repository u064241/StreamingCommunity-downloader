"""Optional, verified FFmpeg fallback update from the imageio-ffmpeg wheel."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

from app import config

PACKAGE = "imageio-ffmpeg"
PYPI_URL = "https://pypi.org/pypi/imageio-ffmpeg/json"


def _root() -> Path:
    root = config.DATA_FILE.with_name("ffmpeg")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _wheel_info() -> dict:
    req = Request(PYPI_URL, headers={"User-Agent": "StreamingCommunityDownloader/ffmpeg-update"})
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _select_wheel(info: dict) -> dict:
    tags = ("win_amd64", "win32", "win_arm64")
    candidates = [f for f in info["urls"] if f["packagetype"] == "bdist_wheel" and any(t in f["filename"] for t in tags)]
    if not candidates:
        raise RuntimeError("PyPI non offre un wheel FFmpeg compatibile con Windows")
    # Prefer the native architecture when possible; imageio wheels contain the
    # executable and expose the SHA-256 digest in the PyPI JSON API.
    arch = "win_arm64" if "ARM64" in os.environ.get("PROCESSOR_ARCHITECTURE", "") else "win_amd64"
    return next((f for f in candidates if arch in f["filename"]), candidates[0])


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
    rows = transcode._detect(str(executable))
    codec = settings.get("video_conversion", "h264")
    requested = settings.get("video_encoder", "auto")
    gpu_rows = [r for r in rows if r["codec"] == codec and r["backend"] in ("nvenc", "qsv", "amf") and r["available"]]
    active_backend = requested
    if requested == "auto":
        active_backend = next((b for b in ("nvenc", "qsv", "amf")
                               if any(r["backend"] == b and r["codec"] == codec and r["available"] for r in current_rows)), "cpu")
    if active_backend != "cpu":
        ok = any(r["backend"] == active_backend for r in gpu_rows)
    else:
        # Auto may legitimately fall back to CPU; require the selected GPU
        # only when the current hardware profile explicitly has one available.
        ok = bool(gpu_rows) or any(r["codec"] == codec and r["backend"] == "cpu" and r["available"] for r in rows)
    if not ok:
        raise RuntimeError(f"Il nuovo FFmpeg non supera il test encoder {codec}/{requested}")
    return {"codec": codec, "requested": requested, "active_backend": active_backend,
            "encoders": [r["encoder"] for r in gpu_rows]}


def update() -> dict:
    info = _wheel_info()
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
        replaced = False
        try:
            if target.exists():
                if previous.exists():
                    previous.unlink()
                target.replace(previous)
            staged.replace(target)
            replaced = True
        except Exception:
            if target.exists() and not previous.exists():
                target.unlink()
            if previous.exists() and not target.exists():
                previous.replace(target)
            raise
        finally:
            if staged.exists():
                staged.unlink()
        if not replaced:
            raise RuntimeError("Sostituzione FFmpeg non completata")
        # The previous binary is only a rollback aid for this transaction.
        if previous.exists():
            previous.unlink()
    marker = {"version": info["info"]["version"], "wheel": wheel["filename"], "sha256": wheel["digests"]["sha256"]}
    (_root() / "version.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return {"ok": True, **marker, "path": str(target), "compatibility": compatibility}
