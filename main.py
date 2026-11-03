import os
import secrets
import threading
import time
import webbrowser

import uvicorn
from app.config import HOST, PORT

if __name__ == "__main__":
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

    threading.Thread(target=open_panel_when_ready, daemon=True).start()
    server.run()
