"""Shared test fixtures.

The panel is local and single-user: there is no login, no session and no
Jellyfin. Every request the test client makes acts as the local user
(app/auth/deps.py LOCAL_USER). A few helpers below (``do_setup``,
``session_for``, ``enable_open_mode``) are kept as no-op shims so tests written
when the panel had accounts still read naturally. The local user is bound to
its own ``jf_user`` row (migration 12); ``make_user`` inserts an extra legacy
row, for the few tests that need a request owned by someone else to prove an
ownership check, and ``restrict_local_user`` takes permissions away to prove a
route guard still refuses.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import models, permissions


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})


class NetworkBlockedError(RuntimeError):
    """Raised when a test reaches for the real network."""


def _host_of(address):
    """The host part of a socket address (tuple for INET/INET6, str for AF_UNIX)."""
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return address


def _is_loopback(host) -> bool:
    if host is None:
        return True  # getaddrinfo(None, port): the local wildcard, not a remote host
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    host = host.strip("[]").lower()
    if host in _LOOPBACK_HOSTS or host.startswith("127."):
        return True
    return False


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """No test reaches the real network.

    ``socket.socket.connect`` (and ``connect_ex``) and ``socket.getaddrinfo``
    refuse any host that is not loopback (127.0.0.1, localhost, ::1) or the
    TestClient's ``testserver``, with an explicit error instead of a slow
    timeout or, worse, a real request to the source. AF_UNIX sockets (str
    path) are local by definition and left alone.

    Applied with monkeypatch, so it is undone after every test, and a test that
    mocks the network on its own (``requests.get``, ``httpx``, a fake
    ``getaddrinfo``...) replaces these guards for its own duration as usual:
    its monkeypatch runs after this autouse fixture. Every refused attempt is
    also recorded and fails the test at teardown, even if the code under test
    swallowed the exception.
    """
    import socket

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    attempts: list = []

    def _refuse(host):
        attempts.append(host)
        raise NetworkBlockedError(f"rete bloccata nei test: tentativo di connessione a {host!r}")

    def guarded_connect(self, address):
        host = _host_of(address)
        if self.family != getattr(socket, "AF_UNIX", object()) and not _is_loopback(host):
            _refuse(host)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        host = _host_of(address)
        if self.family != getattr(socket, "AF_UNIX", object()) and not _is_loopback(host):
            _refuse(host)
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            _refuse(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    # app/core/hosts.py caches DNS verdicts process-wide for a minute: without
    # a reset, whether a test reaches the resolver would depend on which test
    # ran before it.
    from app.core import hosts

    hosts._dns_cache.clear()
    yield
    hosts._dns_cache.clear()
    # Code under test often catches broad exceptions (a source that is down is
    # a normal case), which would swallow the error above: fail anyway.
    if attempts:
        pytest.fail(f"rete bloccata nei test: il test ha tentato di contattare {sorted(set(map(str, attempts)))}")


@pytest.fixture
def public_dns(monkeypatch):
    """Every host resolves to a public address, without asking a resolver.

    For tests that fake the HTTP layer (``requests.get``, ``Session.get``) on
    https URLs: ``hosts.assert_public_https`` still resolves the host before
    the request, to refuse private targets, and that lookup is real network.
    93.184.216.34 is a global address, so the SSRF guard lets the URL through
    to the faked request. Same stand-in tests/test_fix_media_output.py uses.
    """
    from app.core import hosts

    lookups: list[str] = []
    monkeypatch.setattr(hosts, "_resolve",
                        lambda host, port: lookups.append(host) or ["93.184.216.34"])
    return lookups


@pytest.fixture(autouse=True)
def _configured_domain(tmp_path, monkeypatch):
    """Give every test a configured source domain, in a throwaway file.

    This used to come for free from the ``data.json`` committed at the repo
    root, which is no longer shipped — the source domain rotates, so a
    baked-in value is stale by definition. Tests that hit a download or search
    endpoint need one, otherwise the route refuses with 409 before reaching
    what is under test.

    Patched on ``app.config`` because ``configured_domain()`` reads the module
    constant at call time; the routers import the function, not the path. The
    ``source`` fixture separately points ``resolver.DATA_FILE`` at its own file
    for library paths — same split as before.
    """
    from app import config

    data_file = tmp_path / "config-data.json"
    data_file.write_text(json.dumps({"domain": "example.test"}), encoding="utf-8")
    monkeypatch.setattr(config, "DATA_FILE", data_file)
    yield data_file


@pytest.fixture(autouse=True)
def _own_database(tmp_path):
    """Every test on a database of its own, even one that never asks for one.

    The library check reads the registry in panel.db (app/library.py), so a
    plain path test reaches the database too. Without this it read whatever
    the previous test had configured — or, run alone, the panel.db in the
    working directory. Same file the ``client`` fixture uses, which finds it
    already migrated.
    """
    db.configure(tmp_path / "test.db")
    db.run_migrations()


@pytest.fixture(autouse=True)
def _security_defaults(_own_database, monkeypatch):
    """``testserver``, the Host a TestClient sends (and ``panel``, the one
    tests/test_mcp.py uses), is an allowed host — in production only loopback
    names/addresses and ALLOWED_HOSTS are."""
    from app import config

    monkeypatch.setattr(config, "ALLOWED_HOSTS", frozenset({"testserver", "panel"}))


@pytest.fixture(autouse=True)
def _fresh_source_budget():
    """Every test starts from a clean source budget, backoff and page caches.

    The state in app/core/source_budget.py is process-wide by design: without
    this, one test faking a 429 would silence the source for every test after
    it, and a long suite would spend the hourly budget. Reset before and after,
    so a test leaves nothing behind even for code that runs at teardown.
    """
    from app.core import source_budget

    source_budget.reset()
    yield
    source_budget.reset()


@pytest.fixture(autouse=True)
def _restore_source_cap():
    """The global source gate (app/core/m3u8.py) is resized from the settings
    at each download and settings change; put it back after every test."""
    from app.core import m3u8

    cap = m3u8._SOURCE_MAX_CONNECTIONS
    yield
    m3u8._SOURCE_MAX_CONNECTIONS = cap
    m3u8._SOURCE_SLOTS.resize(cap)


@pytest.fixture(autouse=True)
def _no_auto_retry(monkeypatch):
    """A failing job is retried a minute later in production. Tests that fail a
    job on purpose want the failure, not three more calls and three minutes;
    tests/test_job_retry.py turns it back on where it is the subject."""
    from app import jobs

    monkeypatch.setattr(jobs, "AUTO_RETRIES", 0)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient on a throwaway database, with the app lifespan not started.

    The lifespan would start the download scheduler and re-hydrate the schedule
    store, neither of which belongs in a unit test, so migrations are run here
    instead.
    """
    db.configure(tmp_path / "test.db")
    db.run_migrations()

    from app.main import app

    # Not entered as a context manager on purpose: that would run the lifespan,
    # which starts the download scheduler and re-hydrates the schedule store.
    yield TestClient(app)
    db.close_all()


