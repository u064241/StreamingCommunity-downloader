import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _restore_frozen_stdio():
    """Give windowed builds a log stream for Uvicorn and application logs."""
    if not getattr(sys, "frozen", False) or (sys.stdout is not None and sys.stderr is not None):
        return

    log_dir = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "StreamingCommunityDownloader" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = (log_dir / "server.log").open("a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = stream
    sys.stderr = stream


_restore_frozen_stdio()

# Let the installer/uninstaller detect a running windowless application.
# Windows releases the handle automatically when the process exits.
_app_mutex_handle = None
if os.name == "nt" and getattr(sys, "frozen", False):
    import ctypes
    from ctypes import wintypes
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _app_mutex_handle = _kernel32.CreateMutexW(None, False, "Local\\StreamingCommunityDownloaderRunning")

import uvicorn
from app.config import HOST, PORT

if __name__ == "__main__":
    from app.windows_registration import register_portable
    register_portable()
    os.environ.setdefault("SC_PANEL_PORTAL_TOKEN", secrets.token_urlsafe(32))
    from app.main import app

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=PORT,
            reload=False,
            timeout_graceful_shutdown=5,
        )
    )
    app.state.uvicorn_server = server

    def open_panel_when_ready():
        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if server.started:
            browser_host = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(HOST, HOST)
            if ":" in browser_host and not browser_host.startswith("["):
                browser_host = f"[{browser_host}]"
            webbrowser.open(f"http://{browser_host}:{PORT}/")

    if (os.environ.get("SC_PANEL_OPEN_BROWSER", "1") != "0"
            and os.environ.get("SC_PANEL_RESTART") != "1"):
        threading.Thread(target=open_panel_when_ready, daemon=True).start()
    server.run()
