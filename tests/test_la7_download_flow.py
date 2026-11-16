"""Official URL -> queue -> checked HLS -> real local output, with no network."""
from copy import deepcopy
from pathlib import Path
import threading

import pytest

from app import jobs
from app.core import la7
from app.jobs import JobManager
from app.providers import create_default_registry, get_registry, ProviderRegistry
from app.providers.la7 import LA7Provider
from app.source_identity import download_source
from app.progress import DownloadCancelledError
from tests.conftest import restrict_local_user
from app.auth.permissions import Permission
from tests.test_la7_resolver import MANIFEST, URL, Response, network, payload  # noqa: F401


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """Replace only yt-dlp's extraction/download I/O; keep the LA7 checks real."""
    import yt_dlp
    from app.core import ffmpeg_path

    state = {"extractions": [], "processed": [], "write": True, "opts": None,
             "info": {"id": "test", "title": "Test", "formats": [
                 {"format_id": "720", "protocol": "m3u8_native", "ext": "mp4",
                  "url": MANIFEST.rsplit("/", 1)[0] + "/720/index.m3u8"}]}}

    class YDL:
        def __init__(self, opts):
            self.opts = state["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def extract_info(self, url, **kwargs):
            assert kwargs == {"download": False, "process": False, "ie_key": "Generic"}
            state["extractions"].append(url)
            if state.get("on_extract"):
                state["on_extract"]()
            return deepcopy(state["info"])

        def process_ie_result(self, info, download):
            assert download is True
            state["processed"].append(info)
            for fmt in info["formats"]:
                playlist = fmt["hls_media_playlist_data"]
                assert "#EXTINF:" in playlist and "#EXT-X-KEY" not in playlist
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "filename": "test.mp4",
                      "downloaded_bytes": 8, "total_bytes": 8})
            if state.get("error"):
                raise RuntimeError(state["error"])
            if state["write"]:
                target = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
                target.write_bytes(b"test-media")
                for hook in self.opts["post_hooks"]:
                    hook(str(target))
            return info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", YDL)
    monkeypatch.setattr(ffmpeg_path, "get_ffmpeg_exe", lambda: str(tmp_path / "ffmpeg.exe"))
    return state


@pytest.fixture
def manager(monkeypatch, tmp_path):
    manager = JobManager()
    manager.pending = []
    monkeypatch.setattr(manager, "_submit_worker", lambda *a, **kw: manager.pending.append((a, kw)))
    monkeypatch.setattr(jobs, "TMP_DIR", tmp_path / "staging")
    monkeypatch.setattr(jobs, "_get_library_path", lambda kind: str(tmp_path / "library"))
    yield manager
    manager._executor.shutdown(wait=False)


def run_next(manager):
    args, kwargs = manager.pending.pop(0)
    args[0](*args[1:], **kwargs)


def test_registered_as_direct_download_only():
    registry = create_default_registry()
    assert isinstance(registry.get("la7"), LA7Provider)
    assert registry.is_downloadable("la7")
    assert "la7" not in registry.sources()  # no new catalogue/UI entries
    assert "la7" in registry.sources(include_direct=True)
    assert create_default_registry(include_metadata_sources=False).is_downloadable("la7")
    assert download_source("la7", "la7") == "la7"
    for kind, source in [("film", "la7"), ("episode", "la7"), ("la7", "raiplay")]:
        with pytest.raises(ValueError):
            download_source(kind, source)  # never route LA7 through another engine


def test_endpoint_queues_then_worker_resolves_and_publishes(client, monkeypatch, manager, network, engine):
    monkeypatch.setattr(jobs.job_manager, "submit_la7", manager.submit_la7)
    response = client.post("/api/download/la7", json={"url": URL, "title": "Puntata"})
    assert response.status_code == 202, response.text
    job = manager.get(response.json()["job_id"])
    assert response.json() == {"job_id": job.job_id, "status": "queued", "scope": "single"}
    assert job.source == job.type == "la7"
    assert job.external_id == "399460"
    assert job.user_id is None
    assert network.calls == [] and engine["extractions"] == []
    assert job.call[1][0] == URL and MANIFEST not in str(job.call)
    run_next(manager)
    assert job.status == "done" and job.output_created
    assert Path(job.output_path).read_bytes() == b"test-media"
    assert engine["extractions"] == [MANIFEST]
    assert len(engine["processed"]) == 1
    assert engine["opts"]["allow_unplayable_formats"] is False
    assert engine["opts"]["skip_unavailable_fragments"] is False
    assert engine["opts"]["ffmpeg_location"].endswith("ffmpeg.exe")
    assert job.progress["bytes_done"] == 8


