from pathlib import Path
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
    def encode(path,staged,options,backend,*args):
        calls.append(backend)
        staged.write_bytes(b"converted video")
        if backend == "nvenc": raise transcode.TranscodeError("GPU busy")
    monkeypatch.setattr(transcode,"_encode",encode)
    assert transcode.convert_video(str(original),settings()) == str(original)
    assert calls == ["nvenc","cpu"]
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
