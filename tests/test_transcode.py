from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from app import config
from app.core import transcode
from app.jobs import _worth_retrying
from app.progress import DownloadCancelledError
from app.auth.permissions import Permission
from tests.conftest import do_setup, restrict_local_user


def settings(**overrides):
    return {"video_conversion":"h264", "video_encoder":"auto", "video_quality":"balanced", "video_cpu_fallback":True, **overrides}


def available(monkeypatch, backends):
    monkeypatch.setattr(transcode, "capabilities", lambda: {"encoders":[
        {"backend":backend,"codec":"h264","available":True} for backend in backends]})


def test_original_mode_does_not_probe_or_encode(monkeypatch):
    def forbidden(*a): raise AssertionError("No encoding expected")
    monkeypatch.setattr(transcode, "capabilities", forbidden)
    assert transcode.convert_video("original.mkv",settings(video_conversion="copy")) == "original.mkv"


def test_auto_selects_tested_gpu_and_optional_cpu_fallback(monkeypatch):
    available(monkeypatch,["qsv","cpu"])
    assert transcode.select_backend(settings()) == "qsv"
    available(monkeypatch,["cpu"])
    assert transcode.select_backend(settings()) == "cpu"
    with pytest.raises(transcode.TranscodeError):
        transcode.select_backend(settings(video_cpu_fallback=False))


@pytest.fixture
def original(tmp_path, monkeypatch):
    directory = tmp_path/"media"
    directory.mkdir()
    path=directory/"original.mkv"
    path.write_bytes(b"original video")
    available(monkeypatch,["nvenc","cpu"])
    monkeypatch.setattr(transcode,"_duration",lambda path:10)
    return path


def test_failed_conversion_preserves_original_and_cleans_staging(original,monkeypatch):
    def fail(path,staged,*args):
        staged.write_bytes(b"partial")
        raise transcode.TranscodeError("device unavailable")
    monkeypatch.setattr(transcode,"_encode",fail)
    with pytest.raises(transcode.TranscodeError):
        transcode.convert_video(str(original),settings(video_cpu_fallback=False))
    assert original.read_bytes() == b"original video"
    assert list(original.parent.iterdir()) == [original]
    assert not _worth_retrying(transcode.TranscodeError("not a download failure"))


def test_gpu_failure_retries_only_conversion_on_cpu(original,monkeypatch):
    calls=[]
    progress = []
    class Bar:
        def emit_status(self, phase):
            progress.append(phase)
        def emit_conversion(self, seconds, duration, backend, elapsed):
            progress.append((seconds, backend))

    def encode(path,staged,options,backend,*args):
        calls.append(backend)
        staged.write_bytes(b"converted video")
        if backend == "nvenc": raise transcode.TranscodeError("GPU busy")
    monkeypatch.setattr(transcode,"_encode",encode)
    assert transcode.convert_video(str(original), settings(), lambda **kw: Bar()) == str(original)
    assert calls == ["nvenc","cpu"]
    assert progress == ["converting", (0, "NVIDIA NVENC"), "converting", (0, "CPU"), (10, "CPU")]
    assert original.read_bytes() == b"converted video"
    assert list(original.parent.iterdir()) == [original]


def test_cancellation_after_encode_does_not_replace_original(original,monkeypatch):
    event=threading.Event()
    def encode(path,staged,*args):
        staged.write_bytes(b"converted")
        event.set()
    monkeypatch.setattr(transcode,"_encode",encode)
    with pytest.raises(DownloadCancelledError):
        transcode.convert_video(str(original),settings(),cancel_event=event)
    assert original.read_bytes() == b"original video"
    assert list(original.parent.iterdir()) == [original]


def test_settings_validate_encoder_and_keep_previous_settings(client,admin_credentials,monkeypatch):
    auth=do_setup(client,admin_credentials)
    available(monkeypatch,["cpu"])
    response=client.put("/api/domain/settings",json=settings(video_cpu_fallback=False),
                        headers={"X-CSRF-Token":auth["csrf_token"]})
    assert response.status_code == 400
    assert config.get_settings()["video_conversion"] == "copy"
    response=client.put("/api/domain/settings",json=settings(),headers={"X-CSRF-Token":auth["csrf_token"]})
    assert response.status_code == 200
    assert config.get_settings()["video_conversion"] == "h264"


def test_invalid_quality_is_rejected(client,admin_credentials):
    auth=do_setup(client,admin_credentials)
    response=client.put("/api/domain/settings",json={"video_quality":"unknown"},headers={"X-CSRF-Token":auth["csrf_token"]})
    assert response.status_code == 422


def test_parallel_limit_settings_and_restore(client, admin_credentials):
    do_setup(client, admin_credentials)
    endpoint = "/api/domain/settings"
    assert client.get(endpoint).json()["max_parallel_conversions"] == 2
    assert client.put(endpoint, json={"max_parallel_conversions": 1}).status_code == 200
    backup = client.get(endpoint + "/backup").json()
    for invalid in (0, 3, False, 1.5):
        assert client.put(endpoint, json={"max_parallel_conversions": invalid}).status_code in (400, 422)
        assert config.conversion_limit() == 1
    assert client.put(endpoint, json={"max_parallel_conversions": 2}).status_code == 200
    assert client.post(endpoint + "/restore", json=backup).status_code == 200
    assert config.conversion_limit() == 1


