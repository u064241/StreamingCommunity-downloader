"""Read-only update information for the u064241 distribution channel."""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import requests

from app import __version__

UPDATE_REPOSITORY = "u064241/StreamingCommunity-downloader"
UPDATE_API_URL = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest"
CHECK_INTERVAL_SECONDS = 6 * 60 * 60
_lock = threading.Lock()
_cached: tuple[float, dict[str, Any]] | None = None


def _version(value: str) -> tuple[int, ...]:
    match = re.search(r"(?:^|[^0-9])v?(\d+(?:\.\d+){0,3})", value or "", re.I)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _result(*, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "current_version": __version__,
        "repository": UPDATE_REPOSITORY,
        **extra,
    }


def check(*, force: bool = False) -> dict[str, Any]:
    """Return the latest fork release without ever changing local files."""
    global _cached
    now = time.monotonic()
    with _lock:
        if not force and _cached and now - _cached[0] < CHECK_INTERVAL_SECONDS:
            return dict(_cached[1])
        try:
            response = requests.get(
                UPDATE_API_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=(3, 8),
            )
            response.raise_for_status()
            release = response.json()
            tag = str(release.get("tag_name") or "")
            remote_version = _version(tag)
            if not tag or not remote_version:
                result = _result(status="invalid_release")
            else:
                result = _result(
                    status=("update_available" if remote_version > _version(__version__) else "up_to_date"),
                    version=tag.lstrip("v"),
                    tag=tag,
                    name=release.get("name") or tag,
                    published_at=release.get("published_at"),
                    notes=release.get("body") or "",
                    url=release.get("html_url"),
                    prerelease=bool(release.get("prerelease")),
                    draft=bool(release.get("draft")),
                )
        except (requests.RequestException, ValueError, TypeError) as exc:
            result = _result(status="unavailable", error=str(exc))
        _cached = (now, result)
        return dict(result)