@pytest.mark.parametrize("body", [
    {"url": "https://evil.test/puntata-399460"},
    {"url": "http://www.la7play.it/puntata-399460"},
    {"url": "https://www.la7play.it/programma/puntate"},
    {"url": URL + "?next=https://evil.test"},
    {"url": "https://www.la7play.it@127.0.0.1/puntata-399460"},
    {"source": "raiplay"}, {"source": "missing"}, {"source": None},
    {"scope": "series"}, {"scope": "season"}, {"scope": "episode"},
    {"manifest": MANIFEST}, {"title": "  "},
])
def test_invalid_request_never_creates_job(client, monkeypatch, body):
    def unexpected(*a, **kw):
        pytest.fail("Invalid request created a job")
    monkeypatch.setattr(jobs.job_manager, "submit_la7", unexpected)
    response = client.post("/api/download/la7", json={"url": URL, "title": "Test", **body})
    assert response.status_code == 422, response.text


def test_endpoint_checks_registered_capability_even_without_source(client, monkeypatch):
    client.app.dependency_overrides[get_registry] = lambda: ProviderRegistry()
    try:
        monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: pytest.fail("Created job"))
        assert client.post("/api/download/la7", json={"url": URL, "title": "Test"}).status_code == 422
    finally:
        client.app.dependency_overrides.pop(get_registry, None)


def test_endpoint_requires_download_permission(client, monkeypatch):
    restrict_local_user(monkeypatch, Permission.REQUEST)
    monkeypatch.setattr(jobs.job_manager, "submit_la7", lambda *a, **kw: pytest.fail("Created job"))
    assert client.post("/api/download/la7", json={"url": URL, "title": "Test"}).status_code == 403


@pytest.mark.parametrize("protection", [True, None, "false"])
def test_protection_checked_at_worker_start_is_terminal(manager, network, payload, engine, protection):
    job_id = manager.submit_la7(URL, "Test")
    payload["props"]["episode"]["drm"] = protection
    network.set_page(payload)
    run_next(manager)
    job = manager.get(job_id)
    assert job.status == "error" and "DRM" in job.error
    assert job.retries == 0 and job.output_path is None
    assert engine["extractions"] == engine["processed"] == []


def test_changed_manifest_cannot_be_downloaded(manager, network, engine):
    job_id = manager.submit_la7(URL, "Test")
    network.responses[MANIFEST] = Response("#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"key\"\n")
    run_next(manager)
    job = manager.get(job_id)
    assert job.status == "error" and job.retries == 0
    assert "DRM" in job.error and not engine["processed"]


def test_protection_appearing_after_extraction_is_rejected(network, engine, tmp_path):
    def changed():
        network.responses[MANIFEST] = Response(
            '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n')
    engine["on_extract"] = changed
    with pytest.raises(la7.LA7Error, match="cifrato|DRM"):
        la7.download(URL, "Test", str(tmp_path / "library"))
    assert engine["processed"] == []


@pytest.mark.parametrize("change", [
    {"has_drm": True}, {"license_url": "https://evil.test/licence"},
    {"_type": "playlist", "entries": []}, {"formats": []}, {"is_live": True},
    {"formats": [{"url": "https://evil.test/video", "protocol": "https"}]},
    {"formats": [{"url": MANIFEST, "protocol": "m3u8_native"}]},
])
def test_unverified_formats_fail_without_media_download(network, engine, tmp_path, change):
    engine["info"].update(change)
    with pytest.raises(la7.LA7Error):
        la7.download(URL, "Test", str(tmp_path / "library"))
    assert engine["processed"] == []
    assert not (tmp_path / "library").exists()


def test_missing_output_is_error_not_false_success(manager, network, engine):
    engine["write"] = False
    job_id = manager.submit_la7(URL, "Test")
    run_next(manager)
    job = manager.get(job_id)
    assert job.status == "error" and job.retries == 0
    assert job.output_path is None and "file completo" in job.error


def test_failed_job_can_be_retried_with_fresh_resolution(manager, network, engine):
    engine["error"] = "segmento non disponibile"
    job_id = manager.submit_la7(URL, "Test")
    run_next(manager)
    assert manager.get(job_id).status == "error"
    first_calls = len(network.calls)
    engine.pop("error")
    next_id = manager.retry(job_id)
    assert manager.get(next_id).source == "la7"
    run_next(manager)
    assert manager.get(next_id).status == "done"
    assert len(network.calls) > first_calls