@pytest.fixture
def stub_jobs(monkeypatch):
    """Stop download submissions from actually reaching the network.

    The call is bound against the *real* signature before being recorded, so a
    caller passing an argument JobManager does not accept fails here instead of
    passing the tests and raising in production — which is exactly what happened
    with strict_audio.
    """
    import inspect

    from app.jobs import job_manager

    submitted: list[tuple] = []

    def fake_submit(name):
        signature = inspect.signature(getattr(job_manager, name))

        def _submit(*args, **kwargs):
            signature.bind(*args, **kwargs)  # raises TypeError on a bad call
            submitted.append((name, args, kwargs))
            return f"job-{len(submitted)}"

        return _submit

    for name in ("submit_film", "submit_episode", "submit_anime_episode"):
        monkeypatch.setattr(job_manager, name, fake_submit(name))
    return submitted


class FakeSource:
    """Stands in for StreamingCommunity / AnimeUnity during request tests."""

    def __init__(self):
        self.audio = ["ita", "eng"]
        self.subtitles = ["ita", "eng"]
        self.dead = False
        self.episodes = [{"id": 900 + n, "n": str(n), "name": f"Episodio {n}"} for n in (1, 2, 3)]
        self.anime_episodes = [{"id": 800 + n, "number": str(n)} for n in (1, 2, 3)]
        # The title page's props, read for tmdb_id at approval and for a
        # followed series' year. Empty unless a test says otherwise.
        self.props = {}

    @property
    def languages(self) -> dict:
        if self.dead:
            raise RuntimeError("HTTP 404")
        return {"audio": list(self.audio), "subtitles": list(self.subtitles)}


