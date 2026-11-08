import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import require
from app.auth.permissions import Permission
from app.config import configured_domain
from app.core import catalog
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
):
    """The shelves the source itself publishes on its front page."""
    result = await call_provider(registry.home, source)
    return result.to_legacy()


catalog_router = APIRouter(
    tags=["catalog"],
    dependencies=[Depends(require(Permission.REQUEST, Permission.DOWNLOAD, mode="or"))],
)


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
