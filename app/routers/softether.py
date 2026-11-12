"""Local, opt-in opening of the installed SoftEther Client Manager only.

Mirrors tools/softether/Open-SoftEtherClient.ps1's default discovery, without
exposing its InstallationPath override or invoking PowerShell from HTTP.
"""

import ipaddress
import hashlib
import json
import logging
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
import socket
import zipfile
import shutil
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.deps import require
from app.auth.permissions import Permission
from app.core.softether_releases import REPOSITORY, select_windows_client
from app import softether_trust
from app.update_download import SignatureError, system32_tool, verify_authenticode

logger = logging.getLogger(__name__)
_UNVERIFIED = "Installer non verificato (firma o integrità): installazione annullata."
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
_public_ip_lock = threading.Lock()
_original_public_ip = None


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


def _manager_running(manager):
    return _manager_state(manager) != "stopped"


def _manager_state(manager):
    """"stopped", "tray" (running, no window) or "visible" (window open).

    tasklist /V prints the window title as the last CSV column; a process with
    no window reports "N/A" there. That is how a gestore that lives only in the
    notification area is told apart from one the user can interact with.
    """
    if os.name != "nt" or manager is None:
        return "stopped"
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"IMAGENAME eq {manager.name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
            timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError, TypeError):
        return "stopped"
    pids = set()
    for row in csv.reader((result.stdout or "").splitlines()):
        # Image Name, PID, ... : localised output, but the layout is fixed.
        if len(row) >= 2 and row[0].lower() == manager.name.lower():
            try:
                pids.add(int(row[1]))
            except ValueError:
                continue
    if not pids:
        return "stopped"
    # A window title does not tell the two apart: a hidden Manager keeps its
    # title. What matters is whether one of its top-level windows is visible.
    if pids & _visible_window_pids():
        return "visible"
    return "tray"


