"""Regressions for the job manager's concurrency and lifecycle fixes.

Each race is forced rather than waited for: an Event, a Barrier or a patched
method holds one thread exactly where the other used to slip past it.
"""

import asyncio
import inspect
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import jobs as jobs_module
from app.jobs import JobManager
from app.progress import PROGRESS_QUEUE_MAX, post
from app.schedule import ScheduleStore


def _wait_for(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def jm():
    manager = JobManager()
    manager.update_max_concurrent(1)
    yield manager
    manager.cancel_all()
    manager._executor.shutdown(wait=False)


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "_library_roots", lambda: [os.path.realpath(tmp_path)])
    return tmp_path


def _old_file(path, age=3600):
    """A file that was in the library long before the test's download began."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"the good copy")
    past = time.time() - age
    os.utime(path, (past, past))
    return path


# ── 2. park/resume ────────────────────────────────────────────────────────────

def test_a_job_resumed_the_instant_it_parks_is_not_announced_as_finished(jm):
    """resume_batch() clears ``parked`` as soon as the job has parked; the
    parking thread used to read that as "not parked, so over" and told the
    listeners about a job that was queued again on another thread."""
    gate, heard = threading.Event(), []
    jm.add_listener(lambda job: heard.append((job.title, job.status)))

    def fake_download(name, **kwargs):
        gate.wait(timeout=3)
        return "ok"

    real_park = jm._park

    def park_then_resume(job, fn, args, kwargs):
        parked = real_park(job, fn, args, kwargs)
        if parked:
            # Exactly the window: parked, and its thread not yet out.
            assert jm.resume_batch("b1") == 1
        return parked

    jm._park = park_then_resume

    jobs = [jm._make_job(f"t{i}", "episode", batch_id="b1") for i in range(2)]
    jm._submit_job(jobs[0], fake_download, "t0")
    assert _wait_for(lambda: jobs[0].status == "running")
    jm._submit_job(jobs[1], fake_download, "t1")
    assert _wait_for(lambda: jobs[1].queue_seq is not None)

    assert jm.pause_batch("b1") == 1
    gate.set()
    assert _wait_for(lambda: all(j.status == "done" for j in jobs))
    assert _wait_for(lambda: len(heard) == 2)
    time.sleep(0.05)

    assert sorted(heard) == [("t0", "done"), ("t1", "done")]


def test_listeners_only_ever_hear_terminal_states(jm):
    """Pause, resume and cancel in a loop, against jobs racing to park."""
    heard = []
    jm.add_listener(lambda job: heard.append(job.status))
    gate = threading.Event()

    def fake_download(name, **kwargs):
        gate.wait(timeout=3)
        return "ok"

    jobs = [jm._make_job(f"t{i}", "episode", batch_id="b1") for i in range(6)]
    for i, job in enumerate(jobs):
        jm._submit_job(job, fake_download, f"t{i}")
    for _ in range(40):
        jm.pause_batch("b1")
        jm.resume_batch("b1")
    gate.set()

    assert _wait_for(lambda: len(heard) == len(jobs))
    time.sleep(0.05)
    assert heard == ["done"] * len(jobs)


# ── 3. cancelled, then "done" ─────────────────────────────────────────────────

def test_a_cancel_landing_as_the_download_ends_stays_cancelled(jm, library):
    gate, heard = threading.Event(), []
    jm.add_listener(lambda job: heard.append(job.status))
    video = library / "Film (2020)" / "Film.mkv"

    def ignores_the_cancel(**kwargs):
        gate.wait(timeout=3)
        video.parent.mkdir()
        video.write_bytes(b"x")
        return str(video)

    job = jm._make_job("Film", "film")
    jm._submit_job(job, ignores_the_cancel)
    assert _wait_for(lambda: job.status == "running")

    assert jm.cancel(job.job_id)
    gate.set()
    assert _wait_for(lambda: heard)
    time.sleep(0.05)

    assert job.status == "cancelled"
    assert job.output_path is None
    assert heard == ["cancelled"]
    # What the cancelled attempt wrote goes with it.
    assert not video.exists() and not video.parent.exists()


def test_a_download_that_fails_because_it_was_cancelled_is_cancelled(jm):
    """Torn down mid-flight, it can raise anything - not only the tidy
    DownloadCancelledError - and used to end as "error" over "cancelled"."""
    gate, heard = threading.Event(), []
    jm.add_listener(lambda job: heard.append(job.status))

    def dies_ugly(**kwargs):
        gate.wait(timeout=3)
        raise RuntimeError("ffmpeg exited with code 255")

    job = jm._make_job("Film", "film")
    jm._submit_job(job, dies_ugly)
    assert _wait_for(lambda: job.status == "running")
    assert jm.cancel(job.job_id)
    gate.set()

    assert _wait_for(lambda: heard)
    assert (job.status, heard) == ("cancelled", ["cancelled"])


# ── 1. a failed re-download and the copy already in the library ───────────────

def test_a_cancelled_redownload_leaves_the_copy_that_was_there(jm, library):
    """The download found the title already in the library and returned its
    path without writing it. Cancelled as it ended, it used to delete it."""
    gate = threading.Event()
    good = _old_file(library / "Film (2020)" / "Film.mkv")

    def finds_it_there(**kwargs):
        gate.wait(timeout=3)
        return str(good)

    job = jm._make_job("Film", "film")
    jm._submit_job(job, finds_it_there)
    assert _wait_for(lambda: job.status == "running")
    assert jm.cancel(job.job_id)
    gate.set()
    assert _wait_for(lambda: job.finished_at is not None)

    assert job.status == "cancelled"
    assert good.read_bytes() == b"the good copy"


def test_purging_a_batch_leaves_what_it_did_not_write(jm, library):
    """Same for a batch cancelled with its files, on both paths: the episode
    still running and the one already finished."""
    gate = threading.Event()
    old_one = _old_file(library / "Serie (2020)" / "Season 01" / "Serie S01E01.mkv")
    old_two = _old_file(library / "Serie (2020)" / "Season 01" / "Serie S01E02.mkv")
    new_three = library / "Serie (2020)" / "Season 01" / "Serie S01E03.mkv"

    def fake_download(path, wait, write, **kwargs):
        if wait:
            gate.wait(timeout=3)
        if write:
            path.write_bytes(b"x")
        return str(path)

    done_old = jm._make_job("e1", "episode", batch_id="b1")
    done_new = jm._make_job("e3", "episode", batch_id="b1")
    running = jm._make_job("e2", "episode", batch_id="b1")
    jm._submit_job(done_old, fake_download, old_one, False, False)
    assert _wait_for(lambda: done_old.status == "done")
    jm._submit_job(done_new, fake_download, new_three, False, True)
    assert _wait_for(lambda: done_new.status == "done")
    jm._submit_job(running, fake_download, old_two, True, False)
    assert _wait_for(lambda: running.status == "running")

    assert (done_old.output_created, done_new.output_created) == (False, True)
    assert jm.cancel_batch("b1", delete_files=True) == (1, 2)
    gate.set()
    assert _wait_for(lambda: running.finished_at is not None)

    assert old_one.exists() and old_two.exists()
    assert not new_three.exists()


def test_written_since_tells_an_old_file_from_a_new_one(tmp_path):
    started = time.time()
    old = _old_file(tmp_path / "old.mkv")
    new = tmp_path / "new.mkv"
    new.write_bytes(b"x")

    assert jobs_module._written_since(str(new), started)
    assert not jobs_module._written_since(str(old), started)
    # Rewritten by the attempt: now it is the attempt's.
    old.write_bytes(b"partial")
    assert jobs_module._written_since(str(old), started)
    # When in doubt, the file stays.
    assert not jobs_module._written_since(str(new), None)
    assert not jobs_module._written_since(str(tmp_path / "missing.mkv"), started)


# ── 4. fire_now / scheduler ───────────────────────────────────────────────────

@pytest.fixture
def scheduled(jm, tmp_path, monkeypatch):
    """A manager with a schedule store and a call builder that needs no network."""
    store = ScheduleStore(tmp_path / "schedule.json")
    jm.set_schedule_store(store)
    runs = []

    def fake_download(*args, **kwargs):
        runs.append(args)
        return "/lib/film.mkv"

    monkeypatch.setattr(jm, "_build_call", lambda type_, params, job: (fake_download, (), {}))
    jm.runs = runs
    return jm


def _schedule(jm, when=None):
    when = when or datetime.now(timezone.utc) + timedelta(days=1)
    job_id = jm.schedule_film(1, "Film", "example.test", when)
    return jm.get(job_id)


def test_two_fire_now_at_once_start_one_download(scheduled, monkeypatch):
    jm = scheduled
    job = _schedule(jm)
    both_in, release = threading.Barrier(2, timeout=2), threading.Event()
    real_build = jm._build_call

    def slow_build(type_, params, job):
        # Hold whoever got this far until the other has had every chance to.
        release.wait(timeout=2)
        return real_build(type_, params, job)

    monkeypatch.setattr(jm, "_build_call", slow_build)
    results = []

    def click():
        try:
            both_in.wait()
        except threading.BrokenBarrierError:
            pass
        results.append(jm.fire_now(job.job_id))

    clicks = [threading.Thread(target=click) for _ in range(2)]
    for t in clicks:
        t.start()
    time.sleep(0.2)
    release.set()
    for t in clicks:
        t.join(timeout=3)

    assert _wait_for(lambda: job.status == "done")
    time.sleep(0.05)
    assert sorted(results) == [False, True]
    assert len(jm.runs) == 1


def test_a_job_already_fired_is_not_fired_again(scheduled):
    jm = scheduled
    job = _schedule(jm)
    entry = jm._schedule_store.get_by_schedule_id(job.schedule_id)

    assert jm._fire_job(job, entry["type"], entry["params"]) is True
    assert jm._fire_job(job, entry["type"], entry["params"]) is False
    assert _wait_for(lambda: job.status == "done")
    assert len(jm.runs) == 1


def test_a_cancelled_scheduled_job_cannot_be_fired(scheduled):
    jm = scheduled
    job = _schedule(jm)
    assert jm.cancel(job.job_id)

    assert jm.fire_now(job.job_id) is False
    time.sleep(0.05)
    assert job.status == "cancelled" and jm.runs == []


def test_the_schedule_file_is_not_written_under_the_manager_lock(scheduled, monkeypatch):
    jm = scheduled
    job = _schedule(jm)
    held = []
    real = jm._schedule_store.mark_fired
    monkeypatch.setattr(jm._schedule_store, "mark_fired",
                        lambda sid: (held.append(jm._lock.locked()), real(sid))[1])

    assert jm.fire_now(job.job_id)

    assert held == [False]


# ── 13. fragile schedules ─────────────────────────────────────────────────────

def test_scheduled_and_paused_jobs_count_as_work(scheduled):
    jm = scheduled
    assert not jm.has_active_work()
    job = _schedule(jm)
    assert jm.has_active_work()
    jm.cancel(job.job_id)
    assert not jm.has_active_work()

    paused = jm._make_job("p", "episode", batch_id="b1")
    paused.status = "paused"
    jm._jobs[paused.job_id] = paused
    assert jm.has_active_work()


def test_a_call_that_cannot_be_built_fails_the_job_after_a_few_tries(scheduled, monkeypatch):
    jm = scheduled
    heard = []
    jm.add_listener(lambda job: heard.append(job.status))
    job = _schedule(jm, datetime.now(timezone.utc) - timedelta(minutes=1))
    attempts = []

    def broken(type_, params, job):
        attempts.append(1)
        raise RuntimeError("libreria non raggiungibile")

    monkeypatch.setattr(jm, "_build_call", broken)

    for _ in range(jobs_module.MAX_FIRE_FAILURES):
        jm._scheduler_tick()
        assert job.status == "scheduled"   # still to be tried again
    jm._scheduler_tick()
    assert job.status == "error" and "libreria" in job.error
    jm._scheduler_tick()                   # and no further

    assert len(attempts) == jobs_module.MAX_FIRE_FAILURES + 1
    assert heard == ["error"]
    assert jm._schedule_store.get_by_schedule_id(job.schedule_id)["outcome"] == "error"


def test_a_schedule_already_due_is_scheduled_not_stuck_queued(scheduled):
    """A time already past made the job "queued" - with nothing running it."""
    jm = scheduled
    job = _schedule(jm, datetime.now(timezone.utc) - timedelta(hours=1))
    assert job.status == "scheduled"

    jm._scheduler_tick()

    assert _wait_for(lambda: job.status == "done")
    assert len(jm.runs) == 1


def _restart(jm, tmp_path):
    jm._jobs.clear()
    jm.set_schedule_store(ScheduleStore(tmp_path / "schedule.json"))
    jm.load_scheduled_from_store()
    return list(jm._jobs.values())


def test_an_entry_due_while_the_server_was_down_runs_after_the_restart(scheduled, tmp_path):
    jm = scheduled
    _schedule(jm, datetime.now(timezone.utc) - timedelta(hours=1))

    [restored] = _restart(jm, tmp_path)
    assert restored.status == "scheduled"
    jm._scheduler_tick()

    assert _wait_for(lambda: restored.status == "done")


def test_a_schedule_interrupted_by_a_restart_comes_back_as_failed(scheduled, tmp_path):
    """Fired, and the process stopped before it ended: it used to be skipped
    at the next start ("already dispatched") and simply vanish."""
    jm = scheduled
    job = _schedule(jm)
    jm._schedule_store.mark_fired(job.schedule_id)     # fired, never finished

    [restored] = _restart(jm, tmp_path)

    assert restored.status == "error"
    assert "riavvio" in restored.error
    assert restored.schedule_id == job.schedule_id
    assert jm._schedule_store.get_by_schedule_id(job.schedule_id)["outcome"] == "interrupted"
    assert restored.call is not None                   # "Riprova" works on it
    # Still there after another restart, until someone dismisses it.
    [again] = _restart(jm, tmp_path)
    assert again.status == "error"
    assert jm.dismiss(again.job_id)
    assert _restart(jm, tmp_path) == []


def test_a_schedule_that_ran_is_not_restored(scheduled, tmp_path):
    jm = scheduled
    job = _schedule(jm)
    assert jm.fire_now(job.job_id)
    assert _wait_for(lambda: job.finished_at is not None)
    assert jm._schedule_store.get_by_schedule_id(job.schedule_id)["outcome"] == "done"

    assert _restart(jm, tmp_path) == []
    assert jm._schedule_store.list_all() == []


def test_a_cancelled_schedule_does_not_come_back_after_a_restart(scheduled, tmp_path):
    jm = scheduled
    job = _schedule(jm)
    assert jm.cancel(job.job_id)

    assert _restart(jm, tmp_path) == []


def test_an_entry_fired_before_outcomes_existed_is_left_alone(scheduled, tmp_path):
    """No ``outcome`` key at all: written by an older version, which did not
    say how it ended. Not reported as interrupted - most of them ended fine."""
    jm = scheduled
    job = _schedule(jm)
    with jm._schedule_store._lock:
        entry = jm._schedule_store._entries[0]
        entry["fired"] = True
        del entry["outcome"]
        jm._schedule_store._save()

    assert _restart(jm, tmp_path) == []
    assert jm._schedule_store.get_by_schedule_id(job.schedule_id) is not None


# ── 15. backoff ───────────────────────────────────────────────────────────────

def test_the_wait_doubles_with_each_retry_give_or_take_a_fifth(monkeypatch):
    monkeypatch.setattr(jobs_module, "AUTO_RETRY_DELAY", 60)
    for retry, base in ((1, 60), (2, 120), (3, 240)):
        delays = [jobs_module._retry_delay(retry) for _ in range(200)]
        assert base * 0.8 <= min(delays) and max(delays) <= base * 1.2
        assert max(delays) - min(delays) > base * 0.1    # it does vary


def test_each_failure_waits_longer_than_the_last(jm, monkeypatch):
    monkeypatch.setattr(jobs_module, "AUTO_RETRIES", 3)
    monkeypatch.setattr(jobs_module, "AUTO_RETRY_DELAY", 0.01)
    monkeypatch.setattr(jobs_module.random, "uniform", lambda a, b: 1.0)
    waits = []

    def broken():
        raise RuntimeError("503")

    job = jm._make_job("Film", "film")
    real_wait = job.cancel_event.wait
    job.cancel_event.wait = lambda timeout=None: (waits.append(timeout), real_wait(timeout))[1]
    jm._submit_job(job, broken)

    assert _wait_for(lambda: job.status == "error")
    assert waits == pytest.approx([0.01, 0.02, 0.04])
    assert job.retries == 3


# ── 16. memory ────────────────────────────────────────────────────────────────

def test_a_job_s_progress_queue_is_bounded_and_keeps_the_newest():
    async def scenario():
        loop = asyncio.get_running_loop()
        job = JobManager()._make_job("Film", "film")
        assert job.progress_queue.maxsize == PROGRESS_QUEUE_MAX
        for n in range(PROGRESS_QUEUE_MAX + 50):
            assert post(job.progress_queue, loop, {"type": "progress", "n": n})
        post(job.progress_queue, loop, {"type": "done"})
        await asyncio.sleep(0.05)

        assert job.progress_queue.qsize() == PROGRESS_QUEUE_MAX
        frames = [job.progress_queue.get_nowait() for _ in range(PROGRESS_QUEUE_MAX)]
        assert frames[0]["n"] == 51              # the oldest went
        assert frames[-1] == {"type": "done"}    # the terminal one never does

    asyncio.run(scenario())


def test_posting_without_a_loop_is_dropped_not_raised():
    assert post(asyncio.Queue(maxsize=1), None, {"type": "done"}) is False


def test_finished_jobs_expire_after_a_day_and_active_ones_never(jm):
    gate = threading.Event()
    done = jm._make_job("done", "film")
    failed = jm._make_job("failed", "film")
    running = jm._make_job("running", "film")
    jm.update_max_concurrent(3)

    def fails():
        raise RuntimeError("503")

    jm._submit_job(done, lambda: "/lib/a.mkv")
    jm._submit_job(failed, fails)
    jm._submit_job(running, lambda: gate.wait(timeout=3) and "/lib/b.mkv")
    assert _wait_for(lambda: done.finished_at and failed.finished_at)
    assert _wait_for(lambda: running.status == "running")

    # A finished job no longer carries its call - unless it can be retried.
    assert done.call is None and failed.call is not None
    assert running.call is not None

    now = datetime.now(timezone.utc)
    assert jm.expire_finished(now + timedelta(hours=23)) == 0
    # Old enough by its creation, but still running: never touched.
    running.created_at = now - timedelta(days=3)
    assert jm.expire_finished(now + timedelta(hours=25)) == 2

    assert jm.get(done.job_id) is None and jm.get(failed.job_id) is None
    assert jm.get(running.job_id) is running
    gate.set()
    assert _wait_for(lambda: running.status == "done")


# ── 5. shutdown and the staging directories ───────────────────────────────────

def test_shutdown_cancels_the_downloads_and_waits_for_them():
    manager = JobManager()
    manager.update_max_concurrent(1)
    heard = []
    manager.add_listener(lambda job: heard.append((job.title, job.status)))

    def cancellable(cancel_event=None, **kwargs):
        from app.progress import DownloadCancelledError
        while not cancel_event.wait(0.01):
            pass
        raise DownloadCancelledError("stop")

    jobs = [manager._make_job(f"t{i}", "film") for i in range(3)]
    for job in jobs:
        manager._submit_job(job, cancellable, cancel_event=job.cancel_event)
    assert _wait_for(lambda: any(j.status == "running" for j in jobs))

    assert manager.shutdown(timeout=5) is True

    assert {j.status for j in jobs} == {"cancelled"}
    assert sorted(heard) == [(f"t{i}", "cancelled") for i in range(3)]
    assert not manager._workers
    with pytest.raises(RuntimeError):
        manager._executor.submit(lambda: None)


def test_shutdown_does_not_wait_forever_for_a_stuck_worker():
    manager = JobManager()
    release = threading.Event()
    job = manager._make_job("stuck", "film")
    manager._submit_job(job, lambda: release.wait(timeout=5) and None)
    assert _wait_for(lambda: job.status == "running")
    try:
        started = time.monotonic()
        assert manager.shutdown(timeout=0.2) is False
        assert time.monotonic() - started < 2
    finally:
        release.set()


def test_startup_clears_the_staging_directories_and_nothing_else(tmp_path, monkeypatch):
    tmp_root = tmp_path / "tmp"
    outside = tmp_path / "outside"
    for folder in (tmp_root, outside):
        folder.mkdir()
    monkeypatch.setattr(jobs_module, "TMP_DIR", tmp_root)

    stale = tmp_root / str(uuid.uuid4())
    kept = tmp_root / str(uuid.uuid4())
    for folder in (stale, kept):
        folder.mkdir()
        (folder / "0001.ts").write_bytes(b"x")
    lock = tmp_root / "data.json.lock"
    lock.write_text("")
    foreign_dir = tmp_root / "not-a-job"
    foreign_dir.mkdir()
    foreign_file = tmp_root / str(uuid.uuid4())     # named like a job, not a folder
    foreign_file.write_text("x")
    elsewhere = outside / str(uuid.uuid4())
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_text("x")

    assert jobs_module.purge_stale_temp_dirs(frozenset({kept.name})) == 1

    assert not stale.exists()
    assert kept.exists() and (kept / "0001.ts").exists()
    assert lock.exists() and foreign_dir.exists() and foreign_file.exists()
    assert tmp_root.exists()
    assert (elsewhere / "precious.txt").exists()


def test_a_link_out_of_the_staging_directory_is_not_followed(tmp_path, monkeypatch):
    tmp_root = tmp_path / "tmp"
    outside = tmp_path / "outside"
    for folder in (tmp_root, outside):
        folder.mkdir()
    (outside / "precious.txt").write_text("x")
    link = tmp_root / str(uuid.uuid4())
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")
    monkeypatch.setattr(jobs_module, "TMP_DIR", tmp_root)

    assert jobs_module.purge_stale_temp_dirs() == 0

    assert (outside / "precious.txt").exists()


def test_a_missing_staging_directory_is_nothing_to_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "TMP_DIR", tmp_path / "never-made")
    assert jobs_module.purge_stale_temp_dirs() == 0


def test_the_lifespan_clears_staging_first_and_stops_the_downloads_last(monkeypatch):
    import app.main as main_module

    order = []
    manager = main_module.job_manager
    monkeypatch.setattr(main_module, "check_writable_paths", lambda: order.append("check"))
    monkeypatch.setattr(manager, "purge_stale_temp", lambda: order.append("purge") or 0)
    monkeypatch.setattr(main_module.db, "run_migrations", lambda: order.append("migrate"))
    monkeypatch.setattr(main_module.requests_service, "register_job_listener", lambda: None)
    monkeypatch.setattr(main_module.requests_service, "reconcile_orphaned_requests", lambda: None)
    monkeypatch.setattr(main_module, "ScheduleStore", lambda path: object())
    monkeypatch.setattr(manager, "set_schedule_store", lambda store: None)
    monkeypatch.setattr(manager, "load_scheduled_from_store", lambda: None)
    monkeypatch.setattr(manager, "set_loop", lambda loop: None)
    monkeypatch.setattr(manager, "shutdown",
                        lambda timeout: order.append(("shutdown", timeout)) or True)

    async def run():
        async with main_module.lifespan(main_module.app):
            order.append("serving")

    asyncio.run(run())

    assert order == ["check", "purge", "migrate", "serving",
                     ("shutdown", main_module.SHUTDOWN_TIMEOUT)]


# ── 9. no import of the routers ───────────────────────────────────────────────

def test_the_job_manager_reads_the_libraries_without_the_routers(tmp_path, monkeypatch):
    import json

    from app import config

    data = tmp_path / "data.json"
    data.write_text(json.dumps({"libraries": [{"type": "film", "path": str(tmp_path / "f")}]}))
    monkeypatch.setattr(config, "DATA_FILE", data)

    assert jobs_module._get_library_path("film") == str(tmp_path / "f")
    assert jobs_module._get_library_path("tv") == str(config.download_dir(config.VIDEOS_DIR))
    assert os.path.realpath(tmp_path / "f") in jobs_module._library_roots()
    assert "app.routers" not in inspect.getsource(jobs_module)
