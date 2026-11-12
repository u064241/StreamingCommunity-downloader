import asyncio
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.auth.deps import current_user, require
from app.auth.permissions import Permission
from app.config import download_dir, VIDEOS_DIR, DATA_FILE
from app.core import container
from app.core import probe
from app.core.ffmpeg_path import get_ffmpeg_exe, ffmpeg_file_arg, hidden_process_options

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])

# Browsing and streaming is separate from mutating: a user may be allowed to
# watch the library without being able to delete half of it.
CAN_VIEW = [Depends(require(Permission.VIEW_LIBRARY))]
CAN_MANAGE = [Depends(require(Permission.MANAGE_FILES))]


def _safe_path_strict(rel_path: str) -> Path:
    """Resolve a path strictly *inside* VIDEOS_DIR, or raise.

    ValueError for a path that escapes the folder (traversal) or that is the
    folder itself: "", "." and "sub/.." all resolve to the root, and every
    caller acts on an entry — deleting, renaming or moving the root would take
    the whole library with it. FileNotFoundError when nothing is there.
    """
    base = download_dir(VIDEOS_DIR).resolve()
    target = (base / rel_path).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError("Invalid path")
    if not target.exists():
        raise FileNotFoundError("Not found")
    return target


def _safe_path(rel_path: str) -> Path:
    """_safe_path_strict for a route: the same checks, as HTTP errors."""
    try:
        return _safe_path_strict(rel_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")


def _sees_server_paths(request: Request) -> bool:
    """Where things live on the server is configuration: only someone who can
    change it gets to read it. Everyone else is given names relative to the
    library they are browsing."""
    return current_user(request).has(Permission.MANAGE_SETTINGS)


def _public_path(path: Path, request: Request) -> str:
    """A path as this caller may see it: absolute, relative to the download
    folder, or — outside it — just the name."""
    if _sees_server_paths(request):
        return str(path)
    try:
        return str(path.resolve().relative_to(download_dir(VIDEOS_DIR).resolve()))
    except ValueError:
        return path.name


def _loopback(host) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _local(request: Request):
    """Only a browser on the machine the panel runs on.

    Same rule as app/routers/softether.py and updates.py apply to their own
    local-only actions: a loopback peer, a loopback Host, nothing forwarded by
    a proxy, and — when the browser says where the request was made from — the
    panel's own origin.
    """
    hostname = request.url.hostname
    if (not request.client or not _loopback(request.client.host)
            or not (hostname == "localhost" or _loopback(hostname))
            or any(name == "forwarded" or name.startswith("x-forwarded-")
                   for name in request.headers)):
        raise HTTPException(status_code=403, detail="Azione consentita solo dal pannello locale")
    origin = request.headers.get("origin")
    if origin is not None:
        try:
            parts = urlsplit(origin)
        except ValueError:
            parts = None
        if parts is None or (parts.scheme, parts.netloc) != (request.url.scheme, request.url.netloc):
            raise HTTPException(status_code=403, detail="Azione consentita solo dal pannello locale")


def _read_data() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# ── Disk usage ─────────────────────────────────────────────────────────────────
#
# Reported per *volume*, not per library: in practice all three libraries are
# folders on the same mount, and three identical figures said nothing useful
# while costing three stats of a possibly sleeping NFS share.

_DISK_USAGE_TTL = 20  # seconds; the file manager reloads on every navigation
_disk_usage_cache: dict = {"at": 0.0, "data": None}


def _library_paths() -> list[str]:
    paths = [lib["path"] for lib in _read_data().get("libraries", []) if lib.get("path")]
    return paths or [str(download_dir(VIDEOS_DIR))]


def _compute_disk_usage() -> dict:
    now = time.monotonic()
    if _disk_usage_cache["data"] is not None and now - _disk_usage_cache["at"] < _DISK_USAGE_TTL:
        return _disk_usage_cache["data"]

    volumes: dict[int, dict] = {}
    errors: list[str] = []
    for path in _library_paths():
        try:
            device = os.stat(path).st_dev
        except OSError as e:
            # An unmounted or mistyped library path is an expected failure mode
            # and must not blank out the volumes that are fine.
            errors.append(str(e))
            continue
        if device in volumes:
            volumes[device]["paths"].append(path)
            continue
        total, used, free = shutil.disk_usage(path)
        volumes[device] = {"total": total, "used": used, "free": free, "paths": [path]}

    result = {"volumes": list(volumes.values()), "errors": errors}
    _disk_usage_cache["at"] = now
    _disk_usage_cache["data"] = result
    return result


@router.get("/disk-usage", dependencies=CAN_VIEW)
async def get_disk_usage(request: Request):
    usage = await asyncio.to_thread(_compute_disk_usage)
    if _sees_server_paths(request):
        return usage
    # The figures are the point; which mount they belong to is not for a
    # viewer, and neither is the OS error text, which spells the path out.
    return {
        "volumes": [
            {**volume, "paths": [Path(p).name or p for p in volume["paths"]]}
            for volume in usage["volumes"]
        ],
        "errors": ["Percorso di libreria non accessibile" for _ in usage["errors"]],
    }


def _build_tree(directory: Path, base: Path, excluded: set) -> list[dict]:
    entries = []
    try:
        for item in sorted(directory.iterdir()):
            if item.name in excluded:
                continue
            if item.is_dir():
                children = _build_tree(item, base, excluded)
                entries.append({
                    "name": item.name,
                    "type": "directory",
                    "path": str(item.relative_to(base)),
                    "children": children,
                    "empty": len(children) == 0,
                })
            elif item.is_file():
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "type": "file",
                    "path": str(item.relative_to(base)),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })
    except PermissionError:
        pass
    return entries


