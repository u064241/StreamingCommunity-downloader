"""What a download leaves on disk, and what its result admits to.

The destination used to be written in place: the join overwrote it, and a
failure or a cancel afterwards deleted it — so re-downloading a title that was
already in the library, and failing, cost the copy that worked. And a download
that could not get a requested track, or whose conversion failed, either said
nothing or threw the finished file away and started again from segment zero.
"""

import os
import sys
import threading
import time

import ffmpeg
import pytest
import requests

from app import config
from app.core import container, domain_recovery, ffmpeg_path, film, format as fmt, hosts, m3u8, tv
from app.core._shared import StreamSource
from app.core.headers import MAX_FILENAME, fit_title, sanitize_filename
from app.core.m3u8 import DownloadResult, M3U8_Downloader, M3U8_Segments
from app.progress import DownloadCancelledError


class _Response:
    def __init__(self, status_code=200, text="", content=None, headers=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode() if content is None else content
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    hosts._dns_cache.clear()
    monkeypatch.setattr(hosts, "_resolve", lambda host, port: ["93.184.216.34"])
    yield
    hosts._dns_cache.clear()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries wait for nothing here. Tests that time a real wait use events."""
    monkeypatch.setattr(m3u8.time, "sleep", lambda s: None)


def _configure(**settings):
    config.save_settings({**config.get_settings(), **settings})


def _visible_and_hidden(directory):
    return sorted(os.listdir(directory))


# ── The join writes beside the destination ────────────────────────────────────

def _joinable(tmp_path, count=2):
    seg = M3U8_Segments("https://cdn.example.test/p.m3u8", temp_dir=str(tmp_path / "segs"))
    seg.segments = [f"https://cdn.example.test/seg-{i}.ts" for i in range(count)]
    for index in range(count):
        with open(os.path.join(seg.temp_folder, f"{index}.ts"), "wb") as handle:
            handle.write(b"ts" * 10)
    return seg


def _output_of(stream) -> str:
    args = ffmpeg.compile(stream)
    return [a for a in args if a.startswith("file:")][-1].removeprefix("file:")


@pytest.fixture
def fake_join(monkeypatch):
    """Stand in for FFmpeg at the join. ``outcomes`` is consumed one per run."""
    runs = []
    outcomes = []

    def run(stream, cancel_event=None, **kwargs):
        target = _output_of(stream)
        runs.append({"target": target, "args": ffmpeg.compile(stream)})
        outcome = outcomes.pop(0) if outcomes else "ok"
        with open(target, "wb") as handle:
            handle.write(b"joined" if outcome == "ok" else b"half")
        if outcome == "fail":
            raise ffmpeg.Error("ffmpeg", b"", b"boom")
        if outcome == "cancel":
            raise DownloadCancelledError("Download annullato dall'utente")
        return b"", b""

    monkeypatch.setattr(m3u8, "run_ffmpeg", run)
    return runs, outcomes


def test_the_join_never_writes_the_destination_itself(tmp_path, fake_join):
    runs, _ = fake_join
    library = tmp_path / "lib"
    library.mkdir()
    final = library / "Film.mkv"

    _joinable(tmp_path).join(str(final))

    assert final.read_bytes() == b"joined"
    assert [os.path.basename(r["target"]) for r in runs] == [".Film.joining.mkv"]
    assert _visible_and_hidden(library) == ["Film.mkv"]


@pytest.mark.parametrize("outcome", [["fail", "fail"], ["cancel"]])
def test_a_join_that_does_not_finish_leaves_the_existing_file_alone(tmp_path, fake_join, outcome):
    """The reported loss: FFmpeg was pointed at the library file with overwrite,
    so a failed or cancelled join had already truncated the good copy."""
    _, outcomes = fake_join
    outcomes.extend(outcome)
    library = tmp_path / "lib"
    library.mkdir()
    final = library / "Film.mkv"
    final.write_bytes(b"the copy that works")

    with pytest.raises((RuntimeError, DownloadCancelledError)):
        _joinable(tmp_path).join(str(final))

    assert final.read_bytes() == b"the copy that works"
    assert _visible_and_hidden(library) == ["Film.mkv"]


def test_the_join_copies_the_audio_and_re_encodes_only_when_refused(tmp_path, fake_join):
    runs, outcomes = fake_join
    outcomes.extend(["fail", "ok"])
    final = tmp_path / "lib" / "Film.mp4"

    _joinable(tmp_path).join(str(final))

    first, second = (r["args"] for r in runs)
    assert first[first.index("-c:a") + 1] == "copy"
    assert first[first.index("-bsf:a") + 1] == "aac_adtstoasc"
    assert second[second.index("-c:a") + 1] == "aac"
    assert "-bsf:a" not in second
    # The picture is copied both times.
    assert first[first.index("-c:v") + 1] == second[second.index("-c:v") + 1] == "copy"
    assert final.read_bytes() == b"joined"


def test_the_join_passes_the_cancel_flag_to_ffmpeg(tmp_path, monkeypatch):
    seen = {}

    def run(stream, cancel_event=None, **kwargs):
        seen["cancel"] = cancel_event
        with open(_output_of(stream), "wb") as handle:
            handle.write(b"joined")

    monkeypatch.setattr(m3u8, "run_ffmpeg", run)
    cancel = threading.Event()
    seg = _joinable(tmp_path)
    seg._cancel = cancel

    seg.join(str(tmp_path / "lib" / "Film.mkv"))

    assert seen["cancel"] is cancel


def test_the_join_takes_the_playlists_segments_not_whatever_is_in_the_folder(tmp_path, monkeypatch):
    """A numbered file left by a longer earlier attempt is not part of this
    download; listing the directory used to append it to the film."""
    seg = _joinable(tmp_path, count=2)
    with open(os.path.join(seg.temp_folder, "7.ts"), "wb") as handle:
        handle.write(b"STALE")
    combined = {}

    def run(stream, cancel_event=None, **kwargs):
        with open(os.path.join(seg.temp_folder, "_combined.ts"), "rb") as handle:
            combined["bytes"] = handle.read()
        with open(_output_of(stream), "wb") as handle:
            handle.write(b"joined")

    monkeypatch.setattr(m3u8, "run_ffmpeg", run)

    seg.join(str(tmp_path / "lib" / "Film.mkv"))

    assert combined["bytes"] == b"ts" * 20


# ── The whole download publishes once, at the end ─────────────────────────────

class _FakeSegments:
    """Writes the file the real one would, and fetches nothing."""

    after_join = None

    def __init__(self, url, key, temp_dir=None, **kwargs):
        self.url = url

    def get_info(self):
        pass

    def download_ts(self):
        pass

    def join(self, output_filename):
        os.makedirs(os.path.dirname(output_filename), exist_ok=True)
        with open(output_filename, "wb") as handle:
            handle.write(b"new video")
        if _FakeSegments.after_join:
            _FakeSegments.after_join()


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """_download_m3u8 with the network and FFmpeg stood in for."""
    monkeypatch.setattr(m3u8, "M3U8_Segments", _FakeSegments)
    _FakeSegments.after_join = None
    remuxed = []

    def remux(video, audio_tracks=None, subtitle_tracks=None, cancel_event=None):
        remuxed.append({"video": video, "audio": audio_tracks or [],
                        "subs": subtitle_tracks or []})
        if remux.fail:
            raise RuntimeError("FFmpeg remux error: boom")
        with open(video, "wb") as handle:
            handle.write(b"new video, remuxed")
        return video

    remux.fail = False
    monkeypatch.setattr(fmt, "remux", remux)

    library = tmp_path / "lib" / "Film (2020)"
    library.mkdir(parents=True)
    final = library / "Film (2020).mkv"

    def run(**kwargs):
        return m3u8._download_m3u8(
            m3u8_index="https://cdn.example.test/index.m3u8",
            output_filename=str(final),
            temp_dir=str(tmp_path / "tmp"),
            audio_track_urls=[{"url": "https://cdn.example.test/a.m3u8", "language": "ita"}],
            **kwargs,
        )

    yield run, final, library, remux, remuxed
    _FakeSegments.after_join = None


def test_a_download_that_fails_after_the_join_keeps_the_library_file(pipeline):
    """The join used to land on the destination before the audio tracks were
    even fetched, and the failure cleanup then removed it — in every container
    it could have been written in."""
    run, final, library, remux, _ = pipeline
    final.write_bytes(b"the copy that works")
    (library / "Film (2020).mp4").write_bytes(b"the other container")
    remux.fail = True

    with pytest.raises(RuntimeError, match="remux"):
        run()

    assert final.read_bytes() == b"the copy that works"
    assert (library / "Film (2020).mp4").read_bytes() == b"the other container"
    assert _visible_and_hidden(library) == ["Film (2020).mkv", "Film (2020).mp4"]


def test_a_cancel_during_the_download_keeps_the_library_file(pipeline):
    run, final, library, _, _ = pipeline
    final.write_bytes(b"the copy that works")
    cancel = threading.Event()
    _FakeSegments.after_join = cancel.set

    with pytest.raises(DownloadCancelledError):
        run(cancel_event=cancel)

    assert final.read_bytes() == b"the copy that works"
    assert _visible_and_hidden(library) == ["Film (2020).mkv"]


def test_a_failure_with_nothing_there_before_leaves_nothing_behind(pipeline):
    run, final, library, remux, _ = pipeline
    remux.fail = True

    with pytest.raises(RuntimeError):
        run()

    assert not library.exists(), "the folder this attempt created is removed when empty"


def test_a_complete_download_replaces_the_file_in_one_step(pipeline, monkeypatch):
    run, final, library, _, remuxed = pipeline
    final.write_bytes(b"the old copy")
    published = []
    real_replace = os.replace

    def spy(src, dst):
        if os.path.abspath(dst) == os.path.abspath(str(final)):
            published.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr(m3u8.os, "replace", spy)

    result = run()

    assert result == str(final)
    assert final.read_bytes() == b"new video, remuxed"
    # Assembled under the hidden work name, remuxed there, published once.
    assert os.path.basename(remuxed[0]["video"]) == ".Film (2020).part.mkv"
    assert published == [".Film (2020).part.mkv"]
    assert _visible_and_hidden(library) == ["Film (2020).mkv"]


def test_the_staging_names_are_hidden_and_keep_the_extension():
    for suffix in (fmt.WORK_SUFFIX, fmt.JOIN_SUFFIX, fmt.STAGING_SUFFIX):
        staged = fmt.staging_path("/lib/Film (2020)/Film (2020).mp4", suffix)
        assert os.path.basename(staged).startswith(".")
        assert staged.endswith(".mp4"), "FFmpeg picks the muxer from the extension"


# ── A single audio playlist goes through the remux ────────────────────────────

def test_a_directly_named_audio_playlist_is_muxed_by_the_remux(tmp_path, monkeypatch):
    """It had a merge of its own, join_audio, whose -map options sat after the
    output file — where FFmpeg ignores them."""
    monkeypatch.setattr(m3u8, "M3U8_Segments", _FakeSegments)
    remuxed = []
    monkeypatch.setattr(
        fmt, "remux",
        lambda video, audio_tracks=None, subtitle_tracks=None, cancel_event=None:
            remuxed.append(audio_tracks) or video,
    )
    final = tmp_path / "lib" / "Film.mkv"

    result = M3U8_Downloader(
        "https://cdn.example.test/index.m3u8",
        m3u8_audio="https://cdn.example.test/audio-it.m3u8",
        output_filename=str(final), temp_dir=str(tmp_path / "tmp"),
        audio_languages=["ita"],
    ).start()

    assert result == str(final)
    assert [(os.path.basename(t["path"]), t["language"]) for t in remuxed[0]] == [("_audio_0.mp4", "ita")]
    assert not hasattr(M3U8_Downloader, "join_audio")


# ── Subtitles: every segment, and an honest result ────────────────────────────

MASTER = (
    "#EXTM3U\n"
    '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="Italiano",LANGUAGE="ita",URI="subs/ita.m3u8"\n'
    '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="English",LANGUAGE="eng",URI="subs/eng.m3u8"\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=5000000,SUBTITLES="s"\nvideo/1080.m3u8\n'
)
SUB_PLAYLIST = (
    "#EXTM3U\n#EXTINF:600,\nita-0.vtt\n#EXTINF:600,\nita-1.vtt\n#EXTINF:600,\nita-2.vtt?token=t\n"
)
VTT = [
    "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:00.000\n\n1\n00:00:01.000 --> 00:00:02.000\nPrima\n",
    "WEBVTT\r\nX-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:00.000\r\n\r\n2\r\n00:10:01.000 --> 00:10:02.000\r\nSeconda\r\n",
    "\ufeffWEBVTT\n\n3\n00:20:01.000 --> 00:20:02.000\nTerza\n\n",
]


@pytest.fixture
def subtitle_server(monkeypatch):
    base = "https://vixcloud.test/playlist/"
    pages = {
        base + "42": _Response(200, MASTER),
        base + "subs/ita.m3u8": _Response(200, SUB_PLAYLIST),
        base + "subs/ita-0.vtt": _Response(200, VTT[0]),
        base + "subs/ita-1.vtt": _Response(200, VTT[1]),
        base + "subs/ita-2.vtt?token=t": _Response(200, VTT[2]),
        base + "subs/eng.m3u8": _Response(404),
    }
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return pages.get(url, _Response(404))

    monkeypatch.setattr(requests, "get", fake_get)
    return pages, calls


def _master_parser():
    parser = m3u8.M3U8_Parser()
    parser.parse_data(MASTER, base_uri="https://vixcloud.test/playlist/42")
    return parser


def test_every_subtitle_segment_is_downloaded_and_joined(tmp_path, subtitle_server):
    """Only the first segment was ever fetched: the whole film on a source that
    publishes one, the opening ten minutes on one that does not."""
    _, calls = subtitle_server

    done = fmt.download_subtitle_tracks(
        _master_parser(), ["ita"], str(tmp_path), "Film", temp_dir=str(tmp_path / "tmp"),
        referer="https://vixcloud.test/embed/42")

    assert [d["language"] for d in done] == ["ita"]
    text = open(done[0]["path"], encoding="utf-8").read()
    assert text.count("WEBVTT") == 1, "one header, not one per segment"
    assert text.startswith("WEBVTT\n")
    assert [line for line in text.splitlines() if line in ("Prima", "Seconda", "Terza")] \
        == ["Prima", "Seconda", "Terza"]
    assert "\r" not in text and "\ufeff" not in text
    # Fetched like everything else in the download: with its referer, an agent
    # and a timeout — these went out bare.
    for _, kwargs in calls:
        assert kwargs["headers"]["referer"] == "https://vixcloud.test/embed/42"
        assert kwargs["headers"]["user-agent"]
        assert kwargs["timeout"]


def test_a_subtitle_with_a_segment_missing_is_not_written_at_all(tmp_path, subtitle_server):
    pages, _ = subtitle_server
    pages["https://vixcloud.test/playlist/subs/ita-1.vtt"] = _Response(404)

    done = fmt.download_subtitle_tracks(
        _master_parser(), ["ita"], str(tmp_path), "Film", temp_dir=str(tmp_path / "tmp"))

    assert done == []
    assert os.listdir(tmp_path / "tmp") == [], "two thirds of a subtitle is not a subtitle"


def test_a_subtitle_playlist_on_an_internal_host_is_not_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("must not be requested"))
    parser = m3u8.M3U8_Parser()
    parser.parse_data(
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="I",LANGUAGE="ita",'
        'URI="https://192.168.1.1/subs.m3u8"\n')

    assert fmt.download_subtitle_tracks(parser, ["ita"], str(tmp_path), "Film") == []


@pytest.fixture
def no_video(monkeypatch):
    """The video itself is not what these are about."""
    monkeypatch.setattr(m3u8, "M3U8_Segments", _FakeSegments)
    monkeypatch.setattr(fmt, "remux", lambda video, **kwargs: video)
    m3u8._destination_locks.clear()


def test_a_requested_subtitle_that_did_not_arrive_is_on_the_result(tmp_path, subtitle_server, no_video):
    """The job finished as a plain success, with nothing to say a track the
    user had asked for was not in the file."""
    final = tmp_path / "lib" / "Film.mkv"

    result = m3u8.download_m3u8(
        m3u8_index="https://vixcloud.test/playlist/42",
        output_filename=str(final), temp_dir=str(tmp_path / "tmp"),
        referer="https://vixcloud.test/embed/42",
        subtitle_languages=["ita", "eng", "fra"],
    )

    # Still the path, for every caller that only wants that...
    assert result == str(final)
    assert isinstance(result, str)
    assert os.path.exists(result)
    # ...and what the file does not contain, for the ones that ask.
    assert result.missing_tracks == ["subtitle:eng", "subtitle:fra"]
    assert result.warnings == []


def test_a_master_that_cannot_be_read_reports_every_subtitle_missing(tmp_path, monkeypatch, no_video):
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(404))

    result = m3u8.download_m3u8(
        m3u8_index="https://vixcloud.test/playlist/42",
        output_filename=str(tmp_path / "lib" / "Film.mkv"), temp_dir=str(tmp_path / "tmp"),
        subtitle_languages=["ita"],
    )

    assert result.missing_tracks == ["subtitle:ita"]


def test_a_complete_download_has_nothing_missing(tmp_path, subtitle_server, no_video):
    result = m3u8.download_m3u8(
        m3u8_index="https://vixcloud.test/playlist/42",
        output_filename=str(tmp_path / "lib" / "Film.mkv"), temp_dir=str(tmp_path / "tmp"),
        subtitle_languages=["ita"],
    )

    assert result.missing_tracks == []


def test_what_the_caller_could_not_find_is_carried_through(tmp_path, monkeypatch, no_video):
    monkeypatch.setattr(m3u8, "_download_m3u8", lambda **k: k["output_filename"])

    result = m3u8.download_m3u8(output_filename=str(tmp_path / "Film.mkv"),
                                missing_tracks=["audio:fra"])

    assert result.missing_tracks == ["audio:fra"]


AUDIO_MASTER = (
    "#EXTM3U\n"
    '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="English",LANGUAGE="en",URI="audio/en.m3u8"\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=5000000,AUDIO="a"\nvideo/1080.m3u8\n'
)


def test_an_audio_language_skipped_without_strict_is_reported(monkeypatch):
    """Without strict a missing language is skipped — and used to be skipped
    in silence, so the file arrived in another language and said nothing."""
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(200, AUDIO_MASTER))
    missing = []

    tracks = film._collect_audio_tracks("https://vixcloud.test/playlist/42", "r",
                                        ["eng", "fra"], strict=False, missing=missing)

    # A relative rendition URI comes back absolute, ready to be fetched.
    assert tracks == [{"url": "https://vixcloud.test/playlist/audio/en.m3u8", "language": "eng"}]
    assert missing == ["audio:fra"]


def test_an_unreadable_master_reports_every_audio_language(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(500))
    missing = []

    assert film._collect_audio_tracks("https://vixcloud.test/playlist/42", "r",
                                      ["ita", "eng"], strict=False, missing=missing) == []
    assert missing == ["audio:ita", "audio:eng"]


def test_a_muxed_source_is_missing_nothing(monkeypatch):
    """No separate renditions: the only soundtrack is in the video, so nothing
    that was on offer has been left out."""
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(
        200, "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo/1080.m3u8\n"))
    missing = []

    assert film._collect_audio_tracks("https://vixcloud.test/playlist/42", "r",
                                      ["ita"], strict=False, missing=missing) == []
    assert missing == []


# ── A conversion that fails does not undo the download ────────────────────────

def test_a_failed_conversion_delivers_the_file_with_a_warning(tmp_path, monkeypatch, no_video):
    """TranscodeError out of download_m3u8 failed the job, and a failed job is
    retried from the first segment: hours of fetching repeated to arrive at the
    same encoder error. The original is intact by construction."""
    from app.core import transcode

    final = tmp_path / "Film.mkv"
    final.write_bytes(b"downloaded")
    monkeypatch.setattr(m3u8, "_download_m3u8", lambda **k: k["output_filename"])
    monkeypatch.setattr(config, "get_settings", lambda: {"video_conversion": "hevc"})

    def refuse(path, settings, progress_factory=None, cancel_event=None):
        raise transcode.TranscodeError("Nessun encoder utilizzabile")

    monkeypatch.setattr(transcode, "convert_video", refuse)

    result = m3u8.download_m3u8(output_filename=str(final), missing_tracks=["audio:fra"])

    assert result == str(final)
    assert final.read_bytes() == b"downloaded"
    assert len(result.warnings) == 1 and "Nessun encoder utilizzabile" in result.warnings[0]
    assert result.missing_tracks == ["audio:fra"]


def test_a_cancel_during_conversion_is_still_a_cancel(tmp_path, monkeypatch, no_video):
    from app.core import transcode

    monkeypatch.setattr(m3u8, "_download_m3u8", lambda **k: k["output_filename"])
    monkeypatch.setattr(config, "get_settings", lambda: {"video_conversion": "hevc"})

    def cancelled(*args, **kwargs):
        raise DownloadCancelledError("Conversione annullata")

    monkeypatch.setattr(transcode, "convert_video", cancelled)

    with pytest.raises(DownloadCancelledError):
        m3u8.download_m3u8(output_filename=str(tmp_path / "Film.mkv"))


def test_a_successful_conversion_keeps_the_missing_tracks(tmp_path, monkeypatch, no_video):
    from app.core import transcode

    monkeypatch.setattr(m3u8, "_download_m3u8",
                        lambda **k: DownloadResult(k["output_filename"], ["subtitle:eng"]))
    monkeypatch.setattr(config, "get_settings", lambda: {"video_conversion": "hevc"})
    monkeypatch.setattr(transcode, "convert_video", lambda path, *a, **k: str(path))

    result = m3u8.download_m3u8(output_filename=str(tmp_path / "Film.mkv"))

    assert result.missing_tracks == ["subtitle:eng"]
    assert result.warnings == []


# ── FFmpeg can be stopped ─────────────────────────────────────────────────────

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]


def test_a_running_ffmpeg_is_stopped_when_the_job_is_cancelled():
    """subprocess.run could be neither cancelled nor timed out: a cancelled job
    kept its FFmpeg, its slot and the destination lock until the process ended
    by itself."""
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()

    with pytest.raises(DownloadCancelledError):
        ffmpeg_path.run_command(SLEEPER, cancel_event=cancel)

    assert time.monotonic() - started < 30


def test_a_stuck_ffmpeg_is_stopped_at_the_timeout():
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="FFmpeg interrotto"):
        ffmpeg_path.run_command(SLEEPER, timeout=0.5)

    assert time.monotonic() - started < 30


def test_the_output_and_exit_code_come_back():
    code, out, err = ffmpeg_path.run_command(
        [sys.executable, "-c", "import sys; print('out'); sys.stderr.write('x' * 200000); sys.exit(3)"])

    assert code == 3
    assert out.strip() == b"out"
    # More than a pipe holds: output is spooled to a file, so a chatty FFmpeg
    # cannot block on a pipe nobody is draining while this polls.
    assert len(err) == 200000


def test_the_command_is_a_list_and_never_a_shell_string():
    with pytest.raises(TypeError):
        ffmpeg_path.run_command("ffmpeg -i in.ts out.mkv; rm -rf /")


def test_the_timeout_is_two_hours():
    assert ffmpeg_path.FFMPEG_TIMEOUT == 2 * 60 * 60


def test_the_remux_passes_the_cancel_flag_and_cleans_up_when_stopped(tmp_path, monkeypatch):
    library = tmp_path / "lib"
    library.mkdir()
    video = library / "Film.mkv"
    video.write_bytes(b"joined")
    cancel = threading.Event()
    seen = {}

    def run(cmd, cancel_event=None, **kwargs):
        seen["cancel"] = cancel_event
        seen["cmd"] = cmd
        with open(cmd[-1].removeprefix("file:"), "wb") as handle:
            handle.write(b"half")
        raise DownloadCancelledError("Download annullato dall'utente")

    monkeypatch.setattr(fmt, "run_command", run)

    with pytest.raises(DownloadCancelledError):
        fmt.remux(str(video), audio_tracks=[{"path": "a.mp4", "language": "ita"}],
                  cancel_event=cancel)

    assert seen["cancel"] is cancel
    assert isinstance(seen["cmd"], list)
    assert video.read_bytes() == b"joined"
    assert os.listdir(library) == ["Film.mkv"]


# ── File names ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("CON", "_CON"),
    ("con", "_con"),
    ("NUL", "_NUL"),
    ("PRN", "_PRN"),
    ("AUX", "_AUX"),
    ("COM1", "_COM1"),
    ("LPT9", "_LPT9"),
    ("CON.mkv", "_CON.mkv"),
    ("nul.tar.gz", "_nul.tar.gz"),
    ("CON ", "_CON"),            # Windows ignores the trailing space
    ("Aux: The Movie", "Aux The Movie"),
    ("Console", "Console"),
    ("COM10", "COM10"),
    ("Contact", "Contact"),
    ("LPT", "LPT"),
])
def test_windows_device_names_are_prefixed(name, expected):
    """A file called CON.mkv cannot be created on Windows, extension or not —
    and the library is routinely an SMB share written from Linux."""
    assert sanitize_filename(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("Title.", "Title"),
    ("Title. . ", "Title"),
    ("A" * 179 + " B", "A" * 179),   # the cut must not leave a trailing space
])
def test_a_name_never_ends_in_a_dot_or_a_space(name, expected):
    assert sanitize_filename(name) == expected


def test_only_the_caption_is_cut_to_fit():
    assert fit_title("Pilot", taken=30) == "Pilot"
    assert fit_title("x" * 400, taken=30) == "x" * (MAX_FILENAME - 30)
    assert fit_title("word " * 80, taken=30).endswith("word")
    # No room for anything worth reading: the caption goes, the name stays.
    assert fit_title("Pilot episode", taken=MAX_FILENAME - 3) == ""


@pytest.fixture
def episode(tmp_path, monkeypatch):
    """download_episode with resolution and the download itself stood in for."""
    import app.library

    captured = {}
    monkeypatch.setattr(tv, "_get_iframe",
                        lambda *a: ("script", "https://vixcloud.test/embed/9?token=t&expires=1"))
    monkeypatch.setattr(tv, "_parse_content",
                        lambda *a: ({"id": "9", "host": "vixcloud.test"}, {"token": "t", "expires": "1"}))
    monkeypatch.setattr(tv, "_get_m3u8_key", lambda *a: "aa" * 16)
    monkeypatch.setattr(tv, "_collect_audio_tracks", lambda *a, **k: [])
    monkeypatch.setattr(tv, "_collect_subtitle_tracks", lambda *a, **k: [])
    monkeypatch.setattr(app.library, "destination",
                        lambda *a, **k: str(tmp_path / "Show" / "Season 01" / "Show S01E01.mkv"))

    def fake_download(**kwargs):
        captured.update(kwargs)
        return kwargs["output_filename"]

    monkeypatch.setattr(tv, "download_m3u8", fake_download)

    def run(name):
        tv.download_episode(1, [{"id": 5, "n": 1, "name": name}], 0, "example.test", "xsrf",
                            "Show", 1, output_dir=str(tmp_path))
        return captured

    return run


def test_an_episode_without_a_caption_downloads(episode):
    """quote(None) is a TypeError, raised while building the referer: every
    episode the source lists without a name failed before it started."""
    captured = episode(None)

    assert "description=S1%3AE1+&nextEpisode=1" in captured["referer"]
    assert os.path.basename(captured["output_filename"]) == "Show S01E01.mkv"


def test_a_very_long_caption_is_cut_so_the_name_and_its_staging_fit(episode):
    captured = episode("Una lunghissima didascalia " * 30)
    name = os.path.basename(captured["output_filename"])

    assert name.startswith("Show S01E01 - Una lunghissima didascalia")
    assert name.endswith(".mkv")
    assert len(name) <= MAX_FILENAME
    # The longest name written beside it: the conversion's staging file.
    longest_staging = len(".") + len(name) + len(".converting-") + 32
    assert longest_staging <= 255
    for suffix in (fmt.WORK_SUFFIX, fmt.JOIN_SUFFIX, fmt.STAGING_SUFFIX):
        assert len(os.path.basename(fmt.staging_path(fmt.staging_path(name, fmt.WORK_SUFFIX), suffix))) <= 255


def test_an_ordinary_caption_is_untouched(episode):
    captured = episode("Il ritorno")
    assert os.path.basename(captured["output_filename"]) == "Show S01E01 - Il ritorno.mkv"


# ── Domain recovery proposes; it applies only when asked to ───────────────────

@pytest.fixture
def telegram(monkeypatch):
    domain_recovery.clear_pending()
    domain_recovery._last_check_at = None
    monkeypatch.setattr(domain_recovery.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(domain_recovery, "get_domain_version", lambda host: "v1")
    monkeypatch.setattr(domain_recovery, "fetch_telegram_candidates",
                        lambda: ["streamingcommunityz.rodeo"])
    monkeypatch.setattr(domain_recovery, "fetch_candidates", lambda: [])
    monkeypatch.setattr(domain_recovery, "_announce", lambda *a, **k: None)
    monkeypatch.setattr(domain_recovery, "_broadcast_candidate", lambda: None)
    yield
    domain_recovery.clear_pending()
    domain_recovery._last_check_at = None


def test_a_telegram_candidate_is_proposed_when_auto_apply_is_off(telegram):
    """The module's own docstring: propose, do not adopt. A Telegram candidate
    was applied regardless of the setting — a channel edited by strangers
    deciding where every request and every referer of this panel goes."""
    assert not config.get_settings().get("domain_auto_apply")

    result = domain_recovery.run_check()

    assert result["candidate"] == "streamingcommunityz.rodeo"
    assert result["applied"] is False
    assert domain_recovery.pending()["host"] == "streamingcommunityz.rodeo"
    assert config.configured_domain() == "example.test"


def test_a_telegram_candidate_is_applied_when_auto_apply_is_on(telegram):
    _configure(domain_auto_apply=True)

    result = domain_recovery.run_check()

    assert result["applied"] is True
    assert config.configured_domain() == "streamingcommunityz.rodeo"
