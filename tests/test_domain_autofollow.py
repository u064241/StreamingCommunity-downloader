"""The panel follows a domain rotation by itself, safely and visibly.

One test (at least) per rule:

1. ``domain_auto_apply`` is on by default, and a value already saved is kept;
2. an error answer of the source starts one check of the channel at once, in
   background, at most every ten minutes, and a check never starts another;
3. a candidate whose start page lacks the real site's fingerprint is refused,
   with the reason said;
4. ``GET /api/domain`` is not "ok" (green) while the source is paused;
5. a domain adopted automatically is notified (``SOURCE_DOMAIN_APPLIED``).

No network: ``requests.get`` and the resolver are faked, as in
tests/test_domain_recovery.py; conftest blocks any real connection.
"""

import html
import json
from pathlib import Path

import pytest

from app import config
from app.core import domain_recovery, page, source_budget

FIXTURE = Path(__file__).parent / "fixtures" / "sc_home_page.json"
NEW_HOST = "streamingcommunityz.rodeo"


class _Response:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _html(payload: dict) -> str:
    return f'<html><body><div id="app" data-page="{html.escape(json.dumps(payload))}"></div></body></html>'


def _genuine_home() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# A look-alike: an Inertia app with a version, served from a name that passes
# NAME_PATTERN. Everything the old check asked for, nothing of the real site.
LOOKALIKE = {"component": "Welcome", "url": "/", "version": "abc123", "props": {"errors": {}}}


