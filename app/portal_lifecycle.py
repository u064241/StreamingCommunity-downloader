"""Track open panel tabs for the standalone ``main.py`` launcher."""

import threading
import time

_lock = threading.Lock()
_clients: dict[str, float] = {}
_last_heartbeat = 0.0


def heartbeat(client_id: str) -> None:
    global _last_heartbeat
    now = time.monotonic()
    with _lock:
        _clients[client_id] = now
        _last_heartbeat = now


def should_stop_idle(timeout: float = 15.0) -> bool:
    """Stop only after the panel was opened and all its tabs went quiet."""
    now = time.monotonic()
    with _lock:
        if not _last_heartbeat:
            return False
        if any(now - seen < timeout for seen in _clients.values()):
            return False
        _clients.clear()
        return now - _last_heartbeat >= timeout


async def stop_server_when_idle(app, timeout: float = 15.0) -> None:
    import asyncio

    while not should_stop_idle(timeout):
        await asyncio.sleep(2)
    server = getattr(app.state, "uvicorn_server", None)
    if server is not None:
        server.should_exit = True