def _build_library_tree(directory: Path, root: Path | None = None,
                        depth: int = 0, max_depth: int = 3) -> list[dict]:
    """Recursively list subdirectories only (no files) for library navigation.

    With ``root`` each entry carries ``path``, relative to that library root,
    instead of ``abs_path``: see _sees_server_paths.
    """
    if depth >= max_depth:
        return []
    entries = []
    try:
        for item in sorted(directory.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                location = (
                    {"abs_path": str(item.resolve())} if root is None
                    else {"path": item.relative_to(root).as_posix()}
                )
                entries.append({
                    "name": item.name,
                    **location,
                    "children": _build_library_tree(item, root, depth + 1, max_depth),
                })
    except PermissionError:
        pass
    return entries


_DEFAULT_EXCLUDED = {"images", "snippets", "lost+found"}
_TYPE_LABELS = {"film": "Film", "tv": "Serie TV", "anime": "Anime"}


def _search_tree(directory: Path, base: Path, query: str, excluded: set) -> list[dict]:
    results = []
    try:
        for item in sorted(directory.iterdir()):
            if item.name in excluded:
                continue
            if query in item.name.lower():
                if item.is_dir():
                    results.append({
                        "name": item.name,
                        "type": "directory",
                        "path": str(item.relative_to(base)),
                    })
                elif item.is_file():
                    stat = item.stat()
                    results.append({
                        "name": item.name,
                        "type": "file",
                        "path": str(item.relative_to(base)),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
            if item.is_dir():
                results.extend(_search_tree(item, base, query, excluded))
    except PermissionError:
        pass
    return results


@router.get("/search", dependencies=CAN_VIEW)
async def search_files(q: str):
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="Query troppo corta (min 2 caratteri)")
    if not download_dir(VIDEOS_DIR).exists():
        return []
    excluded = _DEFAULT_EXCLUDED | set(_read_data().get("excluded_folders", []))
    return await asyncio.to_thread(_search_tree, download_dir(VIDEOS_DIR), download_dir(VIDEOS_DIR), q.strip().lower(), excluded)


@router.get("", dependencies=CAN_VIEW)
async def list_files():
    if not download_dir(VIDEOS_DIR).exists():
        return []
    excluded = _DEFAULT_EXCLUDED | set(_read_data().get("excluded_folders", []))
    return await asyncio.to_thread(_build_tree, download_dir(VIDEOS_DIR), download_dir(VIDEOS_DIR), excluded)


@router.get("/library-tree", dependencies=CAN_VIEW)
async def list_library_tree(request: Request):
    data = _read_data()
    absolute = _sees_server_paths(request)
    result = []
    for lib in data.get("libraries", []):
        lib_path = Path(lib["path"])
        exists = lib_path.exists()
        lib_type = lib.get("type", "")
        root = None if absolute else lib_path
        result.append({
            "name": _TYPE_LABELS.get(lib_type, lib_type or lib.get("name", "?")),
            **({"abs_path": str(lib_path.resolve())} if absolute else {"path": ""}),
            "exists": exists,
            "children": (
                await asyncio.to_thread(_build_library_tree, lib_path, root)
            ) if exists else [],
        })
    return result


# What a browser is allowed to open in place. The library is a folder other
# things write into too, and a type derived from the extension would have an
# .html or .svg dropped there run as a page of the panel's own origin.
# Everything not listed is handed over as a download, typed as opaque bytes.
_INLINE_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".aac", ".ac3",
    ".srt", ".vtt", ".webp", ".jpg", ".jpeg", ".png",
})