class InlineExecutor:
    """Runs approval work on the calling thread, so tests stay deterministic."""

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


@pytest.fixture
def source(monkeypatch, tmp_path):
    """Fake external source plus a temporary library and configured domain."""
    from app.core import animeunity, film, metadata, page, tv
    from app.requests import resolver, service

    fake = FakeSource()

    data_file = tmp_path / "data.json"
    library = tmp_path / "library"
    library.mkdir()
    data_file.write_text(
        json.dumps({
            "domain": "example.test",
            "libraries": [
                {"type": "film", "path": str(library)},
                {"type": "tv", "path": str(library)},
                {"type": "anime", "path": str(library)},
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(resolver, "DATA_FILE", data_file)
    fake.library = library

    monkeypatch.setattr(film, "get_film_languages", lambda *a, **k: fake.languages)
    monkeypatch.setattr(page, "get_domain_version", lambda *a, **k: "v1")
    monkeypatch.setattr(tv, "get_token", lambda *a, **k: "xsrf-token")
    monkeypatch.setattr(
        tv, "get_info_season",
        lambda *a, **k: ([] if fake.dead else list(fake.episodes)),
    )
    monkeypatch.setattr(tv, "get_episode_languages", lambda *a, **k: fake.languages)
    monkeypatch.setattr(tv, "get_title_props", lambda *a, **k: dict(fake.props))
    # Process-wide, so one test's props would otherwise answer the next's.
    metadata.clear_cache()
    monkeypatch.setattr(
        animeunity, "get_episodes",
        lambda *a, **k: ([] if fake.dead else list(fake.anime_episodes)),
    )
    monkeypatch.setattr(animeunity, "get_episode_languages", lambda *a, **k: fake.languages)

    # Approval resolution runs inline instead of on its worker pool.
    monkeypatch.setattr(service, "_resolution_pool", InlineExecutor())
    return fake


@pytest.fixture
def admin_credentials():
    """Kept for tests written when the panel had a Jellyfin setup step; the
    values are no longer sent anywhere."""
    return {"url": "http://jellyfin.local:8096", "username": "admin", "password": "adminpw"}


def do_setup(client, credentials=None) -> dict:
    """No-op: a local panel has nothing to set up. Returns the shape the old
    setup endpoint answered with, from /api/auth/me, with an empty CSRF token
    so callers that forward it as a header keep working."""
    response = client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    payload = response.json()
    payload["csrf_token"] = ""
    return payload


def enable_open_mode():
    """No-op: the local, account-less mode is the only mode."""


def make_user(username: str, jellyfin_user_id: str, perms: int, enabled: bool = True):
    """Insert a legacy ``jf_user`` row. Nothing in the app creates these any
    more; the request queue's schema still references the table, so its
    service-level tests use this to have a requester id."""
    db.execute(
        "INSERT INTO jf_user(jellyfin_user_id, username, is_jellyfin_admin, permissions, "
        "enabled, device_id, created_at) VALUES(?, ?, 0, ?, ?, '', ?)",
        (jellyfin_user_id, username, permissions.sanitize(perms), int(enabled), models.now_iso()),
    )
    row = db.query_one("SELECT id FROM jf_user WHERE jellyfin_user_id = ?", (jellyfin_user_id,))
    return models.get_user(row["id"])


def restrict_local_user(monkeypatch, perms: int):
    """Swap the local user for one holding only ``perms``.

    In the browser the local user holds every permission, so nothing is ever
    refused; this is how a test still proves a route's ``require(...)`` guard
    (or a handler's own ownership check) refuses someone without the right.
    The stand-in keeps the local user's key, so it binds to the same jf_user
    row and is still recognised as the local user.
    """
    import dataclasses

    from app.auth import deps

    monkeypatch.setattr(deps, "LOCAL_USER",
                        dataclasses.replace(deps.LOCAL_USER, permissions=int(perms)))
    return deps.local_user()


def session_for(client, user_id: int):
    """No-op: there are no sessions, every request is the local user. Returns
    an empty CSRF token for callers that forward it as a header."""
    return ""


ALL = int(permissions.ALL_PERMISSIONS)
