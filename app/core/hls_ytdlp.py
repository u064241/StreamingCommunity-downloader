"""HLS playlists downloaded by the managed yt-dlp.exe instead of the built-in
segment downloader (app/core/m3u8.py). Opt-in: setting ``hls_use_ytdlp``.

Scope, on purpose narrow:
- Only for a playlist already resolved to its final m3u8 URL, and only when no
  external decryption key was handed in (yt-dlp reads the playlist's own AES-128
  key lines; the built-in path keeps handling everything else).
- The caller holds as many source-gate slots as yt-dlp uses connections
  (``-N``), so the process-wide cap is respected. The hourly page budget and
  the source pause are checked before starting; a 429/503 in yt-dlp's output
  reports a pushback exactly like the built-in downloader does.
- Unavailable fragments abort the run (never a silent hole in the file), and
  user yt-dlp config files are ignored.
- Arguments are a list, never a shell string. Cancel kills the subprocess.
  Everything yt-dlp writes lives in the caller's work folder, which is removed
  on every outcome: nothing is left beside the library file.

Any failure raises ``HlsEngineError``; the caller falls back to the built-in
downloader for that playlist, so turning the option on cannot make a download
fail that would otherwise succeed.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

from app.core import source_budget, ytdlp_update
from app.core.headers import get_headers
from app.core.raiplay import PROGRESS_TEMPLATE, _FILE_TAG, _run_download, _outtmpl, parse_progress
from app.progress import DownloadCancelledError

logger = logging.getLogger(__name__)

_STREAM_STEM = "hls_stream"
_MAX_CONNECTIONS = 16  # hard ceiling on -N, whatever the caller asks for
_PUSHBACK = re.compile(rb"HTTP Error (429|503)\b")
_FRAGMENT_ERROR = re.compile(rb"unable to download (video )?fragment")


class HlsEngineError(RuntimeError):
    """yt-dlp could not produce the playlist. The caller falls back."""


def available() -> bool:
    return ytdlp_update.executable() is not None


def connections_for(cap: int) -> int:
    """The -N to use for a source cap: never above the cap, never above the ceiling."""
    return max(1, min(_MAX_CONNECTIONS, int(cap)))


def download_playlist(url: str, out_path: str, *, work_dir: str, ext: str, referer: str | None,
                      connections: int, cancel_event=None, progress_factory=None,
                      phase: str = "video") -> None:
    """Download the m3u8 *url* to *out_path*, remuxed to container *ext*.

    *work_dir* is private to this download and is removed on every outcome.
    The caller must hold ``connections`` source slots while this runs.
    Raises HlsEngineError on any engine failure; DownloadCancelledError on cancel.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelledError()
    reason = source_budget.blocked()
    if reason is not None:
        raise HlsEngineError(f"sorgente in pausa ({reason}): downloader interno")

    work = Path(work_dir)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    ext = ext.lstrip(".")  # container.extension() returns ".mp4"; yt-dlp wants "mp4"
    count = connections_for(connections)
    try:
        outputs, returncode, stderr = _run(url, work, ext, count, referer, cancel_event,
                                           progress_factory, phase)
        if _PUSHBACK.search(stderr or b""):
            source_budget.report_pushback("yt-dlp HLS")
        produced = [Path(p) for p in outputs if p and Path(p).is_file()
                    and Path(p).parent.resolve() == work.resolve()]
        if returncode != 0 or not produced:
            tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["nessun file"]
            raise HlsEngineError(f"yt-dlp HLS fallito: {tail[0][:300]}")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        try:
            os.replace(str(produced[-1]), out_path)
        except OSError as exc:
            raise HlsEngineError(f"yt-dlp HLS: spostamento del file non riuscito ({exc})") from exc
    except (ytdlp_update.YtdlpMissingError, ytdlp_update.YtdlpBusyError) as exc:
        raise HlsEngineError(str(exc)) from exc
    except (HlsEngineError, DownloadCancelledError):
        raise
    except Exception as exc:  # noqa: BLE001 - any engine failure means fallback
        raise HlsEngineError(f"yt-dlp HLS: {exc}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(url, work, ext, count, referer, cancel_event, progress_factory, phase):
    with ytdlp_update.in_use():
        exe = ytdlp_update.require_executable()
        argv = [exe, "--ignore-config", "--no-playlist", "--no-part", "--newline", "-f", "bv*/b",
                "--no-warnings", "--no-color", "--progress",
                "--progress-template", PROGRESS_TEMPLATE,
                "--abort-on-unavailable-fragments",
                "-N", str(count),
                "--add-header", f"User-Agent:{get_headers()}",
                "-o", _outtmpl(work, _STREAM_STEM),
                "--remux-video", ext,
                "--print", f"after_move:{_FILE_TAG}\t%(filepath)s",
                "--ffmpeg-location", _ffmpeg_dir()]
        if referer:
            argv += ["--add-header", f"Referer:{referer}"]
        argv.append(url)

        seen: dict = {}
        outputs: list[str] = []

        def on_line(line: str):
            if cancel_event is not None and cancel_event.is_set():
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
                seen.update(key=data["key"], bar=progress_factory(total=data["total"], phase=phase),
                            done=0)
            seen["bar"].total = data["total"]
            delta = max(0, data["done"] - seen["done"])
            seen["bar"].update(n=delta, bytes=delta)
            seen["done"] = data["done"]
            seen["total_bytes"] = seen.get("total_bytes", 0) + delta

        started = time.monotonic()
        stderr = b""
        try:
            returncode, stderr = _run_download(argv, on_line, cancel_event)
        finally:
            if seen.get("bar"):
                seen["bar"].close()
            # Audio is downloaded separately by the caller: yt-dlp takes video only.
            elapsed = max(time.monotonic() - started, 1e-6)
            done = seen.get("total_bytes", 0)
            logger.info("yt-dlp HLS %s: %.1f MB in %.0f s = %.2f MB/s, %d errori di frammento, conn %d",
                        phase, done / 1e6, elapsed, done / 1e6 / elapsed,
                        len(_FRAGMENT_ERROR.findall(stderr or b"")), count)
    return outputs, returncode, stderr


def _ffmpeg_dir() -> str:
    from app.core.ffmpeg_path import get_ffmpeg_exe
    return str(get_ffmpeg_exe())