@pytest.fixture(autouse=True)
def _state():
    domain_recovery.clear_pending()
    domain_recovery._last_check_at = None
    domain_recovery._last_offschedule_at = None
    page._home_identity.clear()
    yield
    domain_recovery.unregister_offschedule_trigger()
    domain_recovery.clear_pending()
    domain_recovery._last_check_at = None
    domain_recovery._last_offschedule_at = None
    page._home_identity.clear()


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(domain_recovery.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


@pytest.fixture
def site(monkeypatch, public_dns):
    """Fake source pages per host; the channel announces NEW_HOST."""
    pages = {"example.test": _Response("", 404)}   # the old host is gone
    asked = []

    def fake_get(url, *args, **kwargs):
        host = url.split("/")[2]
        asked.append(host)
        return pages[host]

    monkeypatch.setattr(page.requests, "get", fake_get)
    monkeypatch.setattr(domain_recovery, "fetch_telegram_candidates", lambda: [NEW_HOST])
    monkeypatch.setattr(domain_recovery, "fetch_candidates", lambda: [])
    monkeypatch.setattr(domain_recovery, "_broadcast_candidate", lambda: None)
    from app.jobs import job_manager
    monkeypatch.setattr(job_manager, "broadcast", lambda event: None)
    return pages, asked


@pytest.fixture
def notified(monkeypatch):
    from app.requests import notify as notify_module
    sent = []
    monkeypatch.setattr(notify_module, "notify",
                        lambda event, message, user_ids, **kw: sent.append((event, message)))
    monkeypatch.setattr(notify_module, "settings_manager_ids", lambda: [])
    return sent


# ── 1. Auto-apply by default ──────────────────────────────────────────────────

def test_auto_apply_is_the_default_but_a_saved_choice_is_kept():
    assert config.SETTINGS_DEFAULTS["domain_auto_apply"] is True
    # conftest's data file has a domain and no settings: the default applies.
    assert config.get_settings()["domain_auto_apply"] is True

    config.save_settings({**config.get_settings(), "domain_auto_apply": False})
    assert config.get_settings()["domain_auto_apply"] is False


# ── 2. A check outside the schedule, on an error ──────────────────────────────

@pytest.fixture
def trigger(monkeypatch):
    """The real hook, registered; spawned checks recorded instead of run."""
    spawned = []
    clock = [10_000.0]
    monkeypatch.setattr(domain_recovery, "_now", lambda: clock[0])
    monkeypatch.setattr(domain_recovery, "_spawn", spawned.append)
    domain_recovery.register_offschedule_trigger()
    return spawned, clock


@pytest.mark.parametrize("status", [403, 429, 503, 500, 404])
def test_an_error_answer_starts_one_channel_check(trigger, status):
    spawned, _ = trigger

    source_budget.observe(status, "/it/search")

    assert spawned == [domain_recovery._offschedule_check]


def test_a_good_answer_starts_nothing(trigger):
    spawned, _ = trigger
    source_budget.observe(200, "/it/search")
    assert spawned == []


def test_no_answer_at_all_starts_a_check_too(trigger, monkeypatch):
    spawned, _ = trigger

    def down(*a, **k):
        raise ConnectionError("no route")

    monkeypatch.setattr(page.requests, "get", down)
    with pytest.raises(ConnectionError):
        page._budgeted_get("https://example.test/it/search")

    assert spawned == [domain_recovery._offschedule_check]


def test_the_channel_check_is_debounced_for_ten_minutes(trigger):
    spawned, clock = trigger

    source_budget.observe(500, "a")
    clock[0] += domain_recovery.OFFSCHEDULE_DEBOUNCE - 1
    source_budget.observe(500, "b")
    assert len(spawned) == 1

    clock[0] += 1
    source_budget.observe(500, "c")
    assert len(spawned) == 2
    assert domain_recovery.OFFSCHEDULE_DEBOUNCE == 10 * 60


def test_a_check_never_starts_another(trigger, monkeypatch):
    """The check's own probes can fail too: that must not start a new one,
    even with the debounce long over."""
    spawned, clock = trigger
    seen = []

    def check(force=False, offschedule=False):
        seen.append(offschedule)
        clock[0] += domain_recovery.OFFSCHEDULE_DEBOUNCE * 3
        source_budget.observe(500, "/ (domain check)")
        return {"checked": False}

    monkeypatch.setattr(domain_recovery, "_run_check", check)

    source_budget.observe(503, "/it/search")
    assert len(spawned) == 1
    spawned[0]()                      # run the spawned job inline

    assert seen == [True]
    assert len(spawned) == 1


def test_the_hook_is_wired_only_while_the_app_runs(monkeypatch):
    spawned = []
    monkeypatch.setattr(domain_recovery, "_spawn", spawned.append)
    source_budget.observe(500, "x")            # not registered: nothing
    domain_recovery.register_offschedule_trigger()
    domain_recovery.unregister_offschedule_trigger()
    source_budget.observe(500, "y")
    assert spawned == []


def test_during_a_pause_the_channel_is_read_and_the_source_is_not(site, monkeypatch):
    """The channel is not the source: read even in a backoff. The source is
    asked nothing — no request sent, no budget spent."""
    pages, asked = site
    channel = []
    monkeypatch.setattr(domain_recovery, "fetch_telegram_candidates",
                        lambda: channel.append(1) or [NEW_HOST])
    source_budget.report_pushback("HTTP 429 test")
    spent = source_budget.status()["requests_last_hour"]

    result = domain_recovery.run_check(offschedule=True)

    assert channel == [1]
    assert asked == []
    assert source_budget.status()["requests_last_hour"] == spent
    assert result["rejected"] == [{"host": NEW_HOST, "reason": "sorgente in pausa"}]
    assert config.configured_domain() == "example.test"


def test_the_periodic_watcher_still_stays_quiet_in_a_pause(site, monkeypatch):
    channel = []
    monkeypatch.setattr(domain_recovery, "fetch_telegram_candidates",
                        lambda: channel.append(1) or [NEW_HOST])
    source_budget.report_pushback("HTTP 429 test")

    assert domain_recovery.run_check()["checked"] is False
    assert channel == []


# ── 3. The fingerprint ────────────────────────────────────────────────────────

def test_the_fixture_home_page_carries_the_fingerprint():
    assert page.looks_like_home(_genuine_home()) is True
    assert page.looks_like_home(LOOKALIKE) is False
    no_shelves = {**_genuine_home(), "props": {"sliders": [{"name": "promo"}]}}
    assert page.looks_like_home(no_shelves) is False


def test_a_candidate_without_the_fingerprint_is_refused(site, notified):
    pages, _ = site
    pages[NEW_HOST] = _Response(_html(LOOKALIKE))

    result = domain_recovery.run_check()

    assert result["candidate"] is None and result["applied"] is False
    assert result["rejected"] == [{"host": NEW_HOST, "reason": domain_recovery.IDENTITY_MISSING}]
    assert "impronta" in domain_recovery.IDENTITY_MISSING
    assert config.configured_domain() == "example.test"
    assert domain_recovery.pending() is None
    assert notified == []


def test_applying_by_hand_also_wants_the_fingerprint(site):
    pages, _ = site
    pages[NEW_HOST] = _Response(_html(LOOKALIKE))

    with pytest.raises(RuntimeError, match="impronta"):
        domain_recovery.apply_candidate(NEW_HOST)
    assert config.configured_domain() == "example.test"


# ── 5. The notification, on the new path ──────────────────────────────────────

def test_an_error_leads_to_an_applied_and_notified_domain(site, notified, monkeypatch):
    """The whole new road: an error answer, the hook, the channel, the
    fingerprint, the default auto-apply, the notification."""
    from app.requests import notify as notify_module

    pages, _ = site
    pages[NEW_HOST] = _Response(_html(_genuine_home()))
    spawned = []
    monkeypatch.setattr(domain_recovery, "_spawn", spawned.append)
    domain_recovery.register_offschedule_trigger()

    source_budget.observe(404, "/it/search")   # the old host stopped serving pages
    assert len(spawned) == 1
    spawned[0]()

    assert config.configured_domain() == NEW_HOST
    assert [event for event, _ in notified] == [notify_module.SOURCE_DOMAIN_APPLIED]
    assert NEW_HOST in notified[0][1]


# ── 4. Honest status ──────────────────────────────────────────────────────────

def test_get_domain_is_not_ok_while_the_source_is_paused(client, monkeypatch):
    now = [100_000.0]
    monkeypatch.setattr(source_budget, "_now", lambda: now[0])
    source_budget.note_version("example.test", "v7")
    now[0] += domain_recovery.FRESH_VERSION_AGE + 120
    source_budget.report_pushback("HTTP 429 test")
    monkeypatch.setattr(page.requests, "get",
                        lambda *a, **k: pytest.fail("nothing is sent during a pause"))

    body = client.get("/api/domain").json()

    assert body["valid"] is True          # not dead: no replacement hunt
    assert body["status"] == "paused"     # but not green either
    assert body["verified_age"] == domain_recovery.FRESH_VERSION_AGE + 120
    assert body["version"] == "v7"


def test_get_domain_is_ok_after_a_fresh_verification(client, monkeypatch):
    monkeypatch.setattr(page.requests, "get",
                        lambda *a, **k: _Response(_html(_genuine_home())))

    body = client.get("/api/domain").json()

    assert body["status"] == "ok" and body["valid"] is True
    assert body["version"] == "fixture-version-1"
    assert body["verified_age"] == 0


def test_get_domain_without_a_version_is_unverified(client, monkeypatch):
    monkeypatch.setattr(page.requests, "get", lambda *a, **k: _Response("<html></html>"))

    body = client.get("/api/domain").json()

    assert body["valid"] is True and body["status"] == "unverified"
    assert body["verified_age"] is None