def test_cancelled_before_start_does_not_resolve(manager, network, engine):
    job_id = manager.submit_la7(URL, "Test")
    manager.cancel(job_id)
    run_next(manager)
    assert manager.get(job_id).status == "cancelled"
    assert not network.calls and not engine["processed"]


def test_cancellation_during_progress_cleans_staging(network, engine, tmp_path):
    event = threading.Event()
    class Progress:
        def update(self, **kwargs):
            event.set()
        def close(self):
            pass
    with pytest.raises(DownloadCancelledError):
        la7.download(URL, "Test", str(tmp_path / "library"),
                     temp_dir=tmp_path / "staging", cancel_event=event,
                     progress_factory=lambda **kwargs: Progress())
    assert not (tmp_path / "library").exists()
    assert list((tmp_path / "staging").iterdir()) == []


def test_publication_does_not_overwrite_existing_file(network, engine, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    existing = library / "Test [LA7-399460].mp4"
    existing.write_bytes(b"existing-user-file")
    with pytest.raises(la7.LA7Error):
        la7.download(URL, "Test", str(library))
    assert existing.read_bytes() == b"existing-user-file"
    assert list(library.iterdir()) == [existing]


def test_unsupported_publication_is_explicit_and_cleans_temporary_copy(network, engine, monkeypatch, tmp_path):
    import os
    def unsupported(*args):
        raise OSError("filesystem does not support atomic publication")
    monkeypatch.setattr(os, "link", unsupported)
    with pytest.raises(la7.LA7Error, match="atomic publication"):
        la7.download(URL, "Test", str(tmp_path / "library"))
    assert list((tmp_path / "library").iterdir()) == []


def test_missing_ytdlp_fails_before_resolution(network, monkeypatch, tmp_path):
    import sys
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    with pytest.raises(la7.LA7Error, match="yt-dlp non installato"):
        la7.download(URL, "Test", str(tmp_path / "library"))
    assert network.calls == []


def test_real_ytdlp_native_hls_uses_checked_snapshot(network, monkeypatch, tmp_path):
    """Exercise actual yt-dlp parsing, selection, HLS and post-hooks offline.

    Segment bytes are synthetic; disable media fixup in this test only, since
    it tests transport/queue integration rather than codec validity.
    """
    import io
    import yt_dlp
    from yt_dlp.networking import Response as YDLResponse
    from app.core import ffmpeg_path

    real_ydl = yt_dlp.YoutubeDL
    calls = []
    segments = []
    download_calls = []

    class OfflineYDL(real_ydl):
        downloading = False

        def __init__(self, opts):
            super().__init__({**opts, "fixup": "never", "cachedir": False})

        def process_ie_result(self, *args, **kwargs):
            self.downloading = True
            return super().process_ie_result(*args, **kwargs)

        def urlopen(self, request):
            url = request if isinstance(request, str) else request.url
            calls.append(url)
            if self.downloading:
                download_calls.append(url)
            if url in network.responses:
                data = network.responses[url].body
                content_type = "application/vnd.apple.mpegurl"
            elif url.startswith(la7.CDN_HOST) and url.endswith(("segment-000.ts", "segment-001.ts")):
                data = b"synthetic-segment:" + url.rsplit("/", 1)[1].encode()
                segments.append(data)
                content_type = "video/mp2t"
            else:
                pytest.fail(f"Unexpected yt-dlp request: {url}")
            return YDLResponse(io.BytesIO(data), url, {"Content-Type": content_type,
                                                     "Content-Length": str(len(data))})

    monkeypatch.setattr(yt_dlp, "YoutubeDL", OfflineYDL)
    monkeypatch.setattr(ffmpeg_path, "get_ffmpeg_exe", lambda: str(tmp_path / "absent-ffmpeg.exe"))
    result = la7.download(URL, "Test", str(tmp_path / "library"), temp_dir=tmp_path / "staging")
    assert Path(result).read_bytes() == b"".join(segments)
    assert len(segments) == 2
    assert MANIFEST in calls  # Generic probes/reads the master during extraction
    assert not any(url.endswith(".m3u8") for url in download_calls), download_calls
    assert not any("key" in url for url in calls)
    assert list((tmp_path / "staging").iterdir()) == []
