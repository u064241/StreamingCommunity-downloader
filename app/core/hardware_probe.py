"""On-demand, local hardware diagnostics. No downloads or system changes.

``probe()`` only reads. ``diagnose()`` adds the source health and writes the
chosen performance preset into the settings (never the video encoder).
"""
import csv
import io
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app.core.ffmpeg_path import hidden_process_options
from app.core import transcode

_lock = threading.Lock()
_cached = None
_cached_at = 0.0
CACHE_SECONDS = 60


def _positive(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _gpu_name(value):
    return str(value).casefold().removeprefix("nvidia ").strip()


def _run(command):
    """Only fixed diagnostic commands; never a shell or client-supplied args."""
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=8,
                            stdin=subprocess.DEVNULL, **hidden_process_options())
    if result.returncode:
        raise OSError("Hardware diagnostic failed")
    return result.stdout


def _windows_inventory():
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
        "$ErrorActionPreference='Stop';"
        "$c=@(Get-CimInstance Win32_Processor);"
        "$m=Get-CimInstance Win32_OperatingSystem;"
        "@{cpu=@($c|Select-Object Name,NumberOfCores,NumberOfLogicalProcessors);"
        "memory=@{total=$m.TotalVisibleMemorySize*1024;available=$m.FreePhysicalMemory*1024};"
        "gpus=@(Get-CimInstance Win32_VideoController|Select-Object Name)}"
        "|ConvertTo-Json -Depth 4 -Compress"
    )
    return json.loads(_run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script]))


def _inventory():
    cpu = {"name": platform.processor() or None, "physical_cores": None,
           "logical_threads": _positive(os.cpu_count())}
    memory = {"total_bytes": None, "available_bytes": None}
    gpus = []
    warnings = []
    try:
        import psutil  # Optional: never install it from the probe.
        cpu["physical_cores"] = _positive(psutil.cpu_count(logical=False))
        ram = psutil.virtual_memory()
        memory.update(total_bytes=ram.total, available_bytes=ram.available)
    except (ImportError, OSError, RuntimeError):
        pass
    if platform.system() == "Windows":
        try:
            data = _windows_inventory()
            processors = data.get("cpu", [])
            cpu["name"] = ", ".join(str(row["Name"]) for row in processors if row.get("Name")) or cpu["name"]
            for field, key in (("physical_cores", "NumberOfCores"), ("logical_threads", "NumberOfLogicalProcessors")):
                cpu[field] = _positive(sum(_positive(row.get(key)) or 0 for row in processors)) or cpu[field]
            for field, key in (("total_bytes", "total"), ("available_bytes", "available")):
                memory[field] = _positive(data.get("memory", {}).get(key)) or memory[field]
            # Win32_VideoController.AdapterRAM truncates VRAM above 4 GiB.
            gpus = [{"name": str(row["Name"]), "vram_bytes": None} for row in data.get("gpus", []) if row.get("Name")]
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            warnings.append("inventory_unavailable")
    elif platform.system() == "Linux" and memory["total_bytes"] is None:
        try:
            values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
            memory["total_bytes"] = int(values["MemTotal"].split()[0]) * 1024
            memory["available_bytes"] = int(values["MemAvailable"].split()[0]) * 1024
        except (OSError, KeyError, ValueError):
            warnings.append("memory_unavailable")
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            rows = csv.reader(io.StringIO(_run([nvidia, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])))
            for row in rows:
                if len(row) != 2 or not row[0].strip():
                    continue
                name, vram = row[0].strip(), _positive(row[1].strip())
                existing = next((gpu for gpu in gpus if _gpu_name(gpu["name"]) == _gpu_name(name)), None)
                if existing is None:
                    existing = {"name": name, "vram_bytes": None}
                    gpus.append(existing)
                existing["vram_bytes"] = vram * 1024**2 if vram else None
        except (OSError, subprocess.SubprocessError, ValueError):
            warnings.append("gpu_memory_unavailable")
    return cpu, memory, gpus, warnings


GIB = 1024**3
# Thresholds of the preset choice (choose_preset). "Weak" is what the rules
# name explicitly; "strong" is the line above which three downloads of sixteen
# segments do not compete with the rest of the PC.
WEAK_CORES = 4
WEAK_RAM_WITHOUT_GPU = 8 * GIB
STRONG_CORES = 8
STRONG_RAM = 16 * GIB
# Display adapters that are not a GPU anyone can encode or decode on.
_NOT_A_GPU = ("basic display", "basic render", "remote display", "virtual display", "hyper-v")


