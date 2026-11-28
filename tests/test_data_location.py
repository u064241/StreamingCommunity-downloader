"""Where settings and the database live, and the one-time copy from the old folder."""

import os
from pathlib import Path

import pytest

from app import config


@pytest.mark.skipif(os.name != "nt", reason="LOCALAPPDATA is a Windows location")
def test_default_data_dir_is_local_appdata_on_windows(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path)}
    assert config.default_data_dir(env) == tmp_path / "StreamingCommunityDownloader"


def test_default_data_dir_falls_back_to_working_directory_without_localappdata():
    assert config.default_data_dir({}) == Path.cwd()


def test_copies_state_from_old_folder_once(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "aria2").mkdir(parents=True)
    (old / "aria2" / "aria2c.exe").write_bytes(b"bin")
    (old / "data.json").write_text('{"download_dir": "C:/DIVX"}', encoding="utf-8")
    (old / "panel.db").write_bytes(b"sqlite")

    copied = config.migrate_legacy_data(old, new)

    assert set(copied) == {"data.json", "panel.db", "aria2"}
    assert (new / "data.json").read_text(encoding="utf-8") == '{"download_dir": "C:/DIVX"}'
    assert (new / "aria2" / "aria2c.exe").read_bytes() == b"bin"
    # The old files are copied, not moved.
    assert (old / "data.json").exists()


def test_second_run_does_not_bring_back_or_overwrite(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    (old / "data.json").write_text("{}", encoding="utf-8")
    config.migrate_legacy_data(old, new)

    (new / "data.json").write_text('{"changed": true}', encoding="utf-8")
    (old / "data.json").write_text('{"old": true}', encoding="utf-8")

    assert config.migrate_legacy_data(old, new) == []
    assert (new / "data.json").read_text(encoding="utf-8") == '{"changed": true}'


def test_no_copy_when_old_and_new_are_the_same_folder(tmp_path):
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    assert config.migrate_legacy_data(tmp_path, tmp_path) == []


def test_no_copy_when_old_folder_has_nothing(tmp_path):
    assert config.migrate_legacy_data(tmp_path / "missing", tmp_path / "new") == []
    assert not (tmp_path / "new").exists()
