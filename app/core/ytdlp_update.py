"""yt-dlp as a standalone, updatable executable (portable style).

RaiPlay downloads run ``yt-dlp.exe`` as a subprocess instead of importing the
Python package, so yt-dlp can be updated by swapping one file instead of
shipping a new panel release. The executable lives in ``<data dir>/yt-dlp/``,
next to the managed ``ffmpeg/`` folder.

Nothing here runs on its own: no install at first start, no background check.
The user asks for the check (Impostazioni > Download > Hardware, "Controlla
yt-dlp"); the only implicit update is the single one a failed RaiPlay
download may ask for before its one retry (``update_for_retry``).

Update path: the official ``yt-dlp/yt-dlp`` GitHub release, asset
``yt-dlp.exe``, SHA-256 taken from the asset ``digest`` the GitHub API
publishes (no digest, no update). The file is written to ``yt-dlp.exe.new``,
verified, probed with ``--version`` and only then swapped in; any failure
deletes ``.new`` and leaves the old executable untouched. Every redirect is
checked against a short list of GitHub hosts.

While a yt-dlp process is running (``in_use``) no update starts, and while an
update runs new yt-dlp processes wait for it to finish.
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
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from app import config

logger = logging.getLogger(__name__)

REPO = "yt-dlp/yt-dlp"
ASSET = "yt-dlp.exe"
RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# api.github.com answers the release query; github.com serves the asset link,
# which redirects to GitHub's asset storage (objects.* historically,
# release-assets.* since 2025). Nothing else is accepted, redirects included.
ALLOWED_HOSTS = frozenset({"api.github.com", "github.com", "objects.githubusercontent.com",
                           "release-assets.githubusercontent.com"})
USER_AGENT = "StreamingCommunityDownloader/ytdlp-update"
TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 120
VERSION_TIMEOUT_S = 30
MAX_ASSET_BYTES = 100 * 1024 * 1024
WAIT_FOR_UPDATE_S = 15 * 60
_VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?$")
_SHA_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")


class YtdlpMissingError(RuntimeError):
    """No yt-dlp executable to run."""


class YtdlpBusyError(RuntimeError):
    """An update was asked for while yt-dlp downloads are running."""


class YtdlpUpdateError(RuntimeError):
    """The update was refused or failed; the old executable is untouched."""


MISSING_MESSAGE = ("yt-dlp non è installato. Apri Impostazioni > Download > Hardware e usa "
                   "\"Controlla yt-dlp\" per scaricarlo.")

# ── where the executable lives ───────────────────────────────────────────────

def bin_dir() -> Path:
    return config.DATA_FILE.with_name("yt-dlp")


def managed_path() -> Path:
    return bin_dir() / (ASSET if os.name == "nt" else "yt-dlp")


def executable() -> str | None:
    """The managed executable; on POSIX (Docker) also ``yt-dlp`` on PATH.

    On Windows only the managed file counts: a stray yt-dlp on PATH (a venv's
    console script) is not something the update can replace.
    """
    managed = managed_path()
    if managed.is_file():
        return str(managed)
    if os.name != "nt":
        return shutil.which("yt-dlp")
    return None


def can_update() -> bool:
    """The GitHub asset is a Windows executable."""
    return os.name == "nt"


def require_executable() -> str:
    exe = executable()
    if not exe:
        raise YtdlpMissingError(MISSING_MESSAGE)
    return exe


def hidden_process_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def subprocess_env() -> dict:
    """yt-dlp.exe is a frozen Python: force UTF-8 on its pipes."""
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def probe_version(exe: str | os.PathLike, timeout: float = VERSION_TIMEOUT_S) -> str | None:
    """``<exe> --version`` -> a valid yt-dlp version string, else None."""
    try:
        done = subprocess.run([str(exe), "--version"], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=timeout, shell=False,
                              env=subprocess_env(), **hidden_process_options())
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("yt-dlp --version failed for %s: %s", exe, exc)
        return None
    if done.returncode != 0:
        return None
    text = (done.stdout or b"").decode("utf-8", "replace").strip()
    first = text.splitlines()[0].strip() if text else ""
    return first if _VERSION_RE.match(first) else None


def installed_version() -> str | None:
    exe = executable()
    return probe_version(exe) if exe else None


# ── busy gate: downloads vs update ───────────────────────────────────────────

_gate = threading.Condition()
_active = 0
_updating = False
_retry_update_done = False


def active_downloads() -> int:
    with _gate:
        return _active


@contextlib.contextmanager
def in_use():
    """Held for the lifetime of a yt-dlp process. Waits out a running update."""
    global _active
    deadline = time.monotonic() + WAIT_FOR_UPDATE_S
    with _gate:
        while _updating:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise YtdlpBusyError("Aggiornamento di yt-dlp ancora in corso")
            _gate.wait(timeout=min(remaining, 1.0))
        _active += 1
    try:
        yield
    finally:
        with _gate:
            _active -= 1
            _gate.notify_all()


@contextlib.contextmanager
def _updating_lock():
    global _updating
    with _gate:
        if _active:
            raise YtdlpBusyError("Ci sono download yt-dlp in corso: riprova quando sono terminati")
        if _updating:
            raise YtdlpBusyError("Aggiornamento di yt-dlp già in corso")
        _updating = True
    try:
        yield
    finally:
        with _gate:
            _updating = False
            _gate.notify_all()


# ── GitHub ───────────────────────────────────────────────────────────────────

def _check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS or parts.port not in (None, 443):
        raise YtdlpUpdateError(f"Host non consentito per l'aggiornamento di yt-dlp: {parts.hostname or url}")
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
    """``{"version", "url", "sha256", "size"}`` of the latest stable yt-dlp.exe."""
    with _open(RELEASE_URL, TIMEOUT_S) as response:
        data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    version = str(data.get("tag_name") or "").strip()
    if not _VERSION_RE.match(version):
        raise YtdlpUpdateError(f"Versione yt-dlp non valida nella release GitHub: {version!r}")
    asset = next((a for a in data.get("assets") or [] if a.get("name") == ASSET), None)
    if asset is None:
        raise YtdlpUpdateError("La release GitHub di yt-dlp non contiene yt-dlp.exe")
    match = _SHA_RE.match(str(asset.get("digest") or ""))
    url = str(asset.get("browser_download_url") or "")
    if not url.startswith(f"https://github.com/{REPO}/releases/download/"):
        raise YtdlpUpdateError("URL dell'asset yt-dlp.exe inatteso")
    return {"version": version, "url": url, "size": int(asset.get("size") or 0),
            "sha256": match.group(1).lower() if match else None}


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
                raise YtdlpUpdateError("yt-dlp.exe scaricato più grande del previsto")
            out.write(chunk)
            digest.update(chunk)
    if expected_size and written != expected_size:
        raise YtdlpUpdateError("yt-dlp.exe scaricato incompleto")
    return digest.hexdigest()


def _retry_fs(fn, tries: int = 40, delay: float = 0.5):
    """Windows: an antivirus or a closing yt-dlp may hold the file a few seconds."""
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


def update(*, force: bool = False) -> dict:
    """Install or update yt-dlp.exe from GitHub. Refuses while downloads run."""
    if not can_update():
        raise YtdlpUpdateError("L'aggiornamento di yt-dlp.exe è previsto solo su Windows")
    with _updating_lock():
        release = latest_release()
        if not release["sha256"]:
            raise YtdlpUpdateError("GitHub non pubblica lo SHA-256 di yt-dlp.exe: aggiornamento rifiutato")
        current = installed_version()
        if current and not force and not _newer(release["version"], current):
            return {"updated": False, "version": current, "latest": release["version"]}
        root = bin_dir()
        root.mkdir(parents=True, exist_ok=True)
        target = managed_path()
        new = target.with_name(target.name + ".new")
        try:
            digest = _download(release["url"], new, release["size"])
            if not hmac.compare_digest(digest, release["sha256"]):
                raise YtdlpUpdateError("SHA-256 di yt-dlp.exe non corrispondente: file scartato")
            probed = None
            for attempt in range(3):  # an antivirus may block the very first run
                probed = probe_version(new)
                if probed:
                    break
                time.sleep(1.0)
            if not probed:
                raise YtdlpUpdateError("Il nuovo yt-dlp.exe non risponde a --version: file scartato")
            _retry_fs(lambda: new.replace(target))
        finally:
            if new.exists():
                with contextlib.suppress(OSError):
                    _retry_fs(lambda: new.unlink(missing_ok=True), tries=10)
        logger.info("yt-dlp %s installed in %s", probed, root)
        return {"updated": True, "version": probed, "previous": current, "latest": release["version"]}


def update_for_retry() -> bool:
    """The one update a failed RaiPlay download may ask for, once per session.

    True only when a newer yt-dlp was actually installed, so the caller's one
    retry runs a different executable. Never with other yt-dlp downloads
    running, never when yt-dlp is missing (that is the user's explicit step).
    """
    global _retry_update_done
    with _gate:
        if _retry_update_done or _active or _updating:
            return False
        _retry_update_done = True
    if executable() is None:
        return False
    try:
        return bool(update().get("updated"))
    except Exception as exc:
        logger.warning("yt-dlp update before retry failed: %s", exc)
        return False


def status() -> dict:
    """Present? Which version? Latest on GitHub? Called on request only."""
    exe = executable()
    current = probe_version(exe) if exe else None
    try:
        latest = latest_version()
        error = None
    except Exception as exc:  # offline, GitHub down, malformed answer
        logger.warning("yt-dlp version check failed: %s", exc)
        latest, error = None, "Impossibile leggere l'ultima versione su GitHub"
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
