"""HLS playlists downloaded by the managed yt-dlp.exe instead of the built-in
segment downloader (app/core/m3u8.py). Opt-in: setting ``hls_use_ytdlp``.

Scope, on purpose narrow:
- Only for a playlist already resolved to its final m3u8 URL, and only when no
  external decryption key was handed in (yt-dlp reads the playlist's own AES-128
  key lines; the built-in path keeps handling everything else).
- Connections are capped to the same number the built-in downloader uses
  (``-N``), so the source sees the same concurrency. The hourly page budget
  (app/core/source_budget.py) is untouched: it only covers page requests.
- Arguments are a list, never a shell string. Cancel kills the subprocess.

Any failure raises ``HlsEngineError``; the caller falls back to the built-in
downloader for that playlist, so turning the option on cannot make a download
fail that would otherwise succeed.
"""
from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from app.core import ytdlp_update
from app.core.headers import get_headers
from app.core.raiplay import PROGRESS_TEMPLATE, _FILE_TAG, _run_download, _outtmpl, parse_progress
from app.progress import DownloadCancelledError

logger = logging.getLogger(__name__)

_STREAM_STEM = "hls_stream"
_MAX_CONNECTIONS = 16  # the built-in downloader's ceiling is 8; yt-dlp never gets more than this


class HlsEngineError(RuntimeError):
    """yt-dlp could not produce the playlist. The caller falls back."""


def available() -> bool:
    return ytdlp_update.executable() is not None


def _cleanup(temp_dir: Path) -> None:
    """Remove yt-dlp's leftovers for this stream (fragments, .part, .ytdl)."""
    for entry in temp_dir.glob(f"{_STREAM_STEM}*"):
        with contextlib.suppress(OSError):
            entry.unlink()


def download_playlist(url: str, out_path: str, *, ext: str, referer: str | None, connections: int,
                      cancel_event=None, progress_factory=None, phase: str = "video") -> None:
    """Download the m3u8 *url* to *out_path*, remuxed to container *ext*. Raises HlsEngineError."""
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelledError()
    out = Path(out_path)
    temp_dir = out.parent / f"{out.stem}.ytdlp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    count = max(1, min(_MAX_CONNECTIONS, int(connections)))
    ext = ext.lstrip(".")  # container.extension() returns ".mp4"; yt-dlp wants "mp4"

    try:
        with ytdlp_update.in_use():
            exe = ytdlp_update.require_executable()
            argv = [exe, "--no-playlist", "--no-part", "--newline", "--no-warnings", "--no-color",
                    "--progress", "--progress-template", PROGRESS_TEMPLATE,
                    "-N", str(count),
                    "--add-header", f"User-Agent:{get_headers()}",
                    "-o", _outtmpl(temp_dir, _STREAM_STEM),
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

            try:
                returncode, stderr = _run_download(argv, on_line, cancel_event)
            finally:
                if seen.get("bar"):
                    seen["bar"].close()
    except (ytdlp_update.YtdlpMissingError, ytdlp_update.YtdlpBusyError) as exc:
        _cleanup(temp_dir)
        raise HlsEngineError(str(exc)) from exc
    except DownloadCancelledError:
        _cleanup(temp_dir)
        raise
    except Exception as exc:  # noqa: BLE001 - any engine failure means fallback
        _cleanup(temp_dir)
        raise HlsEngineError(f"yt-dlp HLS: {exc}") from exc

    produced = [Path(p) for p in outputs if p and Path(p).is_file() and Path(p).parent.resolve() == temp_dir.resolve()]
    if returncode != 0 or not produced:
        _cleanup(temp_dir)
        tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["nessun file"]
        raise HlsEngineError(f"yt-dlp HLS fallito: {tail[0][:300]}")
    os.replace(str(produced[-1]), out_path)
    _cleanup(temp_dir)
    with contextlib.suppress(OSError):
        temp_dir.rmdir()


def _ffmpeg_dir() -> str:
    from app.core.ffmpeg_path import get_ffmpeg_exe
    return str(get_ffmpeg_exe())
