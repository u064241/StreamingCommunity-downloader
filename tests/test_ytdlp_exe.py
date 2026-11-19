"""yt-dlp as a standalone executable: verified update, busy gate, one retry,
subprocess engine. No real network and no real yt-dlp: urllib and subprocess
are faked at the module boundary."""
import hashlib
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import raiplay, ytdlp_update
from app.progress import DownloadCancelledError

PAGE = "https://www.raiplay.it/video/2026/01/episodio-1.html"
NEW_EXE = b"MZ new yt-dlp payload"
OLD_EXE = b"MZ old yt-dlp"


@pytest.fixture(autouse=True)
def _fresh_gate(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "_active", 0)
    monkeypatch.setattr(ytdlp_update, "_updating", False)
    monkeypatch.setattr(ytdlp_update, "_retry_update_done", False)
    monkeypatch.setattr(ytdlp_update, "can_update", lambda: True)
    monkeypatch.setattr(ytdlp_update.time, "sleep", lambda s: None)


class _Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self._url = url

    def geturl(self):
        return self._url


def _release(sha=None, version="2026.11.10", payload=NEW_EXE):
    digest = hashlib.sha256(payload).hexdigest() if sha is None else sha
    return {"tag_name": version, "assets": [
        {"name": "yt-dlp.exe", "size": len(payload),
         "browser_download_url": f"https://github.com/yt-dlp/yt-dlp/releases/download/{version}/yt-dlp.exe",
         **({"digest": f"sha256:{digest}"} if digest else {})}]}


@pytest.fixture
def github(monkeypatch):
    """Fake GitHub: the release JSON and the asset bytes."""
    state = {"release": _release(), "payload": NEW_EXE, "urls": []}

    def fake_open(url, timeout):
        ytdlp_update._check_url(url)
        state["urls"].append(url)
        if url == ytdlp_update.RELEASE_URL:
            return _Response(json.dumps(state["release"]).encode(), url)
        return _Response(state["payload"], "https://release-assets.githubusercontent.com/x")

    monkeypatch.setattr(ytdlp_update, "_open", fake_open)
    return state


@pytest.fixture
def versions(monkeypatch):
    """--version: the old file answers its version, the .new answers its own."""
    answers = {"old": "2025.10.22", "new": "2026.11.10"}

    def probe(exe, timeout=None):
        path = Path(exe)
        if not path.is_file():
            return None
        return answers["new"] if path.name.endswith(".new") or path.read_bytes() == NEW_EXE else answers["old"]

    monkeypatch.setattr(ytdlp_update, "probe_version", probe)
    return answers


def _install_old():
    exe = ytdlp_update.managed_path()
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(OLD_EXE)
    return exe


# ── update ───────────────────────────────────────────────────────────────────

def test_update_downloads_verifies_and_replaces(github, versions):
    exe = _install_old()
    result = ytdlp_update.update()
    assert result["updated"] is True and result["version"] == "2026.11.10"
    assert result["previous"] == "2025.10.22"
    assert exe.read_bytes() == NEW_EXE
    assert not exe.with_name(exe.name + ".new").exists()


def test_update_installs_when_missing(github, versions):
    assert ytdlp_update.executable() is None or not ytdlp_update.managed_path().exists()
    result = ytdlp_update.update()
    assert result["updated"] is True
    assert ytdlp_update.managed_path().read_bytes() == NEW_EXE


def test_wrong_sha_keeps_old_exe_and_removes_new(github, versions):
    exe = _install_old()
    github["release"] = _release(sha="0" * 64)
    with pytest.raises(ytdlp_update.YtdlpUpdateError, match="SHA-256"):
        ytdlp_update.update()
    assert exe.read_bytes() == OLD_EXE
    assert not exe.with_name(exe.name + ".new").exists()


def test_failed_version_probe_does_not_replace(github, versions, monkeypatch):
    exe = _install_old()
    monkeypatch.setattr(ytdlp_update, "probe_version",
                        lambda path, timeout=None: None if str(path).endswith(".new") else "2025.10.22")
    with pytest.raises(ytdlp_update.YtdlpUpdateError, match="--version"):
        ytdlp_update.update()
    assert exe.read_bytes() == OLD_EXE
    assert not exe.with_name(exe.name + ".new").exists()


def test_release_without_digest_is_refused_before_download(github, versions):
    exe = _install_old()
    github["release"] = _release(sha="")
    with pytest.raises(ytdlp_update.YtdlpUpdateError, match="SHA-256"):
        ytdlp_update.update()
    assert github["urls"] == [ytdlp_update.RELEASE_URL]
    assert exe.read_bytes() == OLD_EXE


def test_up_to_date_downloads_nothing(github, versions):
    _install_old()
    versions["old"] = "2026.11.10"
    assert ytdlp_update.update()["updated"] is False
    assert github["urls"] == [ytdlp_update.RELEASE_URL]


def test_truncated_download_is_discarded(github, versions):
    exe = _install_old()
    github["payload"] = NEW_EXE[:5]
    with pytest.raises(ytdlp_update.YtdlpUpdateError, match="incompleto"):
        ytdlp_update.update()
    assert exe.read_bytes() == OLD_EXE


