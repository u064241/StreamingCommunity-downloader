"""Optional post-download encoding with tested hardware encoders and atomic output."""
import functools
import logging
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from app.core.ffmpeg_path import get_ffmpeg_exe, ffmpeg_file_arg, hidden_process_options
from app.progress import DownloadCancelledError

logger = logging.getLogger(__name__)
_probe_lock = threading.Lock()
_conversion_slot = threading.Semaphore(1)
BACKENDS = {"nvenc": "NVIDIA NVENC", "qsv": "Intel Quick Sync", "amf": "AMD AMF", "cpu": "CPU"}
QUALITY = {"high": 20, "balanced": 24, "compact": 28}


class TranscodeError(RuntimeError):
    pass


def encoder_name(codec, backend):
    return ("libx264" if codec == "h264" else "libx265") if backend == "cpu" else f"{codec}_{backend}"


def encoder_options(codec, backend, quality):
    value = str(QUALITY[quality])
    options = ["-c:v:0", encoder_name(codec, backend), "-filter:v:0", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"]
    if backend == "nvenc":
        options += ["-preset", "p4", "-rc", "vbr", "-cq", value, "-b:v", "0"]
    elif backend == "qsv":
        options += ["-preset", "medium", "-global_quality", value]
    elif backend == "amf":
        options += ["-quality", "balanced", "-rc", "cqp", "-qp_i", value, "-qp_p", value]
    else:
        options += ["-preset", "medium", "-crf", value, "-threads", "4"]
        if codec == "hevc":
            options += ["-x265-params", "pools=4:frame-threads=2"]
    return options


@functools.lru_cache(maxsize=2)
def _detect(executable):
    listed = subprocess.run([executable, "-hide_banner", "-encoders"], capture_output=True,
                            timeout=15, **hidden_process_options())
    text = listed.stdout.decode(errors="replace")
    result = []
    for backend, label in BACKENDS.items():
        for codec in ("h264", "hevc"):
            name = encoder_name(codec, backend)
            supported = re.search(r"\b" + re.escape(name) + r"\b", text) is not None
            reason = "Encoder non incluso in FFmpeg" if not supported else ""
            if supported:
                try:
                    probe = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=128x128:r=10", "-frames:v", "2"] +
                        encoder_options(codec, backend, "balanced") + ["-f", "null", "-"],
                        capture_output=True, stdin=subprocess.DEVNULL, timeout=12, **hidden_process_options())
                    supported = probe.returncode == 0
                    if not supported:
                        reason = probe.stderr.decode(errors="replace").strip()[:600] or "Prova encoder non riuscita"
                except (OSError, subprocess.TimeoutExpired) as exc:
                    supported, reason = False, str(exc)[-600:]
            result.append({"backend": backend, "label": label, "codec": codec, "encoder": name,
                           "available": supported, "reason": reason})
    return result


def capabilities(refresh=False):
    with _probe_lock:
        if refresh:
            _detect.cache_clear()
        try:
            return {"encoders": _detect(get_ffmpeg_exe())}
        except (OSError, subprocess.SubprocessError) as exc:
            raise TranscodeError(f"Rilevamento encoder non riuscito: {exc}") from exc


def select_backend(settings):
    codec = settings["video_conversion"]
    requested = settings.get("video_encoder", "auto")
    available = {row["backend"] for row in capabilities()["encoders"] if row["codec"] == codec and row["available"]}
    choices = ["nvenc", "qsv", "amf"] if requested == "auto" else [requested]
    for choice in choices:
        if choice in available:
            return choice
    if settings.get("video_cpu_fallback", True) and "cpu" in available:
        logger.info("No requested GPU encoder available; using CPU for %s", codec)
        return "cpu"
    raise TranscodeError("Nessun encoder utilizzabile per la conversione richiesta. Il file originale è conservato.")


