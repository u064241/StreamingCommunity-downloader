"""Where the downloader may be sent, how long it waits, and what it logs.

Every URL past the configured domain is somebody else's: the iframe names the
embed, the embed names the playlist, the playlist names variants, segments,
audio and subtitles. None of them was checked, so the panel would fetch
whatever a page told it to — including addresses inside the network it runs
in. The rest of this file is the waiting around those requests: a watchdog
that fired on a wait the source had asked for, a second pass with no end,
sleeps and a lock that a cancel could not interrupt.
"""

import threading
import time

import pytest
import requests

from app.core import _shared, animeunity, film, hosts, m3u8, tv
from app.core.hosts import UnsafeURLError, assert_public_https, redact, safe_get
from app.core.m3u8 import M3U8_Segments
from app.progress import DownloadCancelledError

PUBLIC = "93.184.216.34"


class _Response:
    def __init__(self, status_code=200, text="", content=b"x" * 10, headers=None):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _Bar:
    def __init__(self, total=0, **kwargs):
        self.total, self.n = total, 0

    def update(self, n=1, bytes=0):
        self.n += n

    def close(self):
        pass

    def refresh(self):
        pass


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """A resolver the test controls: public unless a host is listed otherwise."""
    table: dict[str, list[str] | None] = {}
    hosts._dns_cache.clear()
    monkeypatch.setattr(hosts, "_resolve", lambda host, port: table.get(host, [PUBLIC]))
    yield table
    hosts._dns_cache.clear()


@pytest.fixture
def no_sleep(monkeypatch):
    # One patch covers m3u8's backoff and _shared.with_retry alike: both reach
    # the same ``time`` module.
    slept = []
    monkeypatch.setattr(m3u8.time, "sleep", slept.append)
    return slept


def _segments(tmp_path, count=3, **kwargs):
    seg = M3U8_Segments("https://cdn.example.test/playlist.m3u8",
                        temp_dir=str(tmp_path / "segs"),
                        progress_factory=lambda **kw: _Bar(**kw), **kwargs)
    seg.segments = [f"https://cdn.example.test/seg-{i}.ts" for i in range(count)]
    return seg


# ── assert_public_https ───────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://cdn.example.test/seg.ts",       # not https
    "ftp://cdn.example.test/seg.ts",
    "file:///etc/passwd",
    "//cdn.example.test/seg.ts",            # no scheme
    "seg-0.ts",
    "",
    "https:///seg.ts",                      # no host
    "https://127.0.0.1/admin",              # loopback
    "https://[::1]/admin",
    "https://10.0.0.5/x",                   # private
    "https://192.168.1.1/x",
    "https://172.16.0.9/x",
    "https://169.254.169.254/latest/meta-data/",  # link-local: cloud metadata
    "https://[fe80::1]/x",
    "https://[::ffff:10.0.0.5]/x",          # private, wearing an IPv6 address
    "https://0.0.0.0/x",
    "https://100.64.0.1/x",                 # carrier-grade NAT
])
def test_a_url_that_is_not_https_to_a_public_host_is_refused(url):
    with pytest.raises(UnsafeURLError):
        assert_public_https(url)


def test_a_public_https_url_is_returned_unchanged():
    assert assert_public_https("https://cdn.example.test/a?b=1") == "https://cdn.example.test/a?b=1"
    assert assert_public_https("https://8.8.8.8/x") == "https://8.8.8.8/x"


@pytest.mark.parametrize("answer", [
    ["127.0.0.1"], ["10.1.2.3"], ["169.254.169.254"], ["::1"],
    [PUBLIC, "192.168.0.10"],  # one bad address among good ones is enough
])
def test_a_name_that_resolves_to_a_non_public_address_is_refused(dns, answer):
    dns["internal.example.test"] = answer

    with pytest.raises(UnsafeURLError, match="non pubblico"):
        assert_public_https("https://internal.example.test/x")