@pytest.mark.parametrize("url", ["http://github.com/x", "https://evil.example/yt-dlp.exe",
                                 "https://github.com.evil.example/x", "https://github.com:8443/x"])
def test_foreign_hosts_and_redirects_are_refused(url):
    with pytest.raises(ytdlp_update.YtdlpUpdateError):
        ytdlp_update._check_url(url)
    with pytest.raises(ytdlp_update.YtdlpUpdateError):
        ytdlp_update._CheckedRedirect().redirect_request(None, None, 302, "Found", {}, url)


def test_asset_url_outside_the_official_repo_is_refused(github):
    github["release"]["assets"][0]["browser_download_url"] = "https://github.com/other/fork/releases/download/x/yt-dlp.exe"
    with pytest.raises(ytdlp_update.YtdlpUpdateError):
        ytdlp_update.latest_release()


def test_retry_fs_survives_a_file_in_use():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        return "ok"

    assert ytdlp_update._retry_fs(flaky, tries=5, delay=0) == "ok" and len(calls) == 3


# ── busy gate ────────────────────────────────────────────────────────────────

def test_no_update_while_a_ytdlp_download_runs(github, versions):
    exe = _install_old()
    with ytdlp_update.in_use():
        with pytest.raises(ytdlp_update.YtdlpBusyError):
            ytdlp_update.update()
        assert github["urls"] == []  # refused before any request
        assert ytdlp_update.status()["busy"] is True
    assert exe.read_bytes() == OLD_EXE


def test_update_route_answers_409_while_busy(client, github, versions):
    _install_old()
    with ytdlp_update.in_use():
        response = client.post("/api/domain/ytdlp/update")
    assert response.status_code == 409


def test_download_waits_for_a_running_update(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "_updating", True)
    entered = threading.Event()

    def worker():
        with ytdlp_update.in_use():
            entered.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert not entered.wait(0.3)
    with ytdlp_update._gate:
        ytdlp_update._updating = False
        ytdlp_update._gate.notify_all()
    assert entered.wait(2)
    thread.join(2)


# ── one update, one retry ────────────────────────────────────────────────────

def _engine(monkeypatch, tmp_path, outcomes):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: str(tmp_path / "yt-dlp.exe"))
    calls = []

    def attempt(*args, **kwargs):
        calls.append(1)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(raiplay, "_attempt", attempt)
    return calls


def test_failed_download_updates_once_and_retries_once(monkeypatch, tmp_path):
    calls = _engine(monkeypatch, tmp_path, [raiplay.YtdlpEngineError("boom"), "ok.mp4"])
    updates = []
    monkeypatch.setattr(ytdlp_update, "update_for_retry", lambda: updates.append(1) or True)
    assert raiplay.download(PAGE, "Test", str(tmp_path)) == "ok.mp4"
    assert len(calls) == 2 and len(updates) == 1


def test_second_failure_is_final_no_loop(monkeypatch, tmp_path):
    calls = _engine(monkeypatch, tmp_path, [raiplay.YtdlpEngineError("a"), raiplay.YtdlpEngineError("b"),
                                            "never"])
    updates = []
    monkeypatch.setattr(ytdlp_update, "update_for_retry", lambda: updates.append(1) or True)
    with pytest.raises(raiplay.YtdlpEngineError, match="b"):
        raiplay.download(PAGE, "Test", str(tmp_path))
    assert len(calls) == 2 and len(updates) == 1


def test_no_retry_without_a_newer_ytdlp(monkeypatch, tmp_path):
    calls = _engine(monkeypatch, tmp_path, [raiplay.YtdlpEngineError("a"), "never"])
    monkeypatch.setattr(ytdlp_update, "update_for_retry", lambda: False)
    with pytest.raises(raiplay.YtdlpEngineError):
        raiplay.download(PAGE, "Test", str(tmp_path))
    assert len(calls) == 1


def test_drm_refusal_never_updates_or_retries(monkeypatch, tmp_path):
    calls = _engine(monkeypatch, tmp_path, [raiplay.RaiPlayDownloadError("DRM"), "never"])
    monkeypatch.setattr(ytdlp_update, "update_for_retry", lambda: pytest.fail("no update for DRM"))
    with pytest.raises(raiplay.RaiPlayDownloadError):
        raiplay.download(PAGE, "Test", str(tmp_path))
    assert len(calls) == 1


def test_update_for_retry_is_once_per_session_and_never_with_others_running(monkeypatch, github, versions):
    _install_old()
    with ytdlp_update.in_use():
        assert ytdlp_update.update_for_retry() is False
    assert github["urls"] == []  # refused before the session's one chance was spent
    assert ytdlp_update.update_for_retry() is True
    assert ytdlp_update.update_for_retry() is False
    assert github["urls"].count(ytdlp_update.RELEASE_URL) == 1


def test_update_for_retry_never_installs_a_missing_ytdlp(github, versions):
    assert ytdlp_update.update_for_retry() is False
    assert github["urls"] == []


