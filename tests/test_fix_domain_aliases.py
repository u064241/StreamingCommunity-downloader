"""Old aliases of the source are retired on every rotation and never probed again."""

import json

import pytest

from app import config
from app.core import domain_recovery


@pytest.fixture
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "data.json"
    monkeypatch.setattr(config, "DATA_FILE", path)
    return path


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_replacing_the_domain_retires_the_previous_one(data_file):
    _write(data_file, {"domain": "streamingcommunityz.boo"})

    config.update_data({"domain": "streamingcommunityz.nexus"})

    data = json.loads(data_file.read_text(encoding="utf-8"))
    assert data["domain"] == "streamingcommunityz.nexus"
    assert data["retired_domains"] == ["streamingcommunityz.boo"]
    assert config.retired_domains() == {"streamingcommunityz.boo"}


def test_rotation_chain_keeps_every_old_alias_and_no_duplicates(data_file):
    _write(data_file, {"domain": "a.one"})
    config.update_data({"domain": "b.two"})
    config.update_data({"domain": "c.three"})
    config.update_data({"domain": "b.two"})  # back to an old one: it is current again

    data = json.loads(data_file.read_text(encoding="utf-8"))
    assert data["domain"] == "b.two"
    assert "b.two" not in data["retired_domains"]
    assert set(data["retired_domains"]) == {"a.one", "c.three"}


def test_retired_list_is_capped(data_file):
    _write(data_file, {"domain": "d0.x"})
    for i in range(1, config._MAX_RETIRED_DOMAINS + 10):
        config.update_data({"domain": f"d{i}.x"})

    data = json.loads(data_file.read_text(encoding="utf-8"))
    assert len(data["retired_domains"]) == config._MAX_RETIRED_DOMAINS


def test_writing_unrelated_keys_does_not_touch_the_list(data_file):
    _write(data_file, {"domain": "now.x", "retired_domains": ["old.x"]})

    config.update_data({"download_dir": "C:\\DIVX"})

    data = json.loads(data_file.read_text(encoding="utf-8"))
    assert data["retired_domains"] == ["old.x"]


def test_a_retired_candidate_is_never_probed(data_file, monkeypatch):
    _write(data_file, {"domain": "streamingcommunityz.nexus",
                       "retired_domains": ["streamingcommunityz.boo"],
                       "domain_auto_apply": False})
    probed = []

    monkeypatch.setattr(domain_recovery, "fetch_telegram_candidates",
                        lambda: ["streamingcommunityz.boo"])
    monkeypatch.setattr(domain_recovery, "fetch_candidates", lambda *a, **k: [])
    monkeypatch.setattr(domain_recovery, "verify",
                        lambda host: probed.append(host) or "1.0")
    monkeypatch.setattr(domain_recovery, "_last_check_at", None)

    result = domain_recovery._run_check(force=True)

    assert "streamingcommunityz.boo" not in probed
    assert result["candidate"] is None
    assert {"host": "streamingcommunityz.boo", "reason": "dominio ritirato"} in result["rejected"]
