"""Per-phase timing of a job is logged once per phase, when the phase ends."""

import logging

from app import jobs


def test_phase_durations_are_logged_once_each(monkeypatch, caplog):
    # One reading per phase change: video starts at 0, audio_ita at 12.5, finish at 40.
    clock = iter([0.0, 12.5, 40.0])
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(clock))
    job_id = "phase-test-1"
    jobs._PHASE_TIMES.pop(job_id, None)
    with caplog.at_level(logging.INFO, logger=jobs.logger.name):
        jobs._phase_mark(job_id, "video")       # t=0
        jobs._phase_mark(job_id, "video")       # same phase: nothing logged
        jobs._phase_mark(job_id, "audio_ita")   # video ended at 12.5
        jobs._phase_finish(job_id, "done")      # audio_ita ended at 40.0
    lines = [r.getMessage() for r in caplog.records if "fase" in r.getMessage()]
    assert lines == [
        "job phase-te fase video: 12.5 s",
        "job phase-te fase audio_ita: 27.5 s (done)",
    ]
    assert job_id not in jobs._PHASE_TIMES


def test_phase_mark_ignores_empty_phase(monkeypatch):
    job_id = "phase-test-2"
    jobs._PHASE_TIMES.pop(job_id, None)
    jobs._phase_mark(job_id, None)
    assert job_id not in jobs._PHASE_TIMES
