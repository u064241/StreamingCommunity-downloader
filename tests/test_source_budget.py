"""The source budget: how many requests, when to stop, and what the caches do then.

Nothing here touches the network: ``page.requests.get`` is faked throughout.
"""

import html
import json
import logging
import threading
import time

import pytest
import requests

from app.core import catalog, home, metadata, page, source_budget


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(source_budget.ENV_REQUESTS_PER_HOUR, raising=False)
    source_budget.reset()
    home.clear_cache()
    yield
    source_budget.reset()
    home.clear_cache()


class _Response:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code
        self.ok = status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _html(props, version="v1"):
    payload = {"props": props, "version": version}
    return ('<html><body><div id="app" data-page="'
            + html.escape(json.dumps(payload), quote=True) + '"></div></body></html>')


def _title(n):
    return {"id": n, "name": f"Title {n}", "slug": f"t-{n}", "type": "movie",
            "images": [{"type": "poster", "filename": f"p{n}.jpg"}]}


@pytest.fixture
def source(monkeypatch):
    """A fake source. ``status`` and ``props`` can change between calls."""
    state = {"calls": [], "status": 200, "version": "v1",
             "props": {"titles": [_title(1)], "sliders": [
                 {"name": "trending", "titles": [_title(1)]}]}}

    def fake_get(url, *args, **kwargs):
        state["calls"].append(url)
        return _Response(_html(state["props"], state["version"]), state["status"])

    monkeypatch.setattr(page.requests, "get", fake_get)
    return state


