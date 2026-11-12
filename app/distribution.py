"""Explicit distribution identity. Missing/ambiguous markers disable updates."""
import ipaddress
import json
import os
from pathlib import Path
import sys

MARKER = "sc-distribution.json"
PRODUCT = "u064241/StreamingCommunity-downloader"


def desktop_lifecycle(host: str) -> bool:
    """Whether closing the last browser tab may stop the server.

    Rule: (Windows source checkout OR frozen build) AND loopback host
    (localhost, 127.0.0.0/8, ::1) AND not Docker (SCPANEL_INSTALL=docker).
    The panel is single-user and local; anything listening on the LAN or on
    0.0.0.0, and any container, is a server that other tabs must not stop.
    """
    if os.environ.get("SCPANEL_INSTALL", "").strip().lower() == "docker":
        return False
    if os.name != "nt" and not getattr(sys, "frozen", False):
        return False
    host = (host or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def mode() -> str:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return "source"
    try:
        marker = Path(sys.executable).parent / MARKER
        from app.portable_update import _path
        _path(marker, exists=False)  # reject junctions/symlinks, without EXE check
        data = json.loads(marker.read_text(encoding="utf-8-sig"))
        if data.get("schema") == 1 and data.get("repository") == PRODUCT:
            if data.get("mode") in {"portable", "installed"}:
                return data["mode"]
    except (OSError, ValueError, RuntimeError, AttributeError):
        pass
    return "unknown"