def _cores(cpu):
    return _positive(cpu.get("physical_cores")) or _positive(cpu.get("logical_threads"))


def _has_gpu(gpus, encoders):
    real = [g for g in gpus or [] if not any(word in str(g.get("name", "")).casefold() for word in _NOT_A_GPU)]
    hardware_encoder = any(row.get("available") and row.get("backend") in ("nvenc", "qsv", "amf")
                           for row in encoders or [])
    return bool(real) or hardware_encoder


SIGNAL_HOURS = 72        # any refusal in this window -> conservative
CLEAN_HOURS = 7 * 24     # "fast" needs this long without one


def _count(source, key):
    try:
        return max(0, int(source.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _hours(source):
    try:
        value = source.get("history_hours")
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def choose_preset(cpu, memory, gpus, encoders, source):
    """Pick a performance preset. Returns ``(preset, reasons)``.

    Never above "fast" (the most a preset may ask). In order, the first rule
    that applies wins:

    1. a pause still running, or any refusal (403/429/503 or segment pushback)
       in the last 72 hours -> conservative;
    2. no source history, or one covering less than 72 hours -> conservative
       (not enough data to say the source is calm);
    3. cores or total RAM unknown -> conservative (not enough data);
    4. fewer than 4 cores, or no GPU and less than 8 GiB -> conservative;
    5. at least 8 cores and 16 GiB, a history of at least 7 days with no
       refusal in it, and the hourly budget not spent -> fast;
    6. otherwise -> balanced.
    """
    source = source or {}
    cores = _cores(cpu or {})
    ram = _positive((memory or {}).get("total_bytes"))
    gpu = _has_gpu(gpus, encoders)
    recent = _count(source, "pushbacks_last_72h")
    week = _count(source, "pushbacks_last_7d")
    hours = _hours(source)
    if source.get("backoff_active"):
        return "conservative", ["La sorgente è in pausa in questo momento dopo un rifiuto."]
    if recent:
        return "conservative", [f"La sorgente ha rifiutato o rallentato richieste nelle ultime "
                                f"{SIGNAL_HOURS} ore ({recent})."]
    if hours is None or hours < SIGNAL_HOURS:
        seen = "nessuno storico" if hours is None else f"storico di {hours:.0f} ore"
        return "conservative", [f"Dati sulla sorgente insufficienti ({seen}, ne servono almeno "
                                f"{SIGNAL_HOURS} senza rifiuti)."]
    if cores is None or ram is None:
        return "conservative", ["Dati hardware insufficienti (core o RAM non rilevati)."]
    if cores < WEAK_CORES:
        return "conservative", [f"Hardware limitato: {cores} core (meno di {WEAK_CORES})."]
    if not gpu and ram < WEAK_RAM_WITHOUT_GPU:
        return "conservative", [f"Hardware limitato: nessuna GPU e {ram / GIB:.1f} GiB di RAM (meno di 8)."]
    if cores >= STRONG_CORES and ram >= STRONG_RAM:
        if week:
            return "balanced", [f"Hardware potente, ma la sorgente ha rifiutato richieste negli "
                                f"ultimi 7 giorni ({week})."]
        if hours < CLEAN_HOURS:
            return "balanced", [f"Hardware potente, ma lo storico della sorgente copre solo "
                                f"{hours:.0f} ore (Veloce richiede 7 giorni senza rifiuti)."]
        if source.get("budget_exhausted"):
            return "balanced", ["Hardware potente, ma il budget orario di richieste alla sorgente è esaurito."]
        return "fast", [f"Hardware potente ({cores} core, {ram / GIB:.1f} GiB) e nessun rifiuto "
                        f"dalla sorgente negli ultimi 7 giorni."]
    return "balanced", [f"Hardware medio ({cores} core, {ram / GIB:.1f} GiB{', GPU' if gpu else ''}) "
                        f"e nessun rifiuto dalla sorgente nelle ultime {SIGNAL_HOURS} ore."]


def recommendations(cpu, memory, encoders, gpus=None, source=None):
    """Starting points, not network benchmarks or session limits.

    The download limits are those of a preset (app.config.PERFORMANCE_PRESETS)
    chosen by ``choose_preset``; the video encoder is only ever suggested.
    """
    from app.config import PERFORMANCE_PRESETS
    available = [row for row in encoders if row.get("available")]
    backend = next((name for name in ("nvenc", "qsv", "amf", "cpu")
                    if any(row.get("backend") == name for row in available)), None)
    preset, reasons = choose_preset(cpu, memory, gpus, encoders, source)
    values = PERFORMANCE_PRESETS[preset]
    return {
        "video_encoder": backend,
        "video_codec": next((codec for codec in ("h264", "hevc")
                            if any(row.get("backend") == backend and row.get("codec") == codec for row in available)), None),
        "video_quality": "balanced",
        "video_cpu_fallback": any(row.get("backend") == "cpu" for row in available),
        "max_parallel_conversions": 1,  # Actual transcode semaphore capacity.
        "preset": preset,
        "preset_label": values["label"],
        "reasons": reasons,
        "max_concurrent_downloads": values["max_concurrent_downloads"],
        "max_segment_workers": values["max_segment_workers"],
        "source_max_connections": values["source_max_connections"],
        "source_requests_per_hour": values["source_requests_per_hour"],
    }


def _ffmpeg_available() -> bool:
    try:
        from app.core.ffmpeg_path import get_ffmpeg_exe
        exe = get_ffmpeg_exe()
        return bool(exe) and (Path(exe).is_file() or shutil.which(exe) is not None)
    except (OSError, RuntimeError, ValueError):
        return False


def _source_health() -> dict:
    """The fields of source_budget.status() the diagnosis uses."""
    from app.core import source_budget
    status = source_budget.status()
    keys = ("requests_last_hour", "limit_per_hour", "limit_from_env", "budget_exhausted",
            "backoff_active", "backoff_until", "backoff_level", "pushbacks_last_24h",
            "pushbacks_last_72h", "pushbacks_last_7d", "history_since", "history_hours",
            "backoffs_last_hour", "last_pushback", "observed_since")
    return {key: status.get(key) for key in keys}


def diagnose(apply: bool = True) -> dict:
    """Hardware + source health, a chosen preset, and (``apply``) that preset written.

    Only the performance values of the preset are written - exactly them,
    never anything more aggressive. The video encoder and quality are left
    alone: they are suggested in ``recommendations`` and change only when the
    user saves them.
    """
    from app import config
    result = dict(probe())
    source = _source_health()
    rec = recommendations(result.get("cpu") or {}, result.get("memory") or {},
                          result.get("encoders") or [], result.get("gpus") or [], source)
    result.update(source=source, ffmpeg_available=_ffmpeg_available(), recommendations=rec)
    if apply:
        before = config.get_settings()
        after = config.apply_performance_preset(rec["preset"])
        result["applied"] = {
            "preset": rec["preset"],
            "label": rec["preset_label"],
            "reasons": rec["reasons"],
            "values": config.preset_values(rec["preset"]),
            "previous": {key: before.get(key) for key in config.PERFORMANCE_KEYS},
            "source_cap_from_env": bool(os.getenv("SC_SOURCE_MAX_CONNECTIONS", "").strip()),
            "budget_from_env": bool(source.get("limit_from_env")),
        }
        result["settings"] = {key: after.get(key) for key in config.PERFORMANCE_KEYS}
        # The cap actually in force (env override or the new setting), and the
        # gate resized now: requests in flight finish, the next one obeys it.
        try:
            from app.core import m3u8
            result["applied"]["effective_source_max_connections"] = m3u8.apply_source_cap()
        except Exception:
            result["applied"]["effective_source_max_connections"] = None
    return result


def probe():
    """Serialize probes and briefly cache results to bound repeated requests."""
    global _cached, _cached_at
    with _lock:
        if _cached is not None and time.monotonic() - _cached_at < CACHE_SECONDS:
            return _cached
        cpu, memory, gpus, warnings = _inventory()
        try:
            encoders = transcode.capabilities()["encoders"]
        except (OSError, RuntimeError, subprocess.SubprocessError):
            encoders = []
            warnings.append("encoders_unavailable")
        # Raw driver errors may expose local paths; return only diagnostic fields.
        encoders = [{key: row.get(key) for key in ("backend", "codec", "encoder", "available")} for row in encoders]
        _cached = {"cpu": cpu, "memory": memory, "gpus": gpus, "encoders": encoders,
                   "warnings": warnings, "recommendations": recommendations(cpu, memory, encoders, gpus)}
        _cached_at = time.monotonic()
        return _cached
