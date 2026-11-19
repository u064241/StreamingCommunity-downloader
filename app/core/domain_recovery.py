"""Finding the source domain again after it rotates.

The source's domain changes every few weeks. Until now that meant the panel
simply stopped working: searches failed, nothing said why, and an administrator
had to go and find the new address themselves.

A third-party page publishes the current one. This module reads it, checks the
candidate is plausible, proves it actually serves the source *and carries the
real site's identity* (``page.looks_like_home``), and then adopts it
(``domain_auto_apply``, on by default since the identity check exists) or only
proposes it (setting off). The page is editable by people we do not control,
and the domain is not a cosmetic setting: every search, every image fetch and
every download referer goes to whatever host is configured here. That is why
nothing is adopted on the page's word alone, and why every adoption is
notified (``SOURCE_DOMAIN_APPLIED``).

Besides the periodic watcher, an error answer of the source starts one check
of the channel at once (``_on_source_error``, hooked into
``source_budget.add_error_listener``), at most every ``OFFSCHEDULE_DEBOUNCE``.

The candidate is held in memory, not in data.json. It is discovery state, not
configuration, and a candidate frozen across a restart could be applied long
after the page it came from had changed again.
"""

import ipaddress
import logging
import os
import re
import socket
import threading
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app import config
from app.core.headers import get_headers
from app.core import home, metadata, source_budget
from app.core.page import get_domain_version, home_identity

logger = logging.getLogger(__name__)

# Where candidates come from. Overridable because the page is somebody else's
# and may be deleted, renamed, or replaced by a better source.
CANDIDATES_URL = os.getenv(
    "DOMAIN_SOURCE_URL",
    "https://telegra.ph/Link-Aggiornato-StreamingCommunity-09-29",
)

# Which registrable names may ever be adopted automatically.
#
# A module constant with an env override rather than a settings field, on
# purpose: this is the check that stops an edited page from redirecting the
# panel to an arbitrary host, and a text box in the UI that relaxes it would be
# a loaded gun. A genuine rebrand fails closed — the candidate is logged and
# reported as rejected, and an administrator types the new name in by hand,
# which is a decision made by a person.
NAME_PATTERN = re.compile(
    os.getenv("DOMAIN_NAME_PATTERN", r"^streaming(community|unity)[a-z0-9-]{0,12}$")
)

_HOST_RE = re.compile(r"^[a-z0-9.-]{4,253}$")

# Hosts that are never a public streaming site, whatever DNS says.
_LOCAL_SUFFIXES = (".local", ".internal", ".localhost", ".home", ".lan")

# The page carries a handful of links, most of them telegra.ph's own. Anything
# past this is not a candidate list, it is a different page.
_MAX_CANDIDATES = 5

# The reactive trigger fires whenever the configured domain fails to verify,
# which on a broken panel is every page load of every user. Without a floor
# between checks that becomes a small stampede against someone else's page.
_MIN_CHECK_INTERVAL = 60

# The periodic watcher never runs more often than this, whatever the setting
# says: every pass is a request to the source's home page, and one every five
# minutes, refusals included, is the kind of steady knocking that gets the
# panel banned.
MIN_WATCH_INTERVAL_MINUTES = 30

# A host that served a page carrying its version this recently is alive and is
# the source: there is nothing to verify, and no request is sent to find out.
FRESH_VERSION_AGE = MIN_WATCH_INTERVAL_MINUTES * 60

# An error answer of the source starts a check of the channel at once, outside
# the periodic schedule, at most once in this many seconds.
OFFSCHEDULE_DEBOUNCE = 10 * 60

_lock = threading.Lock()
_pending: dict | None = None
_last_check_at: float | None = None
_last_offschedule_at: float | None = None
_running = False
_status = "idle"

def recovery_state() -> dict:
    current = config.configured_domain()
    with _lock:
        return {"status": "checking" if _running else ("found" if _pending else _status),
                "candidate": dict(_pending) if _pending else None,
                "current": current}