def test_resize_gpu_waiters_and_cancel_without_replacing_running_slots(monkeypatch):
    slots = transcode.ConversionSlots()
    limit = [1]
    monkeypatch.setattr(config, "conversion_limit", lambda: limit[0])
    entered, release = threading.Event(), threading.Event()
    waiting, third = threading.Event(), threading.Event()
    cancel = threading.Event()

    def worker(starting, inside, stop=None):
        starting.set()
        with slots.hold("nvenc", stop):
            inside.set()
            assert release.wait(5)

    with ThreadPoolExecutor(2) as pool, slots.hold("qsv", None):
        try:
            second = pool.submit(worker, waiting, entered)
            assert waiting.wait(2) and not entered.wait(.1)
            limit[0] = 2
            slots.refresh()
            assert entered.wait(2)
            limit[0] = 1
            slots.refresh()
            waiting.clear()
            pending = pool.submit(worker, waiting, third, cancel)
            assert waiting.wait(2)
            release.set()
            second.result(2)
            assert not third.wait(.1)
            cancel.set()
            with pytest.raises(DownloadCancelledError):
                pending.result(2)
        finally:
            release.set()
            cancel.set()
    with slots.hold("amf", None):
        pass


def test_gpu_failure_releases_gpu_while_waiting_for_busy_cpu(original, monkeypatch):
    slots = transcode.ConversionSlots()
    monkeypatch.setattr(transcode, "conversion_slots", slots)
    monkeypatch.setattr(config, "conversion_limit", lambda: 1)
    failed, cancelled = threading.Event(), threading.Event()
    calls = []

    def encode(path, staged, options, backend, *args):
        calls.append(backend)
        staged.write_bytes(b"partial")
        if backend == "nvenc":
            failed.set()
            raise transcode.TranscodeError("GPU unavailable")

    monkeypatch.setattr(transcode, "_encode", encode)
    with ThreadPoolExecutor(2) as pool, slots.hold("cpu", None):
        job = pool.submit(transcode.convert_video, original, settings(), None, cancelled)
        try:
            assert failed.wait(2)
            def another_gpu_encode():
                with slots.hold("nvenc", cancelled):
                    return True
            assert pool.submit(another_gpu_encode).result(2)
            assert not job.done()
        finally:
            cancelled.set()
        with pytest.raises(DownloadCancelledError):
            job.result(2)
    assert calls == ["nvenc"]
    assert original.read_bytes() == b"original video"
    assert list(original.parent.iterdir()) == [original]


def test_one_download_slot_can_feed_two_encoders_and_is_returned_once(original, monkeypatch):
    from app.jobs import JobManager
    manager = JobManager()
    manager.update_max_concurrent(1)
    monkeypatch.setattr(config, "conversion_limit", lambda: 2)
    monkeypatch.setattr(transcode, "conversion_slots", transcode.ConversionSlots())
    monkeypatch.setattr(transcode, "select_backend", lambda opts: "nvenc")
    both_encoding = threading.Barrier(2)

    def encode(path, staged, options, backend, duration, bar, event):
        if backend == "nvenc":
            both_encoding.wait(timeout=3)
            if path == original:
                raise transcode.TranscodeError("GPU failed")
        staged.write_bytes(backend.encode())

    monkeypatch.setattr(transcode, "_encode", encode)
    other = original.with_name("second.mkv")
    other.write_bytes(b"second original")
    media = (original, other)
    jobs = [manager._make_job(path.name, "film") for path in media]
    with ThreadPoolExecutor(2) as pool:
        attempts = [pool.submit(manager._attempt, job, transcode.convert_video,
                                (path, settings(video_conversion="hevc")), {})
                    for job, path in zip(jobs, media)]
        for attempt in attempts:
            attempt.result(timeout=6)
    assert [job.status for job in jobs] == ["done", "done"]
    assert [path.read_bytes() for path in media] == [b"cpu", b"nvenc"]
    assert manager._slots._busy == 0


def test_encoder_endpoint_requires_settings_permission(client,admin_credentials,monkeypatch):
    do_setup(client,admin_credentials)
    available(monkeypatch,["cpu"])
    assert client.get("/api/domain/video-encoders").status_code == 200
    restrict_local_user(monkeypatch, Permission.DOWNLOAD)
    assert client.get("/api/domain/video-encoders").status_code == 403


def test_cancel_terminates_running_encoder(original,monkeypatch):
    import io
    class Process:
        def __init__(self):
            self.stdout=io.StringIO("")
            self.terminated=False
        def poll(self): return 0 if self.terminated else None
        def terminate(self): self.terminated=True
        def wait(self,timeout=None): return 0
    process=Process()
    monkeypatch.setattr(transcode.subprocess,"Popen",lambda *a,**kw:process)
    # No real ffmpeg is needed (Popen is faked) and the machine may have none:
    # resolving it used to pass only when an earlier test had left one around.
    monkeypatch.setattr(transcode,"get_ffmpeg_exe",lambda:"ffmpeg")
    event=threading.Event()
    event.set()
    with pytest.raises(DownloadCancelledError):
        transcode._encode(original,original.parent/"staged.mkv",settings(),"nvenc",10,None,event)
    assert process.terminated
    assert original.read_bytes() == b"original video"
