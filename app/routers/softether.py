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
import sys
import tempfile
import threading
import time
import re
import zipfile
import shutil
import csv
import io
from urllib.parse import urlsplit

import requests

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.deps import require
from app.auth.permissions import Permission
from app.core.softether_releases import REPOSITORY, select_windows_client

_TOKEN = secrets.token_urlsafe(32)
_MANAGERS = ("vpncmgr_x64.exe", "vpncmgr.exe")
_VPNCMDS = ("vpncmd_x64.exe", "vpncmd.exe")
_RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=30"
_VPNGATE_URL = "https://www.vpngate.net/api/iphone/"
_VPNGATE_DOWNLOAD_PAGE = "https://www.vpngate.net/en/download.aspx"
_release_cache = None
_release_lock = threading.Lock()
_vpngate_cache = None
_vpngate_lock = threading.Lock()
_vpngate_jobs = {}
_vpngate_jobs_lock = threading.Lock()


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


def _softether_roots():
    """Return possible bundled-tool roots for source and PyInstaller builds."""
    candidates = [Path(__file__).resolve().parents[2]]
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        # Inno may place the executable in an app/bin subdirectory while
        # keeping the external bundled tools at the installation root.
        candidates = [executable_dir, *executable_dir.parents, *candidates]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass))
    seen = set()
    for base in candidates:
        root = (base / "tools" / "softether").resolve()
        if root not in seen:
            seen.add(root)
            yield root


def _find_vpncmd():
    if os.name != "nt":
        return None
    for root in _softether_roots():
        for name in _VPNCMDS:
            candidate = root / name
            if candidate.is_file():
                return candidate
    for root in _program_files():
        for name in _VPNCMDS:
            candidate = root / "SoftEther VPN Client" / name
            if candidate.is_file():
                return candidate
    return None


def _bundled_installer():
    for root in _softether_roots():
        try:
            metadata = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
            name = metadata["fileName"]
            # Current Inno packages place the installer directly under
            # tools/softether; accept the former payload/ layout as well.
            for folder in (root, root / "payload"):
                payload = folder.resolve()
                candidate = (payload / name).resolve(strict=True)
                if candidate.parent != payload or not candidate.is_file():
                    continue
                if candidate.stat().st_size != metadata["size"]:
                    continue
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if secrets.compare_digest(digest, metadata["sha256"]):
                    return candidate
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return None


def _vpngate_download_url():
    response = requests.get(_VPNGATE_DOWNLOAD_PAGE, timeout=(4, 12))
    response.raise_for_status()
    matches = re.findall(r'https?://[^"\'\s>]+vpngate-client-[^"\'\s>]+\.zip', response.text, re.I)
    if not matches:
        raise RuntimeError('VPN Gate download URL not found')
    return matches[0]


def _set_vpngate_job(job_id, **updates):
    with _vpngate_jobs_lock:
        job = _vpngate_jobs.get(job_id)
        if job is not None:
            job.update(updates)


