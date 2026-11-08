"""On-demand, local hardware diagnostics. No downloads or system changes."""
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


def recommendations(cpu, memory, encoders):
    """Conservative starting points, not network benchmarks or session limits."""
    available = [row for row in encoders if row.get("available")]
    backend = next((name for name in ("nvenc", "qsv", "amf", "cpu")
                    if any(row.get("backend") == name for row in available)), None)
    threads = cpu.get("logical_threads") or 1
    ram = memory.get("available_bytes")
    roomy = threads >= 8 and ram is not None and ram >= 8 * 1024**3
    moderate = threads >= 4 and ram is not None and ram >= 4 * 1024**3
    return {
        "video_encoder": backend,
        "video_codec": next((codec for codec in ("h264", "hevc")
                            if any(row.get("backend") == backend and row.get("codec") == codec for row in available)), None),
        "video_quality": "balanced",
        "video_cpu_fallback": any(row.get("backend") == "cpu" for row in available),
        "max_parallel_conversions": 1,  # Actual transcode semaphore capacity.
        "max_concurrent_downloads": 3 if roomy else 2 if moderate else 1,
        "max_segment_workers": 16 if roomy else 8 if moderate else 4,
    }


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
                   "warnings": warnings, "recommendations": recommendations(cpu, memory, encoders)}
        _cached_at = time.monotonic()
        return _cached