def _is_inline_media(target: Path) -> bool:
    return target.suffix.lower() in _INLINE_EXTENSIONS


def _media_type(target: Path) -> str:
    return container.mime_for(str(target)) if _is_inline_media(target) else "application/octet-stream"


@router.get("/stream/{file_path:path}", dependencies=CAN_VIEW)
def stream_file(file_path: str):
    target = _safe_path(file_path)
    if not _is_inline_media(target):
        return FileResponse(
            path=str(target), media_type="application/octet-stream", filename=target.name
        )
    # Derived from the extension: everything used to be announced as video/mp4,
    # which was already wrong for the MKVs the downloader produced and would be
    # wrong half the time now that the container is a setting.
    return FileResponse(
        path=str(target),
        media_type=_media_type(target),
        headers={"Content-Disposition": "inline"},
    )


@router.get("/tracks/{file_path:path}", dependencies=CAN_VIEW)
def file_tracks(file_path: str):
    target = _safe_path(file_path)
    return JSONResponse(probe.media_tracks(str(target)) or {"audio": [], "subtitles": []})


_SUBTITLE_CONCURRENCY = 4
_SUBTITLE_WAIT = 15  # seconds a request waits for a slot before giving up
_subtitle_slots = threading.BoundedSemaphore(_SUBTITLE_CONCURRENCY)


@router.get("/subtitle/{stream_index}/{file_path:path}", dependencies=CAN_VIEW)
def stream_subtitle(stream_index: int, file_path: str):
    """Convert one embedded subtitle to WebVTT for browsers that cannot read MKV text tracks."""
    target = _safe_path(file_path)
    if stream_index < 0 or stream_index > 999:
        raise HTTPException(status_code=400, detail="Invalid subtitle stream")
    executable = get_ffmpeg_exe()
    if not executable:
        raise HTTPException(status_code=503, detail="FFmpeg non disponibile")
    command = [executable, "-v", "error", "-i", ffmpeg_file_arg(str(target)),
               "-map", f"0:{stream_index}", "-f", "webvtt", "pipe:1"]
    # Every request is an FFmpeg process reading a whole video. Without a cap,
    # anyone who can view the library can start as many as they can send URLs.
    if not _subtitle_slots.acquire(timeout=_SUBTITLE_WAIT):
        raise HTTPException(
            status_code=503, detail="Troppe conversioni di sottotitoli in corso, riprova",
            headers={"Retry-After": "5"},
        )
    try:
        # stderr goes nowhere: a pipe nobody reads fills up, and FFmpeg then
        # blocks on its own diagnostics with the stream half sent.
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, **hidden_process_options())
    except OSError as exc:
        _subtitle_slots.release()
        raise HTTPException(status_code=503, detail="Impossibile avviare FFmpeg") from exc

    finished = threading.Lock()

    def finish():
        """Stop FFmpeg and free the slot, once, whichever way the response ends."""
        if not finished.acquire(blocking=False):
            return
        try:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
        finally:
            _subtitle_slots.release()

    def output():
        try:
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            finish()

    # The generator's ``finally`` only runs once the body has started to be
    # read. The background task covers a response that never gets that far.
    return StreamingResponse(output(), media_type="text/vtt",
                             headers={"Content-Disposition": "inline"},
                             background=BackgroundTask(finish))