def _download_vpngate_installer(job_id):
    url = _vpngate_download_url()
    temp_root = Path(tempfile.mkdtemp(prefix='sc-vpngate-'))
    archive = temp_root / 'vpngate-client.zip'
    extracted = temp_root / 'extracted'
    try:
        _set_vpngate_job(job_id, state='downloading', url=url, archive=str(archive))
        curl = shutil.which('curl.exe') or shutil.which('curl')
        if curl:
            completed = subprocess.Popen(
                [curl, '--fail', '--location', '--silent', '--show-error',
                 '--max-time', '120', '--output', str(archive), url],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            while completed.poll() is None:
                size = archive.stat().st_size if archive.exists() else 0
                _set_vpngate_job(job_id, downloaded=size,
                                 total=0, percent=None)
                time.sleep(.25)
            if completed.returncode:
                raise RuntimeError('curl failed')
        else:
            response = requests.get(url, timeout=(5, 120), stream=True)
            response.raise_for_status()
            total = int(response.headers.get('content-length', '0') or 0)
            downloaded = 0
            with archive.open('wb') as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        handle.write(chunk)
                        downloaded += len(chunk)
                        _set_vpngate_job(
                            job_id, downloaded=downloaded, total=total,
                            percent=round(downloaded * 100 / total, 1) if total else None,
                        )
        size = archive.stat().st_size if archive.exists() else 0
        _set_vpngate_job(job_id, downloaded=size, total=size, percent=100)
        if not archive.is_file() or archive.stat().st_size > 256 * 1024 * 1024:
            raise RuntimeError('invalid VPN Gate archive')
        _set_vpngate_job(job_id, state='extracting', percent=100)
        extracted.mkdir()
        with zipfile.ZipFile(archive) as zf:
            if any(Path(item.filename).is_absolute() or '..' in Path(item.filename).parts for item in zf.infolist()):
                raise RuntimeError('unsafe VPN Gate archive')
            zf.extractall(extracted)
        installers = sorted(extracted.rglob('vpngate-client-*.exe'))
        if not installers:
            raise RuntimeError('VPN Gate installer not found')
        return temp_root, installers[0]
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _watch_vpngate_installer(job_id, process, temp_root):
    try:
        process.wait(timeout=30 * 60)
    except (subprocess.TimeoutExpired, OSError):
        pass
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _run_vpngate_install(job_id):
    temp_root = None
    try:
        temp_root, installer = _download_vpngate_installer(job_id)
        _set_vpngate_job(job_id, state='starting', installer=installer.name)
        process = subprocess.Popen([str(installer)], executable=str(installer),
                                   cwd=str(installer.parent), shell=False, close_fds=True)
        _set_vpngate_job(job_id, state='started', percent=100)
        threading.Thread(target=_watch_vpngate_installer,
                         args=(job_id, process, temp_root), daemon=True).start()
    except (OSError, requests.RequestException, RuntimeError, zipfile.BadZipFile) as exc:
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)
        _set_vpngate_job(job_id, state='error', error='softether.installFailed')


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


def _vpngate_servers():
    global _vpngate_cache
    now = time.monotonic()
    with _vpngate_lock:
        if _vpngate_cache and now - _vpngate_cache[0] < 10 * 60:
            return list(_vpngate_cache[1])
        try:
            response = requests.get(_VPNGATE_URL, timeout=(4, 12))
            response.raise_for_status()
            text = response.content.decode("utf-8", errors="replace")
            lines = [line for line in text.splitlines() if line and not line.startswith("*")]
            header_index = next(i for i, line in enumerate(lines) if line.startswith("#HostName,"))
            reader = csv.DictReader([lines[header_index][1:], *lines[header_index + 1:]])
            servers = []
            for row in reader:
                try:
                    ipaddress.ip_address(row.get("IP", ""))
                    ping = int(row.get("Ping", "-1"))
                    speed = int(row.get("Speed", "0"))
                except (ValueError, TypeError):
                    continue
                if ping < 0 or speed <= 0:
                    continue
                servers.append({
                    "host": row["IP"], "name": row.get("HostName", ""),
                    "country": row.get("CountryLong", ""), "countryCode": row.get("CountryShort", ""),
                    "port": int(row.get("Port", "443") or 443),
                    "ping": ping, "speed": speed, "sessions": int(row.get("NumVpnSessions", "0") or 0),
                })
            servers.sort(key=lambda item: (item["ping"], -item["speed"]))
            servers = servers[:50]
        except (requests.RequestException, ValueError, StopIteration, KeyError):
            servers = []
        _vpngate_cache = (now, servers)
        return list(servers)


@router.get("/status")
def status():
    manager = _detect()
    return JSONResponse({
        "supported": os.name == "nt", "installed": manager is not None,
        "bundled_installer": _bundled_installer() is not None,
        "vpngate_available": os.name == "nt",
        "token": _TOKEN,
    }, headers={"Cache-Control": "no-store"})


@router.get("/updates")
def updates():
    return JSONResponse(_release_status(), headers={"Cache-Control": "no-store"})


@router.get("/vpngate")
def vpngate():
    return JSONResponse({"servers": _vpngate_servers(), "source": _VPNGATE_URL},
                        headers={"Cache-Control": "no-store"})