@pytest.fixture
def clock(monkeypatch):
    """One clock for the budget and for every cache."""
    now = [1_000_000.0]
    monkeypatch.setattr(source_budget, "_now", lambda: now[0])
    monkeypatch.setattr(home, "_now", lambda: now[0])
    monkeypatch.setattr(metadata, "_now", lambda: now[0])
    monkeypatch.setattr(catalog.time, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def inline_refresh(monkeypatch):
    monkeypatch.setattr(home, "_spawn", lambda job: job())


# ── Cache hits, expiry ────────────────────────────────────────────────────────

def test_a_cache_hit_sends_no_request(source, clock):
    page.search("x", "s.test")
    page.search("  X ", "s.test")   # same query, normalised
    assert len(source["calls"]) == 1
    assert source_budget.status()["requests_last_hour"] == 1


def test_a_search_expires_after_ten_minutes_with_jitter(source, clock):
    page.search("x", "s.test")
    clock[0] += page.SEARCH_TTL * (1 - source_budget.JITTER) - 1
    page.search("x", "s.test")
    assert len(source["calls"]) == 1
    clock[0] += page.SEARCH_TTL * 2 * source_budget.JITTER + 2
    page.search("x", "s.test")
    assert len(source["calls"]) == 2


def test_page_and_type_are_separate_cache_entries(source, clock):
    page.search("x", "s.test")
    page.search("x", "s.test", page=2)
    page.search("x", "s.test", media_type="movie")
    assert len(source["calls"]) == 3


def test_the_ttls_are_the_agreed_ones():
    assert home._TTL == 30 * 60
    assert page.SEARCH_TTL == 10 * 60
    assert metadata._TTL_HIT == 24 * 60 * 60
    assert source_budget.DEFAULT_REQUESTS_PER_HOUR == 60   # the conservative preset
    assert source_budget.BACKOFF_STEPS[:3] == (300, 600, 1200)
    assert source_budget.BACKOFF_STEPS[-1] == 3600


# ── Jitter ────────────────────────────────────────────────────────────────────

def test_jitter_stays_within_twenty_percent():
    values = [source_budget.jitter(1000) for _ in range(2000)]
    assert min(values) >= 800 and max(values) <= 1200
    # and it does spread: not one constant
    assert max(values) - min(values) > 200


def test_cache_expiry_is_jittered(clock):
    cache = source_budget.SourceCache("t", 1000, max_entries=10, clock=lambda: clock[0])
    expiries = {cache.put(i, i).expires_at - clock[0] for i in range(50)}
    assert all(800 <= e <= 1200 for e in expiries)
    assert len(expiries) > 1


# ── One request in flight per key ─────────────────────────────────────────────

def test_concurrent_callers_share_one_request(monkeypatch, clock):
    gate = threading.Event()
    calls = []

    def slow_fetch(domain, path="", **kwargs):
        calls.append(path)
        gate.wait(5)
        return {"titles": [_title(1)]}

    monkeypatch.setattr(page, "fetch_site_props", slow_fetch)
    results = []
    threads = [threading.Thread(target=lambda: results.append(page.search("x", "s.test")))
               for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert len(results) == 5 and all(r[0]["id"] == 1 for r in results)


def test_home_refresh_runs_once_however_many_tabs(source, clock, monkeypatch):
    home.shelves("streamingcommunity", "s.test")
    clock[0] += home._TTL * 2
    spawned = []
    monkeypatch.setattr(home, "_spawn", lambda job: spawned.append(job))

    for _ in range(5):
        shelves = home.shelves("streamingcommunity", "s.test")
        assert shelves[0]["items"][0]["id"] == 1   # the old copy, at once

    assert len(spawned) == 1
    assert home.last_served("streamingcommunity")["stale"] == "refreshing"
    spawned[0]()
    assert len(source["calls"]) == 2


def test_home_serves_the_copy_then_refreshes(source, clock, inline_refresh):
    home.shelves("streamingcommunity", "s.test")
    first = home.last_served("streamingcommunity")
    assert first["cached"] is False

    clock[0] += 60
    home.shelves("streamingcommunity", "s.test")
    assert home.last_served("streamingcommunity")["cached"] is True
    assert len(source["calls"]) == 1

    clock[0] += home._TTL * 2
    source["props"] = {"sliders": [{"name": "trending", "titles": [_title(2)]}]}
    fresh = home.shelves("streamingcommunity", "s.test")
    assert len(source["calls"]) == 2
    assert fresh[0]["items"][0]["id"] == 2


# ── Backoff ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [403, 429, 503])
def test_a_pushback_status_starts_a_backoff(source, clock, status):
    source["status"] = status
    with pytest.raises(requests.HTTPError):
        page.search("x", "s.test")
    assert source_budget.status()["backoff_active"]

    source["status"] = 200
    with pytest.raises(source_budget.SourceUnavailable):
        page.search("y", "s.test")
    assert len(source["calls"]) == 1   # nothing sent during the pause


def test_backoff_grows_exponentially_and_caps(clock):
    pauses = []
    for _ in range(7):
        until = source_budget.report_pushback("test")
        pauses.append(until - clock[0])
        clock[0] = until + 1
    assert pauses == [300, 600, 1200, 2400, 3600, 3600, 3600]


def test_a_burst_of_refusals_is_one_step(clock):
    source_budget.report_pushback("a")
    until = source_budget.report_pushback("b")
    assert until - clock[0] == 300
    assert source_budget.status()["backoff_level"] == 1


def test_success_after_the_pause_resets_the_ladder(source, clock):
    source_budget.report_pushback("x")
    clock[0] += 301
    page.search("x", "s.test")
    assert source_budget.status()["backoff_level"] == 0
    assert source_budget.report_pushback("y") - clock[0] == 300


def test_backoff_serves_the_expired_cache(source, clock):
    page.search("x", "s.test")
    clock[0] += page.SEARCH_TTL * 2
    source_budget.report_pushback("test")

    assert page.search("x", "s.test")[0]["id"] == 1
    assert len(source["calls"]) == 1
    entry = page._search_cache.entry(page._search_key("x", "s.test", 1, None))
    assert not page._search_cache.is_fresh(entry)


def test_backoff_on_429_keeps_the_home_copy(source, clock, inline_refresh):
    home.shelves("streamingcommunity", "s.test")
    clock[0] += home._TTL * 2
    source["status"] = 429
    shelves = home.shelves("streamingcommunity", "s.test")   # refresh fails
    assert shelves and shelves[0]["items"][0]["id"] == 1
    assert source_budget.status()["backoff_active"]

    clock[0] += 60
    home.shelves("streamingcommunity", "s.test")
    assert home.last_served("streamingcommunity")["stale"] == "backoff"
    assert len(source["calls"]) == 2


def test_the_download_pipeline_pushback_starts_a_backoff(clock):
    """m3u8 halving its segment concurrency reports the pushback directly."""
    from app.core.m3u8 import AdaptiveLimiter

    limiter = AdaptiveLimiter(16, now=lambda: 0.0)
    limiter.penalise()
    assert source_budget.status()["backoff_active"]


def test_a_log_line_alone_no_longer_starts_a_backoff(clock):
    """The log handler is gone: only the explicit call counts, so a log line
    that merely looks like it cannot pause the source."""
    log = logging.getLogger("app.core.m3u8")
    previous = log.level
    log.setLevel(logging.INFO)
    try:
        log.info("Source pushing back — segment concurrency %d → %d (max %d)", 8, 4, 16)
    finally:
        log.setLevel(previous)
    assert not source_budget.status()["backoff_active"]


def test_metadata_in_backoff_is_not_cached_as_a_miss(clock, monkeypatch):
    from app.core import tv
    monkeypatch.setattr(metadata, "configured_domain", lambda: "s.test")
    source_budget.report_pushback("x")
    monkeypatch.setattr(tv.requests, "get",
                        lambda *a, **k: pytest.fail("request sent during backoff"))
    result = metadata.title_metadata("tv", "1", "slug", "v1")
    assert result["source"] == "none"
    assert metadata._cached(metadata._cache_key("tv", "1")) is None


# ── Budget ────────────────────────────────────────────────────────────────────

def test_an_exhausted_budget_serves_the_expired_cache(source, clock, monkeypatch):
    monkeypatch.setenv(source_budget.ENV_REQUESTS_PER_HOUR, "2")
    page.search("a", "s.test")
    page.search("b", "s.test")
    clock[0] += page.SEARCH_TTL * 2   # both expired, budget still spent (same hour)

    assert page.search("a", "s.test")[0]["id"] == 1
    assert len(source["calls"]) == 2
    with pytest.raises(source_budget.SourceUnavailable) as exc:
        page.search("never-seen", "s.test")
    assert exc.value.reason == "budget"
    assert source_budget.status()["budget_exhausted"]


def test_the_budget_refills_after_an_hour(source, clock, monkeypatch):
    monkeypatch.setenv(source_budget.ENV_REQUESTS_PER_HOUR, "1")
    page.search("a", "s.test")
    assert source_budget.blocked() == "budget"
    clock[0] += source_budget.WINDOW + 1
    assert source_budget.blocked() is None


def test_a_bad_budget_value_falls_back_to_the_setting(monkeypatch):
    from app import config
    config.save_settings({**config.get_settings(), "source_requests_per_hour": 90})
    monkeypatch.setenv(source_budget.ENV_REQUESTS_PER_HOUR, "lots")
    assert source_budget.limit() == 90
    monkeypatch.setenv(source_budget.ENV_REQUESTS_PER_HOUR, "0")
    assert source_budget.limit() == 1


def test_get_domain_version_is_outside_the_budget(source, clock, monkeypatch):
    """Domain recovery must be able to tell a live host from a dead one."""
    source_budget.report_pushback("x")
    assert page.get_domain_version("s.test") == "v1"


# ── Version check ─────────────────────────────────────────────────────────────

def test_an_unchanged_version_keeps_derived_pages(source, clock):
    page.search("x", "s.test")
    page.fetch_site_props("s.test")          # home page, same version
    page.search("x", "s.test")
    assert source["calls"].count("https://s.test/it/search") == 1


def test_a_new_version_expires_derived_pages_early(source, clock):
    page.search("x", "s.test")
    source["version"] = "v2"
    page.fetch_site_props("s.test")          # the home refresh sees a deploy
    page.search("x", "s.test")
    assert source["calls"].count("https://s.test/it/search") == 2


# ── Status ────────────────────────────────────────────────────────────────────

def test_status_reports_requests_backoff_and_refreshes(source, clock):
    page.search("x", "s.test")
    source_budget.report_pushback("t")
    status = source_budget.status()
    assert status["requests_last_hour"] == 1
    assert status["remaining"] == status["limit_per_hour"] - 1
    assert status["backoff_active"] and status["backoff_until"]
    assert "search" in status["last_refresh"]
    assert status["versions"] == {"s.test": "v1"}
    assert "x" not in json.dumps(status["last_refresh"])  # no search text


# ── Stale on error, and what the routes say ───────────────────────────────────

def test_a_failed_refresh_keeps_the_last_good_title_card(clock, monkeypatch):
    from app.core import tv
    monkeypatch.setattr(metadata, "configured_domain", lambda: "s.test")
    monkeypatch.setattr(tv, "get_title_props",
                        lambda *a, **k: {"plot": "Una trama", "type": "movie"})
    assert metadata.title_metadata("movie", "7", "slug", "v1")["plot"] == "Una trama"

    clock[0] += metadata._TTL_HIT * 2
    monkeypatch.setattr(tv, "get_title_props",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert metadata.title_metadata("movie", "7", "slug", "v1")["plot"] == "Una trama"


def test_the_search_route_says_when_an_answer_is_cached(client, source):
    from tests.conftest import enable_open_mode
    enable_open_mode()

    first = client.get("/api/search?q=x")
    second = client.get("/api/search?q=x")

    assert first.status_code == 200 and first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert second.headers["X-Cache-Updated"] == first.headers["X-Cache-Updated"]
    assert "X-Stale" not in second.headers
    assert len(source["calls"]) == 1


def test_the_search_route_flags_a_stale_answer(client, source, clock):
    from tests.conftest import enable_open_mode
    enable_open_mode()
    client.get("/api/search?q=x")
    clock[0] += page.SEARCH_TTL * 2
    source_budget.report_pushback("t")

    response = client.get("/api/search?q=x")

    assert response.status_code == 200
    assert response.headers["X-Stale"] == "1"
    assert response.json()[0]["id"] == 1
    assert len(source["calls"]) == 1


# ── Title pages a download reads ──────────────────────────────────────────────

def test_download_title_pages_are_refused_when_the_budget_is_spent(clock, monkeypatch):
    """The title page (props, seasons) and the watch page (XSRF token) a
    download reads are source pages like any other: with the budget spent
    nothing is sent, and the job fails with a message saying why."""
    from app.core import tv

    def no_request(*a, **k):
        pytest.fail("request sent with the budget spent")

    monkeypatch.setattr(page.requests, "get", no_request)
    monkeypatch.setattr(tv.requests.Session, "get", no_request)
    monkeypatch.setenv(source_budget.ENV_REQUESTS_PER_HOUR, "1")
    source_budget.acquire("spent")

    with pytest.raises(source_budget.SourceUnavailable, match="budget orario"):
        tv.get_title_props(1, "slug", "v1", "s.test")
    with pytest.raises(source_budget.SourceUnavailable, match="budget orario"):
        tv.get_info_season(1, "slug", "s.test", "v1", "tok", 1)
    with pytest.raises(source_budget.SourceUnavailable, match="budget orario"):
        tv.get_token(1, "s.test")


def test_download_title_pages_are_refused_during_a_backoff(clock, monkeypatch):
    from app.core import tv

    monkeypatch.setattr(tv.requests.Session, "get",
                        lambda *a, **k: pytest.fail("request sent during backoff"))
    source_budget.report_pushback("test")

    with pytest.raises(source_budget.SourceUnavailable, match="sorgente in pausa"):
        tv.get_token(1, "s.test")


def test_the_watch_page_counts_against_the_budget(clock, monkeypatch):
    from app.core import tv

    def fake_get(session, url, **kwargs):
        session.cookies.set("XSRF-TOKEN", "abc")
        return _Response("", 200)

    monkeypatch.setattr(tv.requests.Session, "get", fake_get)
    assert tv.get_token(1, "s.test") == "abc"
    assert source_budget.status()["requests_last_hour"] == 1


# ── Source health and backoff listeners (diagnosis, automatic protection) ─────

def test_status_counts_refusals_over_the_last_day(clock):
    source_budget.report_pushback("HTTP 429 /search")
    source_budget.report_pushback("HTTP 429 /search")   # same pause, still counted
    status = source_budget.status()
    assert status["pushbacks_last_24h"] == 2
    assert status["backoffs_last_hour"] == 1
    assert status["last_pushback"]["reason"] == "HTTP 429 /search"
    assert status["observed_since"]

    clock[0] += 2 * 3600
    status = source_budget.status()
    assert status["pushbacks_last_24h"] == 2
    assert status["backoffs_last_hour"] == 0

    clock[0] += 23 * 3600
    status = source_budget.status()
    assert status["pushbacks_last_24h"] == 0
    assert status["last_pushback"] is None


def test_backoff_remaining_and_counters(clock):
    assert source_budget.backoff_remaining() == 0
    until = source_budget.report_pushback("x")
    assert source_budget.backoff_remaining() == until - clock[0]
    assert source_budget.pushbacks_since(60) == 1
    assert source_budget.backoffs_since(60) == 1
    clock[0] = until + 1
    assert source_budget.backoff_remaining() == 0


def test_listeners_hear_each_new_pause_once(clock, monkeypatch):
    # A list of its own, so nothing registered here outlives the test.
    monkeypatch.setattr(source_budget, "_backoff_listeners", [])
    heard = []
    listener = lambda until, reason: heard.append((until, reason))
    source_budget.add_backoff_listener(listener)
    until = source_budget.report_pushback("HTTP 503 x")
    source_budget.report_pushback("HTTP 503 y")          # same pause: no new event
    assert heard == [(until, "HTTP 503 x")]


def test_a_failing_listener_does_not_break_the_backoff(clock, monkeypatch):
    monkeypatch.setattr(source_budget, "_backoff_listeners", [])
    def boom(until, reason):
        raise RuntimeError("listener bug")
    source_budget.add_backoff_listener(boom)
    assert source_budget.report_pushback("x") > clock[0]
    assert source_budget.backoff_until() is not None


def test_a_bound_method_listener_is_held_weakly(clock, monkeypatch):
    monkeypatch.setattr(source_budget, "_backoff_listeners", [])
    import gc

    class Owner:
        def __init__(self):
            self.heard = 0

        def on(self, until, reason):
            self.heard += 1

    owner = Owner()
    source_budget.add_backoff_listener(owner.on)
    source_budget.report_pushback("x")
    assert owner.heard == 1
    del owner
    gc.collect()
    clock[0] += 10_000
    source_budget.report_pushback("y")   # the dead listener is skipped and dropped
    assert source_budget._backoff_listeners == []
