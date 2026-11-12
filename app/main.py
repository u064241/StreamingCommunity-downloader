import asyncio
import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import __version__, config, db, downloads_hooks, downloads_notify, library, writable
from app import portal_lifecycle
from app.auth import router as auth_router
from app.auth.deps import LocalAccessMiddleware
from app.auth.security_headers import SecurityHeadersMiddleware
from app.core import domain_recovery
from app.jobs import job_manager
from app.requests import router as requests_router, service as requests_service
from app.schedule import ScheduleStore
from app.config import SCHEDULE_FILE
from app.mcp import server as mcp_http
from app.routers import (
    domain, search, home, tv, downloads, progress, files, images, anime, notification_channels,
    metadata as metadata_router, download_hooks, library as library_router, mcp as mcp_router,
    updates as updates_router,
    softether as softether_router, titles as titles_router,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.log_view import install_log_buffer
install_log_buffer()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"


@lru_cache(maxsize=None)
def _asset_version(name: str) -> str:
    """Short content hash of a static file. Empty when the file is missing.

    Computed once per process: the files are baked into the image and cannot
    change while it runs.
    """
    try:
        return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
    except OSError:
        logger.warning("Static asset not found, serving it unversioned: %s", name)
        return ""


def asset(name: str) -> str:
    """URL for a static file, carrying a hash of its contents.

    Templates must go through this rather than hardcoding ``/static/...``. The
    URL is the only thing an intermediate cache is guaranteed to key on: with a
    fixed path, a reverse proxy is free to keep serving the previous ``app.js``
    against freshly deployed HTML for as long as its own heuristics say, which
    looks exactly like a deploy that did nothing.
    """
    version = _asset_version(name)
    return f"/static/{name}?v={version}" if version else f"/static/{name}"


class VersionedStaticFiles(StaticFiles):
    """Static files with an explicit caching policy.

    Starlette sends only ``ETag``/``Last-Modified`` and no ``Cache-Control``,
    which leaves the decision to each intermediary. Requests carrying a version
    query (everything ``asset()`` emits) may be kept indefinitely, since a
    changed file means a changed URL. Anything else must be revalidated — the
    ETag keeps that cheap — so an unversioned path can never pin a stale file.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        versioned = b"v=" in scope.get("query_string", b"")
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if versioned else "no-cache"
        )
        return response


def _library_dirs() -> list[Path]:
    """Every folder a download can land in: each configured library, plus
    VIDEOS_DIR while some kind has none (it is the fallback, see jobs.py)."""
    libraries = config.read_data().get("libraries", [])
    dirs = {Path(lib["path"]) for lib in libraries if lib.get("path")}
    if not {"film", "tv", "anime"} <= {lib.get("type") for lib in libraries}:
        dirs.add(config.download_dir())
    return sorted(dirs)


def check_writable_paths() -> None:
    """Fail at boot, not three retries into the first download (#28)."""
    writable.check(
        tmp_dir=config.TMP_DIR,
        config_files=[db.current_path(), config.DATA_FILE, config.SCHEDULE_FILE],
        library_dirs=_library_dirs(),
    )


# How long the shutdown waits for cancelled downloads to wind down.
SHUTDOWN_TIMEOUT = 20.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_log_buffer()
    # First: a database the process cannot write fails migrations with SQLite's
    # "unable to open database file", which names neither the path nor the uid.
    check_writable_paths()
    # Jobs live in memory, so the staging directories of a previous run belong
    # to nobody now. After the check above, which is what vouches for TMP_DIR.
    job_manager.purge_stale_temp()
    db.run_migrations()
    requests_service.register_job_listener()
    # Second listener, registered after the request one so a request's own row is
    # updated first: this one only speaks up for jobs no request owns.
    downloads_notify.register_batch_listener()
    # Third and last: outbound side effects only, so it can neither delay a
    # request's own row nor swallow a notification if it fails.
    downloads_hooks.register_hook_listener()
    # Files where each finished download went, under its title's id. Order does
    # not matter: nothing else waits on it, and only the library check reads it.
    library.register_job_listener()
    # Before anything can approve or complete a request: any row still
    # "approved" or "downloading" from a previous run has no in-memory worker
    # left, and never will — it needs recovering before the app is reachable.
    requests_service.reconcile_orphaned_requests()
    store = ScheduleStore(SCHEDULE_FILE)
    job_manager.set_schedule_store(store)
    job_manager.load_scheduled_from_store()
    job_manager.set_loop(asyncio.get_event_loop())
    # Watches the source's domain, not anything in it; it sleeps before its
    # first pass so the lifespan never does network I/O.
    domain_task = asyncio.create_task(domain_recovery.domain_watch_loop())
    portal_task = None
    if os.environ.get("SC_PANEL_PORTAL_TOKEN"):
        portal_task = asyncio.create_task(portal_lifecycle.stop_server_when_idle(app))
    try:
        # Always running, whatever the switch says: the endpoint reads the
        # switch per request, so turning MCP on or off needs no restart.
        async with mcp_http.running():
            yield
    finally:
        domain_task.cancel()
        if portal_task is not None:
            portal_task.cancel()
        # Downloads run on non-daemon threads: left alone they keep going
        # against a loop that is about to close, and hold the process open
        # until the last one ends. On a thread, with the loop still running,
        # because the workers post their last events to it as they stop.
        try:
            await asyncio.to_thread(job_manager.shutdown, SHUTDOWN_TIMEOUT)
        except Exception:
            logger.exception("Download shutdown failed")


app = FastAPI(title="StreamingCommunity Web Panel", version=__version__, lifespan=lifespan)

# No login: the panel is local and single-user. This guard, applied once to
# every request, only lets through loopback / ALLOWED_HOSTS and refuses
# cross-site writes (CSRF); see app/auth/deps.py.
app.add_middleware(LocalAccessMiddleware)
# Added after, so it wraps LocalAccessMiddleware: the 403 that one answers with
# carries the security headers too.
app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", VersionedStaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["asset"] = asset
templates.env.globals["app_version"] = __version__
templates.env.globals["portal_token"] = os.environ.get("SC_PANEL_PORTAL_TOKEN", "")
templates.env.globals["image_generation"] = lambda: images.generation()

app.include_router(auth_router.router)
app.include_router(requests_router.router)
app.include_router(requests_router.notifications_router)
app.include_router(domain.router)
app.include_router(metadata_router.router)
app.include_router(notification_channels.router)
app.include_router(download_hooks.router)
app.include_router(library_router.router)
app.include_router(mcp_router.router)
app.include_router(updates_router.router)
app.include_router(softether_router.router)
# Raw ASGI, outside FastAPI's routing: the MCP transport speaks JSON-RPC itself.
app.add_route(mcp_http.PATH, mcp_http.endpoint, include_in_schema=False)
app.include_router(search.router)
app.include_router(home.router)
app.include_router(home.catalog_router)
app.include_router(titles_router.router)
app.include_router(tv.router)
app.include_router(downloads.router)
app.include_router(progress.router)
app.include_router(files.router)
app.include_router(images.router)
app.include_router(anime.router)


@app.post("/api/portal/heartbeat", include_in_schema=False)
async def portal_heartbeat(request: Request):
    expected = os.environ.get("SC_PANEL_PORTAL_TOKEN", "")
    supplied = request.headers.get("X-Portal-Token", "")
    if not expected or not hmac.compare_digest(expected, supplied):
        return Response(status_code=404)
    try:
        payload = await request.json()
        client_id = payload.get("client_id", "")
    except (ValueError, AttributeError):
        return Response(status_code=400)
    if not isinstance(client_id, str) or not client_id or len(client_id) > 80:
        return Response(status_code=400)
    portal_lifecycle.heartbeat(client_id)
    return Response(status_code=204)


@app.post("/api/portal/close", include_in_schema=False)
async def portal_close(request: Request):
    try:
        payload = await request.json()
        token = payload.get("token", "")
        client_id = payload.get("client_id", "")
    except (ValueError, AttributeError):
        return Response(status_code=400)
    expected = os.environ.get("SC_PANEL_PORTAL_TOKEN", "")
    if not expected or not isinstance(token, str) or not hmac.compare_digest(expected, token):
        return Response(status_code=404)
    if not isinstance(client_id, str) or not client_id or len(client_id) > 80:
        return Response(status_code=400)
    portal_lifecycle.close(client_id)
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    """There is no login any more: old bookmarks land on the panel."""
    return RedirectResponse("/", status_code=302)
