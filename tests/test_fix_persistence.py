"""Regressions for data.json, schedule.json and the database migrations."""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

import pytest

from app import config, db
from app.schedule import ScheduleStore


def _corrupt_copies(path):
    return sorted(p for p in path.parent.iterdir() if p.name.startswith(path.name + ".corrupt-"))


# ── 6. data.json ──────────────────────────────────────────────────────────────

@pytest.fixture
def data_file(_configured_domain):
    """The throwaway data.json conftest points app.config at."""
    return _configured_domain


def test_a_missing_data_file_is_an_empty_configuration(data_file):
    data_file.unlink()
    assert config.read_data() == {}
    assert config.get_settings() == config.SETTINGS_DEFAULTS


def test_a_corrupt_data_file_is_set_aside_not_read_as_empty_in_silence(data_file, caplog):
    data_file.write_text('{"domain": "example.te', encoding="utf-8")   # cut short

    with caplog.at_level(logging.ERROR, logger="app.config"):
        assert config.read_data() == {}

    [copy] = _corrupt_copies(data_file)
    assert copy.read_text(encoding="utf-8") == '{"domain": "example.te'
    assert not data_file.exists()
    assert any("corrupt" in r.getMessage() for r in caplog.records)
    # Nothing left to set aside the second time: just "no file".
    assert config.read_data() == {}
    assert len(_corrupt_copies(data_file)) == 1


def test_saving_over_a_corrupt_data_file_does_not_destroy_it(data_file):
    """update_data() used to read a corrupt file as {} and write that back,
    with the one changed key, over every other setting."""
    data_file.write_text("not json at all", encoding="utf-8")

    config.update_data({"domain": "new.test"})

    assert json.loads(data_file.read_text(encoding="utf-8")) == {"domain": "new.test"}
    [copy] = _corrupt_copies(data_file)
    assert copy.read_text(encoding="utf-8") == "not json at all"


def test_a_corrupt_file_that_cannot_be_set_aside_is_never_overwritten(data_file, monkeypatch):
    data_file.write_text("not json at all", encoding="utf-8")

    def refuse(path):
        raise PermissionError(13, "in use", str(path))

    monkeypatch.setattr(config, "quarantine_corrupt_file", refuse)

    with pytest.raises(config.CorruptDataFile):
        config.update_data({"domain": "new.test"})
    assert data_file.read_text(encoding="utf-8") == "not json at all"


def test_settings_come_from_the_same_reader(data_file):
    data_file.write_text("{{{", encoding="utf-8")

    assert config.get_settings() == config.SETTINGS_DEFAULTS
    assert len(_corrupt_copies(data_file)) == 1


