"""How many requests the panel sends to the source, and when it must stop.

The panel runs on one person's PC, so whatever it sends, the source sees from one
IP. Every page the panel shows from the source (the start page, a search, a title
card, a catalogue page) goes through here first, and three rules apply:

* **Budget.** At most ``source_requests_per_hour`` requests (a field of the
  performance preset, app/config.py: 60, 90 or 120, never above 120) in any
  sliding hour; ``SC_SOURCE_REQUESTS_PER_HOUR``, when set, is an explicit
  override. Past it, nothing is sent: the caches answer, expired or not, and
  say so (``X-Stale``).
* **Backoff.** A 403, 429 or 503 - or the download pipeline halving its
  segment concurrency (app/core/m3u8.py calls ``report_pushback()``) - means
  the source is pushing back. Nothing is sent for 5
  minutes, then 10, 20, 40, 60 on repeated pushback. A successful answer after
  the pause resets the ladder.
* **Jitter.** Every cache lifetime is stretched or shrunk by up to 20%, so
  entries fetched together do not all expire, and refetch, together.

``SourceCache`` is the cache every page module uses. Beyond the TTL it knows two
more things: the site version an entry was fetched under (a new deploy of the
site expires derived pages early; an unchanged one never does), and which key
is already being fetched, so concurrent callers wait for one request instead of
sending one each.
"""

from __future__ import annotations

import inspect
import logging
import os
import random
import threading
import time
import weakref
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

ENV_REQUESTS_PER_HOUR = "SC_SOURCE_REQUESTS_PER_HOUR"
# Used only when the settings cannot be read: the conservative preset's budget.
DEFAULT_REQUESTS_PER_HOUR = 60
WINDOW = 60 * 60

# Seconds of silence after each consecutive pushback: 5, 10, 20, 40, 60 min.
BACKOFF_STEPS = (5 * 60, 10 * 60, 20 * 60, 40 * 60, 60 * 60)
PUSHBACK_STATUSES = frozenset({403, 429, 503})

# Every lifetime is multiplied by a factor in [1 - JITTER, 1 + JITTER].
JITTER = 0.2

_lock = threading.RLock()
_sent: deque[float] = deque()
_backoff_until = 0.0
_backoff_level = 0
_budget_warned = False
_refreshed: dict[str, float] = {}
_versions: dict[str, str] = {}
_rng = random.Random()
_caches: list["SourceCache"] = []

# What the diagnosis and the automatic protection read. The backoff and the
# ladder live in memory: a restart starts from a clean slate (see
# DownloadSlots in app/jobs.py). The *history* of refusals does not: the
# diagnosis looks back 72 hours and 7 days, far past one session, so every
# pause started is also written to ``source_history.json`` beside data.json,
# with the time the history began. A missing or unreadable file starts a new
# history now, which the diagnosis reads as "not enough data" (conservative).
HISTORY_WINDOW = 24 * 60 * 60
SIGNAL_WINDOW = 72 * 60 * 60          # any refusal in here -> conservative
LONG_WINDOW = 7 * 24 * 60 * 60        # "fast" needs this long without one
HISTORY_FILE_NAME = "source_history.json"
_MAX_HISTORY_EVENTS = 200
_pushbacks: deque[tuple[float, str]] = deque(maxlen=500)   # every refusal seen
_backoff_starts: deque[float] = deque(maxlen=100)          # every pause started
_started_at = time.time()
_history_loaded = False
_history_since: float | None = None
_history_events: list[tuple[float, str]] = []   # persisted pause starts
# Called as fn(until, reason) each time a new pause starts, outside the lock.
_backoff_listeners: list[Callable[[], Any]] = []   # weak references


class SourceUnavailable(RuntimeError):
    """No request was sent: the source is in backoff or the budget is spent.

    A RuntimeError so that every existing ``except RuntimeError`` / ``except
    Exception`` around a fetch keeps treating it as "the source did not answer".
    """

    def __init__(self, reason: str, until: float | None = None):
        self.reason = reason
        self.until = until
        when = f" fino alle {_hhmm(until)}" if until else ""
        text = ("sorgente in pausa dopo un rifiuto" if reason == "backoff"
                else "budget orario di richieste alla sorgente esaurito")
        super().__init__(f"Nessuna richiesta inviata: {text}{when}")