# ── subprocess engine ────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, expected", [
    ("SCDL-PROGRESS\t512\t1024\tNA\t1080p\n", {"done": 512, "total": 1024, "key": "1080p"}),
    ("SCDL-PROGRESS\t512\tNA\t2048.5\tNA\n", {"done": 512, "total": 2048, "key": None}),
    ("SCDL-PROGRESS\t512\tNA\tNA\taudio\r\n", {"done": 512, "total": 512, "key": "audio"}),
    ("SCDL-PROGRESS\tNA\tNA\tNA\tNA\n", None),
    ("[download]  50.0% of 10MiB\n", None),
    ("SCDL-FILE\tC:/x.mp4\n", None),
])
def test_progress_lines_are_parsed(line, expected):
    assert raiplay.parse_progress(line) == expected


def test_missing_executable_gives_a_clear_italian_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: None)
    monkeypatch.setattr(raiplay.subprocess, "run", lambda *a, **k: pytest.fail("nothing to run"))
    with pytest.raises(raiplay.RaiPlayDownloadError, match="Controlla yt-dlp"):
        raiplay.download(PAGE, "Test", str(tmp_path))


def test_drm_from_dumped_json_stops_before_download(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: str(tmp_path / "yt-dlp.exe"))
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        assert kwargs["shell"] is False and kwargs["timeout"] == raiplay.INFO_TIMEOUT_S
        return SimpleNamespace(returncode=0, stderr=b"", stdout=json.dumps(
            {"title": "x", "formats": [{"url": "https://m/x", "license_url": "https://lic"}]}).encode())

    monkeypatch.setattr(raiplay.subprocess, "run", run)
    monkeypatch.setattr(raiplay.subprocess, "Popen", lambda *a, **k: pytest.fail("no download for DRM"))
    monkeypatch.setattr(ytdlp_update, "update_for_retry", lambda: pytest.fail("no update for DRM"))
    with pytest.raises(raiplay.RaiPlayDownloadError, match="DRM"):
        raiplay.download(PAGE, "Test", str(tmp_path))
    assert seen[0] == [str(tmp_path / "yt-dlp.exe"), "--dump-single-json", "--no-playlist", "--no-warnings", PAGE]


def test_ytdlp_error_exit_is_an_engine_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: str(tmp_path / "yt-dlp.exe"))
    monkeypatch.setattr(raiplay.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=b"", stderr=b"WARNING: x\n\x1b[0;31mERROR:\x1b[0m [RaiPlay] Unable to extract\n"))
    monkeypatch.setattr(ytdlp_update, "update_for_retry", lambda: False)
    with pytest.raises(raiplay.YtdlpEngineError) as info:
        raiplay.download(PAGE, "Test", str(tmp_path))
    assert str(info.value) == "ERROR: [RaiPlay] Unable to extract"


def test_cancel_kills_the_ytdlp_process():
    killed = []

    class Endless:
        returncode = None
        stdout = iter([])

        def wait(self, timeout=None):
            if killed:
                return -9
            import subprocess
            raise subprocess.TimeoutExpired("yt-dlp", timeout)

        def poll(self):
            return -9 if killed else None

        def kill(self):
            killed.append(1)

    cancel = threading.Event()
    cancel.set()
    original = raiplay.subprocess.Popen
    raiplay.subprocess.Popen = lambda *a, **k: Endless()
    try:
        with pytest.raises(DownloadCancelledError):
            raiplay._run_download(["yt-dlp.exe"], lambda line: None, cancel)
    finally:
        raiplay.subprocess.Popen = original
    assert killed


def test_progress_reaches_the_job_bar(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp_update, "executable", lambda: str(tmp_path / "yt-dlp.exe"))
    monkeypatch.setattr("app.core.ffmpeg_path.get_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(raiplay.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stderr=b"", stdout=json.dumps({"formats": [{"url": "https://m/clear"}]}).encode()))

    def fake_run_download(argv, on_line, cancel_event=None, timeout=None):
        assert "%(ext)s" in argv[argv.index("-o") + 1]
        target = Path(argv[argv.index("-o") + 1].replace("%(ext)s", "mp4"))
        target.write_bytes(b"x")
        for line in ("SCDL-PROGRESS\t10\t100\tNA\t1\n", "SCDL-PROGRESS\t60\t100\tNA\t1\n",
                     f"SCDL-FILE\t{target}\n"):
            on_line(line)
        return 0, b""

    monkeypatch.setattr(raiplay, "_run_download", fake_run_download)
    bars = []

    class Bar:
        def __init__(self, total, phase):
            self.total, self.n, self.closed = total, 0, False
            bars.append(self)

        def update(self, n, bytes):
            self.n += n

        def close(self):
            self.closed = True

    result = raiplay.download(PAGE, "Test", str(tmp_path), progress_factory=lambda **kw: Bar(**kw))
    assert Path(result).name == "Test.mp4"
    assert len(bars) == 1 and bars[0].n == 60 and bars[0].total == 100 and bars[0].closed
