"""Local diagnostics, bounded commands and settings-only API access."""
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import config
from app.auth.permissions import Permission
from app.core import hardware_probe as hw
from tests.conftest import do_setup, restrict_local_user

GIB = 1024**3


@pytest.fixture(autouse=True)
def clear_probe(monkeypatch):
    monkeypatch.setattr(hw, "_cached", None)
    monkeypatch.setattr(hw, "_cached_at", 0)


GPU = [{"name": "NVIDIA Test", "vram_bytes": None}]

# A source history of eight days with no refusal in it: the only state in
# which the hardware alone decides.
CLEAN_WEEK = {"pushbacks_last_72h": 0, "pushbacks_last_7d": 0, "history_hours": 8 * 24,
              "backoff_active": False, "budget_exhausted": False}


@pytest.mark.parametrize("threads,ram,gpus,preset", [
    (16, 16 * GIB, [], "fast"),                   # strong
    (4, 8 * GIB, [], "balanced"),                 # medium
    (4, 4 * GIB, GPU, "balanced"),                # little RAM, but a GPU
    (4, 4 * GIB, [], "conservative"),             # no GPU and under 8 GiB
    (2, 32 * GIB, GPU, "conservative"),           # under 4 cores
    (None, None, GPU, "conservative"),            # not enough data
    (32, None, GPU, "conservative"),              # RAM unknown
])
def test_preset_recommendations(threads, ram, gpus, preset):
    encoders = [{"backend": "cpu", "codec": "h264", "available": True},
                {"backend": "nvenc", "codec": "h264", "available": False}]
    result = hw.recommendations({"logical_threads": threads}, {"total_bytes": ram}, encoders, gpus,
                                CLEAN_WEEK)
    assert result["video_encoder"] == "cpu"
    assert result["preset"] == preset
    values = config.PERFORMANCE_PRESETS[preset]
    for key in config.PERFORMANCE_KEYS:
        assert result[key] == values[key]
    assert result["max_parallel_conversions"] == 1
    assert result["reasons"]


def test_no_source_history_is_not_enough_data():
    # The read-only probe has no source health: never more than conservative.
    result = hw.recommendations({"logical_threads": 32}, {"total_bytes": 64 * GIB}, [], GPU)
    assert result["preset"] == "conservative"
    assert "insufficienti" in " ".join(result["reasons"])


@pytest.mark.parametrize("source,preset,why", [
    ({**CLEAN_WEEK, "pushbacks_last_72h": 1, "pushbacks_last_7d": 1}, "conservative", "72 ore"),
    ({**CLEAN_WEEK, "backoff_active": True}, "conservative", "pausa"),
    ({**CLEAN_WEEK, "history_hours": 71}, "conservative", "insufficienti"),
    ({**CLEAN_WEEK, "history_hours": None}, "conservative", "insufficienti"),
    ({**CLEAN_WEEK, "pushbacks_last_7d": 2}, "balanced", "7 giorni"),
    ({**CLEAN_WEEK, "history_hours": 100}, "balanced", "7 giorni"),
    ({**CLEAN_WEEK, "budget_exhausted": True}, "balanced", "budget"),
    (CLEAN_WEEK, "fast", "7 giorni"),
])
def test_strong_hardware_is_fast_only_after_a_clean_week(source, preset, why):
    chosen, reasons = hw.choose_preset({"physical_cores": 16}, {"total_bytes": 64 * GIB}, GPU, [], source)
    assert chosen == preset
    assert why in " ".join(reasons)


def test_no_automatic_choice_goes_past_the_fast_preset():
    fast = config.preset_values("fast")
    for source in (CLEAN_WEEK, {}, None, {**CLEAN_WEEK, "pushbacks_last_72h": 3}):
        for cpu, ram in (({"physical_cores": 128}, 1024 * GIB), ({"physical_cores": 2}, GIB)):
            result = hw.recommendations(cpu, {"total_bytes": ram}, [], GPU, source)
            assert result["preset"] in config.PRESET_ORDER
            for key in config.PERFORMANCE_KEYS:
                assert result[key] <= fast[key]
            assert result["source_max_connections"] <= 4
            assert result["source_requests_per_hour"] <= 120


