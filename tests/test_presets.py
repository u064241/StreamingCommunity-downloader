"""Performance presets: conservative by default, existing installs untouched.

The presets live in app.config. A new installation (no data.json) starts on
"Conservativo" and its first save pins it there; an installation that predates
the presets keeps the 3/16 it was running with until the user runs the
diagnosis. The global source cap (m3u8) reads the same setting, and the
environment variable still wins when it is set.
"""

import json

import pytest

from app import config
from app.core import m3u8
from tests.conftest import do_setup


def _perf(settings):
    return {key: settings[key] for key in config.PERFORMANCE_KEYS}


def test_the_presets_are_the_documented_values():
    assert config.preset_values("conservative") == {
        "max_concurrent_downloads": 1, "max_segment_workers": 4, "source_max_connections": 2,
        "source_requests_per_hour": 60}
    assert config.preset_values("balanced") == {
        "max_concurrent_downloads": 1, "max_segment_workers": 6, "source_max_connections": 3,
        "source_requests_per_hour": 90}
    assert config.preset_values("fast") == {
        "max_concurrent_downloads": 2, "max_segment_workers": 8, "source_max_connections": 4,
        "source_requests_per_hour": 120}
    assert config.PRESET_ORDER == ("conservative", "balanced", "fast")


def test_no_preset_goes_above_the_source_ceilings():
    assert config.SOURCE_MAX_CONNECTIONS_CEILING == 4
    assert config.SOURCE_REQUESTS_PER_HOUR_CEILING == 120
    for name in config.PRESET_ORDER:
        values = config.preset_values(name)
        assert values["source_max_connections"] <= 4
        assert values["source_requests_per_hour"] <= 120
    # Each step is at least as gentle as the next one.
    for gentler, harder in zip(config.PRESET_ORDER, config.PRESET_ORDER[1:]):
        for key in config.PERFORMANCE_KEYS:
            assert config.preset_values(gentler)[key] <= config.preset_values(harder)[key]


def test_a_stored_cap_above_the_ceiling_is_clamped_when_read(monkeypatch):
    monkeypatch.delenv("SC_SOURCE_MAX_CONNECTIONS", raising=False)
    monkeypatch.delenv("SC_SOURCE_REQUESTS_PER_HOUR", raising=False)
    from app.core import source_budget
    config.save_settings({**config.get_settings(), "source_max_connections": 32,
                          "source_requests_per_hour": 5000})
    assert m3u8._source_max_connections() == 4
    assert source_budget.limit() == 120


def test_the_hourly_budget_is_read_from_the_settings(monkeypatch):
    from app.core import source_budget
    monkeypatch.delenv("SC_SOURCE_REQUESTS_PER_HOUR", raising=False)
    for name in config.PRESET_ORDER:
        config.apply_performance_preset(name)
        assert source_budget.limit() == config.preset_values(name)["source_requests_per_hour"]
        assert source_budget.status()["limit_per_hour"] == source_budget.limit()
        assert source_budget.status()["limit_from_env"] is False
    # The env override still wins, explicitly, and says so.
    monkeypatch.setenv("SC_SOURCE_REQUESTS_PER_HOUR", "30")
    assert source_budget.limit() == 30
    assert source_budget.status()["limit_from_env"] is True


def test_a_new_installation_is_conservative_by_default(_configured_domain):
    _configured_domain.unlink()
    assert config.DEFAULT_PERFORMANCE_PRESET == "conservative"
    assert _perf(config.get_settings()) == config.preset_values("conservative")
    assert config.matching_preset(config.get_settings()) == "conservative"


def test_the_first_save_of_a_new_installation_pins_the_conservative_preset(_configured_domain):
    _configured_domain.unlink()
    config.update_data({"domain": "new.test"})   # e.g. the first-run domain setup

    stored = json.loads(_configured_domain.read_text(encoding="utf-8"))
    assert stored["domain"] == "new.test"
    assert stored["settings"] == config.preset_values("conservative")
    # Now that data.json exists it is still read as conservative, not legacy.
    assert _perf(config.get_settings()) == config.preset_values("conservative")


