import os
import secrets

import uvicorn
from app.config import HOST, PORT

if __name__ == "__main__":
    os.environ.setdefault("SC_PANEL_PORTAL_TOKEN", secrets.token_urlsafe(32))
    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, reload=False))
    app.state.uvicorn_server = server
    server.run()