def test_a_basic_display_adapter_is_not_a_gpu():
    preset, _ = hw.choose_preset({"physical_cores": 4}, {"total_bytes": 4 * GIB},
                                 [{"name": "Microsoft Basic Display Adapter"}], [], CLEAN_WEEK)
    assert preset == "conservative"
    # A verified hardware encoder counts as a GPU even when none is listed.
    preset, _ = hw.choose_preset({"physical_cores": 4}, {"total_bytes": 4 * GIB}, [],
                                 [{"backend": "qsv", "codec": "h264", "available": True}], CLEAN_WEEK)
    assert preset == "balanced"


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
    restrict_local_user(monkeypatch, Permission.DOWNLOAD)
    assert client.get("/api/domain/hardware").status_code == 403
    assert calls == [1]


# ── Diagnosis: source health, preset choice and what gets written ─────────────

STRONG = ({"name": "CPU", "physical_cores": 8, "logical_threads": 16},
          {"total_bytes": 32 * GIB, "available_bytes": 20 * GIB}, GPU, [])


def _fake_probe(monkeypatch, cpu, memory, gpus, warnings):
    encoders = [{"backend": "nvenc", "codec": "h264", "encoder": "h264_nvenc", "available": True}]
    monkeypatch.setattr(hw, "_inventory", lambda: (cpu, memory, gpus, warnings))
    monkeypatch.setattr(hw.transcode, "capabilities", lambda: {"encoders": encoders})
    monkeypatch.setattr(hw, "_ffmpeg_available", lambda: True)


def _history(days, pushbacks=(), now=None):
    """Write the persisted source history (source_history.json beside data.json)."""
    import json
    import time
    from app.core import source_budget
    now = time.time() if now is None else now
    path = source_budget._history_path()
    path.write_text(json.dumps({"since": now - days * 86400,
                                "pushbacks": [[now - h * 3600, "HTTP 429 test"] for h in pushbacks]}),
                    encoding="utf-8")
    source_budget.reset()   # read it again


def test_strong_hardware_after_a_clean_week_is_fast(monkeypatch):
    _fake_probe(monkeypatch, *STRONG)
    _history(days=8)
    result = hw.diagnose()
    assert result["recommendations"]["preset"] == "fast"
    assert result["applied"]["preset"] == "fast"
    assert result["ffmpeg_available"] is True
    assert result["source"]["pushbacks_last_72h"] == 0
    assert result["source"]["pushbacks_last_7d"] == 0
    stored = config.get_settings()
    assert {k: stored[k] for k in config.PERFORMANCE_KEYS} == config.preset_values("fast")
    assert result["applied"]["values"]["source_max_connections"] == 4
    assert result["applied"]["values"]["source_requests_per_hour"] == 120


def test_a_first_diagnosis_without_history_is_conservative(monkeypatch):
    _fake_probe(monkeypatch, *STRONG)
    result = hw.diagnose()   # no source_history.json yet: it starts now
    assert result["applied"]["preset"] == "conservative"
    assert "insufficienti" in " ".join(result["applied"]["reasons"])
    from app.core import source_budget
    assert source_budget._history_path().exists()


def test_a_recent_backoff_makes_the_diagnosis_conservative(monkeypatch):
    from app.core import source_budget
    _fake_probe(monkeypatch, *STRONG)
    _history(days=8)
    source_budget.report_pushback("HTTP 429 test")
    result = hw.diagnose()
    assert result["source"]["backoff_active"] is True
    assert result["source"]["pushbacks_last_72h"] == 1
    assert result["applied"]["preset"] == "conservative"
    assert config.get_settings()["max_concurrent_downloads"] == 1
    assert config.get_settings()["source_max_connections"] == 2
    assert config.get_settings()["source_requests_per_hour"] == 60


