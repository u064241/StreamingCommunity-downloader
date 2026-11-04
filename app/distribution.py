"""Explicit distribution identity. Missing/ambiguous markers disable updates."""
import json
import os
from pathlib import Path
import sys

MARKER = "sc-distribution.json"
PRODUCT = "u064241/StreamingCommunity-downloader"


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
