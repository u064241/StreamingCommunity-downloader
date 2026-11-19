"""An interrupted download leaves no partial files behind.

RaiPlay: yt-dlp writes ``.part``/``.ytdl``/``.fNNN`` files next to the final
video, in the library. A cancel or a final failure removes them; a finished
video never matches. Jobs: a paused job cancelled while parked removes its
staging directory at once.
"""
import contextlib
import os
import threading
import time

import pytest

from app import jobs as jobs_module
from app.core import raiplay
from app.jobs import JobManager
from app.progress import DownloadCancelledError


def _touch(path):
    path.write_bytes(b"x")
    return path


def test_discard_removes_only_partials_of_the_stem(tmp_path):
    stem = "Titolo S01E02"
    partials = [_touch(tmp_path / n) for n in (
        f"{stem}.mp4.part", f"{stem}.mp4.ytdl", f"{stem}.temp.mp4",
        f"{stem}.f137.mp4", f"{stem}.f251.webm.part")]
    keep = [_touch(tmp_path / n) for n in (
        f"{stem}.mp4", "Altro titolo.mp4.part", f"{stem}.f.mp4")]

    removed = raiplay.discard_partials(tmp_path, stem)

    assert sorted(removed) == sorted(str(p) for p in partials)
    assert all(not p.exists() for p in partials)
    assert all(p.exists() for p in keep)


def _stub_engine(monkeypatch, attempt):
    from app.providers.raiplay import parser
    monkeypatch.setattr(parser, "video_page", lambda url: "https://www.raiplay.it/video.html")
    monkeypatch.setattr(raiplay.ytdlp_update, "executable", lambda: "yt-dlp.exe")
    monkeypatch.setattr(raiplay.ytdlp_update, "in_use", contextlib.nullcontext)
    monkeypatch.setattr(raiplay.ytdlp_update, "require_executable", lambda: "yt-dlp.exe")
    monkeypatch.setattr(raiplay, "_attempt", attempt)


def test_a_cancelled_download_removes_its_partials(tmp_path, monkeypatch):
    def attempt(exe, url, target_dir, stem, work_dir, progress_factory, cancel_event):
        _touch(target_dir / f"{stem}.mp4.part")
        raise DownloadCancelledError()

    _stub_engine(monkeypatch, attempt)
    lib = tmp_path / "lib"
    with pytest.raises(DownloadCancelledError):
        raiplay.download("https://www.raiplay.it/video.html", "Titolo", str(lib))
    assert list(lib.iterdir()) == []


def test_a_final_failure_removes_partials(tmp_path, monkeypatch):
    def attempt(exe, url, target_dir, stem, work_dir, progress_factory, cancel_event):
        _touch(target_dir / f"{stem}.f137.mp4")
        raise raiplay.YtdlpEngineError("connessione persa")

    _stub_engine(monkeypatch, attempt)
    monkeypatch.setattr(raiplay.ytdlp_update, "update_for_retry", lambda: False)
    lib = tmp_path / "lib"
    with pytest.raises(raiplay.YtdlpEngineError):
        raiplay.download("https://www.raiplay.it/video.html", "Titolo", str(lib))
    assert list(lib.iterdir()) == []


def test_a_retry_keeps_partials_for_resume(tmp_path, monkeypatch):
    calls = []

    def attempt(exe, url, target_dir, stem, work_dir, progress_factory, cancel_event):
        calls.append((target_dir / f"{stem}.mp4.part").exists())
        if len(calls) == 1:
            _touch(target_dir / f"{stem}.mp4.part")
            raise raiplay.YtdlpEngineError("errore transitorio")
        return str(_touch(target_dir / f"{stem}.mp4"))

    _stub_engine(monkeypatch, attempt)
    monkeypatch.setattr(raiplay.ytdlp_update, "update_for_retry", lambda: True)
    lib = tmp_path / "lib"
    out = raiplay.download("https://www.raiplay.it/video.html", "Titolo", str(lib))
    assert out.endswith("Titolo.mp4")
    assert (lib / "Titolo.mp4").exists()
    # The retry found the first attempt's partial still there, to resume from.
    assert calls == [False, True]


def _wait_for(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_cancelling_a_parked_job_removes_its_staging_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "TMP_DIR", tmp_path)
    jm = JobManager()
    jm.update_max_concurrent(1)
    gate = threading.Event()

    def fake_download(name, **kwargs):
        gate.wait(timeout=2)
        return "ok"

    try:
        jobs = [jm._make_job(f"t{i}", "episode", batch_id="b1") for i in range(2)]
        for i, job in enumerate(jobs):
            jm._submit_job(job, fake_download, f"t{i}")
        assert _wait_for(lambda: sum(j.status == "running" for j in jobs) == 1)
        assert jm.pause_batch("b1") == 1
        waiting = next(j for j in jobs if j.status != "running")
        assert _wait_for(lambda: waiting.parked is not None)

        staging = tmp_path / waiting.job_id
        staging.mkdir()
        _touch(staging / "0001.ts.part")

        assert jm.cancel(waiting.job_id) is True
        assert not staging.exists()
        assert waiting.status == "cancelled"
    finally:
        gate.set()
        jm._executor.shutdown(wait=False)
