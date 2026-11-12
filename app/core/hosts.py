"""The hosts besides the StreamingCommunity domain, and their manual overrides.

AnimeUnity and the video host (vixcloud) both move TLD from time to time. The
video host follows the embed URL on its own; AnimeUnity has a default. Either
can be set by hand in Settings › Sorgente, so a move the code does not foresee
is a form to fill in rather than a release to wait for.

A value saved here decides where the panel sends requests, so it is validated
as a bare hostname — no scheme, path, port or IP literal — and verified against
the service it claims to be before it is stored.
"""

import ipaddress
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlparse

import requests

from app.core.headers import get_headers

_HOSTNAME = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_host(value: str) -> str:
    """A bare lowercase hostname, or ValueError.

    Lenient about what people paste — ``https://www.animeunity.co/`` becomes
    ``www.animeunity.co`` — and strict about what is left: a path, a port or an
    IP address is refused rather than quietly dropped.
    """
    raw = (value or "").strip().lower()
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https") or parsed.path not in ("", "/") \
                or parsed.query or parsed.port:
            raise ValueError("Inserisci solo il nome host, ad esempio www.animeunity.co")
        raw = parsed.hostname or ""
    raw = raw.rstrip("/")
    if not _HOSTNAME.match(raw):
        raise ValueError(f"Host non valido: {value.strip()[:100]!r}")
    return raw


def verify_animeunity(host: str) -> None:
    """Raise RuntimeError unless ``host`` answers like AnimeUnity."""
    from app.core.animeunity import _get_scraper

    try:
        r = _get_scraper().get(f"https://{host}/", headers={"user-agent": get_headers()},
                               timeout=15)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"{host} non raggiungibile: {e}")
    if "animeunity" not in r.text.lower():
        raise RuntimeError(f"{host} risponde, ma non sembra AnimeUnity")


def verify_video_host(host: str) -> None:
    """Raise RuntimeError unless ``host`` serves a 16-byte AES key at /storage/enc.key."""
    try:
        r = requests.get(f"https://{host}/storage/enc.key",
                         headers={"user-agent": get_headers()}, timeout=15)
    except Exception as e:
        raise RuntimeError(f"{host} non raggiungibile: {e}")
    if not r.ok or len(r.content) != 16:
        raise RuntimeError(f"{host} non serve la chiave video (HTTP {r.status_code})")


# ── Where the downloader may be sent ──────────────────────────────────────────
#
# Every URL past the configured domain comes from somebody else's page: the
# iframe names the embed, the embed names the playlist, the playlist names its
# variants, segments, audio and subtitles. Fetching whatever they say makes the
# panel a willing HTTP client for the network it runs inside, so each of those
# URLs is checked here before it is requested, and again on every redirect.

class UnsafeURLError(RuntimeError):
    """A URL the downloader refuses to fetch. Never worth a retry."""


_DNS_TTL = 60.0
_DNS_CACHE_MAX = 256
_dns_cache: dict[str, tuple[float, str | None]] = {}
_dns_guard = threading.Lock()

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5

_SECRET_PARAM = re.compile(r"(?i)\b(token|expires)=[^&\s'\"<>]+")


def redact(text) -> str:
    """*text* with the playback token and its expiry blanked, for logs and errors.

    The pair is a bearer credential for the stream: whoever reads it out of a
    log can play the title until it expires.
    """
    return _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=<redacted>", str(text))


def _resolve(host: str, port: int) -> list[str] | None:
    """The addresses *host* resolves to, or None when it does not resolve."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    return [info[4][0] for info in infos]


def _is_public(raw: str) -> bool:
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    # ::ffff:127.0.0.1 is loopback wearing an IPv6 address.
    address = getattr(address, "ipv4_mapped", None) or address
    return address.is_global and not address.is_multicast


def _non_public_address(host: str, port: int) -> str | None:
    """The first non-public address *host* resolves to, cached for a minute.

    Cached because a download asks this once per segment host and a playlist
    has thousands of segments; a lookup each would be the DNS storm the session
    in m3u8.py exists to avoid.
    """
    now = time.monotonic()
    with _dns_guard:
        cached = _dns_cache.get(host)
        if cached and cached[0] > now:
            return cached[1]
    addresses = _resolve(host, port)
    # A name that does not resolve is let through: there is no address to be
    # sent to, the request fails by itself, and a resolver that is briefly
    # unwell must stay a retriable network error rather than become a verdict.
    bad = next((a for a in addresses or [] if not _is_public(a)), None)
    with _dns_guard:
        if len(_dns_cache) >= _DNS_CACHE_MAX:
            _dns_cache.clear()
        _dns_cache[host] = (now + _DNS_TTL, bad)
    return bad


def assert_public_https(url: str) -> str:
    """Return *url*, or raise UnsafeURLError unless it is https to a public host.

    Refused: any other scheme, a missing host, an IP literal that is private,
    loopback, link-local or otherwise not globally routable, and a name that
    resolves to one. The check and the connection resolve separately, so a
    resolver that changes its answer in between is not covered (DNS rebinding);
    closing that would mean pinning the address at the socket.
    """
    try:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower()
        port = parsed.port or 443
    except ValueError:
        raise UnsafeURLError("URL non valido") from None
    if parsed.scheme != "https" or not host:
        raise UnsafeURLError(
            f"URL non consentito (serve https con un host): {redact(str(url))[:120]!r}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        bad = _non_public_address(host, port)
        if bad:
            raise UnsafeURLError(f"{host} risolve a un indirizzo non pubblico ({bad})")
    else:
        if not _is_public(host):
            raise UnsafeURLError(f"Indirizzo non pubblico non consentito: {host}")
    return url


def safe_get(get, url: str, **kwargs):
    """GET *url* through *get*, validating it and every redirect on the way.

    *get* is ``requests.get`` or a session's bound ``get``. Redirects are
    followed here rather than by requests, so a public host cannot bounce the
    panel to an internal one: each ``Location`` passes assert_public_https
    before it is requested.
    """
    kwargs["allow_redirects"] = False
    for _ in range(MAX_REDIRECTS + 1):
        assert_public_https(url)
        response = get(url, **kwargs)
        location = (getattr(response, "headers", None) or {}).get("Location")
        if getattr(response, "status_code", None) not in _REDIRECTS or not location:
            return response
        url = urljoin(url, location)
    raise UnsafeURLError("Troppi reindirizzamenti")
