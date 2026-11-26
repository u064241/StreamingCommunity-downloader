"""aria2c as a managed, updatable executable (Windows), used by app/core/hls_aria2.py.

The file lives in ``<data dir>/aria2/aria2c.exe``. Nothing runs on its own: the
user asks for the check ("Controlla aria2c" in Impostazioni > Download). If
aria2c is neither managed nor on PATH, the check offers the download.

Update path: the official ``aria2/aria2`` GitHub release, asset
``aria2-<version>-win-64bit-build<n>.zip``. Its SHA-256 comes from the asset
``digest`` when GitHub publishes one, otherwise only from ``KNOWN_SHA256``
(versions whose hash was checked by hand). No hash, no update. The executable
is read out of the zip, probed with ``--version`` and only then swapped in.
Every redirect is checked against a short list of GitHub hosts.

No update while an aria2c download runs (``running``), and no download starts
while an update runs.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from app import config

logger = logging.getLogger(__name__)

REPO = "aria2/aria2"
RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_RE = re.compile(r"^aria2-(\d+\.\d+\.\d+)-win-64bit-build\d+\.zip$")
ALLOWED_HOSTS = frozenset({"api.github.com", "github.com", "objects.githubusercontent.com",
                           "release-assets.githubusercontent.com"})
USER_AGENT = "StreamingCommunityDownloader/aria2-update"
TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 120
VERSION_TIMEOUT_S = 15
MAX_ASSET_BYTES = 20 * 1024 * 1024
MAX_EXE_BYTES = 20 * 1024 * 1024
# SHA-256 of release archives checked by hand (winget manifest and the download agree).
KNOWN_SHA256 = {
    "1.37.0": "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288",
}
_VERSION_LINE = re.compile(r"^aria2 version (\d+\.\d+\.\d+)")


class Aria2UpdateError(RuntimeError):
    """The update was refused or failed; the old executable is untouched."""


class Aria2BusyError(RuntimeError):
    """An aria2c download and an update cannot run at the same time."""


# ── where the executable lives ───────────────────────────────────────────────

def bin_dir() -> Path:
    return config.DATA_FILE.with_name("aria2")


def managed_path() -> Path:
    return bin_dir() / "aria2c.exe"


def executable() -> str | None:
    """The managed executable, else aria2c on PATH, else None."""
    managed = managed_path()
    if managed.is_file():
        return str(managed)
    return shutil.which("aria2c")


def can_update() -> bool:
    """The GitHub asset is a Windows executable."""
    return os.name == "nt"


def _hidden() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def probe_version(exe, timeout: float = VERSION_TIMEOUT_S) -> str | None:
    """``<exe> --version`` -> the version number, else None."""
    try:
        done = subprocess.run([str(exe), "--version"], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=timeout, shell=False, **_hidden())
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("aria2c --version failed for %s: %s", exe, exc)
        return None
    if done.returncode != 0:
        return None
    lines = (done.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    match = _VERSION_LINE.match(lines[0].strip()) if lines else None
    return match.group(1) if match else None


def installed_version() -> str | None:
    exe = executable()
    return probe_version(exe) if exe else None


# ── busy gate: aria2c downloads vs update ────────────────────────────────────

_state = threading.Lock()
_running = 0
_updating = False


def active_downloads() -> int:
    with _state:
        return _running


@contextlib.contextmanager
def running():
    """Held for the lifetime of one aria2c download. Refused while an update runs."""
    global _running
    with _state:
        if _updating:
            raise Aria2BusyError("Aggiornamento di aria2c in corso")
        _running += 1
    try:
        yield
    finally:
        with _state:
            _running -= 1


# ── GitHub ───────────────────────────────────────────────────────────────────

def _check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS or parts.port not in (None, 443):
        raise Aria2UpdateError(f"Host non consentito per l'aggiornamento di aria2c: {parts.hostname or url}")
    return url


class _CheckedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: float):
    opener = build_opener(HTTPSHandler(), _CheckedRedirect())
    response = opener.open(Request(_check_url(url), headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json, application/octet-stream"}),
        timeout=timeout)
    try:
        _check_url(response.geturl())
    except Exception:
        response.close()
        raise
    return response


def latest_release() -> dict:
    """``{"version", "url", "sha256", "size"}`` of the latest Windows aria2 release."""
    with _open(RELEASE_URL, TIMEOUT_S) as response:
        data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    for asset in data.get("assets") or []:
        match = ASSET_RE.match(str(asset.get("name") or ""))
        if not match:
            continue
        version = match.group(1)
        url = str(asset.get("browser_download_url") or "")
        if not url.startswith(f"https://github.com/{REPO}/releases/download/"):
            raise Aria2UpdateError("URL dell'asset aria2c inatteso")
        digest = re.match(r"^sha256:([0-9a-fA-F]{64})$", str(asset.get("digest") or ""))
        sha = digest.group(1).lower() if digest else KNOWN_SHA256.get(version)
        if not sha:
            raise Aria2UpdateError("GitHub non pubblica lo SHA-256 di questa versione di aria2c: aggiornamento rifiutato")
        return {"version": version, "url": url, "size": int(asset.get("size") or 0), "sha256": sha}
    raise Aria2UpdateError("La release GitHub di aria2 non contiene l'archivio Windows a 64 bit")


def latest_version() -> str:
    return latest_release()["version"]


def _download(url: str, dest: Path, expected_size: int) -> str:
    digest = hashlib.sha256()
    written = 0
    with _open(url, DOWNLOAD_TIMEOUT_S) as response, open(dest, "wb") as out:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_ASSET_BYTES:
                raise Aria2UpdateError("Archivio aria2 scaricato più grande del previsto")
            out.write(chunk)
            digest.update(chunk)
    if expected_size and written != expected_size:
        raise Aria2UpdateError("Archivio aria2 scaricato incompleto")
    return digest.hexdigest()


def _retry_fs(fn, tries: int = 40, delay: float = 0.5):
    """Windows: an antivirus may hold the file for a few seconds."""
    for attempt in range(tries):
        try:
            return fn()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(delay)


def _key(version: str) -> tuple[int, ...] | None:
    parts = re.findall(r"\d+", version or "")
    return tuple(int(p) for p in parts) if parts else None


def _newer(latest: str | None, current: str | None) -> bool:
    new_key, cur_key = _key(latest or ""), _key(current or "")
    return bool(new_key and cur_key and new_key > cur_key)


# ── install / update ─────────────────────────────────────────────────────────

def update(*, force: bool = False) -> dict:
    """Install or update aria2c.exe from GitHub. Refused while aria2c downloads run."""
    global _updating
    if not can_update():
        raise Aria2UpdateError("L'aggiornamento di aria2c.exe è previsto solo su Windows")
    with _state:
        if _running:
            raise Aria2BusyError("Ci sono download con aria2c in corso: riprova quando sono terminati")
        if _updating:
            raise Aria2BusyError("Aggiornamento di aria2c già in corso")
        _updating = True
    try:
        return _update(force)
    finally:
        with _state:
            _updating = False


def _update(force: bool) -> dict:
    release = latest_release()
    current = installed_version()
    if current and not force and not _newer(release["version"], current):
        return {"updated": False, "version": current, "latest": release["version"]}
    root = bin_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = managed_path()
    archive = root / "aria2.zip.new"
    new = root / "aria2c.exe.new"
    try:
        digest = _download(release["url"], archive, release["size"])
        if not hmac.compare_digest(digest, release["sha256"]):
            raise Aria2UpdateError("SHA-256 dell'archivio aria2 non corrispondente: file scartato")
        with zipfile.ZipFile(archive) as zf:
            member = next((m for m in zf.infolist() if m.filename.endswith("aria2c.exe")), None)
            if member is None or member.file_size > MAX_EXE_BYTES:
                raise Aria2UpdateError("aria2c.exe non trovato nell'archivio")
            with zf.open(member) as src, open(new, "wb") as dst:
                shutil.copyfileobj(src, dst)
        probed = None
        for _attempt in range(3):  # an antivirus may block the very first run
            probed = probe_version(new)
            if probed:
                break
            time.sleep(1.0)
        if not probed:
            raise Aria2UpdateError("Il nuovo aria2c.exe non risponde a --version: file scartato")
        _retry_fs(lambda: new.replace(target))
    finally:
        for leftover in (archive, new):
            if leftover.exists():
                with contextlib.suppress(OSError):
                    _retry_fs(lambda p=leftover: p.unlink(missing_ok=True), tries=10)
    logger.info("aria2c %s installed in %s", probed, root)
    return {"updated": True, "version": probed, "previous": current, "latest": release["version"]}


def status() -> dict:
    """Present? Which version? Latest on GitHub? Called on request only."""
    exe = executable()
    current = probe_version(exe) if exe else None
    try:
        latest = latest_version()
        error = None
    except Exception as exc:  # offline, GitHub down, no hash to verify with
        logger.warning("aria2c version check failed: %s", exc)
        latest, error = None, str(exc) if isinstance(exc, Aria2UpdateError) else "Impossibile leggere l'ultima versione su GitHub"
    return {
        "present": exe is not None,
        "path": exe or str(managed_path()),
        "installed": current,
        "latest": latest,
        "update_available": _newer(latest, current),
        "can_update": can_update(),
        "busy": active_downloads() > 0,
        "error": error,
    }
