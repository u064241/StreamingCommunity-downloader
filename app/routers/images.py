import asyncio
import logging
import os
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Literal

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.auth.deps import require
from app.auth.permissions import Permission
from app.config import configured_domain
from app.core import source_budget
from app.core.headers import get_headers

logger = logging.getLogger(__name__)

# Posters accompany search results and the request queue, so approvers need
# them too even when they cannot search.
router = APIRouter(
    dependencies=[
        Depends(
            require(
                Permission.REQUEST,
                Permission.DOWNLOAD,
                Permission.MANAGE_REQUESTS,
                mode="or",
            )
        )
    ]
)

# Poster filenames come from a third-party API and end up in a URL, so they are
# constrained to what a filename can actually be.
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


def _fetch_image(domain: str, filename: str) -> tuple[bytes, str]:
    """Fetch image from CDN or main domain (blocking, runs in thread)."""
    candidates = [
        f"https://cdn.{domain}/images/{filename}",
        f"https://{domain}/images/{filename}",
    ]
    last_status = 404
    for url in candidates:
        if source_budget.backoff_remaining() > 0:
            raise HTTPException(status_code=503, detail="Sorgente in pausa: riprova più tardi")
        if not _image_allowance():
            raise HTTPException(status_code=503, detail="Limite orario locandine raggiunto")
        try:
            res = requests.get(
                url,
                headers={
                    "user-agent": get_headers(),
                    "referer": f"https://{domain}/",
                },
                timeout=10,
                stream=True,
            )
            content_type = res.headers.get("content-type", "")
            logger.info("Image proxy %s -> HTTP %d ct=%s final=%s",
                        url, res.status_code, content_type, res.url)
            if res.ok and content_type.startswith("image/"):
                return res.content, content_type
            source_budget.observe(res.status_code, "image")
            last_status = res.status_code
            logger.warning("Image proxy miss: %s -> HTTP %d ct=%r", url, res.status_code, content_type)
        except Exception as e:
            logger.warning("Image proxy error for %s: %s", url, e)

    raise HTTPException(status_code=last_status, detail="Image not found")


@router.get("/api/image/{filename:path}")
async def proxy_image(filename: str, source: Literal["streamingcommunity"] = "streamingcommunity"):
    """Proxy images from the streaming site to avoid hotlink protection.

    This endpoint serves StreamingCommunity filenames only. AnimeUnity artwork
    uses the absolute URLs supplied by that source; it must never be redirected
    to the StreamingCommunity CDN. The caller cannot supply an upstream host.
    """
    domain = configured_domain()
    if not domain:
        raise HTTPException(status_code=409, detail="Nessun dominio configurato")
    if ".." in filename or not SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Nome file non valido")

    key = (domain, filename)
    cached = _cache_get(key)
    if cached is None:
        cached = await asyncio.to_thread(_fetch_image, domain, filename)
        _cache_put(key, cached)
    content, content_type = cached
    return Response(
        content=content,
        media_type=content_type,
        # The browser keeps it too: a poster is asked for once per visit, not once per view.
        headers={"Cache-Control": f"public, max-age={_CACHE_MAX_AGE}"},
    )


# Every poster and backdrop used to be a fresh request to the source's CDN, on
# every page view. A small LRU keeps the artwork in memory: a few hundred images
# at a few tens of kilobytes each, and the source sees each file once.
_CACHE_MAX_ENTRIES = 500
_CACHE_MAX_BYTES = 2 * 1024 * 1024
_CACHE_MAX_AGE = 86400
_cache: "OrderedDict[tuple[str, str], tuple[bytes, str]]" = OrderedDict()
_cache_lock = threading.Lock()
# Bumped by a manual refresh. The pages put it in every image URL, so the
# browser asks again too instead of showing its own copy.
_generation = 0


# Artwork has its own allowance, apart from the page budget: a single home page
# can carry seventy posters, and spending the page budget on them would leave
# the pages themselves empty. Most of them come from the cache after the first
# view, so this only caps the misses. Pauses after 403/429/503 still apply.
_IMAGE_WINDOW = 3600.0
try:
    _IMAGE_MAX_PER_HOUR = max(1, int(os.getenv("SC_IMAGE_REQUESTS_PER_HOUR", "300")))
except ValueError:
    _IMAGE_MAX_PER_HOUR = 300
_image_sent: deque[float] = deque()
_image_lock = threading.Lock()


def _image_allowance() -> bool:
    now = time.monotonic()
    with _image_lock:
        while _image_sent and now - _image_sent[0] >= _IMAGE_WINDOW:
            _image_sent.popleft()
        if len(_image_sent) >= _IMAGE_MAX_PER_HOUR:
            return False
        _image_sent.append(now)
        return True


def generation() -> int:
    with _cache_lock:
        return _generation


@router.post("/api/image/refresh")
async def refresh_images():
    """Forget every cached poster and backdrop; the next view fetches them again."""
    global _generation
    # A new poster is a new filename on the source: the listings that name the
    # old one must be read again too, or the old file keeps being requested.
    from app.core import catalog, home, metadata, page
    home.clear_cache()
    metadata.clear_cache()
    catalog.clear_cache()
    page.clear_search_cache()
    with _cache_lock:
        cleared = len(_cache)
        _cache.clear()
        _generation += 1
        current = _generation
    return {"cleared": cleared, "generation": current}


def _cache_get(key):
    with _cache_lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
        return value


def _cache_put(key, value) -> None:
    content, _ = value
    if len(content) > _CACHE_MAX_BYTES:
        return
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)