def _now() -> float:
    """Wall clock, indirected so tests can move time without sleeping."""
    return time.time()


def _hhmm(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def env_limit() -> int | None:
    """The explicit SC_SOURCE_REQUESTS_PER_HOUR override, or None when unset/invalid."""
    raw = os.environ.get(ENV_REQUESTS_PER_HOUR, "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("%s=%r is not a number; using the setting", ENV_REQUESTS_PER_HOUR, raw)
        return None


def limit() -> int:
    """The hourly budget: the env override, else the setting (read each time).

    The setting is clamped to config.SOURCE_REQUESTS_PER_HOUR_CEILING. If the
    settings cannot be read the conservative budget applies, never a larger one.
    """
    override = env_limit()
    if override is not None:
        return override
    try:
        from app import config
        return config.source_requests_per_hour_setting()
    except Exception:  # an unreadable data.json must not lift the budget
        return DEFAULT_REQUESTS_PER_HOUR


def _history_path():
    from pathlib import Path
    from app import config
    return Path(config.DATA_FILE).with_name(HISTORY_FILE_NAME)


def _write_history() -> None:
    """Under _lock. Best effort: a failed write is logged, never raised."""
    import json
    try:
        from app import config
        payload = {"since": _history_since,
                   "pushbacks": [[ts, reason] for ts, reason in _history_events]}
        config.atomic_write_text(_history_path(), json.dumps(payload))
    except Exception as exc:
        logger.warning("Could not save the source history: %s", exc)


def _load_history() -> None:
    """Under _lock. Read the persisted history once per process (or reset)."""
    import json
    global _history_loaded, _history_since, _history_events
    if _history_loaded:
        return
    _history_loaded = True
    now = _now()
    since = None
    events: list[tuple[float, str]] = []
    try:
        raw = json.loads(_history_path().read_text(encoding="utf-8"))
        since = float(raw["since"])
        for item in raw.get("pushbacks") or []:
            ts, reason = float(item[0]), str(item[1])[:200]
            if now - LONG_WINDOW < ts <= now:
                events.append((ts, reason))
        if since > now:   # a clock that went back: do not trust the history span
            since = now
    except FileNotFoundError:
        since = None
    except Exception as exc:
        logger.warning("Source history unreadable (%s): starting a new one", exc)
        since = None
    _history_events = sorted(events)[-_MAX_HISTORY_EVENTS:]
    if since is None:
        _history_since = now
        _write_history()
    else:
        _history_since = since


def _record_history(ts: float, reason: str) -> None:
    """Under _lock: one pause started, kept across restarts."""
    global _history_events
    _load_history()
    _history_events = [(t, r) for t, r in _history_events if t > ts - LONG_WINDOW]
    _history_events.append((ts, reason[:200]))
    _history_events = _history_events[-_MAX_HISTORY_EVENTS:]
    _write_history()


def _signals_since(cutoff: float) -> int:
    """Under _lock: refusals after *cutoff*, this session's and persisted ones."""
    _load_history()
    session = [ts for ts, _ in _pushbacks if ts > cutoff]
    first_session = min(session) if session else None
    # Persisted pause starts from before this session's first refusal (the
    # in-memory list already counts this session's, burst included).
    earlier = sum(1 for ts, _ in _history_events
                  if ts > cutoff and (first_session is None or ts < first_session))
    return len(session) + earlier


def history_hours() -> float:
    """How long the refusal history covers, in hours (since its first record)."""
    with _lock:
        _load_history()
        return max(0.0, (_now() - (_history_since or _now())) / 3600)


def _prune(now: float) -> None:
    while _sent and _sent[0] <= now - WINDOW:
        _sent.popleft()


def blocked() -> str | None:
    """``"backoff"``, ``"budget"``, or None when a request may be sent."""
    with _lock:
        now = _now()
        if now < _backoff_until:
            return "backoff"
        _prune(now)
        if len(_sent) >= limit():
            return "budget"
        return None


def acquire(what: str = "") -> None:
    """Spend one request of the budget, or raise SourceUnavailable."""
    global _budget_warned
    with _lock:
        reason = blocked()
        if reason == "backoff":
            raise SourceUnavailable("backoff", _backoff_until)
        if reason == "budget":
            refill = _sent[0] + WINDOW if _sent else None
            if not _budget_warned:
                _budget_warned = True
                logger.warning("Source request budget spent (%d/h): serving caches "
                               "until %s", limit(), _hhmm(refill))
            raise SourceUnavailable("budget", refill)
        _budget_warned = False
        _sent.append(_now())
    logger.debug("Source request: %s", what)


def observe(status: int | None, what: str = "") -> None:
    """Read one response status: pushback starts a backoff, success ends the ladder."""
    global _backoff_level
    if status is None:
        return
    if status in PUSHBACK_STATUSES:
        report_pushback(f"HTTP {status} {what}".strip())
    elif 200 <= status < 400:
        with _lock:
            if _backoff_level and _now() >= _backoff_until:
                _backoff_level = 0


def report_pushback(reason: str = "") -> float:
    """The source pushed back. Pause every request; returns the pause's end.

    A pushback arriving while a pause is already running does not climb the
    ladder again: a burst of 429s from one download is one refusal, not five.
    """
    global _backoff_until, _backoff_level
    with _lock:
        now = _now()
        _pushbacks.append((now, reason or "unknown"))
        if now < _backoff_until:
            return _backoff_until
        _backoff_level = min(_backoff_level + 1, len(BACKOFF_STEPS))
        delay = BACKOFF_STEPS[_backoff_level - 1]
        _backoff_until = now + delay
        _backoff_starts.append(now)
        _record_history(now, reason or "unknown")
        until = _backoff_until
        listeners = _live_listeners()
    logger.warning("Source pushing back (%s): no requests for %d min, until %s",
                   reason or "unknown", delay // 60, _hhmm(until))
    for listener in listeners:
        try:
            listener(until, reason or "unknown")
        except Exception:
            logger.exception("Backoff listener failed")
    return until


def add_backoff_listener(callback: Callable[[float, str], None]) -> None:
    """Call *callback(until, reason)* each time a new pause starts.

    Runs on the thread that reported the pushback, outside this module's lock.
    Survives ``reset()``: listeners are wiring, not state. A bound method is
    held weakly, so registering does not keep its object alive.
    """
    ref = weakref.WeakMethod(callback) if inspect.ismethod(callback) else (lambda: callback)
    with _lock:
        _backoff_listeners.append(ref)


def _live_listeners() -> list[Callable[[float, str], None]]:
    """Under _lock: the listeners still alive, dropping the dead ones."""
    alive = [(ref, ref()) for ref in _backoff_listeners]
    _backoff_listeners[:] = [ref for ref, fn in alive if fn is not None]
    return [fn for _, fn in alive if fn is not None]


def backoff_until() -> float | None:
    with _lock:
        return _backoff_until if _now() < _backoff_until else None


def backoff_remaining() -> float:
    """Seconds until the current pause ends; 0 when there is none."""
    with _lock:
        return max(0.0, _backoff_until - _now())


def pushbacks_since(seconds: float) -> int:
    """Refusals (403/429/503 or segment pushback) seen in the last *seconds*."""
    with _lock:
        cutoff = _now() - seconds
        return sum(1 for ts, _ in _pushbacks if ts > cutoff)


def backoffs_since(seconds: float) -> int:
    """Pauses started in the last *seconds* (one per climb of the ladder)."""
    with _lock:
        cutoff = _now() - seconds
        return sum(1 for ts in _backoff_starts if ts > cutoff)


def jitter(seconds: float) -> float:
    """*seconds* stretched or shrunk by up to ``JITTER``."""
    return seconds * _rng.uniform(1 - JITTER, 1 + JITTER)


def note_refresh(label: str) -> None:
    with _lock:
        if label not in _refreshed and len(_refreshed) >= 64:
            _refreshed.pop(next(iter(_refreshed)), None)
        _refreshed[label] = _now()


VERSION_FILE_NAME = "source_version.json"
_MAX_PERSISTED_VERSIONS = 16
_persisted_versions: dict[str, str] = {}
_versions_loaded_from = None


def _version_path():
    from pathlib import Path
    from app import config
    return Path(config.DATA_FILE).with_name(VERSION_FILE_NAME)


def _load_versions() -> None:
    """Under _lock. Read the last versions seen, once per data.json location."""
    import json
    global _persisted_versions, _versions_loaded_from
    path = _version_path()
    if _versions_loaded_from == path:
        return
    _versions_loaded_from = path
    found: dict[str, str] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for host, value in (raw.get("versions") or {}).items():
            if isinstance(host, str) and isinstance(value, str) and host and value:
                found[host[:255]] = value[:200]
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Source version file unreadable (%s): starting over", exc)
    _persisted_versions = dict(list(found.items())[-_MAX_PERSISTED_VERSIONS:])


def _save_versions() -> None:
    """Under _lock. Best effort: a failed write is logged, never raised."""
    import json
    try:
        from app import config
        config.atomic_write_text(_version_path(),
                                 json.dumps({"versions": _persisted_versions}, sort_keys=True))
    except Exception as exc:
        logger.warning("Could not save the source version: %s", exc)


def clear_host_caches(host: str) -> None:
    """Drop every SourceCache entry fetched from *host* (search, home, catalog, cards).

    The keys of those caches all carry the host as one element of a tuple, so
    one predicate fits every cache and leaves other sources' entries alone.
    """
    with _lock:
        caches = list(_caches)
    for cache in caches:
        cache.clear(lambda key: isinstance(key, tuple) and host in key)


def note_version(host: str | None, version: str | None) -> bool:
    """Remember the site version a page carried. True when it changed.

    Free: the version rides in every server-rendered page already fetched, so
    no request is ever sent just to check it. A change means a new deploy of
    the site: every cached search, browse page, start page and title card from
    that host is dropped at once, and anything fetched under the old version
    stops counting as fresh (see ``SourceCache.is_fresh``). The last version
    seen per host is kept in ``source_version.json`` beside data.json (atomic
    write), so a deploy that happened while the panel was closed is noticed too.
    """
    if not host or not version:
        return False
    version = str(version)[:200]
    with _lock:
        _load_versions()
        previous = _versions.get(host) or _persisted_versions.get(host)
        _versions[host] = version
        if _persisted_versions.get(host) != version:
            _persisted_versions.pop(host, None)
            _persisted_versions[host] = version
            while len(_persisted_versions) > _MAX_PERSISTED_VERSIONS:
                _persisted_versions.pop(next(iter(_persisted_versions)))
            _save_versions()
    if previous is not None and previous != version:
        logger.info("%s changed version %s -> %s: cached pages dropped",
                    host, previous, version)
        clear_host_caches(host)
        return True
    return False


def note_fields(kind: str, props) -> None:
    """Count the fields of a page already fetched that the panel does not know."""
    try:
        from app.core import source_fields
        source_fields.record(kind, props)
    except Exception as exc:   # bookkeeping must never break a page
        logger.debug("Source field bookkeeping failed: %s", exc)


def version(host: str | None) -> str | None:
    if not host:
        return None
    with _lock:
        return _versions.get(host)


def status() -> dict:
    """Read-only snapshot for ``GET /api/source/status`` and the logs."""
    try:
        from app.core import source_fields
        unknown_fields = source_fields.summary()
    except Exception:
        unknown_fields = {}
    with _lock:
        now = _now()
        _prune(now)
        used = len(_sent)
        cap = limit()
        active = now < _backoff_until
        return {
            "requests_last_hour": used,
            "limit_per_hour": cap,
            "remaining": max(0, cap - used),
            "budget_exhausted": used >= cap,
            "backoff_active": active,
            "backoff_until": _iso(_backoff_until) if active else None,
            "backoff_level": _backoff_level,
            "last_refresh": {k: _iso(v) for k, v in _refreshed.items()},
            "versions": dict(_versions),
            "limit_from_env": env_limit() is not None,
            # Source health, for the diagnosis. The 24h/72h/7d counts include
            # the refusals persisted from earlier sessions (source_history.json),
            # which covers the last ``history_hours`` hours.
            "pushbacks_last_24h": _signals_since(now - HISTORY_WINDOW),
            "pushbacks_last_72h": _signals_since(now - SIGNAL_WINDOW),
            "pushbacks_last_7d": _signals_since(now - LONG_WINDOW),
            "history_since": _iso(_history_since),
            "history_hours": round(max(0.0, (now - (_history_since or now)) / 3600), 1),
            "backoffs_last_hour": sum(1 for ts in _backoff_starts if ts > now - WINDOW),
            "last_pushback": ({"at": _iso(_pushbacks[-1][0]), "reason": _pushbacks[-1][1]}
                              if _pushbacks and _pushbacks[-1][0] > now - HISTORY_WINDOW else None),
            "observed_since": _iso(_started_at),
            # Fields the source sends that the panel does not know, by path,
            # with the number of pages they were seen on. Names only.
            "unknown_fields": unknown_fields,
        }


def reset() -> None:
    """Forget everything: budget, backoff, versions and every SourceCache.

    The state is process-wide by design; the test suite calls this before
    every test (tests/conftest.py) so one test's faked 429 or spent budget
    never reaches the next."""
    global _backoff_until, _backoff_level, _budget_warned
    global _history_loaded, _history_since, _history_events
    global _persisted_versions, _versions_loaded_from
    with _lock:
        _persisted_versions = {}
        _versions_loaded_from = None
        _history_loaded = False
        _history_since = None
        _history_events = []
        _sent.clear()
        _backoff_until = 0.0
        _backoff_level = 0
        _budget_warned = False
        _refreshed.clear()
        _versions.clear()
        _pushbacks.clear()
        _backoff_starts.clear()
        caches = list(_caches)
    for cache in caches:
        cache.clear()
    try:
        from app.core import source_fields
        source_fields.reset()
    except Exception:
        pass


# ── The cache ─────────────────────────────────────────────────────────────────

class Entry:
    __slots__ = ("value", "fetched_at", "expires_at", "version")

    def __init__(self, value, fetched_at, expires_at, version):
        self.value = value
        self.fetched_at = fetched_at
        self.expires_at = expires_at
        self.version = version


class _Flight:
    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.error: BaseException | None = None


class SourceCache:
    """A bounded TTL cache of source pages, with jitter, version and single-flight.

    ``clock`` is a callable so a module can keep its own ``_now`` that tests
    patch, as home and metadata always have.
    """

    def __init__(self, name: str, ttl: float, *, max_entries: int,
                 clock: Callable[[], float] | None = None):
        self.name = name
        self.ttl = ttl
        self.max_entries = max_entries
        self._clock = clock or (lambda: _now())
        self._entries: dict[Any, Entry] = {}
        self._lock = threading.Lock()
        self._inflight: dict[Any, _Flight] = {}
        with _lock:
            _caches.append(self)

    def now(self) -> float:
        return self._clock()

    def entry(self, key) -> Entry | None:
        """The entry, fresh or not. Never expires anything by itself."""
        with self._lock:
            return self._entries.get(key)

    def is_fresh(self, entry: Entry, host: str | None = None) -> bool:
        if self.now() >= entry.expires_at:
            return False
        current = version(host)
        return not (current and entry.version and current != entry.version)

    def put(self, key, value, *, host: str | None = None, ttl: float | None = None) -> Entry:
        now = self.now()
        entry = Entry(value, now, now + jitter(self.ttl if ttl is None else ttl), version(host))
        with self._lock:
            if key not in self._entries and len(self._entries) >= self.max_entries:
                for stale in [k for k, e in self._entries.items() if e.expires_at <= now]:
                    self._entries.pop(stale, None)
                if len(self._entries) >= self.max_entries:
                    self._entries.pop(next(iter(self._entries)), None)
            self._entries[key] = entry
        return entry

    def pop(self, key) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self, predicate: Callable[[Any], bool] | None = None) -> None:
        with self._lock:
            if predicate is None:
                self._entries.clear()
            else:
                for key in [k for k in self._entries if predicate(k)]:
                    self._entries.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # ── One request in flight per key ──

    def _claim(self, key) -> tuple[_Flight, bool]:
        with self._lock:
            flight = self._inflight.get(key)
            if flight is not None:
                return flight, False
            flight = self._inflight[key] = _Flight()
            return flight, True

    def _run(self, key, flight: _Flight, fn: Callable[[], Any]):
        try:
            flight.value = fn()
            return flight.value
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            flight.done.set()

    def single_flight(self, key, fn: Callable[[], Any], *, timeout: float = 60):
        """Run *fn* once for *key*; concurrent callers wait for its answer."""
        flight, leader = self._claim(key)
        if leader:
            return self._run(key, flight, fn)
        if not flight.done.wait(timeout):
            raise TimeoutError(f"{self.name}: the request in flight did not finish")
        if flight.error is not None:
            raise flight.error
        return flight.value

    def refresh_in_background(self, key, fn: Callable[[], Any],
                              spawn: Callable[[Callable[[], None]], None]) -> bool:
        """Start *fn* for *key* unless one is already running. True if started."""
        flight, leader = self._claim(key)
        if not leader:
            return False

        def job():
            try:
                self._run(key, flight, fn)
            except Exception as exc:
                logger.warning("%s: background refresh failed: %s", self.name, exc)

        try:
            spawn(job)
        except Exception:
            with self._lock:
                self._inflight.pop(key, None)
            flight.done.set()
            raise
        return True

    def in_flight(self, key) -> bool:
        with self._lock:
            return key in self._inflight


def spawn_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True, name="source-refresh").start()


def meta(entry: Entry | None, *, cached: bool, stale: str | None = None) -> dict:
    """What a caller tells the browser about where an answer came from."""
    return {
        "cached": cached,
        "stale": stale,
        "updated_at": entry.fetched_at if entry else None,
    }


def get_or_load(cache: SourceCache, key, loader: Callable[[], Any], *,
                host: str | None, label: str, swr: bool = False,
                spawn: Callable[[Callable[[], None]], None] = spawn_thread,
                ttl_for: Callable[[Any], float | None] | None = None,
                ) -> tuple[Any, dict]:
    """The cached value for *key*, loading it only when the rules allow.

    * fresh entry: returned, no request;
    * expired entry while the source is in backoff or out of budget: returned
      as stale, no request;
    * expired entry with ``swr``: returned at once, refreshed in background
      (one refresh per key at a time);
    * otherwise: loaded, with concurrent callers for the same key sharing one
      request. A failed load serves the expired entry when there is one.

    Returns ``(value, meta)``; see ``meta``.
    """
    entry = cache.entry(key)
    if entry is not None and cache.is_fresh(entry, host):
        return entry.value, meta(entry, cached=True)

    def run():
        value = loader()
        ttl = ttl_for(value) if ttl_for else None
        cache.put(key, value, host=host, ttl=ttl)
        note_refresh(label)
        return value

    if entry is not None:
        reason = blocked()
        if reason:
            logger.info("%s: serving an expired copy from %s (%s)",
                        label, _hhmm(entry.fetched_at), reason)
            return entry.value, meta(entry, cached=True, stale=reason)
        if swr:
            cache.refresh_in_background(key, run, spawn)
            fresh = cache.entry(key)
            if fresh is not None and fresh is not entry and cache.is_fresh(fresh, host):
                # The refresh already finished (a synchronous spawn, or a fast one).
                return fresh.value, meta(fresh, cached=False)
            return entry.value, meta(entry, cached=True, stale="refreshing")

    try:
        value = cache.single_flight(key, run)
    except Exception as exc:
        if entry is not None:
            logger.warning("%s: refresh failed (%s), serving the copy from %s",
                           label, exc, _hhmm(entry.fetched_at))
            reason = exc.reason if isinstance(exc, SourceUnavailable) else "error"
            return entry.value, meta(entry, cached=True, stale=reason)
        raise
    return value, meta(cache.entry(key), cached=False)