def _visible_window_pids() -> set[int]:
    """Process ids that own at least one visible top-level window (Windows only)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    visible: set[int] = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def on_window(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            visible.add(pid.value)
        return True

    # Keep a reference for the whole call: ctypes callbacks must not be collected.
    callback = callback_type(on_window)
    user32.EnumWindows(callback, 0)
    return visible


def _ensure_client_service():
    if os.name != "nt":
        return
    for service in ("sevpnclient", "vpnclient"):
        query = subprocess.run(["sc.exe", "query", service], capture_output=True,
                               text=True, timeout=8, check=False)
        if query.returncode != 0:
            continue
        if "RUNNING" not in query.stdout.upper():
            started = subprocess.run(["sc.exe", "start", service], capture_output=True,
                                     text=True, timeout=15, check=False)
            if started.returncode != 0:
                raise RuntimeError("SoftEther client service is not running")
        return
    raise RuntimeError("SoftEther client service not found")


def _installed_client_version():
    if os.name != "nt":
        return None
    import winreg
    pattern = re.compile(r"(\d+)\.(\d+)\.(\d+)")
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                                0, winreg.KEY_READ | view) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)) as item:
                            publisher = str(winreg.QueryValueEx(item, "Publisher")[0])
                            display = str(winreg.QueryValueEx(item, "DisplayVersion")[0])
                            if "softether" not in publisher.lower():
                                continue
                            match = pattern.search(display)
                            if match:
                                return tuple(map(int, match.groups()))
                    except (OSError, ValueError):
                        continue
        except OSError:
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


def _trusted_vpngate_url(url):
    """HTTPS on an allowlisted VPN Gate host, default port, no credentials."""
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https"
                and parts.hostname in softether_trust.VPNGATE_DOWNLOAD_HOSTS
                and not parts.username and not parts.password
                and parts.port in (None, 443))
    except ValueError:
        return False


def _vpngate_download_url():
    response = requests.get(_VPNGATE_DOWNLOAD_PAGE, timeout=(4, 12))
    response.raise_for_status()
    matches = re.findall(r'https?://[^"\'\s>]+vpngate-client-[^"\'\s>]+\.zip', response.text, re.I)
    for url in matches:
        if _trusted_vpngate_url(url):
            return url
    # Plain HTTP or a foreign host is never an acceptable source.
    raise RuntimeError('VPN Gate download URL not found or not trusted')


def _curl():
    """System32 curl.exe only: a curl found through PATH is never run."""
    return system32_tool("curl.exe")


def _job_cancelled(job_id):
    with _vpngate_jobs_lock:
        return bool(_vpngate_jobs.get(job_id, {}).get("cancel_requested"))


def _download_with_requests(job_id, url, archive):
    # Redirects are refused: the allowlisted HTTPS URL must answer directly.
    response = requests.get(url, timeout=(20, 900), stream=True, allow_redirects=False)
    try:
        response.raise_for_status()
        if response.status_code != 200:
            raise RuntimeError('unexpected VPN Gate redirect')
        total = int(response.headers.get('content-length', '0') or 0)
        downloaded = 0
        with archive.open('wb') as handle:
            for chunk in response.iter_content(1024 * 1024):
                if _job_cancelled(job_id):
                    raise RuntimeError('install cancelled')
                if chunk:
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > 256 * 1024 * 1024:
                        raise RuntimeError('invalid VPN Gate archive')
                    _set_vpngate_job(job_id, downloaded=downloaded, total=total,
                                     percent=round(downloaded * 100 / total, 1) if total else None)
    finally:
        response.close()


def _extract_verified_vpngate(archive, extracted):
    """Extract only an archive whose every entry is expected, then verify the
    installer's Authenticode signer and the pinned plug-in hashes."""
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        names = [item.filename for item in entries]
        installers = [name for name in names
                      if re.fullmatch(r'vpngate-client-[A-Za-z0-9._-]+\.exe', name)]
        allowed = (set(installers[:1]) | set(softether_trust.VPNGATE_PLUGIN_SHA256)
                   | softether_trust.VPNGATE_DATA_FILES)
        if (len(installers) != 1 or len(set(names)) != len(names) or set(names) - allowed
                or sum(item.file_size for item in entries) > 512 * 1024 * 1024):
            raise SignatureError('unexpected VPN Gate archive content')
        zf.extractall(extracted)
    for name in names:
        pins = softether_trust.VPNGATE_PLUGIN_SHA256.get(name)
        if pins is None:
            continue
        with (extracted / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() not in pins:
                raise SignatureError(f'no SHA-256 pin matches {name}')
    installer = extracted / installers[0]
    verify_authenticode(installer, softether_trust.SOFTETHER_SIGNER_THUMBPRINTS)
    return installer


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
        _set_vpngate_job(job_id, state='downloading', archive=str(archive))
        curl = _curl()
        if curl:
            # curl's silent mode does not expose progress to the panel. Read
            # the archive size first, then report the file growing while curl
            # downloads it (some VPN Gate mirrors omit this header).
            try:
                total = int(requests.head(url, allow_redirects=False,
                                          timeout=(4, 12)).headers.get('content-length', '0') or 0)
            except (requests.RequestException, ValueError):
                total = 0
            completed = subprocess.Popen(
                [str(curl), '--fail', '--location', '--silent', '--show-error',
                 '--proto', '=https', '--proto-redir', '=https',
                 '--connect-timeout', '20', '--max-time', '900',
                 '--retry', '5', '--retry-delay', '3', '--retry-all-errors',
                 '--output', str(archive), url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _set_vpngate_job(job_id, process=completed)
            while completed.poll() is None:
                if _job_cancelled(job_id):
                    completed.kill()
                    raise RuntimeError("install cancelled")
                size = archive.stat().st_size if archive.exists() else 0
                _set_vpngate_job(job_id, downloaded=size,
                                 total=total,
                                 percent=round(size * 100 / total, 1) if total else None)
                time.sleep(.25)
            if completed.returncode:
                # VPN Gate mirrors occasionally close a curl transfer. Retry
                # through requests before declaring the installation failed.
                archive.unlink(missing_ok=True)
                _download_with_requests(job_id, url, archive)
        else:
            _download_with_requests(job_id, url, archive)
        size = archive.stat().st_size if archive.exists() else 0
        _set_vpngate_job(job_id, downloaded=size, total=size, percent=100)
        if not archive.is_file() or archive.stat().st_size > 256 * 1024 * 1024:
            raise RuntimeError('invalid VPN Gate archive')
        _set_vpngate_job(job_id, state='extracting', percent=100)
        extracted.mkdir()
        # Raises, and the handler below deletes everything, unless the installer
        # is signed by the expected publisher and its plug-ins match their pins.
        return temp_root, _extract_verified_vpngate(archive, extracted)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _watch_vpngate_installer(job_id, process, temp_root):
    try:
        process.wait(timeout=30 * 60)
    except (subprocess.TimeoutExpired, OSError):
        pass
    finally:
        # The user may close or cancel the official installer. In every case
        # its temporary ZIP/extracted payload is no longer needed; do not leave
        # VPN Gate installers behind in %TEMP%.
        if process.poll() not in (None, 0):
            _set_vpngate_job(job_id, state='cancelled')
        else:
            # The official VPN Gate setup can hand off to a child process and
            # exit its launcher immediately. Give that child a short window
            # to become visible before reporting completion to the panel.
            time.sleep(5)
            _set_vpngate_job(job_id, state='completed', percent=100)
        shutil.rmtree(temp_root, ignore_errors=True)


def _remove_after_exit(process, temp_root):
    try:
        process.wait(timeout=30 * 60)
    except (subprocess.TimeoutExpired, OSError):
        pass
    shutil.rmtree(temp_root, ignore_errors=True)


def _run_vpngate_install(job_id):
    temp_root = None
    try:
        temp_root, installer = _download_vpngate_installer(job_id)
        _set_vpngate_job(job_id, state='starting', installer=installer.name)
        process = subprocess.Popen([str(installer)], executable=str(installer),
                                   cwd=str(installer.parent), shell=False, close_fds=True)
        _set_vpngate_job(job_id, process=process)
        _set_vpngate_job(job_id, state='installing', percent=100)
        threading.Thread(target=_watch_vpngate_installer,
                         args=(job_id, process, temp_root), daemon=True).start()
    except (OSError, requests.RequestException, RuntimeError, subprocess.SubprocessError,
            zipfile.BadZipFile) as exc:
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)
        cancelled = _job_cancelled(job_id)
        if not cancelled:
            # The detail stays in the server log; the panel gets a fixed message.
            logger.warning("VPN Gate installation failed: %r", exc)
        error = _UNVERIFIED if isinstance(exc, SignatureError) else 'softether.installFailed'
        _set_vpngate_job(job_id, state='cancelled' if cancelled else 'error',
                         error=None if cancelled else error)


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
            result = {"status": "unavailable", "repository": REPOSITORY}
            if release:
                result.update({
                    "version": ".".join(map(str, release.version)),
                    "tag": release.tag,
                    "asset_name": release.asset_name,
                    "asset_url": release.asset_url,
                    "release_url": release.release_url,
                    "size_limit": 256 * 1024 * 1024,
                })
                local = _installed_client_version()
                result["local_version"] = ".".join(map(str, local)) if local else None
                result["status"] = (
                    "update_available" if local is None or release.version > local
                    else "up_to_date"
                )
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
            candidates = []
            for row in reader:
                try:
                    ipaddress.ip_address(row.get("IP", ""))
                    ping = int(row.get("Ping", "-1"))
                    speed = int(row.get("Speed", "0"))
                except (ValueError, TypeError):
                    continue
                if ping < 0 or speed <= 0:
                    continue
                candidates.append({
                    "host": row["IP"], "name": row.get("HostName", ""),
                    "country": row.get("CountryLong", ""), "countryCode": row.get("CountryShort", ""),
                    "port": int(row.get("Port", "443") or 443),
                    "ping": ping, "speed": speed, "sessions": int(row.get("NumVpnSessions", "0") or 0),
                    "uptime": int(row.get("Uptime", "0") or 0),
                    "score": int(row.get("Score", "0") or 0),
                    "lineSpeed": int(row.get("LineSpeed", "0") or 0),
                    "totalUsers": int(row.get("TotalUsers", "0") or 0),
                })
            candidates.sort(key=lambda item: (item["ping"], -item["speed"]))
            candidates = candidates[:50]

            def check(server):
                try:
                    with socket.create_connection((server["host"], server["port"]), timeout=1.5):
                        server["reachable"] = True
                except (OSError, TimeoutError):
                    server["reachable"] = False
                return server

            with ThreadPoolExecutor(max_workers=12, thread_name_prefix="vpngate-check") as pool:
                checked = list(pool.map(check, candidates))
            # Prefer servers that are reachable, then low latency, high
            # throughput and fewer concurrent sessions.  The CSV uptime field
            # is deliberately not used: VPN Gate exposes it as a historical
            # counter, not as the current uninterrupted uptime.
            servers = [server for server in checked if server["reachable"]]
            servers.sort(key=lambda item: (
                not item["reachable"], item["ping"], -item["speed"], item["sessions"]
            ))
            servers = servers[:25]
        except (requests.RequestException, ValueError, StopIteration, KeyError):
            servers = []
        _vpngate_cache = (now, servers)
        return list(servers)


@router.get("/status")
def status():
    manager = _detect()
    return JSONResponse({
        "supported": os.name == "nt", "installed": manager is not None,
        "running": _manager_running(manager),
        "manager_state": _manager_state(manager),
        "bundled_installer": _bundled_installer() is not None,
        "vpngate_available": os.name == "nt",
        "token": _TOKEN,
    }, headers={"Cache-Control": "no-store"})


@router.get("/updates")
def updates():
    return JSONResponse(_release_status(), headers={"Cache-Control": "no-store"})


@router.get("/vpngate")
def vpngate():
    servers = _vpngate_servers()
    try:
        _, _, details = _read_vpngate_profiles()
    except HTTPException:
        # Client missing or service stopped: the public list stays usable,
        # simply without any server marked as registered.
        details = {}
    return JSONResponse({"servers": _annotate_registered(servers, details), "source": _VPNGATE_URL},
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
    # I dettagli interni del job non sono serializzabili e non devono uscire
    # dall'endpoint di polling della pagina.
    result.pop("process", None)
    result.pop("cancel_requested", None)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/install-cancel")
async def install_cancel(request: Request, job: str):
    if await request.body():
        raise HTTPException(400, "softether.noParameters")
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,64}", job or ""):
        raise HTTPException(400, "softether.invalidJob")
    with _vpngate_jobs_lock:
        item = _vpngate_jobs.get(job)
        if item is None:
            raise HTTPException(404, "softether.jobNotFound")
        item["cancel_requested"] = True
        process = item.get("process")
        state = item.get("state")
    if process is not None and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    if state in {"queued", "downloading", "extracting", "starting", "installing"}:
        _set_vpngate_job(job, state="cancelled")
    return {"cancelled": True, "job_id": job}


@router.post("/download-update")
async def download_update(request: Request):
    """Download and start only the selected official release asset."""
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    if os.name != "nt":
        raise HTTPException(409, "softether.unsupported")
    release = _release_status()
    # _release_status() reports "update_available", "up_to_date" or
    # "unavailable"; only the first one names an asset worth downloading.
    if release.get("status") != "update_available":
        # A local payload is the offline fallback when GitHub is unavailable
        # or does not expose a compatible stable asset. It is pinned by the
        # size and SHA-256 shipped in bundle.json, which is never rewritten.
        installer = _bundled_installer()
        if installer is None:
            raise HTTPException(404, "softether.updateUnavailable")
        try:
            subprocess.Popen([str(installer)], executable=str(installer), cwd=str(installer.parent), shell=False, close_fds=True)
        except OSError as exc:
            logger.warning("Bundled SoftEther installer failed to start: %r", exc)
            raise HTTPException(503, "softether.installFailed") from exc
        return {"started": True, "source": "bundled"}
    filename = Path(release["asset_name"]).name
    if filename != release["asset_name"] or not filename.lower().endswith(".exe"):
        raise HTTPException(502, "softether.invalidAsset")
    curl = _curl()
    if curl is None:
        logger.warning("SoftEther update refused: curl.exe not found in System32")
        raise HTTPException(503, "softether.downloadFailed")
    # A private temporary folder, never the bundled payload: the download must
    # not replace the pinned installer nor record its own hash as trusted.
    temp_root = Path(tempfile.mkdtemp(prefix="sc-softether-"))
    target = temp_root / filename
    try:
        completed = subprocess.run(
            [str(curl), "--fail", "--location", "--silent", "--show-error",
             "--proto", "=https", "--proto-redir", "=https",
             "--max-time", "120", "--output", str(target), release["asset_url"]],
            check=False, capture_output=True, text=True, timeout=130,
        )
        if completed.returncode or not target.is_file() or not target.stat().st_size:
            raise RuntimeError("curl failed")
        if target.stat().st_size > release["size_limit"]:
            raise RuntimeError("asset too large")
        verify_authenticode(target, softether_trust.SOFTETHER_SIGNER_THUMBPRINTS)
        process = subprocess.Popen([str(target)], executable=str(target), cwd=str(target.parent), shell=False, close_fds=True)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        shutil.rmtree(temp_root, ignore_errors=True)
        logger.warning("SoftEther update failed: %r", exc)
        if isinstance(exc, SignatureError):
            raise HTTPException(502, _UNVERIFIED) from exc
        raise HTTPException(502, "softether.downloadFailed") from exc
    threading.Thread(target=_remove_after_exit, args=(process, temp_root), daemon=True).start()
    return {"started": True, "version": release["version"]}


@router.get("/public-ip")
def public_ip():
    """Read the current public address and compare it with this app session."""
    global _original_public_ip
    try:
        response = requests.get("https://api.ipify.org", params={"format": "json"}, timeout=(3, 6))
        response.raise_for_status()
        current = str(response.json().get("ip", "")).strip()
        ipaddress.ip_address(current)
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(503, "softether.publicIpUnavailable") from exc
    with _public_ip_lock:
        if _original_public_ip is None:
            _original_public_ip = current
        original = _original_public_ip
    return {"original": original, "current": current, "changed": current != original,
            "restored": current == original}


class VpnGateProfile(BaseModel):
    host: str = Field(min_length=1, max_length=64)
    port: int = Field(ge=1, le=65535)
    name: str = Field(default="VPN Gate", min_length=1, max_length=64)


def _validate_profile_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9 ._-]+", name):
        raise HTTPException(422, "softether.invalidProfileName")
    return name


_VPN_COUNTRY_NAMES = {
    "RO": "Romania", "JP": "Japan", "IT": "Italy", "DE": "Germany",
    "FR": "France", "NL": "Netherlands", "US": "United States",
    "CA": "Canada", "GB": "United Kingdom", "ES": "Spain",
    "CH": "Switzerland", "PL": "Poland", "CZ": "Czech Republic",
    "SE": "Sweden", "NO": "Norway", "FI": "Finland", "BR": "Brazil",
    "AU": "Australia", "HK": "Hong Kong", "KR": "South Korea",
}


_CONNECTED_WORDS = ("connected", "connesso", "session established", "sessione stabilita")
_DISCONNECTED_WORDS = ("disconnect", "disconness", "not connected", "non conness", "offline")


def _parse_connected(output, returncode=0):
    """Interpret AccountStatusGet output: True, False or None (unknown).

    Only a "Status"/"Stato" row decides; "Disconnected" and "non connesso" are
    checked first because they contain the positive words as substrings.
    """
    text = output or ""
    for line in text.splitlines():
        if "|" not in line:
            continue
        label, value = (part.strip() for part in line.split("|", 1))
        normalized = label.lower()
        if "status" not in normalized and "stato" not in normalized:
            continue
        value = value.lower()
        if any(word in value for word in _DISCONNECTED_WORDS):
            return False
        if any(word in value for word in _CONNECTED_WORDS):
            return True
        # "Connecting", "Retrying", "Negotiating": not (yet) connected.
        return False
    lowered = text.lower()
    # vpncmd answers AccountStatusGet on an offline account with an error
    # ("The specified VPN Connection Setting is not connected").
    if any(word in lowered for word in ("not connected", "non è connesso", "non connesso")):
        return False
    # Failed command or unknown format: the state is unknown, not "offline".
    return None


def _account_connected(name):
    """Real connection state of one account; never raises (None = unknown)."""
    cmd = _find_vpncmd()
    if cmd is None:
        return None
    try:
        result = subprocess.run([str(cmd), "/client", "localhost", "/cmd", "AccountStatusGet", name],
                                capture_output=True, text=True, encoding="mbcs", errors="replace",
                                timeout=20, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return _parse_connected((result.stdout or "") + "\n" + (result.stderr or ""), result.returncode)


def _annotate_registered(servers, details):
    """Copy the public servers adding ``registered_profile``.

    The match is on the exact server IP (never a substring and never the
    profile name), so 1.2.3.4 cannot claim 1.2.3.45 and "VPN Gate IT" cannot
    claim "VPN Gate ITA-2".  Copies keep the shared server cache untouched.
    """
    by_host = {}
    for name in sorted(details):
        host = str(details[name].get("host") or "").strip()
        if host and host not in by_host:
            by_host[host] = name
    return [dict(server, registered_profile=by_host.get(str(server.get("host") or "").strip()))
            for server in servers]


def _read_vpngate_profiles():
    """AccountList + AccountGet of every "VPN Gate …" account: (raw, names, details)."""
    cmd = _find_vpncmd()
    if cmd is None:
        raise HTTPException(404, "softether.missing")
    try:
        _ensure_client_service()
        result = subprocess.run([str(cmd), "/client", "localhost", "/cmd", "AccountList"],
                                capture_output=True, text=True, encoding="mbcs", errors="replace",
                                timeout=20, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise HTTPException(503, "softether.serviceUnavailable") from exc
    if result.returncode != 0:
        raise HTTPException(502, "softether.profileFailed")
    raw = result.stdout or ""
    profiles = []
    for line in raw.splitlines():
        if "|" not in line:
            continue
        label, value = (part.strip() for part in line.split("|", 1))
        # AccountList prints the account name as the value of the labelled
        # row, not as the first column.  Keep every VPN Gate account name,
        # including profiles created manually with names such as "VPN Gate RO".
        if label.lower() == "vpn connection setting name" and value.startswith("VPN Gate ") and value not in profiles:
            profiles.append(value)
    details = {}
    for name in profiles:
        try:
            account = _run_vpncmd("AccountGet", name).stdout or ""
        except HTTPException:
            account = ""
        item = {"name": name, "registered": True}
        for line in account.splitlines():
            if "|" not in line:
                continue
            label, value = (part.strip() for part in line.split("|", 1))
            normalized = label.lower()
            if "destination vpn server host name" in normalized or "server hostname" in normalized:
                item["host"] = value
            elif "destination vpn server port number" in normalized or "server port" in normalized:
                try:
                    item["port"] = int(value)
                except ValueError:
                    pass
            elif "destination vpn server virtual hub name" in normalized or "virtual hub" in normalized:
                item["hub"] = value
            elif normalized == "status":
                item["status"] = value
        details[name] = item
    return raw, profiles, details


@router.get("/vpngate/profiles")
def list_vpngate_profiles():
    raw, profiles, details = _read_vpngate_profiles()
    for name, item in details.items():
        item["connected"] = _account_connected(name)
    live_by_host = {server["host"]: server for server in _vpngate_servers()}
    for item in details.values():
        match = re.search(r"\b([A-Z]{2})\s*$", item["name"])
        if match:
            country_code = match.group(1)
            item.setdefault("countryCode", country_code)
            item.setdefault("country", _VPN_COUNTRY_NAMES.get(country_code, country_code))
        live = live_by_host.get(item.get("host"))
        if live:
            for field in ("country", "countryCode", "ping", "speed", "sessions", "reachable"):
                item[field] = live.get(field)
            item["serverName"] = live.get("name")
        elif item.get("host"):
            # A saved profile can outlive the current VPN Gate top results.
            # Probe the configured endpoint so it still gets a live latency
            # and reachability indication.
            started = time.perf_counter()
            try:
                with socket.create_connection((item["host"], item.get("port", 443)), timeout=2.0):
                    item["reachable"] = True
                    item["ping"] = round((time.perf_counter() - started) * 1000)
            except (OSError, TimeoutError):
                item["reachable"] = False
    return {"raw": raw, "profiles": profiles, "details": details}


class VpnGateProfileDelete(BaseModel):
    name: str = Field(min_length=1, max_length=64)


def _run_vpncmd(*args):
    cmd = _find_vpncmd()
    if cmd is None:
        raise HTTPException(404, "softether.missing")
    try:
        _ensure_client_service()
        result = subprocess.run([str(cmd), "/client", "localhost", "/cmd", *args],
                                capture_output=True, text=True, encoding="mbcs", errors="replace",
                                timeout=30, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise HTTPException(503, "softether.serviceUnavailable") from exc
    if result.returncode != 0:
        raise HTTPException(502, "softether.profileFailed")
    return result


@router.post("/vpngate/profile/delete")
def delete_vpngate_profile(body: VpnGateProfileDelete):
    name = _validate_profile_name(body.name)
    cmd = _find_vpncmd()
    if cmd is None:
        raise HTTPException(404, "softether.missing")
    try:
        _ensure_client_service()
        result = subprocess.run([str(cmd), "/client", "localhost", "/cmd", "AccountDelete", name],
                                capture_output=True, text=True, encoding="mbcs", errors="replace",
                                timeout=20, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise HTTPException(503, "softether.serviceUnavailable") from exc
    if result.returncode != 0:
        raise HTTPException(502, "softether.profileFailed")
    return {"deleted": True, "name": name}


@router.post("/vpngate/profile/connect")
def connect_vpngate_profile(body: VpnGateProfileDelete):
    name = _validate_profile_name(body.name)
    result = _run_vpncmd("AccountConnect", name)
    status = _run_vpncmd("AccountStatusGet", name)
    # Same parser as the profile list: a bare substring test would read
    # "Disconnected"/"not connected" as connected.
    connected = _parse_connected(status.stdout or "") is True
    return {"connected": connected, "name": name, "output": result.stdout or ""}


@router.post("/vpngate/profile/disconnect")
def disconnect_vpngate_profile(body: VpnGateProfileDelete):
    name = _validate_profile_name(body.name)
    _run_vpncmd("AccountDisconnect", name)
    return {"connected": False, "name": name}


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
    _validate_profile_name(body.name)
    # The list check may be minutes old (cache): confirm in this very request
    # that the server still accepts TCP on the VPN port before writing a profile.
    if not _port_open(body.host, body.port):
        raise HTTPException(409, "softether.serverUnreachable")
    try:
        _ensure_client_service()
        result = subprocess.run([
            str(cmd), "/client", "localhost", "/cmd", "AccountCreate",
            body.name, "/SERVER:" + f"{body.host}:{body.port}", "/HUB:VPNGATE",
            "/USERNAME:vpn", "/NICNAME:VPN",
        ], capture_output=True, text=True, encoding="mbcs", errors="replace", timeout=20, check=False,
            stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise HTTPException(503, "softether.serviceUnavailable") from exc
    if result.returncode != 0:
        raise HTTPException(502, "softether.profileFailed")
    try:
        password = subprocess.run([
            str(cmd), "/client", "localhost", "/cmd", "AccountPasswordSet",
            body.name, "/PASSWORD:vpn", "/TYPE:standard",
        ], capture_output=True, text=True, encoding="mbcs", errors="replace", timeout=20, check=False,
            stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(503, "softether.serviceUnavailable") from exc
    if password.returncode != 0:
        raise HTTPException(502, "softether.profileFailed")
    verify = subprocess.run([
        str(cmd), "/client", "localhost", "/cmd", "AccountGet", body.name,
    ], capture_output=True, text=True, encoding="mbcs", errors="replace", timeout=20, check=False,
        stdin=subprocess.DEVNULL)
    if verify.returncode != 0:
        raise HTTPException(502, "softether.profileFailed")
    # The profile is never connected automatically, so it normally is not
    # connected yet: only a real "connected" state counts as success.
    connected = _account_connected(body.name)
    result = {"created": True, "connected": connected, "success": connected is True,
              "name": body.name, "host": body.host, "port": body.port}
    if connected is not True:
        result["detail"] = "softether.createdNotConnected"
    return result


def _port_open(host, port, timeout=2.0):
    """TCP reachability only: the port answers, the VPN session is not tested."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


@router.post("/open-manager")
async def open_manager(request: Request):
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    if os.name != "nt":
        raise HTTPException(409, "softether.unsupported")
    manager = _detect()  # Recheck at click time; never trust a path from the UI.
    if manager is None:
        raise HTTPException(404, "softether.missing")
    # Il Client Manager può essere stato chiuso insieme al servizio. Avvialo
    # prima della GUI, così il click funziona anche senza icona nella tray.
    try:
        _ensure_client_service()
    except (OSError, RuntimeError, TypeError, subprocess.SubprocessError):
        # Il Client Manager può comunque avviarsi autonomamente; il servizio
        # viene quindi trattato come best effort e non blocca l'apertura GUI.
        pass
    state = _manager_state(manager)
    if state == "visible":
        # Its window is already on screen: nothing to open.
        return {"opened": True, "already_running": True}
    # "tray": the Manager is alive but hidden. Starting it again asks for its
    # window (SoftEther's Manager is single-instance, so the running copy is
    # the one that shows up). A fresh start stays minimized in the tray.
    show_command = 1 if state == "tray" else 6  # SW_SHOWNORMAL / SW_MINIMIZE
    try:
        # Do not inherit the Uvicorn handles: the GUI must remain independent
        # when the panel is started with ``py main.py``.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = show_command
        subprocess.Popen([str(manager)], cwd=str(manager.parent),
                         shell=False, close_fds=True, startupinfo=startupinfo)
    except OSError as exc:
        raise HTTPException(503, "softether.openFailed") from exc
    return {"opened": True}


@router.post("/hide-manager")
async def hide_manager(request: Request):
    """Send the open Manager window back to the tray. The program keeps running."""
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    manager = _detect()
    if manager is None:
        raise HTTPException(404, "softether.missing")
    if os.name != "nt" or _manager_state(manager) != "visible":
        return {"hidden": False, "reason": "not_visible"}
    hidden = _hide_manager_windows(manager)
    return {"hidden": hidden > 0}


def _hide_manager_windows(manager) -> int:
    """SW_HIDE on every visible top-level window of the Manager process.

    The tray icon belongs to the running process, so it stays where it is.
    Returns how many windows were hidden.
    """
    import ctypes
    from ctypes import wintypes
    pids = set()
    for row in csv.reader(
        (subprocess.run(["tasklist.exe", "/FI", f"IMAGENAME eq {manager.name}", "/FO", "CSV", "/NH"],
                        capture_output=True, text=True, encoding="mbcs", errors="replace",
                        timeout=5, check=False).stdout or "").splitlines()
    ):
        if len(row) >= 2 and row[0].lower() == manager.name.lower():
            try:
                pids.add(int(row[1]))
            except ValueError:
                continue
    if not pids:
        return 0
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    hidden = 0
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def on_window(hwnd, _lparam):
        nonlocal hidden
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
                hidden += 1
        return True

    callback = callback_type(on_window)
    user32.EnumWindows(callback, 0)
    return hidden


@router.post("/close-manager")
async def close_manager(request: Request):
    if request.query_params or await request.body():
        raise HTTPException(400, "softether.noParameters")
    manager = _detect()
    if manager is None:
        raise HTTPException(404, "softether.missing")
    # A plain taskkill sends a close request, which the Manager answers by
    # hiding to the tray: that is what "hide" already does. /F ends the Manager
    # process itself. The VPN connection runs in the separate vpnclient
    # service, so it stays up after this call.
    try:
        result = subprocess.run(["taskkill.exe", "/F", "/IM", manager.name, "/T"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(503, "softether.closeFailed") from exc
    # taskkill exits 128 when no such process exists: nothing was open to close.
    if result.returncode == 128:
        return {"closed": False, "reason": "not_running"}
    if result.returncode != 0:
        raise HTTPException(503, "softether.closeFailed")
    return {"closed": True}