def test_an_existing_installation_keeps_its_values_and_is_not_rewritten(_configured_domain):
    """data.json from before the presets: no performance keys stored."""
    _configured_domain.write_text(json.dumps({"domain": "old.test"}), encoding="utf-8")
    before = _configured_domain.read_bytes()

    perf = _perf(config.get_settings())
    assert {k: perf[k] for k in config.LEGACY_PERFORMANCE_DEFAULTS} == config.LEGACY_PERFORMANCE_DEFAULTS
    # The source side is not "as before": cap at the ceiling, conservative budget.
    assert perf["source_max_connections"] == 4
    assert perf["source_requests_per_hour"] == 60
    assert config.matching_preset(config.get_settings()) is None
    assert config.get_settings()["max_concurrent_downloads"] == 3
    assert config.get_settings()["max_segment_workers"] == 16
    # Reading never writes: nothing changes until the diagnosis runs.
    assert _configured_domain.read_bytes() == before


def test_values_the_user_saved_win_over_any_default(_configured_domain):
    config.save_settings({**config.get_settings(), "max_concurrent_downloads": 5,
                          "max_segment_workers": 12})
    settings = config.get_settings()
    assert settings["max_concurrent_downloads"] == 5
    assert settings["max_segment_workers"] == 12
    assert config.matching_preset(settings) is None


def test_applying_a_preset_writes_exactly_its_values():
    config.save_settings({**config.get_settings(), "max_concurrent_downloads": 9,
                          "max_segment_workers": 99, "source_max_connections": 40})
    after = config.apply_performance_preset("balanced")
    assert _perf(after) == config.preset_values("balanced")
    assert _perf(config.get_settings()) == config.preset_values("balanced")


def test_only_lower_never_raises_a_value():
    config.save_settings({**config.get_settings(), "max_concurrent_downloads": 3,
                          "max_segment_workers": 2, "source_max_connections": 1,
                          "source_requests_per_hour": 100})
    after = config.apply_performance_preset("conservative", only_lower=True)
    assert _perf(after) == {"max_concurrent_downloads": 1, "max_segment_workers": 2,
                            "source_max_connections": 1, "source_requests_per_hour": 60}


def test_unknown_preset_is_refused():
    with pytest.raises(KeyError):
        config.apply_performance_preset("turbo")


# ── The global source cap (m3u8) ──────────────────────────────────────────────

def test_source_cap_comes_from_the_setting(monkeypatch):
    monkeypatch.delenv("SC_SOURCE_MAX_CONNECTIONS", raising=False)
    config.save_settings({**config.get_settings(), "source_max_connections": 3})
    assert m3u8._source_max_connections() == 3


def test_source_cap_env_wins_only_when_present(monkeypatch):
    config.save_settings({**config.get_settings(), "source_max_connections": 3})
    monkeypatch.setenv("SC_SOURCE_MAX_CONNECTIONS", "9")
    assert m3u8._source_max_connections() == 9      # explicit override, not clamped
    assert m3u8.source_cap_env() == 9
    monkeypatch.setenv("SC_SOURCE_MAX_CONNECTIONS", "")
    assert m3u8._source_max_connections() == 3
    assert m3u8.source_cap_env() is None
    monkeypatch.setenv("SC_SOURCE_MAX_CONNECTIONS", "lots")
    assert m3u8._source_max_connections() == 3


