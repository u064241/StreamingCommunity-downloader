"""Local, opt-in opening of the installed SoftEther Client Manager only.

Mirrors tools/softether/Open-SoftEtherClient.ps1's default discovery, without
exposing its InstallationPath override or invoking PowerShell from HTTP.
"""

import ipaddress
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

import requests

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.deps import require
from app.auth.permissions import Permission
from app.core.softether_releases import REPOSITORY, select_windows_client

_TOKEN = secrets.token_urlsafe(32)
_MANAGERS = ("vpncmgr_x64.exe", "vpncmgr.exe")
_RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=30"
_release_cache = None
_release_lock = threading.Lock()


def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _local(request: Request):
    if (not request.client or not _loopback(request.client.host)
            or not (request.url.hostname == "localhost" or _loopback(request.url.hostname))
            or any(name == "forwarded" or name.startswith("x-forwarded-")
                   for name in request.headers)):
        raise HTTPException(403, "softether.localOnly")
    if request.method == "POST":
        try:
            origin = urlsplit(request.headers.get("origin", ""))
        except ValueError as exc:
            raise HTTPException(403, "softether.localOnly") from exc
        if ((origin.scheme, origin.netloc) != (request.url.scheme, request.url.netloc)
                or not secrets.compare_digest(
                    request.headers.get("x-softether-token", "").encode(), _TOKEN.encode())):
            raise HTTPException(403, "softether.localOnly")


router = APIRouter(
    prefix="/api/softether", tags=["softether"],
    dependencies=[Depends(require(Permission.MANAGE_SETTINGS)), Depends(_local)],
)


def _program_files():
    # Machine registry, never a request parameter, PATH or user environment.
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion", 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        for name in ("ProgramW6432Dir", "ProgramFilesDir", "ProgramFilesDir (x86)"):
            try:
                value, kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue
            if kind == winreg.REG_SZ and value:
                yield Path(value)


def _find_manager():
    if os.name != "nt":
        return None
    for root in _program_files():
        if not root.is_absolute() or str(root).startswith("\\\\"):
            continue
        for name in _MANAGERS:
            candidate = root / "SoftEther VPN Client" / name
            try:
                # Reject junctions/symlinks, including ancestors of Program Files.
                if any(getattr(part.lstat(), "st_file_attributes", 0)
                       & stat.FILE_ATTRIBUTE_REPARSE_POINT
                       or part.is_symlink()
                       for part in (candidate, *candidate.parents)):
                    continue
                resolved = candidate.resolve(strict=True)
                if resolved == candidate and resolved.is_file():
                    return resolved
            except (OSError, RuntimeError):
                continue
    return None


def _bundled_installer():
    root = Path(__file__).resolve().parents[2] / "tools" / "softether"
    try:
        metadata = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
        name = metadata["fileName"]
        candidate = (root / "payload" / name).resolve(strict=True)
        if candidate.parent != (root / "payload").resolve() or not candidate.is_file():
            return None
        if candidate.stat().st_size != metadata["size"]:
            return None
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return candidate if secrets.compare_digest(digest, metadata["sha256"]) else None
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _detect():
    try:
        return _find_manager()
    except OSError as exc:
        raise HTTPException(503, "softether.detectionFailed") from exc


def _release_status():
    """Read-only, bounded check of the official stable Windows client release."""
    global _release_cache
    now = time.monotonic()
    with _release_lock:
        if _release_cache and now - _release_cache[0] < 6 * 60 * 60:
            return dict(_release_cache[1])
        try:
            response = requests.get(
                _RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=(3, 8),
            )
            response.raise_for_status()
            release = select_windows_client(response.json())
            result = {
                "status": "available" if release else "unavailable",
                "repository": REPOSITORY,
            }
            if release:
                result.update({
                    "version": ".".join(map(str, release.version)),
                    "tag": release.tag,
                    "asset_name": release.asset_name,
                    "asset_url": release.asset_url,
                    "release_url": release.release_url,
                    "size_limit": 256 * 1024 * 1024,
                })
        except (requests.RequestException, ValueError, TypeError):
            result = {"status": "unavailable", "repository": REPOSITORY}
        _release_cache = (now, result)
        return dict(result)


@router.get("/status")
def status():
    manager = _detect()
    return JSONResponse({
        "supported": os.name == "nt", "installed": manager is not None,
        "bundled_installer": _bundled_installer() is not None,
        "token": _TOKEN,
    }, headers={"Cache-Control": "no-store"})


@router.get("/updates")
def updates():
    return JSONResponse(_release_status(), headers={"Cache-Control": "no-store"})


@router.post("/install")
async def install(request: Request):
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    if os.name != "nt":
        raise HTTPException(409, "softether.unsupported")
    installer = _bundled_installer()
    if installer is None:
        raise HTTPException(404, "softether.installerMissing")
    try:
        subprocess.Popen([str(installer)], executable=str(installer), cwd=str(installer.parent), shell=False, close_fds=True)
    except OSError as exc:
        raise HTTPException(503, "softether.installFailed") from exc
    return {"started": True}


@router.post("/download-update")
async def download_update(request: Request):
    """Download and start only the selected official release asset."""
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    if os.name != "nt":
        raise HTTPException(409, "softether.unsupported")
    release = _release_status()
    if release.get("status") != "available":
        raise HTTPException(404, "softether.updateUnavailable")
    filename = Path(release["asset_name"]).name
    if filename != release["asset_name"] or not filename.lower().endswith(".exe"):
        raise HTTPException(502, "softether.invalidAsset")
    target = Path(tempfile.gettempdir()) / f"sc-softether-{filename}"
    try:
        completed = subprocess.run(
            ["curl.exe", "--fail", "--location", "--silent", "--show-error",
             "--max-time", "120", "--output", str(target), release["asset_url"]],
            check=False, capture_output=True, text=True, timeout=130,
        )
        if completed.returncode or not target.is_file() or not target.stat().st_size:
            raise RuntimeError("curl failed")
        if target.stat().st_size > release["size_limit"]:
            raise RuntimeError("asset too large")
        subprocess.Popen([str(target)], executable=str(target), cwd=str(target.parent), shell=False, close_fds=True)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(502, "softether.downloadFailed") from exc
    return {"started": True, "version": release["version"]}


@router.post("/open-manager")
async def open_manager(request: Request):
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    if os.name != "nt":
        raise HTTPException(409, "softether.unsupported")
    manager = _detect()  # Recheck at click time; never trust a path from the UI.
    if manager is None:
        raise HTTPException(404, "softether.missing")
    try:
        subprocess.Popen([str(manager)], executable=str(manager),
                         cwd=str(manager.parent), shell=False, close_fds=True)
    except OSError as exc:
        raise HTTPException(503, "softether.openFailed") from exc
    return {"opened": True}