def test_a_name_that_does_not_resolve_is_left_to_the_request(dns):
    """No address means nowhere to be sent, and the request fails by itself. A
    resolver that is briefly unwell must stay a retriable network error — the
    comment in m3u8.py on "Temporary failure in name resolution" is about
    exactly that — rather than become a verdict."""
    dns["flaky.example.test"] = None

    assert assert_public_https("https://flaky.example.test/x")


def test_the_lookup_is_done_once_per_host_not_once_per_segment(monkeypatch):
    lookups = []
    monkeypatch.setattr(hosts, "_resolve", lambda host, port: lookups.append(host) or [PUBLIC])

    for index in range(500):
        assert_public_https(f"https://cdn.example.test/seg-{index}.ts")

    assert lookups == ["cdn.example.test"]


# ── safe_get: every hop ───────────────────────────────────────────────────────

def test_a_redirect_to_an_internal_address_is_not_followed():
    """A public host must not be able to bounce the panel inside."""
    requested = []

    def get(url, **kwargs):
        requested.append((url, kwargs.get("allow_redirects")))
        return _Response(302, headers={"Location": "https://169.254.169.254/latest/"})

    with pytest.raises(UnsafeURLError):
        safe_get(get, "https://cdn.example.test/seg.ts", timeout=5)

    assert requested == [("https://cdn.example.test/seg.ts", False)]


def test_a_redirect_to_a_public_host_is_followed_and_revalidated(dns):
    requested = []

    def get(url, **kwargs):
        requested.append(url)
        if url.endswith("/a"):
            return _Response(302, headers={"Location": "/b"})
        if url.endswith("/b"):
            return _Response(307, headers={"Location": "https://edge.example.test/c"})
        return _Response(200, content=b"done")

    assert safe_get(get, "https://cdn.example.test/a").content == b"done"
    assert requested == ["https://cdn.example.test/a", "https://cdn.example.test/b",
                         "https://edge.example.test/c"]


def test_a_redirect_loop_ends():
    def get(url, **kwargs):
        return _Response(302, headers={"Location": url})

    with pytest.raises(UnsafeURLError, match="reindirizzamenti"):
        safe_get(get, "https://cdn.example.test/a")


# ── The call sites ────────────────────────────────────────────────────────────

@pytest.fixture
def session_get(monkeypatch):
    calls = []
    replies = []

    def fake_get(self, url, *args, **kwargs):
        calls.append((url, kwargs))
        reply = replies.pop(0) if replies else _Response(200)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(requests.Session, "get", fake_get)
    return replies, calls


def test_a_segment_on_an_internal_host_is_never_requested(tmp_path, session_get, no_sleep):
    _, calls = session_get
    seg = _segments(tmp_path)
    seg.segments = ["https://192.168.1.10/seg-0.ts", "https://192.168.1.10/seg-1.ts"]

    with pytest.raises(RuntimeError, match="URL di un segmento non valido"):
        seg.download_ts()

    assert calls == []


@pytest.mark.parametrize("bad", ["seg-0.ts", "//cdn/seg-0.ts", "http://cdn.example.test/seg-0.ts"])
def test_an_unusable_segment_url_is_fatal_at_once_not_after_three_tries(
    tmp_path, session_get, no_sleep, bad
):
    """MissingSchema used to be retried like an outage, three times per segment
    with backoff between, for every segment of the playlist."""
    _, calls = session_get
    seg = _segments(tmp_path, count=50)
    seg.segments = [bad] * 50

    with pytest.raises(RuntimeError, match="URL di un segmento non valido"):
        seg.download_ts()

    assert calls == []
    assert no_sleep == []


def test_requests_own_invalid_url_errors_are_not_retried_either(tmp_path, session_get, no_sleep):
    replies, calls = session_get
    replies.append(requests.exceptions.InvalidURL("bad label"))
    seg = _segments(tmp_path, count=1)

    assert seg.get_req_ts("https://cdn.example.test/seg-0.ts") is None
    assert len(calls) == 1
    assert seg._fatal and seg._abort.is_set()