def test_nothing_in_the_project_sets_the_env_cap():
    """Only an explicit environment can set the override: no project file
    (Dockerfile, compose templates, scripts, app code) assigns it."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    # ENV/compose/PowerShell assignments at the start of a line, and Python
    # setenv/environ assignments; a log message naming the variable is fine.
    assign = re.compile(r"(?m)^\s*(?:-\s*|ENV\s+|\$env:)?[\"']?SC_SOURCE_MAX_CONNECTIONS[\"']?\s*[:=]"
                        r"|setenv\(\s*[\"']SC_SOURCE_MAX_CONNECTIONS"
                        r"|environ\[\s*[\"']SC_SOURCE_MAX_CONNECTIONS[\"']\s*\]\s*=")
    candidates = [root / "Dockerfile", *root.glob("docker-compose*.yml"), *root.glob("*.ps1"),
                  *(root / "app").rglob("*.py")]
    if (root / "scripts").is_dir():
        candidates += [p for p in (root / "scripts").rglob("*") if p.is_file()]
    offenders = [p.name for p in candidates
                 if p.is_file() and assign.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert offenders == []


def test_a_settings_change_resizes_the_gate_at_once(client, admin_credentials, monkeypatch):
    do_setup(client, admin_credentials)
    monkeypatch.delenv("SC_SOURCE_MAX_CONNECTIONS", raising=False)
    assert client.put("/api/domain/settings", json={"source_max_connections": 3}).status_code == 200
    assert m3u8._SOURCE_SLOTS.maximum == 3
    assert client.post("/api/domain/performance-preset", json={"preset": "conservative"}).status_code == 200
    assert m3u8._SOURCE_SLOTS.maximum == 2


def test_the_gate_follows_the_setting_at_the_next_download(monkeypatch):
    monkeypatch.delenv("SC_SOURCE_MAX_CONNECTIONS", raising=False)
    config.save_settings({**config.get_settings(), "source_max_connections": 1})
    assert m3u8.apply_source_cap() == 1
    assert m3u8._SOURCE_SLOTS.maximum == 1


def test_source_cap_default_follows_the_preset(monkeypatch, _configured_domain):
    monkeypatch.delenv("SC_SOURCE_MAX_CONNECTIONS", raising=False)
    _configured_domain.unlink()
    assert m3u8._source_max_connections() == 2               # new install: conservative
    _configured_domain.write_text(json.dumps({"domain": "old.test"}), encoding="utf-8")
    assert m3u8._source_max_connections() == 4               # existing install: the ceiling


# ── API ───────────────────────────────────────────────────────────────────────

def test_settings_api_accepts_the_source_cap_and_a_backup_restores_it(client, admin_credentials):
    do_setup(client, admin_credentials)
    response = client.put("/api/domain/settings", json={"source_max_connections": 3})
    assert response.status_code == 200
    assert config.get_settings()["source_max_connections"] == 3
    assert client.put("/api/domain/settings", json={"source_max_connections": 0}).status_code == 400
    # Above the ceilings: refused by the API.
    assert client.put("/api/domain/settings", json={"source_max_connections": 5}).status_code == 400
    assert client.put("/api/domain/settings", json={"source_requests_per_hour": 121}).status_code == 400
    assert client.put("/api/domain/settings", json={"source_requests_per_hour": 90}).status_code == 200
    assert config.get_settings()["source_requests_per_hour"] == 90

    backup = client.get("/api/domain/settings/backup").json()
    assert backup["data"]["settings"]["source_max_connections"] == 3
    response = client.post("/api/domain/settings/restore", json=backup)
    assert response.status_code == 200, response.text

    # A backup from before the ceilings (6 connections) is lowered, not refused.
    backup["data"]["settings"]["source_max_connections"] = 6
    response = client.post("/api/domain/settings/restore", json=backup)
    assert response.status_code == 200, response.text
    assert config.get_settings()["source_max_connections"] == 4


def test_source_protection_endpoint_reports_the_current_preset(client, admin_credentials):
    do_setup(client, admin_credentials)
    config.apply_performance_preset("conservative")
    body = client.get("/api/domain/source-protection").json()
    assert body["preset"] == "conservative"
    assert body["paused"] is False
    assert body["message"] is None
    assert body["source_cap_from_env"] is False
    assert body["budget_from_env"] is False
