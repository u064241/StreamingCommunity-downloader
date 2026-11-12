"""Track standalone portal tabs without mistaking browser throttling for closure."""
import logging
import threading
import time

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_clients: dict[str, float] = {}
_last_heartbeat = 0.0
_last_close = 0.0
_closed_clients: dict[str, float] = {}
# Seconds with no open portal tab before the standalone server stops.
CLOSE_GRACE_SECONDS = 3.0


def heartbeat(client_id: str) -> None:
    global _last_heartbeat, _last_close
    now = time.monotonic()
    with _lock:
        if client_id in _closed_clients:
            return
        _clients[client_id] = now
        _last_heartbeat = now
        _last_close = 0.0


def close(client_id: str) -> None:
    global _last_close, _last_heartbeat
    now = time.monotonic()
    with _lock:
        _clients.pop(client_id, None)
        _closed_clients[client_id] = now
        if len(_closed_clients) > 1024:
            del _closed_clients[next(iter(_closed_clients))]
        _last_close = now
        if not _last_heartbeat:
            _last_heartbeat = now


def should_stop_idle(timeout: float | None = None) -> bool:
    now = time.monotonic()
    with _lock:
        if not _last_heartbeat:
            return False
        if not _clients and _last_close:
            # Long enough for a reload or a navigation inside the portal to
            # send its first heartbeat; short enough that closing the window
            # stops the server almost at once.
            return now - _last_close >= CLOSE_GRACE_SECONDS
        return timeout is not None and now - _last_heartbeat >= timeout


async def stop_server_when_idle(app, timeout: float | None = None) -> None:
    import asyncio
    from app.jobs import job_manager
    while True:
        await asyncio.sleep(1)
        if not should_stop_idle(timeout):
            continue
        # A throttled or closed browser must not destroy a download's event loop.
        if job_manager.has_active_work():
            continue
        server = getattr(app.state, "uvicorn_server", None)
        if server is not None:
            logger.info("Stopping standalone server: all portal tabs closed, no active downloads")
            server.should_exit = True
        return