class OpenFileRequest(BaseModel):
    path: str


# Launching a program on the server is not "viewing the library": it takes the
# right to manage files, and it only makes sense — and is only allowed — from a
# browser on the machine itself, where the player window will appear.
@router.post("/open", dependencies=[*CAN_MANAGE, Depends(_local)])
def open_with_system_player(body: OpenFileRequest):
    target = _safe_path(body.path)
    if target.suffix.lower() not in {".mp4", ".mkv"}:
        raise HTTPException(status_code=400, detail="Formato non riproducibile")
    try:
        if os.name == "nt":
            os.startfile(str(target))
        else:
            subprocess.Popen(["xdg-open", str(target)], start_new_session=True)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(status_code=503, detail="Player di sistema non disponibile") from exc
    return {"ok": True}


@router.get("/download/{file_path:path}", dependencies=CAN_VIEW)
def download_file(file_path: str):
    target = _safe_path(file_path)
    # filename= has FileResponse build the header, quoting and encoding the
    # name. Written by hand, a name with a quote or a non-ASCII character
    # produced a header the browser could not parse — or one it parsed
    # differently from what was meant.
    return FileResponse(path=str(target), media_type=_media_type(target), filename=target.name)


class MoveRequest(BaseModel):
    path: str
    dest_dir_path: str | None = None  # destination dir relative to download_dir(VIDEOS_DIR) (empty = root)
    dest_abs_path: str | None = None  # destination absolute path in a library
    library_name: str | None = None   # legacy


class BatchMoveRequest(BaseModel):
    paths: list[str]
    dest_dir_path: str


class BatchDeleteRequest(BaseModel):
    paths: list[str]


class RenameRequest(BaseModel):
    path: str
    new_name: str


@router.post("/move", dependencies=CAN_MANAGE)
async def move_to_library(body: MoveRequest, request: Request):
    data = _read_data()
    source = _safe_path(body.path)

    if body.dest_dir_path is not None:
        # Move within VIDEOS_DIR
        base = download_dir(VIDEOS_DIR).resolve()
        dest_dir = (base / body.dest_dir_path).resolve() if body.dest_dir_path else base
        if not dest_dir.is_relative_to(base) and dest_dir != base:
            raise HTTPException(status_code=400, detail="Destinazione non valida")
        if not dest_dir.exists():
            raise HTTPException(status_code=400, detail="Cartella di destinazione non esiste")
        if source.is_dir():
            src_resolved = source.resolve()
            if dest_dir == src_resolved or dest_dir.is_relative_to(src_resolved):
                raise HTTPException(status_code=400, detail="Non puoi spostare una cartella dentro se stessa")
        dest = dest_dir / source.name
    elif body.dest_abs_path:
        lib_paths = [Path(lib["path"]).resolve() for lib in data.get("libraries", [])]
        dest_dir = Path(body.dest_abs_path).resolve()
        if not dest_dir.exists():
            raise HTTPException(status_code=400, detail="Cartella di destinazione non esiste")
        if not any(dest_dir == lp or dest_dir.is_relative_to(lp) for lp in lib_paths):
            raise HTTPException(status_code=400, detail="Destinazione non in una libreria configurata")
        dest = dest_dir / source.name
    elif body.library_name:
        lib_map = {lib.get("type", lib.get("name", "")): lib["path"] for lib in data.get("libraries", [])}
        if body.library_name not in lib_map:
            raise HTTPException(status_code=400, detail=f"Libreria '{body.library_name}' non configurata")
        dest_dir_root = Path(lib_map[body.library_name])
        if not dest_dir_root.exists():
            raise HTTPException(status_code=400, detail="Il percorso della libreria non esiste")
        rel = source.relative_to(download_dir(VIDEOS_DIR).resolve())
        dest = dest_dir_root / rel
    else:
        raise HTTPException(status_code=400, detail="Specificare dest_dir_path, dest_abs_path o library_name")

    if dest.exists():
        raise HTTPException(status_code=409, detail=f"'{source.name}' esiste già nella destinazione")

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.move, str(source), str(dest))
    except Exception:
        # The OS error names server paths; it belongs in the log.
        logger.exception("Move failed: %s -> %s", source, dest)
        raise HTTPException(status_code=500, detail="Spostamento fallito")

    return {"moved_to": _public_path(dest, request)}