def _now() -> float:
    """Monotonic clock, indirected so tests can move it without touching time."""
    return time.monotonic()


# ── Candidates ────────────────────────────────────────────────────────────────

def fetch_candidates(url: str | None = None) -> list[str]:
    """Hosts linked from the source-of-truth page, in order, deduplicated."""
    target = url or CANDIDATES_URL
    response = requests.get(target, headers={"user-agent": get_headers()}, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    hosts: list[str] = []
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        parsed = urlparse(href)
        # Keep the scheme: is_plausible refuses anything but https, and it must
        # see what the page actually published rather than an assumed default.
        host = (parsed.hostname or "").lower().strip(".")
        if not host or parsed.scheme != "https":
            continue
        if host not in hosts:
            hosts.append(host)
    return hosts[:_MAX_CANDIDATES]


TELEGRAM_URL = "https://t.me/s/Streaming_community_sito"


def fetch_telegram_candidates() -> list[str]:
    """Read newest public Telegram messages, including domains posted as plain text."""
    try:
        response = requests.get(TELEGRAM_URL, headers={"user-agent": get_headers()}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        hosts = []
        for message in reversed(soup.select(".tgme_widget_message_text")):
            # Ignore old/replaced domains shown struck out in announcements.
            for old in message.select("s, del, strike"):
                old.decompose()
            # The channel writes a blocked domain defanged, e.g.
            # "streamingcommunityz[.]nexus", and the new one after the word
            # "Nuovo". A defanged host never matches (no literal dot), so it is
            # never a candidate. When "Nuovo" is present, only what follows it
            # counts: the blocked domain is listed before it and must not win.
            body = message.get_text(" ", strip=True)
            parts = re.split(r"(?i)\bnuovo\b", body)
            anchored = len(parts) > 1
            texts = [parts[-1]] if anchored else [body] + [a.get("href", "") for a in message.find_all("a")]
            for text in texts:
                for host in re.findall(r"(?i)(?<![\w.-])(?:www\.)?streaming(?:community|unity)[a-z0-9-]{0,12}\.[a-z]{2,24}(?![\w.-])", text):
                    host = host.lower()
                    if host not in hosts:
                        hosts.append(host)
            if hosts:
                return hosts[:_MAX_CANDIDATES]
        return []
    except Exception as exc:
        logger.warning("Cannot read public Telegram channel: %s", exc)
        return []


# ── The guard ─────────────────────────────────────────────────────────────────

def _check_shape(host: str) -> str | None:
    """Syntactic rejection reason, or None if the host looks like a candidate."""
    if not host or not _HOST_RE.match(host):
        return "forma non valida"
    if host.endswith(_LOCAL_SUFFIXES) or host == "localhost":
        return "nome locale"

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return "indirizzo IP"

    labels = host.split(".")
    if len(labels) == 3 and labels[0] == "www":
        labels = labels[1:]
    if len(labels) != 2:
        # The load-bearing rule. Checking only the first label would accept
        # streamingcommunity.attacker.tld, where the part that decides where the
        # traffic goes is the attacker's. Multi-part suffixes (.co.uk) are
        # rejected as collateral; typing the domain by hand still works.
        return "non è un dominio di secondo livello"

    name = labels[0]
    if not NAME_PATTERN.match(name):
        return f"nome «{name}» non riconosciuto"
    return None


def _resolves_to_public(host: str) -> bool:
    """Whether every address the host resolves to is a public one.

    Without this, a candidate could point the panel's own fetches at the network
    it is running inside — the panel is a willing HTTP client with an image
    proxy attached.
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        logger.info("Candidate %s does not resolve: %s", host, exc)
        return False
    if not infos:
        return False

    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            logger.warning("Candidate %s resolves to a non-public address %s", host, address)
            return False
    return True


def is_plausible(host: str) -> tuple[bool, str]:
    """Whether this host may be adopted, and why not when it may not.

    The reason is returned rather than logged and dropped: a rebranded source is
    indistinguishable from an attack from in here, and the administrator who has
    to tell them apart needs to see what was refused.
    """
    reason = _check_shape(host)
    if reason:
        return False, reason
    if not _resolves_to_public(host):
        return False, "risolve a un indirizzo non pubblico"
    return True, ""


def verify(host: str) -> str | None:
    """The site version this host serves, or None if it does not serve the source.

    Deliberately stricter than ``PUT /api/domain``, which accepts a host whose
    version string comes back empty: an administrator typing a domain in is
    making a decision, and a page we do not control is not. Here an empty
    version is not proof of anything, so it is not enough.

    A paused source (``source_budget.SourceUnavailable``, a refusal included)
    is not an answer either way, so it reaches the caller instead of reading as
    "not the source".
    """
    try:
        version = get_domain_version(host)
    except source_budget.SourceUnavailable:
        raise
    except Exception as exc:
        logger.info("Candidate %s failed verification: %s", host, exc)
        return None
    return version or None


# Why a candidate that answers with a version is still refused: its start page
# lacks the identity of the real site (app.core.page.looks_like_home). A
# look-alike domain running some Inertia app passes the name pattern and the
# version check; it does not carry the Home component with the source's shelves.
IDENTITY_MISSING = "impronta del sito assente (pagina Home con le vetrine della sorgente)"


# ── Pending candidate ─────────────────────────────────────────────────────────

def pending() -> dict | None:
    with _lock:
        return dict(_pending) if _pending else None


def clear_pending() -> None:
    global _pending, _status
    with _lock:
        _pending = None
        _status = "idle"


def _set_pending(candidate: dict | None) -> None:
    global _pending
    with _lock:
        _pending = candidate


def apply_candidate(host: str) -> str:
    """Write a candidate as the configured domain, re-verifying it first.

    Returns the site version. Raises RuntimeError if the host no longer checks
    out — a candidate can go stale between being proposed and being confirmed.
    """
    ok, reason = is_plausible(host)
    if not ok:
        raise RuntimeError(f"Dominio non adottabile: {reason}")
    version = verify(host)
    if version is None:
        raise RuntimeError(f"«{host}» non risponde come la sorgente attesa")
    if home_identity(host) is not True:
        raise RuntimeError(f"«{host}» non mostra l'impronta del sito: {IDENTITY_MISSING}")

    config.update_data({"domain": host})
    # The shelves and the artwork are the old domain's until they are dropped.
    home.clear_cache("streamingcommunity")
    metadata.clear_cache()
    clear_pending()
    logger.info("Source domain set to %s (version %s)", host, version)
    try:
        from app.jobs import job_manager
        job_manager.broadcast({"type": "domain_changed", "domain": host, "version": version})
    except Exception:
        logger.debug("Cannot broadcast the applied source domain", exc_info=True)
    return version


# ── The cycle ─────────────────────────────────────────────────────────────────

def _settings() -> dict:
    return config.get_settings()


def run_check(force: bool = False, offschedule: bool = False) -> dict:
    global _running, _status
    with _lock:
        if _running:
            return {"checked": False, "candidate": None, "current_ok": False, "applied": False, "rejected": []}
        _running = True
    try:
        result = _run_check(force, offschedule)
        if result["checked"]:
            with _lock:
                _status = "applied" if result["applied"] else "found" if result["candidate"] else "healthy" if result["current_ok"] else "not_found"
        return result
    except Exception:
        with _lock: _status = "error"
        raise
    finally:
        with _lock: _running = False

def _run_check(force: bool = False, offschedule: bool = False) -> dict:
    """One pass: is the configured domain alive, and if not, what could replace it.

    ``force`` is the "Controlla ora" button: it bypasses the throttle and looks
    for a candidate even when the current domain is working, because an
    administrator who pressed it wants an answer rather than a shrug.

    ``offschedule`` is the check an error answer of the source started
    (``_on_source_error``): it reads the channel even while the source is
    paused — the channel is not the source — but it still keeps the throttle,
    and every probe of the source below still goes through the budget, so a
    pause means nothing is sent to it.
    """
    global _last_check_at

    result: dict = {
        "current": config.configured_domain(),
        "current_ok": False,
        "candidate": None,
        "version": None,
        "applied": False,
        "rejected": [],
        "checked": False,
    }

    if not force:
        with _lock:
            if _last_check_at is not None and _now() - _last_check_at < _MIN_CHECK_INTERVAL:
                logger.debug("Domain check skipped: within the throttle window")
                return result

    # During a backoff the watcher stays quiet: no request goes to the source,
    # and a pause is not a reason to go looking for a replacement.
    paused = source_budget.blocked()
    if paused and not force and not offschedule:
        logger.info("Domain check skipped: source paused (%s)", paused)
        return result

    with _lock:
        _last_check_at = _now()
    result["checked"] = True

    _set_pending(None)
    # Telegram is the authoritative source.  Read it even when the configured
    # domain still answers: the old host may remain reachable briefly after a
    # rotation and must not win over the official announcement.
    candidates = fetch_telegram_candidates()
    # An old fallback link must never undo the channel's current announcement,
    # including when Telegram already points to our configured domain.
    if not candidates:
        try:
            candidates = fetch_candidates()
        except Exception as exc:
            logger.warning("Cannot read the domain source page: %s", exc)
            return result

    if result["current"]:
        age = source_budget.version_age(result["current"])
        if age is not None and age < FRESH_VERSION_AGE:
            # It served a page with its version moments ago: alive, no request.
            result["current_ok"] = True
        else:
            try:
                result["current_ok"] = verify(result["current"]) is not None
            except source_budget.SourceUnavailable as exc:
                # A refusal (403/429/503) or a pause is the source asking for
                # room, not a dead domain: never a reason to replace it.
                logger.info("Domain %s not verified: %s", result["current"], exc)
                result["current_ok"] = True

    # Old aliases are dropped before any request goes to them: probing a retired
    # host is traffic toward the source for nothing, and it can be proposed back.
    retired = config.retired_domains()
    for host in candidates:
        if host == result["current"]:
            continue
        if host.strip().lower() in retired:
            result["rejected"].append({"host": host, "reason": "dominio ritirato"})
            continue
        ok, reason = is_plausible(host)
        if not ok:
            logger.info("Rejected candidate %s: %s", host, reason)
            result["rejected"].append({"host": host, "reason": reason})
            continue
        try:
            version = verify(host)
        except source_budget.SourceUnavailable:
            # Paused, or refused just now: probe nothing else until it ends.
            result["rejected"].append({"host": host, "reason": "sorgente in pausa"})
            break
        if version is None:
            result["rejected"].append({"host": host, "reason": "non verificato"})
            continue
        if home_identity(host) is not True:
            # Read off the page verify() just fetched: no request of its own.
            logger.warning("Rejected candidate %s: %s", host, IDENTITY_MISSING)
            result["rejected"].append({"host": host, "reason": IDENTITY_MISSING})
            continue

        result["candidate"] = host
        result["version"] = version
        break

    if result["candidate"] is None:
        return result

    # Applied when ``domain_auto_apply`` is on (the default). Telegram is read
    # first and wins over the other page, but it is still a channel edited by
    # people we do not control: that is why the candidate had to pass the name
    # pattern, the public-address check, a live verification and the identity
    # fingerprint above, and why every adoption is announced. With the setting
    # off the candidate is only proposed.
    if _settings().get("domain_auto_apply"):
        try:
            apply_candidate(result["candidate"])
            result["applied"] = True
            _announce(
                "applied",
                f"Il dominio della sorgente è cambiato: ora è «{result['candidate']}». "
                "Il pannello lo ha applicato da solo.",
            )
        except RuntimeError as exc:
            logger.warning("Auto-apply of %s failed: %s", result["candidate"], exc)
        return result

    _set_pending({
        "host": result["candidate"],
        "version": result["version"],
        "found_at": time.time(),
    })
    _announce(
        "found",
        f"Trovato il nuovo dominio «{result['candidate']}»: apri il pannello per applicarlo.",
    )
    _broadcast_candidate()
    return result


# ── Telling somebody ──────────────────────────────────────────────────────────

def _announce(kind: str, message: str) -> None:
    """Notify whoever can act on this, and never let that break the check.

    ``app.requests.notify`` is imported here rather than at module scope: it
    reaches the database and the job manager, and app.core has no business
    depending on the request system at import time. The event vocabulary still
    lives in one place — this only picks from it.
    """
    try:
        from app.requests import notify as notify_module

        event = {
            "found": notify_module.SOURCE_DOMAIN_FOUND,
            "applied": notify_module.SOURCE_DOMAIN_APPLIED,
        }[kind]
        notify_module.notify(
            event,
            message,
            notify_module.settings_manager_ids(),
            title="Dominio sorgente",
            # The panel itself owns this one when there are no accounts: an open
            # installation still needs to be told its source moved.
            panel_wide=True,
        )
    except Exception:
        logger.exception("Cannot announce the domain change (%s)", kind)


def _broadcast_candidate() -> None:
    """Nudge open tabs so the banner appears without a reload."""
    try:
        from app.jobs import job_manager

        job_manager.broadcast({"type": "domain_candidate"})
    except Exception:
        logger.debug("Cannot broadcast the domain candidate", exc_info=True)


# ── The check outside the schedule ────────────────────────────────────────────
#
# source_budget cannot import this module (this module imports it), so the
# link goes the other way: source_budget keeps a list of error listeners and
# calls them on every error answer; this module registers one at startup
# (app.main lifespan) and removes it at shutdown. Registered there and not at
# import, so a test that merely imports the app never starts a check.

def _spawn(job) -> None:
    """Start *job* on a daemon thread. Indirected so tests can run it inline."""
    threading.Thread(target=job, daemon=True, name="domain-offschedule").start()


def _on_source_error(what: str) -> None:
    """An error answer of the source: read the channel now, in background.

    Never blocks the request that got the error, never sends anything itself.
    Two rules keep it from becoming a loop: an error raised while a check is
    running (the check's own probes included) starts nothing, and at most one
    check starts every ``OFFSCHEDULE_DEBOUNCE`` seconds, the stamp being taken
    when it is started.
    """
    global _last_offschedule_at
    with _lock:
        if _running:
            return
        now = _now()
        if _last_offschedule_at is not None and now - _last_offschedule_at < OFFSCHEDULE_DEBOUNCE:
            return
        _last_offschedule_at = now
    logger.info("Source error (%s): checking the domain channel now", what)
    try:
        _spawn(_offschedule_check)
    except Exception:
        logger.exception("Cannot start the off-schedule domain check")


def _offschedule_check() -> None:
    try:
        if not _settings().get("domain_auto_check_enabled", True):
            return
        run_check(offschedule=True)
    except Exception:
        logger.exception("Off-schedule domain check failed")


def register_offschedule_trigger() -> None:
    source_budget.add_error_listener(_on_source_error)


def unregister_offschedule_trigger() -> None:
    source_budget.remove_error_listener(_on_source_error)


# ── The periodic check ────────────────────────────────────────────────────────

async def domain_watch_loop():
    """Check at startup in a worker, then at the configured interval.

    Never more often than ``MIN_WATCH_INTERVAL_MINUTES``: a lower setting, or
    the old five-minute default, is raised to it.
    """
    import asyncio

    while True:
        if _settings().get("domain_auto_check_enabled", True):
            try:
                await asyncio.to_thread(run_check)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Domain check failed")
        try:
            interval = int(_settings().get("domain_check_interval_minutes")
                           or MIN_WATCH_INTERVAL_MINUTES)
        except (TypeError, ValueError):
            interval = MIN_WATCH_INTERVAL_MINUTES
        await asyncio.sleep(max(MIN_WATCH_INTERVAL_MINUTES, interval) * 60)
