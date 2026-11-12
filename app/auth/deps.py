"""Local single-user access: the request guard and the authorisation dependency.

The panel is a local application: each person installs their own copy on their
own PC and reaches it from their own browser. There is no login, no account and
no session — every request that passes the guard below acts as the one local
user, ``LOCAL_USER``.

What the guard still protects against is the browser itself being used against
the panel by some other site the user visits:

* **DNS rebinding** — a page pointing a hostname of its own at the panel's
  address could otherwise read the whole API. Only loopback names and
  addresses, plus ``ALLOWED_HOSTS``, are answered.
* **CSRF** — a page could otherwise drive any state-changing endpoint. Writes
  must come from the panel's own origin (``Sec-Fetch-Site`` / ``Origin``).

Authorisation still goes through ``require(...)`` so every route keeps its
declared permission: the local user holds every permission (see
``LOCAL_PERMISSIONS``), and is bound to a ``jf_user`` row so the request queue,
which stores requester and approver by row id, works locally.
"""

import dataclasses
import ipaddress
import logging
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from app import config, db
from app.auth import models
from app.auth.permissions import ALL_PERMISSIONS, Permission
from app.config import TRUST_PROXY_HEADERS

logger = logging.getLogger(__name__)

# What the local user may do: everything. The panel has one user, who is also
# the one who approves their own requests, so the request queue (REQUEST,
# MANAGE_REQUESTS) is theirs too. MANAGE_USERS is held for completeness but no
# route uses it any more: there is no user list to manage.
LOCAL_PERMISSIONS = int(ALL_PERMISSIONS)

# The stable key of the local user's jf_user row (migration 12, app/db.py).
LOCAL_USER_KEY = db.LOCAL_USER_KEY

# The local user's shape. ``id`` is filled in from its jf_user row on each
# request (``local_user()``): the request queue stores requester and approver
# by row id, and AUTOINCREMENT, not this module, decides that id.
LOCAL_USER = models.User(
    id=0,
    jellyfin_user_id=LOCAL_USER_KEY,
    username="Pannello",
    is_jellyfin_admin=False,
    permissions=LOCAL_PERMISSIONS,
    enabled=True,
    device_id="",
    created_at="",
    last_login_at=None,
)

# Historical names, kept for callers and tests written against open mode.
OPEN_MODE_PERMISSIONS = LOCAL_PERMISSIONS
OPEN_MODE_USER = LOCAL_USER


def _local_row_id() -> int | None:
    """The id of the local user's jf_user row, or None when there is none
    (a database not migrated yet)."""
    try:
        row = db.query_one("SELECT id FROM jf_user WHERE jellyfin_user_id = ?", (LOCAL_USER_KEY,))
    except Exception:
        return None
    return row["id"] if row else None


def local_user():
    """The local user, bound to its jf_user row.

    Read from ``LOCAL_USER`` at call time, so a test can swap the template for
    a stand-in. Permissions always come from the code, never from the row: the
    row exists for the foreign keys of the request queue, not to grant rights.
    """
    template = LOCAL_USER
    if not isinstance(template, models.User):
        return template
    row_id = _local_row_id()
    if row_id is None or row_id == template.id:
        return template
    return dataclasses.replace(template, id=row_id)


def is_local_user(user) -> bool:
    """Whether *user* is the local user (the template or its bound copy)."""
    return user is LOCAL_USER or getattr(user, "jellyfin_user_id", None) == LOCAL_USER_KEY


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ── Host and Origin ────────────────────────────────────────────────────────────

_LOOPBACK_NAMES = {"localhost"}


def _hostname(netloc: str) -> str | None:
    """The bare, lower-cased host of a ``host[:port]`` value, or None."""
    try:
        return urlsplit(f"//{netloc.strip()}").hostname
    except ValueError:
        return None


def host_allowed(netloc: str) -> bool:
    """Whether the panel answers to this Host.

    ``localhost``, any loopback address (127.0.0.0/8, ::1) and the names in
    ALLOWED_HOSTS. Nothing else: the panel is meant to be reached only from
    the machine it runs on, and a DNS name or LAN address that is not listed
    is either a misconfiguration or a rebinding attempt.
    """
    hostname = _hostname(netloc or "")
    if not hostname:
        return False
    if hostname in _LOOPBACK_NAMES or hostname in config.ALLOWED_HOSTS:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _effective_host(request: Request) -> str:
    """The Host the browser used. Behind a trusted proxy that is
    X-Forwarded-Host — the proxy's own Host only names the upstream."""
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-host")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.headers.get("host", "")


def local_rejection(request: Request) -> str | None:
    """Why a request must be refused, or None when it may proceed."""
    host = _effective_host(request)
    if not host_allowed(host):
        return "Host non consentito"

    if request.method in SAFE_METHODS:
        return None

    # Sec-Fetch-Site is set by the browser and cannot be forged by a page. A
    # client without it (curl, an MCP bridge) is not a browser being driven by
    # somebody else's page, which is the only thing this guards against.
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.strip().lower() not in ("same-origin", "none"):
        return "Richiesta cross-site non consentita"

    origin = request.headers.get("origin")
    if origin is not None:
        try:
            origin_netloc = urlsplit(origin.strip()).netloc
        except ValueError:
            origin_netloc = ""
        # Hosts are compared, not schemes: behind a TLS-terminating proxy the
        # panel sees http while the browser's origin says https.
        if not origin_netloc or origin_netloc.lower() != host.lower():
            return "Origine non consentita"
    return None


# Historical name, kept for callers and tests written against open mode.
open_mode_rejection = local_rejection


class LocalAccessMiddleware:
    """Refuses foreign Hosts and cross-site writes; everything else is the
    local user."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope["state"] = scope.get("state", {})
        request = Request(scope, receive=receive)
        reason = local_rejection(request)
        if reason is not None:
            logger.warning("Refused %s %s (%s)", request.method, request.url.path, reason)
            await JSONResponse({"detail": reason}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


# Historical name.
AuthMiddleware = LocalAccessMiddleware


# ── Dependencies ───────────────────────────────────────────────────────────────

def current_user(request: Request):
    """The local user. A caller that acts on someone's behalf outside a browser
    request (the MCP tools) puts the user on ``request.state.user``; everything
    else is the local user, resolved lazily so static files never touch the
    database."""
    return getattr(request.state, "user", None) or local_user()


def get_local_user(request: Request):
    return current_user(request)


def require(*required: Permission, mode: str = "and"):
    """Dependency factory checking permission bits against the acting user.

    The local user holds every permission, so in the browser this never
    refuses; it still documents what each route needs, and refuses a stand-in
    user with fewer rights (tests, a future restricted MCP owner).
    """

    def dependency(request: Request):
        user = current_user(request)
        if not user.has(*required, mode=mode):
            names = ", ".join(p.name for p in required)
            logger.info(
                "Refused %s %s: needs %s",
                request.method, request.url.path, names,
            )
            raise HTTPException(status_code=403, detail="Permesso negato")
        return user

    dependency.__required_permissions__ = tuple(required)
    return dependency
