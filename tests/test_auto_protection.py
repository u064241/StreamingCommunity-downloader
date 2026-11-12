"""Automatic protection: a source pause holds the download queue, then lets go.

When source_budget opens a backoff (403/429/503, or the segment pipeline
pushing back) no waiting job gets a download slot until it ends; then the line
starts again by itself. Nothing is cancelled or failed. More than three pauses
in an hour switch to the conservative preset. Also here: Ctrl+C closing the
panel without a traceback (main.run_server).
"""

import threading
import time

import pytest

from app import config
from app.core import source_budget
from app.jobs import JobManager, PROTECTION_MAX_BACKOFFS


def _wait_for(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def clock(monkeypatch):
    """source_budget's clock, moved by hand: no test waits out a real pause."""
    now = [time.time()]
    monkeypatch.setattr(source_budget, "_now", lambda: now[0])
    return now


@pytest.fixture
def jm(monkeypatch):
    # The app-wide manager listens to the same pauses: keep what it changes
    # from leaking into later tests.
    from app.jobs import job_manager
    monkeypatch.setattr(job_manager._slots, "_limit", job_manager._slots._limit)
    monkeypatch.setattr(job_manager, "_protection_downgraded_at", job_manager._protection_downgraded_at)
    manager = JobManager()
    manager.update_max_concurrent(2)
    yield manager
    manager.cancel_all()
    manager._executor.shutdown(wait=False)


def test_the_queue_holds_during_a_backoff_and_resumes_by_itself(jm, clock):
    calls = []

    def fake_download(name, **kwargs):
        calls.append(name)
        return "ok"

    until = source_budget.report_pushback("HTTP 429 test")
    status = jm.protection_status()
    assert status["paused"] is True
    assert status["message"].startswith("Sorgente in pausa fino alle ")
    assert status["message"].endswith("la coda riprenderà automaticamente")

    jobs = [jm._make_job(f"t{i}", "film") for i in range(2)]
    for i, job in enumerate(jobs):
        jm._submit_job(job, fake_download, f"t{i}")
    assert _wait_for(lambda: all(j.queue_seq is not None for j in jobs))
    time.sleep(0.2)
    # Held: queued, not started, and certainly not failed or cancelled.
    assert calls == []
    assert all(j.status == "queued" for j in jobs)
    assert jm.protection_status()["queued_jobs"] == 2

    clock[0] = until + 1          # the pause is over
    jm._slots.wake()              # what the timed wait would do on its own
    assert _wait_for(lambda: all(j.status == "done" for j in jobs))
    assert sorted(calls) == ["t0", "t1"]
    assert jm.protection_status()["paused"] is False


def test_the_held_line_wakes_on_its_own_when_the_pause_ends(jm, monkeypatch):
    """No wake() from outside: the head's wait is timed to the pause's end."""
    from app.jobs import DownloadSlots
    monkeypatch.setattr(DownloadSlots, "HOLD_RECHECK", 0.05)
    source_budget.report_pushback("HTTP 503 test")
    job = jm._make_job("t", "film")
    jm._submit_job(job, lambda **kwargs: "ok")
    time.sleep(0.2)
    assert job.status == "queued"
    source_budget.reset()         # the pause ends (as it would by time)
    assert _wait_for(lambda: job.status == "done")


def test_a_running_download_is_not_interrupted_by_a_pause(jm):
    gate, started = threading.Event(), threading.Event()

    def fake_download(**kwargs):
        started.set()
        gate.wait(timeout=2)
        return "ok"

    job = jm._make_job("running", "film")
    jm._submit_job(job, fake_download)
    assert started.wait(2)
    source_budget.report_pushback("download pipeline")
    gate.set()
    assert _wait_for(lambda: job.status == "done")


def test_a_held_job_can_still_be_cancelled(jm):
    source_budget.report_pushback("HTTP 429 test")
    job = jm._make_job("t", "film")
    jm._submit_job(job, lambda **kwargs: "ok")
    assert _wait_for(lambda: job.queue_seq is not None)
    assert jm.cancel(job.job_id)
    assert _wait_for(lambda: job.queue_seq is None)
    assert job.status == "cancelled"


def test_more_than_three_pauses_in_an_hour_switch_to_conservative(jm, clock):
    config.save_settings({**config.get_settings(), **config.preset_values("fast")})
    jm.update_max_concurrent(3)
    for _ in range(PROTECTION_MAX_BACKOFFS):
        until = source_budget.report_pushback("HTTP 429 test")
        clock[0] = until + 1      # each pause ends; the next refusal opens another
    # Three pauses: not yet.
    assert config.matching_preset(config.get_settings()) == "fast"
    assert jm.protection_status()["downgraded_at"] is None

    source_budget.report_pushback("HTTP 429 test")
    assert config.matching_preset(config.get_settings()) == "conservative"
    assert jm._slots._limit == 1
    status = jm.protection_status()
    assert status["downgraded_at"] is not None
    assert status["backoffs_last_hour"] == PROTECTION_MAX_BACKOFFS + 1


def test_pauses_spread_over_more_than_an_hour_do_not_downgrade(jm, clock):
    config.save_settings({**config.get_settings(), **config.preset_values("fast")})
    for _ in range(PROTECTION_MAX_BACKOFFS + 2):
        source_budget.report_pushback("HTTP 429 test")
        clock[0] += 3600 + 1      # well apart
    assert config.matching_preset(config.get_settings()) == "fast"


def test_a_restart_starts_from_no_backoff(jm):
    """The backoff is in memory: a restart (here, reset()) is a clean slate,
    and a fresh refusal opens a new pause from the first step."""
    source_budget.report_pushback("HTTP 429 test")
    source_budget.reset()
    assert source_budget.backoff_until() is None
    assert jm.protection_status()["paused"] is False
    until = source_budget.report_pushback("HTTP 429 test")
    assert until - source_budget._now() == pytest.approx(source_budget.BACKOFF_STEPS[0], abs=2)


# ── Ctrl+C ────────────────────────────────────────────────────────────────────

def test_ctrl_c_closes_the_panel_without_a_traceback(capsys):
    import main

    class Server:
        def run(self):
            raise KeyboardInterrupt

    assert main.run_server(Server()) == 0
    out, err = capsys.readouterr()
    assert "Pannello chiuso" in out
    assert "Traceback" not in out + err


def test_a_normal_stop_exits_zero_silently(capsys):
    import main

    class Server:
        def run(self):
            return None

    assert main.run_server(Server()) == 0
    assert "Pannello chiuso" not in capsys.readouterr().out
