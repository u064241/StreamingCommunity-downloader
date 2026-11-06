"""Prepare a pinned fork release and launch the existing standalone helper.

Only the application EXE is replaced. Data and helper upgrades remain manual.
After a verified replacement the external helper restarts the application.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote
import zipfile

from app import distribution, updates
from app.portable_update import _path
from app.update_download import UpdateDownloadError, download_verified, github_response

EXE = "StreamingCommunityDownloader.exe"
HELPER = "StreamingCommunityDownloader-Updater.exe"
_lock = threading.Lock()
_prepared = None
_running = None
PREPARED_TTL = 30 * 60
HELPER_READY_TIMEOUT = 120


def _release_asset(release, name):
    matches = [a for a in release["assets"] if a["name"] == name]
    if len(matches) != 1:
        raise UpdateDownloadError("Asset release mancante o duplicato")
    asset = matches[0]
    prefix = f"https://github.com/{distribution.PRODUCT}/releases/download/{quote(release['tag'], safe='')}/"
    if asset["url"] != prefix + quote(name, safe=""):
        raise UpdateDownloadError("Asset estraneo alla release selezionata")
    return asset


def _manifest(release):
    asset = _release_asset(release, "update-manifest.json")
    with github_response(asset["url"]) as response:
        payload = bytearray()
        for chunk in response.iter_content(65536):
            payload.extend(chunk)
            if len(payload) > 256 * 1024:
                raise UpdateDownloadError("Manifest troppo grande")
    data = json.loads(payload.decode("utf-8-sig"))
    if (data.get("schema") != 1 or data.get("variant") != "u064241"
            or data.get("channel") != "stable" or data.get("repository") != distribution.PRODUCT
            or data.get("version") != release["version"]):
        raise UpdateDownloadError("Manifest incompatibile con questa release")
    name = f"StreamingCommunityDownloader-{release['version']}-Portable.zip"
    records = [a for a in data["assets"] if a["name"] == name]
    if len(records) != 1:
        raise UpdateDownloadError("Pacchetto portable mancante o duplicato")
    record = records[0]
    asset = _release_asset(release, name)
    if (type(record["size"]) is not int or not 0 < record["size"] <= 2 * 1024**3
            or record["size"] != asset["size"]
            or not re.fullmatch(r"[0-9a-fA-F]{64}", record["sha256"])):
        raise UpdateDownloadError("Dimensione o SHA256 non valido")
    return asset, record


def _idle():
    from app.jobs import job_manager
    if job_manager.has_active_work() or any(
        job["status"] in {"running", "queued", "paused", "scheduled"}
        for job in job_manager.list_jobs()
    ):
        raise UpdateDownloadError("Completa o annulla i lavori prima di aggiornare")


def _runtime():
    if distribution.mode() != "portable":
        raise UpdateDownloadError("Aggiornamento diretto consentito solo alla versione portable")
    exe = _path(sys.executable)
    helper = _path(exe.parent / HELPER)
    if exe.name != EXE:
        raise UpdateDownloadError("Eseguibile portable non riconosciuto")
    return exe, helper


def prepare(owner):
    global _prepared
    with _lock:
        if _running is not None:
            raise UpdateDownloadError("Aggiornamento già avviato; riavvia l'app per riprovare")
        _runtime()
        _idle()
        release = updates.check(force=True)
        if (release.get("status") != "update_available" or release.get("draft")
                or release.get("prerelease")
                or not re.fullmatch(r"\d+\.\d+\.\d+", release.get("version", ""))):
            raise UpdateDownloadError("Nessuna release stabile aggiornabile")
        asset, record = _manifest(release)
        folder = tempfile.TemporaryDirectory(prefix="sc-portable-update-")
        try:
            archive = download_verified(asset["url"], record["sha256"], folder.name)
            if archive.stat().st_size != record["size"]:
                raise UpdateDownloadError("Dimensione pacchetto non corrispondente")
            target = Path(folder.name) / EXE
            # Never extract paths supplied by ZIP entries. Only the exact root EXE.
            with zipfile.ZipFile(archive) as package:
                entries = package.infolist()
                if (sorted(e.filename for e in entries) != sorted([EXE, HELPER, distribution.MARKER])
                        or any(e.file_size > 2 * 1024**3 or e.is_dir() for e in entries)
                        or package.getinfo(distribution.MARKER).file_size > 4096):
                    raise UpdateDownloadError("Contenuto pacchetto portable non supportato")
                marker = json.loads(package.read(distribution.MARKER))
                if marker != {"schema": 1, "repository": distribution.PRODUCT, "mode": "portable"}:
                    raise UpdateDownloadError("Marker pacchetto non valido")
                with package.open(EXE) as src, target.open("xb") as dst:
                    import shutil
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if _prepared:
                _prepared["folder"].cleanup()
            token = secrets.token_urlsafe(32)
            _prepared = dict(folder=folder, asset=target, sha256=digest, token=token,
                             owner=owner, expires=time.monotonic() + PREPARED_TTL)
            return {"token": token, "version": release["version"]}
        except BaseException:
            folder.cleanup()
            raise


def apply(owner, token, app=None):
    global _prepared, _running
    with _lock:
        state = _prepared
        if (not state or state["owner"] != owner or state["expires"] < time.monotonic()
                or not secrets.compare_digest(token, state["token"])):
            raise UpdateDownloadError("Preparazione scaduta o non autorizzata")
        exe, helper = _runtime()
        _idle()
        backup = exe.with_name(f"{exe.stem}.previous-{secrets.token_hex(8)}.exe")
        ready = Path(state["folder"].name) / f"ready-{secrets.token_hex(8)}"
        # No shell, no caller-controlled paths. Helper waits on a pinned PID.
        process = subprocess.Popen([
            str(helper), "apply", "--pid", str(os.getpid()), "--exe", str(exe),
            "--asset", str(state["asset"]), "--backup", str(backup),
            "--sha256", state["sha256"], "--timeout", "300", "--ready-file", str(ready),
            "--restart",
        ], cwd=exe.parent, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # The helper is a one-file PyInstaller executable and the staged app
        # is roughly 100 MB. Extraction, antivirus inspection and SHA256
        # verification can legitimately take longer than the old 15-second
        # window, especially on the first update.
        for _ in range(HELPER_READY_TIMEOUT * 10):
            if ready.is_file() or process.poll() is not None:
                break
            time.sleep(0.1)
        if not ready.is_file() or process.poll() is not None:
            # Only terminate our own helper, before it has received process exit.
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            raise UpdateDownloadError(
                "Helper non pronto entro 120 secondi: applicazione lasciata in esecuzione. "
                "Riprova dopo aver escluso la cartella portable dal controllo in tempo reale "
                "dell'antivirus, se applicabile."
            )
        _running = (process, state)  # retain staged files until application exit
        _prepared = None
        if app is not None:
            def stop_after_response():
                time.sleep(0.8)
                server = getattr(app.state, "uvicorn_server", None)
                if server is not None:
                    server.should_exit = True
            threading.Thread(target=stop_after_response, daemon=True).start()
        return {"status": "restarting", "backup": backup.name,
                "message": "Aggiornamento installato. L'applicazione si sta riavviando automaticamente."}