def test_a_write_that_fails_midway_leaves_the_old_file_whole(data_file, monkeypatch):
    """Opened with "w" the file was truncated first: a full disk or a crash
    between that and the last byte left half a JSON document."""
    before = data_file.read_text(encoding="utf-8")

    def disk_full(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(config.os, "replace", disk_full)
    with pytest.raises(OSError):
        config.update_data({"domain": "new.test"})
    monkeypatch.undo()

    assert data_file.read_text(encoding="utf-8") == before


def test_update_data_keeps_the_other_keys_and_leaves_no_temp_file(data_file):
    config.update_data({"libraries": [{"type": "film", "path": "/films"}]})
    config.save_settings({"max_concurrent_downloads": 5})

    data = config.read_data()
    assert data["domain"] == "example.test"
    assert data["libraries"] == [{"type": "film", "path": "/films"}]
    assert config.get_settings()["max_concurrent_downloads"] == 5
    assert not data_file.with_name(data_file.name + ".tmp").exists()


def test_a_data_file_saved_with_a_bom_still_reads(data_file):
    """Notepad's "UTF-8 with BOM": a file edited by hand, not a corrupt one."""
    data_file.write_bytes(b"\xef\xbb\xbf" + json.dumps({"domain": "bom.test"}).encode())

    assert config.read_data() == {"domain": "bom.test"}
    assert _corrupt_copies(data_file) == []


def test_readers_never_see_a_half_written_file(data_file):
    """Concurrent readers during a burst of writes: always a whole document."""
    import threading

    stop, problems = threading.Event(), []

    def read():
        while not stop.is_set():
            data = config.read_data()
            if data.get("domain") != "example.test":
                problems.append(data)

    readers = [threading.Thread(target=read) for _ in range(4)]
    for t in readers:
        t.start()
    try:
        for n in range(150):
            config.update_data({"counter": n, "padding": "x" * 4000})
    finally:
        stop.set()
        for t in readers:
            t.join(timeout=5)

    assert problems == []
    assert _corrupt_copies(data_file) == []
    assert config.read_data()["counter"] == 149


# ── 7. schedule.json ──────────────────────────────────────────────────────────

WHEN = datetime(2999, 1, 1, tzinfo=timezone.utc)


def test_a_corrupt_schedule_file_is_set_aside_not_overwritten(tmp_path, caplog):
    path = tmp_path / "schedule.json"
    path.write_text('[{"schedule_id": "abc", "type": "fil', encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="app.schedule"):
        store = ScheduleStore(path)

    assert store.list_all() == []
    [copy] = _corrupt_copies(path)
    assert copy.read_text(encoding="utf-8").startswith('[{"schedule_id": "abc"')
    assert any("corrupt" in r.getMessage() for r in caplog.records)
    # The next save writes a new file; what was set aside is still there.
    store.add("film", WHEN, {"id": 1})
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1
    assert copy.exists()


def test_a_schedule_file_that_is_not_a_list_is_corrupt_too(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text('{"oops": true}', encoding="utf-8")

    assert ScheduleStore(path).list_all() == []
    assert len(_corrupt_copies(path)) == 1


def test_a_schedule_that_cannot_be_saved_is_not_scheduled(tmp_path, monkeypatch, caplog):
    """_save() logged and carried on: the entry lived in memory, the caller
    was told it was scheduled, and the next restart forgot it."""
    path = tmp_path / "schedule.json"
    store = ScheduleStore(path)
    kept = store.add("film", WHEN, {"id": 1})

    def disk_full(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(config.os, "replace", disk_full)
    with caplog.at_level(logging.ERROR, logger="app.schedule"):
        with pytest.raises(OSError):
            store.add("film", WHEN, {"id": 2})
        # A change to an existing entry reports instead of raising.
        assert store.mark_fired(kept) is False
    monkeypatch.undo()

    assert [e["params"]["id"] for e in store.list_all()] == [1]
    assert [e["params"]["id"] for e in ScheduleStore(path).list_all()] == [1]
    assert any("Could not save" in r.getMessage() for r in caplog.records)


def test_schedule_writes_are_atomic_and_round_trip(tmp_path):
    path = tmp_path / "schedule.json"
    store = ScheduleStore(path)
    sid = store.add("film", WHEN, {"id": 1, "title": "Perché"})

    assert store.set_job_id(sid, "job-1") is True
    assert store.mark_fired(sid) is True
    assert store.set_outcome(sid, "done") is True
    assert store.set_outcome("no-such-id", "done") is False

    [entry] = ScheduleStore(path).list_all()
    assert (entry["job_id"], entry["fired"], entry["outcome"]) == ("job-1", True, "done")
    assert entry["params"]["title"] == "Perché"
    assert not path.with_name(path.name + ".tmp").exists()


# ── 10-12. database ───────────────────────────────────────────────────────────

@pytest.fixture
def at_version(tmp_path):
    """A database migrated only as far as the given version, then handed back
    with the full list restored (by hand: see tests/test_auth_mode_migration)."""
    def build(version):
        db.configure(tmp_path / "old.db")
        full = db.MIGRATIONS
        db.MIGRATIONS = full[:version]
        try:
            db.run_migrations()
        finally:
            db.MIGRATIONS = full
        return tmp_path / "old.db"

    yield build
    db.close_all()


def _seed_v4():
    now = "2026-01-01T00:00:00+00:00"
    db.execute(
        "INSERT INTO jf_user(id, jellyfin_user_id, username, device_id, created_at) "
        "VALUES(1, 'jf-1', 'anna', 'dev', ?)", (now,))
    db.execute(
        "INSERT INTO jf_series_watch(id, source, media_type, external_id, title, "
        "audio_languages, subtitle_languages, created_by, created_at, updated_at) "
        "VALUES(7, 'streamingcommunity', 'tv', '77', 'Serie', '[]', '[]', 1, ?, ?)", (now, now))
    db.execute(
        "INSERT INTO jf_series_watch_subscriber(watch_id, user_id, created_at) VALUES(7, 1, ?)",
        (now,))
    db.execute(
        "INSERT INTO jf_watch_seen_episode(watch_id, episode_key, created_at) VALUES(7, 'S01E1', ?)",
        (now,))
    db.execute(
        "INSERT INTO jf_request(id, content_key, source, media_type, external_id, title, "
        "audio_languages, subtitle_languages, status, requested_by, created_at, updated_at, "
        "watch_id) VALUES(3, 'k', 'streamingcommunity', 'episode', '77', 'Serie', '[]', '[]', "
        "'completed', 1, ?, ?, 7)", (now, now))


def test_migration_5_keeps_the_watch_a_request_came_from(at_version):
    """It dropped jf_series_watch with foreign keys enforced, and the
    ON DELETE SET NULL on jf_request.watch_id fired for every request."""
    at_version(4)
    _seed_v4()

    db.run_migrations()

    assert db.query_one("PRAGMA user_version")[0] == len(db.MIGRATIONS)
    assert db.query_one("SELECT watch_id FROM jf_request WHERE id = 3")["watch_id"] == 7
    assert db.query_one("SELECT COUNT(*) AS n FROM jf_series_watch_subscriber")["n"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM jf_watch_seen_episode")["n"] == 1
    assert db.query_one("SELECT title FROM jf_series_watch WHERE id = 7")["title"] == "Serie"
    assert db.query("PRAGMA foreign_key_check") == []


def test_foreign_keys_are_enforced_again_after_migrating(at_version):
    at_version(4)
    _seed_v4()
    db.run_migrations()

    assert db.query_one("PRAGMA foreign_keys")[0] == 1
    # The rebuilt tables still point at each other: the link is live, not
    # merely declared.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO jf_watch_seen_episode(watch_id, episode_key, created_at) "
            "VALUES(999, 'S01E1', 'now')")
    db.execute("DELETE FROM jf_series_watch WHERE id = 7")
    assert db.query_one("SELECT watch_id FROM jf_request WHERE id = 3")["watch_id"] is None
    assert db.query_one("SELECT COUNT(*) AS n FROM jf_watch_seen_episode")["n"] == 0


def test_foreign_keys_come_back_on_even_when_a_migration_fails(at_version):
    at_version(4)
    full = db.MIGRATIONS
    db.MIGRATIONS = full[:4] + [["CREATE TABLE ok_so_far (id INTEGER)", "THIS IS NOT SQL"]]
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.run_migrations()
    finally:
        db.MIGRATIONS = full

    assert db.query_one("PRAGMA foreign_keys")[0] == 1
    assert db.query_one("PRAGMA user_version")[0] == 4
    # Rolled back whole, not half applied.
    assert db.query_one("SELECT 1 FROM sqlite_master WHERE name = 'ok_so_far'") is None


def test_a_migration_that_breaks_a_reference_is_rolled_back(at_version):
    at_version(4)
    _seed_v4()
    full = db.MIGRATIONS
    db.MIGRATIONS = full[:4] + [["DELETE FROM jf_user WHERE id = 1"]]
    try:
        with pytest.raises(sqlite3.IntegrityError, match="foreign key"):
            db.run_migrations()
    finally:
        db.MIGRATIONS = full

    assert db.query_one("PRAGMA user_version")[0] == 4
    assert db.query_one("SELECT COUNT(*) AS n FROM jf_user")["n"] == 1


def test_a_database_from_a_newer_version_is_refused(at_version):
    """It returned without a word and ran on a schema it did not know."""
    at_version(len(db.MIGRATIONS))
    db.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 2}")

    with pytest.raises(db.SchemaTooNew, match=f"v{len(db.MIGRATIONS) + 2}"):
        db.run_migrations()
    # Untouched.
    assert db.query_one("PRAGMA user_version")[0] == len(db.MIGRATIONS) + 2


def test_the_database_is_copied_before_it_is_migrated(at_version):
    path = at_version(4)
    _seed_v4()

    db.run_migrations()

    backup = path.with_name(path.name + ".bak-v4")
    assert backup.exists()
    copy = sqlite3.connect(str(backup))
    try:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == 4
        assert copy.execute("SELECT watch_id FROM jf_request WHERE id = 3").fetchone()[0] == 7
    finally:
        copy.close()


def test_an_earlier_backup_of_the_same_version_is_kept(at_version):
    path = at_version(4)
    backup = path.with_name(path.name + ".bak-v4")
    backup.write_bytes(b"the first, and so the better one")

    db.run_migrations()

    assert backup.read_bytes() == b"the first, and so the better one"


def test_no_backup_for_a_new_database_or_one_already_current(tmp_path):
    db.configure(tmp_path / "fresh.db")
    db.run_migrations()
    db.run_migrations()

    assert [p.name for p in tmp_path.iterdir() if ".bak-" in p.name] == []
    db.close_all()


def test_the_version_is_read_again_once_the_write_lock_is_held(at_version, monkeypatch):
    """Two processes starting together both read "v4"; the second must not
    apply migration 5 again when it finally gets the lock."""
    at_version(4)
    full = db.MIGRATIONS
    applied = []

    def other_process_got_there_first(conn, fresh):
        applied.append("v5")

    def never(conn, fresh):
        applied.append("again")

    real_backup = db._backup_before_migrating

    def backup_then_lose_the_race(conn, version):
        real_backup(conn, version)
        # Between this process reading v4 and taking the lock.
        conn.execute("PRAGMA user_version = 5")
        db.MIGRATIONS = full[:4] + [never]

    monkeypatch.setattr(db, "_backup_before_migrating", backup_then_lose_the_race)
    try:
        db.MIGRATIONS = full[:4] + [other_process_got_there_first]
        db.run_migrations()
    finally:
        db.MIGRATIONS = full

    assert applied == []


def test_a_transaction_is_rolled_back_by_any_way_out(tmp_path):
    """``except Exception`` let a KeyboardInterrupt leave the connection in an
    open write transaction, holding the write lock for the life of the thread."""
    class Stop(BaseException):
        pass

    with pytest.raises(Stop):
        with db.tx() as conn:
            conn.execute("INSERT INTO jf_setting(key, value) VALUES('k', 'v')")
            raise Stop()

    assert db.get_conn().in_transaction is False
    assert db.query_one("SELECT 1 FROM jf_setting WHERE key = 'k'") is None
    with db.tx() as conn:   # and the next one can begin
        conn.execute("INSERT INTO jf_setting(key, value) VALUES('k', 'v2')")
    assert db.query_one("SELECT value FROM jf_setting WHERE key = 'k'")["value"] == "v2"
