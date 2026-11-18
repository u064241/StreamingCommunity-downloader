"""Download of non-DRM RaiPlay pages through yt-dlp.

This module intentionally does not implement a DRM path.  If yt-dlp reports
DRM, a licence URL, or no usable formats, the job fails before writing media.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from app.progress import DownloadCancelledError


def _outtmpl(target_dir: Path, stem: str) -> str:
    """yt-dlp output template. Every '%' is escaped, in the directory too:
    a library path or title containing '%' would otherwise break the template."""
    return os.path.join(str(target_dir).replace("%", "%%"), stem.replace("%", "%%") + ".%(ext)s")

try:
    import yt_dlp
except ImportError:  # keeps catalogue-only installs importable
    yt_dlp = None


class RaiPlayDownloadError(RuntimeError):
    pass


def _safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (value or "RaiPlay")).strip(" .")
    return value[:180] or "RaiPlay"


def _declares_drm(info: dict) -> bool:
    return any(info.get(key) not in (None, False, "", {}, [], "none", "false")
               for key in ("drm", "has_drm", "license_url"))


def _is_drm(info: dict) -> bool:
    formats = info.get("formats") or []
    return _declares_drm(info) or bool(formats and all(_declares_drm(f) for f in formats))


def download(url: str, title: str, output_dir: str, *, temp_dir=None,
             progress_factory=None, cancel_event=None, year: str | None = None,
             season: int | None = None, episode_number: str | None = None,
             episode_title: str | None = None) -> str:
    if yt_dlp is None:
        raise RaiPlayDownloadError("yt-dlp non è installato")
    from app.providers.raiplay.parser import video_page
    url = video_page(url)
    if url is None:
        raise RaiPlayDownloadError("URL RaiPlay non valido")
    if cancel_event and cancel_event.is_set():
        raise DownloadCancelledError()
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
    outtmpl = _outtmpl(target_dir, stem)
    seen = {}
    outputs = []

    def hook(data):
        if cancel_event and cancel_event.is_set():
            raise DownloadCancelledError()
        if progress_factory and data.get("status") == "downloading":
            done = data.get("downloaded_bytes", 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or done
            key = data.get("filename") or (data.get("info_dict") or {}).get("format_id")
            if total:
                if seen.get("key") != key or "bar" not in seen:
                    if seen.get("bar"):
                        seen["bar"].close()
                    seen.update(key=key, bar=progress_factory(total=total, phase="downloading"), done=0)
                seen["bar"].total = total
                delta = max(0, done - seen["done"])
                seen["bar"].update(n=delta, bytes=delta)
                seen["done"] = done

    from app.core.ffmpeg_path import get_ffmpeg_exe
    ffmpeg_exe = get_ffmpeg_exe()
    opts = {"format": "bv*+ba/b", "outtmpl": outtmpl, "noplaylist": True,
            "merge_output_format": "mp4", "progress_hooks": [hook], "quiet": True,
            "no_warnings": True, "noprogress": True, "color": "never",
            "allow_unplayable_formats": False, "post_hooks": [outputs.append],
            "ffmpeg_location": str(ffmpeg_exe)}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
                raise RaiPlayDownloadError("Era atteso un singolo video RaiPlay, non una playlist")
            if _is_drm(info):
                raise RaiPlayDownloadError("Il contenuto RaiPlay è protetto da DRM e non è scaricabile")
            formats = [f for f in (info.get("formats") or [])
                       if (f.get("url") or f.get("manifest_url")) and not _declares_drm(f)]
            if not formats and not info.get("url"):
                raise RaiPlayDownloadError("RaiPlay non ha fornito un formato scaricabile senza DRM")
            if formats:
                info["formats"] = formats
            # Process this already checked result. A second extraction could
            # choose different formats and doubles upstream traffic.
            result = ydl.process_ie_result(info, download=True)
            if not outputs and isinstance(result, dict):
                outputs.append(result.get("filepath") or ydl.prepare_filename(result))
    except RaiPlayDownloadError:
        raise
    except DownloadCancelledError:
        raise
    except Exception as exc:
        if cancel_event and cancel_event.is_set():
            raise DownloadCancelledError() from exc
        raise RaiPlayDownloadError(re.sub(r"\x1b\[[0-9;]*m", "", str(exc))) from exc
    finally:
        if seen.get("bar"):
            seen["bar"].close()
    candidates = [Path(p) for p in outputs if p and Path(p).is_file()
                  and Path(p).suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
                  and Path(p).resolve().parent == target_dir.resolve()]
    if not candidates:
        raise RaiPlayDownloadError("yt-dlp non ha prodotto alcun file")
    return str(candidates[-1])