def test_a_playlist_on_an_internal_host_is_refused_without_retrying(tmp_path, session_get, no_sleep):
    _, calls = session_get
    seg = M3U8_Segments("https://10.0.0.7/playlist.m3u8", temp_dir=str(tmp_path / "s"))

    with pytest.raises(RuntimeError, match="URL non valido"):
        seg.get_info()

    assert calls == []
    assert no_sleep == []


def test_a_variant_on_an_internal_host_is_refused(tmp_path, session_get, no_sleep):
    replies, calls = session_get
    replies.append(_Response(200, text=(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttps://127.0.0.1:8096/System/Info\n")))
    seg = M3U8_Segments("https://cdn.example.test/master.m3u8", temp_dir=str(tmp_path / "s"))

    with pytest.raises(RuntimeError, match="URL non valido"):
        seg.get_info()

    assert [url for url, _ in calls] == ["https://cdn.example.test/master.m3u8"]


def test_a_relative_variant_is_resolved_against_the_master(tmp_path, session_get, no_sleep):
    replies, calls = session_get
    replies.extend([
        _Response(200, text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n1080/playlist.m3u8\n"),
        _Response(200, text="#EXTM3U\n#EXTINF:4,\nseg-0.ts\n"),
    ])
    seg = M3U8_Segments("https://cdn.example.test/hls/master.m3u8?token=t",
                        temp_dir=str(tmp_path / "s"))

    seg.get_info()

    assert calls[1][0] == "https://cdn.example.test/hls/1080/playlist.m3u8"
    assert seg.segments == ["https://cdn.example.test/hls/1080/seg-0.ts"]


def test_segments_are_requested_without_letting_requests_follow_redirects(
    tmp_path, session_get, no_sleep
):
    _, calls = session_get
    _segments(tmp_path, count=1).get_req_ts("https://cdn.example.test/seg-0.ts")

    assert calls[0][1]["allow_redirects"] is False


def test_the_embed_url_is_validated_before_the_scraper_goes_there(monkeypatch, no_sleep):
    reached = []

    class Scraper:
        def get(self, url, **kwargs):
            reached.append(url)
            return _Response(200, text="<html><body><script>x</script></body></html>")

    monkeypatch.setattr(_shared, "_get_scraper", lambda: Scraper())

    with pytest.raises(UnsafeURLError):
        _shared._fetch_vixcloud_embed("https://127.0.0.1/embed/1?token=t")
    with pytest.raises(UnsafeURLError):
        _shared._fetch_vixcloud_embed("http://vixcloud.test/embed/1?token=t")

    assert reached == []


def test_the_embed_fetch_is_retried(monkeypatch, no_sleep):
    """It went out once: a timeout during resolution threw the whole job away."""
    attempts = []

    class Scraper:
        def get(self, url, **kwargs):
            attempts.append(url)
            if len(attempts) < 3:
                raise requests.exceptions.Timeout()
            return _Response(200, text="<html><body><script>window.video</script></body></html>")

    monkeypatch.setattr(_shared, "_get_scraper", lambda: Scraper())

    assert _shared._fetch_vixcloud_embed("https://vixcloud.test/embed/1") == "window.video"
    assert len(attempts) == 3


def test_an_anime_embed_url_pointing_inside_is_refused(monkeypatch, no_sleep):
    reached = []

    class Scraper:
        def get(self, url, **kwargs):
            reached.append(url)
            return _Response(200, text="https://192.168.0.2/embed/9?token=t")

    monkeypatch.setattr(animeunity, "_get_scraper", lambda: Scraper())
    monkeypatch.setattr(animeunity, "host", lambda: "animeunity.example.test")

    with pytest.raises(UnsafeURLError):
        animeunity._get_embed_content(7)

    assert reached == ["https://animeunity.example.test/embed-url/7"]


def test_an_anime_embed_answer_that_is_not_a_url_is_not_quoted(monkeypatch, no_sleep):
    class Scraper:
        def get(self, url, **kwargs):
            return _Response(200, text="<html>SECRET-ERROR-PAGE " + "x" * 500 + "</html>")

    monkeypatch.setattr(animeunity, "_get_scraper", lambda: Scraper())
    monkeypatch.setattr(animeunity, "host", lambda: "animeunity.example.test")

    with pytest.raises(RuntimeError) as excinfo:
        animeunity._get_embed_content(7)

    assert "SECRET-ERROR-PAGE" not in str(excinfo.value)
    assert "HTTP 200" in str(excinfo.value)


# ── Playlists fetched outside a download's session ────────────────────────────

def test_the_language_probe_sends_its_referer_and_an_agent(monkeypatch, no_sleep):
    """``fetch_master_languages`` took a referer and never used it: the request
    went out bare, to a host that checks."""
    seen = []

    def fake_get(url, **kwargs):
        seen.append(kwargs)
        return _Response(200, text=(
            "#EXTM3U\n"
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Italiano",LANGUAGE="it",URI="a.m3u8"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="a"\nv.m3u8\n'))

    monkeypatch.setattr(m3u8.requests, "get", fake_get)

    langs = m3u8.fetch_master_languages("https://vixcloud.test/playlist/1",
                                        "https://vixcloud.test/embed/1")

    assert langs == {"audio": ["it"], "subtitles": []}
    assert seen[0]["headers"]["referer"] == "https://vixcloud.test/embed/1"
    assert seen[0]["headers"]["user-agent"]
    assert seen[0]["timeout"]


def test_the_playlist_helpers_retry_a_flap(monkeypatch, no_sleep):
    replies = [requests.exceptions.ConnectionError(), _Response(503), _Response(200, text="#EXTM3U\n")]

    def fake_get(url, **kwargs):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(m3u8.requests, "get", fake_get)

    assert m3u8._fetch_text("https://vixcloud.test/playlist/1") == "#EXTM3U\n"
    assert replies == []


def test_the_playlist_helpers_refuse_an_internal_url(monkeypatch):
    monkeypatch.setattr(m3u8.requests, "get",
                        lambda *a, **k: pytest.fail("must not be requested"))

    with pytest.raises(UnsafeURLError):
        m3u8._fetch_text("https://10.0.0.1/playlist")
    with pytest.raises(UnsafeURLError):
        m3u8._fetch_text_with_b1_fallback("http://vixcloud.test/playlist/1")


def test_an_index_url_is_judged_by_its_scheme_not_by_a_substring(tmp_path, monkeypatch):
    """"https" appears in plenty of things that are not https URLs."""
    monkeypatch.setattr(m3u8, "M3U8_Downloader",
                        lambda *a, **k: pytest.fail("must not start a download"))
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttp://cdn.example.test/https/v.m3u8\n"

    with pytest.raises(RuntimeError, match="valid M3U8 index"):
        m3u8._download_m3u8(m3u8_playlist=master, output_filename=str(tmp_path / "o.mkv"))


# ── Resolution: retried, and honest about a missing iframe ────────────────────

IFRAME_PAGE = '<html><body><iframe src="https://vixcloud.test/embed/42?token=SECRETTOKEN&expires=1999999999"></iframe></body></html>'


def test_the_film_iframe_is_retried_and_logs_no_token(monkeypatch, no_sleep, caplog):
    replies = [requests.exceptions.Timeout(), _Response(503), _Response(200, text=IFRAME_PAGE)]
    asked = []

    def fake_get(url, **kwargs):
        asked.append(url)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(film, "_fetch_vixcloud_embed",
                        lambda url, referer=None: "window.video={id:'42'}; token: 'SECRETTOKEN'")

    with caplog.at_level("DEBUG"):
        script, url_embed = film._get_iframe(1, "example.test")

    assert len(asked) == 3
    assert url_embed.startswith("https://vixcloud.test/embed/42")
    assert "SECRETTOKEN" not in caplog.text
    assert "1999999999" not in caplog.text


@pytest.mark.parametrize("page", ["<html><body>Titolo rimosso</body></html>",
                                  "<html><body><iframe></iframe></body></html>"])
def test_a_page_without_the_iframe_says_so(monkeypatch, no_sleep, page):
    """It used to be "'NoneType' object has no attribute 'get'"."""
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(200, text=page))

    with pytest.raises(RuntimeError, match="iframe"):
        film._get_iframe(1, "example.test")
    with pytest.raises(RuntimeError, match="iframe"):
        tv._get_iframe(1, 2, "example.test", "xsrf")


def test_the_episode_iframe_is_retried(monkeypatch, no_sleep):
    replies = [requests.exceptions.ConnectionError(), _Response(200, text=IFRAME_PAGE)]

    def fake_get(url, **kwargs):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(tv, "_fetch_vixcloud_embed", lambda url, referer=None: "script")

    assert tv._get_iframe(1, 2, "example.test", "xsrf")[0] == "script"
    assert replies == []


def test_the_xsrf_token_fetch_is_retried(monkeypatch, no_sleep):
    attempts = []

    def fake_get(self, url, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            raise requests.exceptions.Timeout()
        self.cookies.set("XSRF-TOKEN", "abc%3D")
        return _Response(200)

    monkeypatch.setattr(requests.Session, "get", fake_get)

    assert tv.get_token(5, "example.test") == "abc="
    assert len(attempts) == 2


# ── Nothing of the response, and no token, in errors or logs ──────────────────

def test_the_token_and_expiry_are_redacted():
    url = "https://vixcloud.test/playlist/1?token=abcDEF123&expires=1999999999&h=1"

    assert redact(url) == "https://vixcloud.test/playlist/1?token=<redacted>&expires=<redacted>&h=1"
    assert redact("Max retries exceeded with url: /p?b=1&token=zzz (Caused by X)") \
        == "Max retries exceeded with url: /p?b=1&token=<redacted> (Caused by X)"
    assert redact("nothing secret here") == "nothing secret here"


def test_an_embed_that_cannot_be_parsed_does_not_quote_the_page():
    """The message lands on job.error, the notification bell and a webhook, and
    used to carry the first 400 characters of the page — token included."""
    page = "window.nothing = 1; params: {'token': 'SECRETTOKEN', 'expires': '1999999999'}" + "x" * 400

    with pytest.raises(RuntimeError) as excinfo:
        _shared._parse_content(page, "https://vixcloud.test/embed/1?token=SECRETTOKEN")

    assert "SECRETTOKEN" not in str(excinfo.value)
    assert f"{len(page)} characters" in str(excinfo.value)


def test_a_refused_embed_url_does_not_put_its_query_in_the_error():
    with pytest.raises(RuntimeError) as excinfo:
        _shared.embed_host("http://vixcloud.test/embed/1?token=SECRETTOKEN&expires=9")

    assert "SECRETTOKEN" not in str(excinfo.value)


def test_a_playlist_that_is_not_one_reports_status_and_length_only(tmp_path, session_get, no_sleep):
    replies, _ = session_get
    body = "#EXTM3U\n# private notice SECRET-BODY " + "y" * 300
    replies.append(_Response(200, text=body))
    seg = M3U8_Segments("https://cdn.example.test/p.m3u8", temp_dir=str(tmp_path / "s"))

    with pytest.raises(RuntimeError) as excinfo:
        seg.get_info()

    assert "SECRET-BODY" not in str(excinfo.value)
    assert f"HTTP 200, {len(body)} characters" in str(excinfo.value)


def test_a_403_on_a_playlist_does_not_log_its_token(tmp_path, session_get, no_sleep, caplog):
    replies, _ = session_get
    replies.extend([_Response(403), _Response(200, text="#EXTM3U\n#EXTINF:4,\nseg-0.ts?token=SEGTOKEN\n")])
    seg = M3U8_Segments("https://cdn.example.test/p.m3u8?token=SECRETTOKEN&expires=1999999999",
                        temp_dir=str(tmp_path / "s"))

    with caplog.at_level("DEBUG"):
        seg.get_info()

    assert "SECRETTOKEN" not in caplog.text
    assert "SEGTOKEN" not in caplog.text
    assert "1999999999" not in caplog.text


# ── The watchdog and Retry-After ──────────────────────────────────────────────

def test_the_watchdog_outlasts_an_honoured_retry_after(tmp_path):
    """Thirty seconds of watchdog against thirty seconds of Retry-After: the
    source said "come back in 30", every worker did, and the download was
    killed for stalling."""
    seg = _segments(tmp_path)

    assert seg.progress_timeout >= 90
    assert seg.progress_timeout >= m3u8.MAX_RETRY_AFTER + m3u8.SEGMENT_TIMEOUT[1] + 30


def test_a_retry_after_wait_moves_the_heartbeat(tmp_path, session_get, no_sleep):
    replies, _ = session_get
    replies.extend([_Response(503, headers={"Retry-After": "30"}), _Response(200, content=b"ok")])
    seg = _segments(tmp_path, count=1)
    before = time.monotonic()

    assert seg.get_req_ts("https://cdn.example.test/seg-0.ts") == b"ok"

    assert no_sleep == [30.0]
    assert seg._heartbeat >= before + 30


def _run_timer(seg, bar, seconds):
    fired, quit_event = threading.Event(), threading.Event()
    thread = threading.Thread(target=seg.timer, args=(bar, quit_event, fired.set))
    thread.start()
    fired.wait(seconds)
    quit_event.set()
    thread.join(5)
    return fired.is_set()


def test_the_watchdog_reads_the_heartbeat(tmp_path):
    """Waiting as the source asked is not a source that stopped answering."""
    seg = _segments(tmp_path)
    seg.progress_timeout = 0.05

    seg._heartbeat = time.monotonic() + 60
    assert _run_timer(seg, _Bar(), 0.4) is False

    seg._heartbeat = time.monotonic() - 60
    assert _run_timer(seg, _Bar(), 3) is True


# ── The sequential pass ends ──────────────────────────────────────────────────

def test_the_sequential_pass_stops_after_a_run_of_failures(tmp_path, session_get, no_sleep):
    """It had no limit: each lost segment cost three attempts and their backoff,
    one after another, for as many segments as a dead source had lost."""
    replies, calls = session_get
    replies.extend([_Response(503)] * 5000)
    seg = _segments(tmp_path, count=300)
    seg._failed_segments = set(range(300))

    with pytest.raises(RuntimeError, match="recupero sequenziale abbandonato") as excinfo:
        seg._retry_failed_sequentially(_Bar(total=300))

    assert "20 segmenti consecutivi" in str(excinfo.value)
    assert len(calls) == m3u8.SEQUENTIAL_MAX_STREAK * seg.max_retry
    # What was not reached is still missing, and is counted as such.
    assert len(seg._failed_segments) == 300


def test_a_recovery_resets_the_run(tmp_path, monkeypatch, no_sleep):
    """A run, not a total — the same shape as the parallel pass's breaker: a
    source serving one segment in ten is throttling, and is worth waiting for."""
    def fake_get(self, url, *args, **kwargs):
        index = int(url.rsplit("seg-", 1)[1].removesuffix(".ts"))
        return _Response(200) if index % 10 == 9 else _Response(404)

    monkeypatch.setattr(requests.Session, "get", fake_get)
    seg = _segments(tmp_path, count=200)
    seg._failed_segments = set(range(200))
    bar = _Bar(total=200)

    seg._retry_failed_sequentially(bar)

    assert bar.n == 20
    assert len(seg._failed_segments) == 180


def test_the_sequential_pass_has_a_time_budget(tmp_path, session_get, no_sleep, monkeypatch):
    _, calls = session_get
    monkeypatch.setattr(m3u8, "SEQUENTIAL_BUDGET", -1)
    seg = _segments(tmp_path, count=5)
    seg._failed_segments = {0, 1, 2}

    with pytest.raises(RuntimeError, match="superato il limite"):
        seg._retry_failed_sequentially(_Bar(total=5))

    assert calls == []


# ── Waits a cancel can interrupt ──────────────────────────────────────────────

def test_a_backoff_ends_when_the_job_is_cancelled(tmp_path, session_get):
    """time.sleep could not be interrupted: a cancelled download sat out every
    pending backoff — up to thirty seconds each — before looking at the flag."""
    replies, calls = session_get
    replies.extend([_Response(503, headers={"Retry-After": "30"})] * 3)
    cancel = threading.Event()
    seg = _segments(tmp_path, count=1, cancel_event=cancel)
    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()

    assert seg.get_req_ts("https://cdn.example.test/seg-0.ts") is None

    assert time.monotonic() - started < 10
    assert len(calls) == 1


def test_the_sequential_pass_notices_a_cancel_while_waiting(tmp_path, session_get):
    cancel = threading.Event()
    cancel.set()
    seg = _segments(tmp_path, count=2, cancel_event=cancel)
    seg._failed_segments = {0, 1}

    with pytest.raises(DownloadCancelledError):
        seg._retry_failed_sequentially(_Bar(total=2))


def test_a_playlist_retry_wait_is_cancellable(tmp_path, session_get):
    replies, calls = session_get
    replies.extend([_Response(500)] * 3)
    cancel = threading.Event()
    cancel.set()
    seg = M3U8_Segments("https://cdn.example.test/p.m3u8", temp_dir=str(tmp_path / "s"),
                        cancel_event=cancel)

    with pytest.raises(DownloadCancelledError):
        seg.get_info()

    assert len(calls) == 1


# ── The destination lock ──────────────────────────────────────────────────────

@pytest.fixture
def clean_locks():
    m3u8._destination_locks.clear()
    yield
    m3u8._destination_locks.clear()


def test_a_download_waiting_for_the_destination_can_be_cancelled(tmp_path, monkeypatch, clean_locks):
    """The wait was a bare acquire(): a job queued behind another download of
    the same file could not be cancelled until that one had finished."""
    path = str(tmp_path / "Film.mkv")
    monkeypatch.setattr(m3u8, "_download_m3u8",
                        lambda **k: pytest.fail("must not start while the file is held"))
    held = m3u8._acquire_destination(path)
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()

    try:
        with pytest.raises(DownloadCancelledError):
            m3u8.download_m3u8(output_filename=path, cancel_event=cancel)
        assert time.monotonic() - started < 5
    finally:
        m3u8._release_destination(held)

    assert m3u8._destination_locks == {}


def test_the_lock_table_does_not_keep_every_path_ever_downloaded(tmp_path, monkeypatch, clean_locks):
    monkeypatch.setattr(m3u8, "_download_m3u8", lambda **k: k["output_filename"])

    for index in range(50):
        m3u8.download_m3u8(output_filename=str(tmp_path / f"Film {index}.mkv"))

    assert m3u8._destination_locks == {}


def test_a_failed_download_releases_its_lock(tmp_path, monkeypatch, clean_locks):
    def boom(**kwargs):
        raise RuntimeError("source down")

    monkeypatch.setattr(m3u8, "_download_m3u8", boom)
    path = str(tmp_path / "Film.mkv")

    with pytest.raises(RuntimeError, match="source down"):
        m3u8.download_m3u8(output_filename=path)

    assert m3u8._destination_locks == {}
    m3u8._release_destination(m3u8._acquire_destination(path))  # free to take again
