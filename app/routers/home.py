import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth.deps import require
from app.auth.permissions import Permission
from app.config import configured_domain
from app.core import catalog, source_budget
from app.core import home as home_core
from app.providers import ProviderRegistry, get_registry
from app.routers._providers import call_provider

logger = logging.getLogger(__name__)

# The start page is the search page before anything is typed, so it is reached
# by whoever may search: either privilege grants it.
router = APIRouter(
    prefix="/api/home",
    tags=["home"],
    dependencies=[Depends(require(Permission.REQUEST, Permission.DOWNLOAD, mode="or"))],
)


@router.get("")
async def home(
    source: str = Query(default="streamingcommunity", min_length=1, max_length=64),
    registry: ProviderRegistry = Depends(get_registry),
    # Last and defaulted: tests call these handlers directly, positionally.
    # FastAPI still injects it, by annotation.
    response: Response = None,
):
    """The shelves the source itself publishes on its front page.

    The body keeps its shape; where the shelves came from rides in headers, as
    for ``/api/search``: ``X-Cache`` (``HIT``/``MISS``), ``X-Cache-Updated``
    (epoch seconds of the fetch from the source) and ``X-Stale`` naming why an
    expired copy was served (``refreshing``, ``backoff``, ``budget``,
    ``error``).
    """
    result = await call_provider(registry.home, source)
    info = home_core.last_served(source) or {}
    if response is not None and info:
        response.headers["X-Cache"] = "HIT" if info.get("cached") else "MISS"
        if info.get("updated_at"):
            response.headers["X-Cache-Updated"] = str(int(info["updated_at"]))
        if info.get("stale"):
            response.headers["X-Stale"] = str(info["stale"])
    return result.to_legacy()


catalog_router = APIRouter(
    tags=["catalog"],
    dependencies=[Depends(require(Permission.REQUEST, Permission.DOWNLOAD, mode="or"))],
)


@catalog_router.get("/api/source/status")
async def source_status():
    """Requests sent to the source in the last hour, backoff, last refreshes.

    Read-only, no request to the source. Keys are categories (``home:<source>``,
    ``search``, ``metadata``, ``catalog``), never a search text.
    ``unknown_fields`` maps field paths the panel does not know to the number
    of pages they were seen on (names and counts only; app/core/source_fields.py).
    """
    return source_budget.status()


@catalog_router.get("/api/browse")
@router.get("/section")
async def browse(
    section: str = Query(pattern="^(trending|latest|top10|upcoming|genre)$"),
    genre_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1, le=50),
    media_type: str | None = Query(default=None, pattern="^(movie|tv)$"),
):
    host = configured_domain()
    if not host:
        raise HTTPException(status_code=409, detail="Nessun dominio configurato")
    try:
        result = await asyncio.to_thread(catalog.browse, host, section,
                                        genre_id=genre_id, number=page, media_type=media_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logger.exception("Catalog fetch failed")
        raise HTTPException(status_code=502, detail="Catalogo sorgente non disponibile")
    return result


@catalog_router.get("/api/archive")
async def archive(
    page: int = Query(default=1, ge=1, le=50),
    genre_id: int | None = Query(default=None, ge=1),
    country_id: int | None = Query(default=None, ge=1),
    media_type: str | None = Query(default=None, pattern="^(movie|tv)$"),
    response: Response = None,
):
    """One page of the source's /archive (60 titles), on explicit user action.

    Same cache and budget as ``/api/browse``; where the page came from rides in
    ``X-Cache`` / ``X-Cache-Updated`` / ``X-Stale`` as for ``/api/home``. With
    nothing cached and the source in backoff or out of budget: 503, nothing sent.
    """
    host = configured_domain()
    if not host:
        raise HTTPException(status_code=409, detail="Nessun dominio configurato")
    try:
        result, info = await asyncio.to_thread(
            catalog.archive, host, number=page, genre_id=genre_id,
            country_id=country_id, media_type=media_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except source_budget.SourceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        logger.exception("Archive fetch failed")
        raise HTTPException(status_code=502, detail="Archivio sorgente non disponibile")
    if response is not None and info:
        response.headers["X-Cache"] = "HIT" if info.get("cached") else "MISS"
        if info.get("updated_at"):
            response.headers["X-Cache-Updated"] = str(int(info["updated_at"]))
        if info.get("stale"):
            response.headers["X-Stale"] = str(info["stale"])
    return result
