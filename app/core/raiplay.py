"""Download of non-DRM RaiPlay pages through the standalone yt-dlp executable.

This module intentionally does not implement a DRM path.  If yt-dlp reports
DRM, a licence URL, or no usable formats, the job fails before writing media.

yt-dlp runs as a subprocess (``yt-dlp.exe`` managed by ``ytdlp_update``):
first ``--dump-single-json`` for the DRM check, then the download of that same
checked result (``--load-info-json``), so the formats checked are the formats
downloaded and RaiPlay is not asked twice. Arguments are always a list, never
a shell string; cancelling kills the process.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from app.core import ytdlp_update
from app.progress import DownloadCancelledError

logger = logging.getLogger(__name__)

INFO_TIMEOUT_S = 120
DOWNLOAD_TIMEOUT_S = 6 * 60 * 60
_POLL_S = 0.2
_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
_PROGRESS_TAG = "SCDL-PROGRESS"
_FILE_TAG = "SCDL-FILE"
# One line per progress tick, fields separated by a tab: titles never reach it.
PROGRESS_TEMPLATE = (f"download:{_PROGRESS_TAG}\t%(progress.downloaded_bytes)s\t"
                     "%(progress.total_bytes)s\t%(progress.total_bytes_estimate)s\t%(info.format_id)s")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _outtmpl(target_dir: Path, stem: str) -> str:
    """yt-dlp output template. Every '%' is escaped, in the directory too:
    a library path or title containing '%' would otherwise break the template."""
    return os.path.join(str(target_dir).replace("%", "%%"), stem.replace("%", "%%") + ".%(ext)s")


class RaiPlayDownloadError(RuntimeError):
    pass


class YtdlpEngineError(RaiPlayDownloadError):
    """yt-dlp itself failed (not DRM, not a policy refusal): an update may help."""


def _safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (value or "RaiPlay")).strip(" .")
    return value[:180] or "RaiPlay"


def _declares_drm(info: dict) -> bool:
    return any(info.get(key) not in (None, False, "", {}, [], "none", "false")
               for key in ("drm", "has_drm", "license_url"))


def _is_drm(info: dict) -> bool:
    formats = info.get("formats") or []
    return _declares_drm(info) or bool(formats and all(_declares_drm(f) for f in formats))


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def parse_progress(line: str) -> dict | None:
    """A ``PROGRESS_TEMPLATE`` line -> ``{"done", "total", "key"}``; else None."""
    parts = line.rstrip("\r\n").split("\t")
    if len(parts) != 5 or parts[0] != _PROGRESS_TAG:
        return None
    done = _number(parts[1])
    if done is None:
        return None
    total = _number(parts[2]) or _number(parts[3]) or done
    key = parts[4] if parts[4] not in ("", "NA") else None
    return {"done": int(done), "total": int(total), "key": key}


def _error_text(stderr: bytes) -> str:
    lines = [_ANSI.sub("", line).strip()
             for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    return (errors or lines or ["yt-dlp terminato con errore"])[-1][:500]


def _video_info(exe: str, url: str, cancel_event=None) -> dict:
    if cancel_event and cancel_event.is_set():
        raise DownloadCancelledError()
    try:
        done = subprocess.run([exe, "--dump-single-json", "--no-playlist", "--no-warnings", url],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=INFO_TIMEOUT_S,
                              shell=False, env=ytdlp_update.subprocess_env(),
                              **ytdlp_update.hidden_process_options())
    except subprocess.TimeoutExpired as exc:
        raise YtdlpEngineError("yt-dlp non ha risposto in tempo leggendo il video RaiPlay") from exc
    except OSError as exc:
        raise YtdlpEngineError(f"Impossibile avviare yt-dlp: {exc}") from exc
    if cancel_event and cancel_event.is_set():
        raise DownloadCancelledError()
    if done.returncode != 0:
        raise YtdlpEngineError(_error_text(done.stderr or b""))
    try:
        info = json.loads((done.stdout or b"").decode("utf-8"))
    except ValueError as exc:
        raise YtdlpEngineError("yt-dlp ha restituito metadati non validi") from exc
    if not isinstance(info, dict):
        raise RaiPlayDownloadError("Era atteso un singolo video RaiPlay, non una playlist")
    return info


def _checked(info: dict) -> dict:
    """Policy: single video, no DRM, at least one clear format. Fail closed."""
    if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        raise RaiPlayDownloadError("Era atteso un singolo video RaiPlay, non una playlist")
    if _is_drm(info):
        raise RaiPlayDownloadError("Il contenuto RaiPlay è protetto da DRM e non è scaricabile")
    formats = [f for f in (info.get("formats") or [])
               if isinstance(f, dict) and (f.get("url") or f.get("manifest_url")) and not _declares_drm(f)]
    if not formats and not info.get("url"):
        raise RaiPlayDownloadError("RaiPlay non ha fornito un formato scaricabile senza DRM")
    if formats:
        info = {**info, "formats": formats}
        # Selection is redone on the filtered list, never reused from the dump.
        for key in ("requested_formats", "requested_downloads", "format_id", "format"):
            info.pop(key, None)
    return info


def _stop(process) -> None:
    if process.poll() is not None:
        return
    process.kill()
    with contextlib.suppress(Exception):
        process.wait(timeout=10)


def _run_download(argv: list[str], on_line, cancel_event=None,
                  timeout: float = DOWNLOAD_TIMEOUT_S) -> tuple[int, bytes]:
    """Run yt-dlp, feed each stdout line to *on_line*; kill it on cancel/timeout."""
    deadline = time.monotonic() + timeout
    failure: list[BaseException] = []
    with tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                                   shell=False, env=ytdlp_update.subprocess_env(),
                                   **ytdlp_update.hidden_process_options())

        def pump():
            try:
                for raw in process.stdout:
                    on_line(raw.decode("utf-8", "replace"))
            except BaseException as exc:  # a progress callback raising: stop the process
                failure.append(exc)
                _stop(process)

        reader = threading.Thread(target=pump, name="yt-dlp-stdout", daemon=True)
        reader.start()
        try:
            while True:
                try:
                    process.wait(timeout=_POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelledError()
                if failure:
                    raise failure[0]
                if time.monotonic() > deadline:
                    raise YtdlpEngineError(
                        f"yt-dlp interrotto: ancora in esecuzione dopo {int(timeout // 60)} minuti")
        finally:
            _stop(process)
            reader.join(timeout=5)
            with contextlib.suppress(Exception):
                process.stdout.close()
        if failure:
            raise failure[0]
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelledError()
        err.seek(0)
        return process.returncode, err.read()


def _attempt(exe: str, url: str, target_dir: Path, stem: str, work_dir: Path,
             progress_factory=None, cancel_event=None) -> str:
    info = _checked(_video_info(exe, url, cancel_event))
    info_path = work_dir / "raiplay.info.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")
    from app.core.ffmpeg_path import get_ffmpeg_exe
    argv = [exe, "--load-info-json", str(info_path), "-f", "bv*+ba/b",
            "-o", _outtmpl(target_dir, stem), "--no-playlist", "--merge-output-format", "mp4",
            "--newline", "--no-warnings", "--no-color", "--no-simulate", "--progress",
            "--progress-template", PROGRESS_TEMPLATE,
            "--print", f"after_move:{_FILE_TAG}\t%(filepath)s",
            "--ffmpeg-location", str(get_ffmpeg_exe())]
    seen: dict = {}
    outputs: list[str] = []

    def on_line(line: str):
        if cancel_event and cancel_event.is_set():
            raise DownloadCancelledError()
        if line.startswith(_FILE_TAG + "\t"):
            outputs.append(line.split("\t", 1)[1].strip())
            return
        data = parse_progress(line)
        if not data or not progress_factory or not data["total"]:
            return
        if seen.get("key") != data["key"] or "bar" not in seen:
            if seen.get("bar"):
                seen["bar"].close()
            seen.update(key=data["key"], bar=progress_factory(total=data["total"], phase="downloading"), done=0)
        seen["bar"].total = data["total"]
        delta = max(0, data["done"] - seen["done"])
        seen["bar"].update(n=delta, bytes=delta)
        seen["done"] = data["done"]

    try:
        returncode, stderr = _run_download(argv, on_line, cancel_event)
    finally:
        if seen.get("bar"):
            seen["bar"].close()
        with contextlib.suppress(OSError):
            info_path.unlink()
    if returncode != 0:
        raise YtdlpEngineError(_error_text(stderr))
    resolved = target_dir.resolve()
    candidates = [Path(p) for p in outputs if p and Path(p).is_file()
                  and Path(p).suffix.lower() in _VIDEO_EXT and Path(p).resolve().parent == resolved]
    if not candidates:
        raise RaiPlayDownloadError("yt-dlp non ha prodotto alcun file")
    return str(candidates[-1])


def download(url: str, title: str, output_dir: str, *, temp_dir=None,
             progress_factory=None, cancel_event=None, year: str | None = None,
             season: int | None = None, episode_number: str | None = None,
             episode_title: str | None = None) -> str:
    from app.providers.raiplay.parser import video_page
    url = video_page(url)
    if url is None:
        raise RaiPlayDownloadError("URL RaiPlay non valido")
    if cancel_event and cancel_event.is_set():
        raise DownloadCancelledError()
    if ytdlp_update.executable() is None:
        raise RaiPlayDownloadError(ytdlp_update.MISSING_MESSAGE)
    os.makedirs(output_dir, exist_ok=True)
    stem = _safe_name(title + (f" ({year})" if year else ""))
    # Programme/season expansion happens before jobs are created. One worker
    # owns one video, one progress bar at a time and one final output file.
    target_dir = Path(output_dir)
    if season is not None:
        target_dir = target_dir / stem / f"Season {season:02d}"
        stem = _safe_name(f"{title} S{season:02d}E{int(episode_number or 1):02d}" +
                          (f" - {episode_title}" if episode_title else ""))
    target_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(temp_dir) if temp_dir else target_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    def run_once() -> str:
        try:
            with ytdlp_update.in_use():
                exe = ytdlp_update.require_executable()
                return _attempt(exe, url, target_dir, stem, work_dir, progress_factory, cancel_event)
        except ytdlp_update.YtdlpMissingError as exc:
            raise RaiPlayDownloadError(str(exc)) from exc
        except ytdlp_update.YtdlpBusyError as exc:
            raise RaiPlayDownloadError(str(exc)) from exc

    try:
        return run_once()
    except YtdlpEngineError as first:
        if cancel_event and cancel_event.is_set():
            raise DownloadCancelledError() from first
        # One update, one retry, never a loop: the retry's failure is final.
        if not ytdlp_update.update_for_retry():
            raise
        logger.info("RaiPlay download failed (%s): yt-dlp updated, retrying once", first)
        return run_once()