def _duration(path):
    result = subprocess.run([get_ffmpeg_exe(), "-hide_banner", "-i", ffmpeg_file_arg(str(path))],
                            capture_output=True, timeout=20, **hidden_process_options())
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr.decode(errors="replace"))
    return (int(match[1])*3600 + int(match[2])*60 + float(match[3])) if match else 0.0


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _encode(path, staged, settings, backend, duration, bar, cancel_event):
    codec = settings["video_conversion"]
    command = [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
               "-i", ffmpeg_file_arg(str(path)), "-map", "0", "-map_metadata", "0", "-map_chapters", "0", "-c", "copy"]
    command += encoder_options(codec, backend, settings.get("video_quality", "balanced"))
    if path.suffix.lower() == ".mp4":
        command += ["-movflags", "+faststart"]
        if codec == "hevc":
            command += ["-tag:v:0", "hvc1"]
    command += ["-progress", "pipe:1", "-nostats", ffmpeg_file_arg(str(staged))]
    logger.info("Converting %s with %s, codec=%s quality=%s", path.name, BACKENDS[backend], codec, settings.get("video_quality"))
    lines = queue.Queue()
    started = time.monotonic()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                                   text=True, encoding="utf-8", errors="replace", **hidden_process_options())
        def read_progress():
            try:
                for line in process.stdout:
                    lines.put(line.strip())
            finally:
                process.stdout.close()
        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        try:
            while process.poll() is None or not lines.empty():
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelledError("Conversione annullata; il file originale è conservato.")
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line.startswith("out_time_us="):
                    try:
                        seconds = max(0, int(line.split("=",1)[1])) / 1000000
                    except ValueError:
                        continue
                    if bar is not None and hasattr(bar, "emit_conversion"):
                        bar.emit_conversion(seconds, duration, BACKENDS[backend], time.monotonic()-started)
            process.wait()
            reader.join(timeout=2)
            if process.returncode:
                errors.seek(0, os.SEEK_END)
                size = errors.tell()
                errors.seek(max(0, size-8000))
                raise TranscodeError("FFmpeg: " + errors.read().decode(errors="replace")[-1200:])
        finally:
            _stop(process)
            reader.join(timeout=2)


def convert_video(path, settings, progress_factory=None, cancel_event=None):
    if settings.get("video_conversion", "copy") == "copy":
        return path
    path = Path(path)
    acquired = False
    staged = path.with_name(f".{path.stem}.converting-{uuid.uuid4().hex}{path.suffix}")
    bar = progress_factory(total=1000, phase="converting") if progress_factory else None
    if bar is not None and hasattr(bar, "emit_status"):
        bar.emit_status("converting")
    try:
        while not acquired:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelledError("Conversione annullata")
            acquired = _conversion_slot.acquire(timeout=0.2)
        backend = select_backend(settings)
        duration = _duration(path)
        if bar is not None and hasattr(bar, "emit_conversion"):
            bar.emit_conversion(0, duration, BACKENDS[backend], 0)
        try:
            _encode(path, staged, settings, backend, duration, bar, cancel_event)
        except TranscodeError:
            if backend == "cpu" or not settings.get("video_cpu_fallback", True):
                raise
            logger.warning("GPU conversion failed; retrying conversion on CPU (original preserved)")
            backend = "cpu"
            _encode(path, staged, settings, backend, duration, bar, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelledError("Conversione annullata")
        if not staged.exists() or staged.stat().st_size == 0:
            raise TranscodeError("FFmpeg non ha prodotto un video valido. Originale conservato.")
        os.replace(staged, path)
        logger.info("Conversion complete: %s (%d bytes)", path, path.stat().st_size)
        if bar is not None and hasattr(bar, "emit_conversion"):
            bar.emit_conversion(max(duration, 1), max(duration, 1), BACKENDS[backend], max(0.1,time.monotonic()-getattr(bar,"_start_time",time.monotonic())))
        return str(path)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TranscodeError(f"Conversione non riuscita; originale conservato: {exc}") from exc
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Cannot remove conversion staging file %s: %s", staged, exc)
        finally:
            if acquired:
                _conversion_slot.release()