@router.post("/install")
async def install(request: Request):
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    if os.name != "nt":
        raise HTTPException(409, "softether.unsupported")
    job_id = secrets.token_urlsafe(18)
    with _vpngate_jobs_lock:
        _vpngate_jobs[job_id] = {
            "state": "queued", "percent": 0, "downloaded": 0,
            "total": 0, "created": time.monotonic(),
        }
    threading.Thread(target=_run_vpngate_install, args=(job_id,), daemon=True).start()
    return {"started": True, "source": "vpngate", "job_id": job_id}


@router.get("/install-status")
def install_status(job: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,64}", job or ""):
        raise HTTPException(400, "softether.invalidJob")
    with _vpngate_jobs_lock:
        result = _vpngate_jobs.get(job)
        if result is None:
            raise HTTPException(404, "softether.jobNotFound")
        result = dict(result)
    result.pop("archive", None)
    result.pop("created", None)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/download-update")
async def download_update(request: Request):
    """Download and start only the selected official release asset."""
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    if os.name != "nt":
        raise HTTPException(409, "softether.unsupported")
    release = _release_status()
    if release.get("status") != "available":
        # A local payload is the offline fallback when GitHub is unavailable
        # or does not expose a compatible stable asset.
        installer = _bundled_installer()
        if installer is None:
            raise HTTPException(404, "softether.updateUnavailable")
        try:
            subprocess.Popen([str(installer)], executable=str(installer), cwd=str(installer.parent), shell=False, close_fds=True)
        except OSError as exc:
            raise HTTPException(503, "softether.installFailed") from exc
        return {"started": True, "source": "bundled"}
    filename = Path(release["asset_name"]).name
    if filename != release["asset_name"] or not filename.lower().endswith(".exe"):
        raise HTTPException(502, "softether.invalidAsset")
    softether_root = next(_softether_roots())
    payload = softether_root / "payload"
    target = payload / filename
    manifest_path = payload.parent / "bundle.json"
    try:
        payload.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            ["curl.exe", "--fail", "--location", "--silent", "--show-error",
             "--max-time", "120", "--output", str(target), release["asset_url"]],
            check=False, capture_output=True, text=True, timeout=130,
        )
        if completed.returncode or not target.is_file() or not target.stat().st_size:
            raise RuntimeError("curl failed")
        if target.stat().st_size > release["size_limit"]:
            raise RuntimeError("asset too large")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({
            "tag": release["tag"], "fileName": filename,
            "size": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "sourceUrl": release["release_url"],
            "silentInstallVerified": False,
        })
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        subprocess.Popen([str(target)], executable=str(target), cwd=str(target.parent), shell=False, close_fds=True)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(502, "softether.downloadFailed") from exc
    return {"started": True, "version": release["version"]}


class VpnGateProfile(BaseModel):
    host: str = Field(min_length=1, max_length=64)
    port: int = Field(ge=1, le=65535)
    name: str = Field(default="VPN Gate", min_length=1, max_length=64)


@router.post("/vpngate/profile")
async def create_vpngate_profile(body: VpnGateProfile, request: Request):
    """Create a VPN Gate account profile, but never connect it automatically."""
    if request.query_params:
        raise HTTPException(400, "softether.noParameters")
    try:
        ipaddress.ip_address(body.host)
    except ValueError as exc:
        raise HTTPException(422, "softether.invalidServer") from exc
    cmd = _find_vpncmd()
    if cmd is None:
        raise HTTPException(404, "softether.missing")
    if not re.fullmatch(r"[A-Za-z0-9 ._-]+", body.name):
        raise HTTPException(422, "softether.invalidProfileName")
    result = subprocess.run([
        str(cmd), "/client", "localhost", "/cmd", "AccountCreate",
        body.name, f"/SERVER:{body.host}:{body.port}", "/HUB:VPNGATE",
        "/USERNAME:vpn",
    ], capture_output=True, text=True, timeout=20, check=False)
    if result.returncode != 0:
        raise HTTPException(502, "softether.profileFailed")
    password = subprocess.run([
        str(cmd), "/client", "localhost", "/cmd", "AccountPasswordSet",
        body.name, "/PASSWORD:vpn", "/TYPE:standard",
    ], capture_output=True, text=True, timeout=20, check=False)
    if password.returncode != 0:
        raise HTTPException(502, "softether.profileFailed")
    return {"created": True, "name": body.name, "host": body.host, "port": body.port}


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