def test_a_pushback_in_the_last_72_hours_is_conservative_after_the_pause(monkeypatch):
    from app.core import source_budget
    _fake_probe(monkeypatch, *STRONG)
    clock = [2_000_000_000.0]
    monkeypatch.setattr(source_budget, "_now", lambda: clock[0])
    _history(days=10, now=clock[0])
    source_budget.report_pushback("HTTP 403 test")
    clock[0] += 30 * 3600   # pause long over, more than a day, within 72 hours
    result = hw.diagnose()
    assert result["applied"]["preset"] == "conservative"
    assert "72 ore" in " ".join(result["applied"]["reasons"])
    clock[0] += 50 * 3600   # now 80 hours ago: within the week -> balanced, not fast
    result = hw.diagnose()
    assert result["applied"]["preset"] == "balanced"
    assert "7 giorni" in " ".join(result["applied"]["reasons"])
    clock[0] += 6 * 86400   # more than a week ago
    assert hw.diagnose()["applied"]["preset"] == "fast"


def test_a_refusal_from_an_earlier_session_still_counts(monkeypatch):
    """The 72h/7d history survives a restart (source_history.json)."""
    _fake_probe(monkeypatch, *STRONG)
    _history(days=8, pushbacks=[48])     # a refusal two days ago, before "restart"
    result = hw.diagnose()
    assert result["source"]["pushbacks_last_72h"] == 1
    assert result["applied"]["preset"] == "conservative"
    _history(days=8, pushbacks=[100])    # four days ago
    assert hw.diagnose()["applied"]["preset"] == "balanced"


def test_an_unreadable_history_starts_over_and_is_conservative(monkeypatch):
    from app.core import source_budget
    _fake_probe(monkeypatch, *STRONG)
    source_budget._history_path().write_text("{not json", encoding="utf-8")
    source_budget.reset()
    assert hw.diagnose()["applied"]["preset"] == "conservative"


def test_the_diagnosis_never_writes_more_than_the_chosen_preset(monkeypatch):
    # A user who had cranked everything up gets exactly the preset back.
    config.save_settings({**config.get_settings(), "max_concurrent_downloads": 10,
                          "max_segment_workers": 64, "source_max_connections": 32})
    _fake_probe(monkeypatch, {"physical_cores": 4}, {"total_bytes": 8 * GIB}, [], [])
    _history(days=8)
    result = hw.diagnose()
    preset = result["applied"]["preset"]
    assert preset == "balanced"
    stored = config.get_settings()
    for key, value in config.preset_values(preset).items():
        assert stored[key] <= value
        assert result["settings"][key] <= value
    assert result["applied"]["previous"]["max_concurrent_downloads"] == 10


def test_the_diagnosis_does_not_touch_the_video_output(monkeypatch):
    config.save_settings({**config.get_settings(), "video_conversion": "copy",
                          "video_encoder": "auto", "video_quality": "high"})
    _fake_probe(monkeypatch, *STRONG)
    result = hw.diagnose()
    assert result["recommendations"]["video_encoder"] == "nvenc"   # suggested only
    stored = config.get_settings()
    assert (stored["video_conversion"], stored["video_encoder"], stored["video_quality"]) == (
        "copy", "auto", "high")


def test_diagnose_endpoint_applies_the_preset_and_resizes_the_queue(client, admin_credentials, monkeypatch):
    do_setup(client, admin_credentials)
    from app.jobs import job_manager
    monkeypatch.setattr(job_manager._slots, "_limit", job_manager._slots._limit)  # restored after
    monkeypatch.delenv("SC_SOURCE_MAX_CONNECTIONS", raising=False)
    _fake_probe(monkeypatch, *STRONG)
    _history(days=8)
    response = client.post("/api/domain/hardware/diagnose")
    assert response.status_code == 200
    assert response.json()["applied"]["preset"] == "fast"
    assert response.json()["applied"]["source_cap_from_env"] is False
    assert job_manager._slots._limit == 2
    from app.core import m3u8
    assert m3u8._SOURCE_SLOTS.maximum == 4
    response = client.post("/api/domain/performance-preset", json={"preset": "conservative"})
    assert response.status_code == 200
    assert response.json()["settings"] == config.preset_values("conservative")
    assert job_manager._slots._limit == 1
    assert client.post("/api/domain/performance-preset", json={"preset": "turbo"}).status_code == 422
    restrict_local_user(monkeypatch, Permission.DOWNLOAD)
    assert client.post("/api/domain/hardware/diagnose").status_code == 403
    assert client.post("/api/domain/performance-preset", json={}).status_code == 403
