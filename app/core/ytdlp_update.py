"""Version check for yt-dlp, the engine behind RaiPlay downloads.

yt-dlp changes often and RaiPlay downloads break when it lags behind. This
module only reads the installed version and PyPI's latest, and only when the
caller asks. It installs nothing: in the frozen build yt-dlp is bundled, so an
update means a new release, not a file swap.
"""
from __future__ import annotations

import json
import logging
import re
from importlib import metadata
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

PACKAGE = "yt-dlp"
PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
TIMEOUT_S = 10


def installed_version() -> str | None:
    try:
        from yt_dlp.version import __version__
        return str(__version__)
    except Exception:
        pass
    try:
        return metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:
        return None


def latest_version() -> str:
    req = Request(PYPI_URL, headers={"User-Agent": "StreamingCommunityDownloader/ytdlp-check"})
    with urlopen(req, timeout=TIMEOUT_S) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["info"]["version"])


def _key(version: str) -> tuple[int, ...] | None:
    parts = re.findall(r"\d+", version)
    return tuple(int(p) for p in parts) if parts else None


def status() -> dict:
    current = installed_version()
    try:
        latest = latest_version()
        error = None
    except Exception as exc:  # offline, PyPI down, malformed answer
        logger.warning("yt-dlp version check failed: %s", exc)
        latest, error = None, "Impossibile leggere la versione su PyPI"
    cur_key, new_key = (_key(current) if current else None), (_key(latest) if latest else None)
    return {
        "installed": current,
        "latest": latest,
        "update_available": bool(cur_key and new_key and new_key > cur_key),
        "error": error,
    }
