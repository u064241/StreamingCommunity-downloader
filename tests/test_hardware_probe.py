"""Local diagnostics, bounded commands and settings-only API access."""
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import config
from app.auth.permissions import Permission
from app.core import hardware_probe as hw
from tests.conftest import do_setup, make_user, session_for

GIB = 1024**3


@pytest.fixture(autouse=True)
def clear_probe(monkeypatch):
    monkeypatch.setattr(hw, "_cached", None)
    monkeypatch.setattr(hw, "_cached_at", 0)


@pytest.mark.parametrize("threads,ram,downloads,workers", [
    (16, 16 * GIB, 3, 16), (4, 4 * GIB, 2, 8),
    (2, GIB, 1, 4), (None, None, 1, 4), (32, GIB, 1, 4),
])
def test_conservative_recommendations(threads, ram, downloads, workers):
    encoders = [{"backend": "cpu", "codec": "h264", "available": True},
                {"backend": "nvenc", "codec": "h264", "available": False}]
    result = hw.recommendations({"logical_threads": threads}, {"available_bytes": ram}, encoders)
    assert result["video_encoder"] == "cpu"
    assert result["max_concurrent_downloads"] == downloads
    assert result["max_segment_workers"] == workers
    assert result["max_parallel_conversions"] == 1


def test_only_verified_encoders_are_recommended():
    result = hw.recommendations({}, {}, [{"backend": "nvenc", "codec": "hevc", "available": True}])
    assert result["video_encoder"] == "nvenc"
    assert result["video_codec"] == "hevc"
    assert result["video_cpu_fallback"] is False
    result = hw.recommendations({}, {}, [])
    assert result["video_encoder"] is None
    assert result["video_codec"] is None


def test_windows_inventory_and_nvidia_vram(monkeypatch):
    monkeypatch.setattr(hw.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hw, "_windows_inventory", lambda: {
        "cpu": [{"Name": "Test CPU", "NumberOfCores": 4, "NumberOfLogicalProcessors": 8}],
        "memory": {"total": 16 * GIB, "available": 9 * GIB},
        "gpus": [{"Name": "NVIDIA Test"}, {"Name": "Intel Test"}],
    })
    monkeypatch.setattr(hw.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(hw, "_run", lambda args: "Test, 8192\n")
    cpu, memory, gpus, warnings = hw._inventory()
    assert cpu == {"name": "Test CPU", "physical_cores": 4, "logical_threads": 8}
    assert memory == {"total_bytes": 16 * GIB, "available_bytes": 9 * GIB}
    assert gpus == [{"name": "NVIDIA Test", "vram_bytes": 8 * GIB},
                    {"name": "Intel Test", "vram_bytes": None}]
    assert not warnings


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("probe", 8), ValueError("bad JSON"), OSError("missing")])
def test_missing_or_failed_tools_are_partial(monkeypatch, error):
    monkeypatch.setattr(hw.platform, "system", lambda: "Windows")
    def fail():
        raise error
    monkeypatch.setattr(hw, "_windows_inventory", fail)
    monkeypatch.setattr(hw.shutil, "which", lambda _: None)
    cpu, memory, gpus, warnings = hw._inventory()
    assert "logical_threads" in cpu
    assert "total_bytes" in memory
    assert gpus == []
    assert "inventory_unavailable" in warnings


def test_commands_use_timeout_and_no_shell(monkeypatch):
    def run(command, **kwargs):
        assert command == ["diagnostic", "fixed-arg"]
        assert kwargs["timeout"] == 8
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert not kwargs.get("shell")
        return subprocess.CompletedProcess(command, 0, stdout="ok")
    monkeypatch.setattr(hw.subprocess, "run", run)
    assert hw._run(["diagnostic", "fixed-arg"]) == "ok"


def test_concurrent_calls_share_probe_and_sanitize_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(hw, "_inventory", lambda: ({}, {}, [], []))
    def encoders():
        calls.append(1)
        return {"encoders": [{"backend": "cpu", "codec": "h264", "encoder": "libx264",
                              "available": True, "reason": "private path"}]}
    monkeypatch.setattr(hw.transcode, "capabilities", encoders)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: hw.probe(), range(8)))
    assert len(calls) == 1
    assert all(result == results[0] for result in results)
    assert "reason" not in results[0]["encoders"][0]
    monkeypatch.setattr(hw, "_cached_at", -hw.CACHE_SECONDS)
    hw.probe()
    assert len(calls) == 2


def test_ffmpeg_failure_still_returns_hardware(monkeypatch):
    monkeypatch.setattr(hw, "_inventory", lambda: ({"logical_threads": 8}, {}, [], []))
    def fail():
        raise RuntimeError("missing ffmpeg")
    monkeypatch.setattr(hw.transcode, "capabilities", fail)
    result = hw.probe()
    assert result["cpu"]["logical_threads"] == 8
    assert result["encoders"] == []
    assert "encoders_unavailable" in result["warnings"]
    assert result["recommendations"]["video_encoder"] is None


def test_endpoint_requires_manage_settings_and_does_not_save(client, admin_credentials, monkeypatch):
    do_setup(client, admin_credentials)
    calls = []
    def probe():
        calls.append(1)
        return {"cpu": {}, "recommendations": {}}
    monkeypatch.setattr(hw, "probe", probe)
    before = config.get_settings().copy()
    response = client.get("/api/domain/hardware")
    assert response.status_code == 200
    assert response.json() == {"cpu": {}, "recommendations": {}}
    assert config.get_settings() == before
    client.cookies.clear()
    assert client.get("/api/domain/hardware").status_code in (401, 403)
    user = make_user("viewer", "jf-viewer", int(Permission.DOWNLOAD))
    session_for(client, user.id)
    assert client.get("/api/domain/hardware").status_code == 403
    assert calls == [1]