def _delete_sync(target: Path):
    """Synchronous delete helper (runs in thread)."""
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


@router.delete("/delete/{file_path:path}", status_code=204, dependencies=CAN_MANAGE)
async def delete_path(file_path: str):
    target = _safe_path(file_path)
    await asyncio.to_thread(_delete_sync, target)


def _batch_move_sync(paths: list[str], dest_dir_path: str) -> list[dict]:
    """Move multiple paths within VIDEOS_DIR (blocking, runs in thread)."""
    base = download_dir(VIDEOS_DIR).resolve()
    dest_dir = (base / dest_dir_path).resolve() if dest_dir_path else base
    results = []
    for p in paths:
        try:
            try:
                source = _safe_path_strict(p)
            except (ValueError, FileNotFoundError):
                results.append({"path": p, "ok": False, "error": "File non trovato"})
                continue
            if not dest_dir.is_relative_to(base) and dest_dir != base:
                results.append({"path": p, "ok": False, "error": "Destinazione non valida"})
                continue
            if source.is_dir():
                if dest_dir == source.resolve() or dest_dir.is_relative_to(source.resolve()):
                    results.append({"path": p, "ok": False, "error": "Non puoi spostare una cartella dentro se stessa"})
                    continue
            dest = dest_dir / source.name
            if dest.exists():
                results.append({"path": p, "ok": False, "error": f"'{source.name}' esiste già nella destinazione"})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(dest))
            # Always inside the download folder here, so always relative to it.
            results.append({"path": p, "ok": True, "moved_to": str(dest.relative_to(base))})
        except Exception:
            logger.exception("Batch move failed for %r", p)
            results.append({"path": p, "ok": False, "error": "Spostamento fallito"})
    return results


@router.post("/move-batch", dependencies=CAN_MANAGE)
async def batch_move(body: BatchMoveRequest):
    if not body.paths:
        raise HTTPException(status_code=400, detail="Nessun file selezionato")
    results = await asyncio.to_thread(_batch_move_sync, body.paths, body.dest_dir_path)
    return {"results": results}


def _batch_delete_sync(paths: list[str]) -> list[dict]:
    """Delete multiple paths within VIDEOS_DIR (blocking, runs in thread)."""
    results = []
    for p in paths:
        try:
            target = _safe_path_strict(p)
            _delete_sync(target)
            results.append({"path": p, "ok": True})
        except (ValueError, FileNotFoundError) as e:
            # Our own two messages ("Invalid path", "Not found"), never the OS's.
            results.append({"path": p, "ok": False, "error": str(e)})
        except Exception:
            logger.exception("Batch delete failed for %r", p)
            results.append({"path": p, "ok": False, "error": "Eliminazione fallita"})
    return results


@router.post("/rename", dependencies=CAN_MANAGE)
async def rename_path(body: RenameRequest):
    if not body.new_name or '/' in body.new_name or '\\' in body.new_name or body.new_name in ('.', '..') or '\x00' in body.new_name:
        raise HTTPException(status_code=400, detail="Nome non valido")
    source = _safe_path(body.path)
    dest = source.parent / body.new_name
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"'{body.new_name}' esiste già")
    try:
        await asyncio.to_thread(source.rename, dest)
    except Exception:
        logger.exception("Rename failed: %s -> %s", source, dest)
        raise HTTPException(status_code=500, detail="Rinomina fallita")
    return {"renamed_to": str(dest.relative_to(download_dir(VIDEOS_DIR).resolve()))}


@router.post("/delete-batch", dependencies=CAN_MANAGE)
async def batch_delete(body: BatchDeleteRequest):
    if not body.paths:
        raise HTTPException(status_code=400, detail="Nessun file selezionato")
    results = await asyncio.to_thread(_batch_delete_sync, body.paths)
    return {"results": results}
