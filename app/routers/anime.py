import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import require
from app.auth.permissions import Permission
from app.core import animeunity
from app.core import animeunity_catalog

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/anime",
    tags=["anime"],
    dependencies=[Depends(require(Permission.REQUEST, Permission.DOWNLOAD, mode="or"))],
)


@router.get("/catalog")
async def catalogue(
    section: Literal["latest", "archive", "latest_animes", "upcoming", "top_day", "top_week", "top_month"] = "archive",
    page: int = Query(default=1, ge=1, le=10000),
    per_page: int = Query(default=60, ge=20, le=60),
    q: str = Query(default="", max_length=200),
    media_type: Literal["movie", "tv", "ova", "ona", "special"] | None = None,
    dubbed: bool = False,
    sort: Literal["title_az", "title_za", "latest", "score", "views"] = "title_az",
):
    if per_page not in (20, 30, 60):
        raise HTTPException(status_code=422, detail="per_page must be 20, 30 or 60")
    try:
        return await asyncio.to_thread(animeunity_catalog.browse, section=section, page=page,
                                       per_page=per_page, query=q, media_type=media_type,
                                       dubbed=dubbed, sort=sort)
    except Exception:
        logger.exception("AnimeUnity catalogue error")
        raise HTTPException(status_code=502, detail="AnimeUnity catalogue unavailable")


@router.get("/calendar")
async def calendar(dubbed: bool = False):
    try:
        return await asyncio.to_thread(animeunity_catalog.calendar, dubbed=dubbed)
    except Exception:
        logger.exception("AnimeUnity calendar error")
        raise HTTPException(status_code=502, detail="AnimeUnity calendar unavailable")


@router.get("/{anime_id}/episodes")
async def get_episodes(anime_id: str, source: Literal["animeunity"] = "animeunity",
                       language: str | None = Query(default=None, pattern=r"^[a-z]{2,3}$")):
    """Fetch all episodes for an anime from AnimeUnity info_api."""
    try:
        kwargs = {"language": language} if language else {}
        episodes = await asyncio.to_thread(animeunity.get_episodes, anime_id, **kwargs)
    except Exception as e:
        logger.exception("AnimeUnity episodes error for %s", anime_id)
        raise HTTPException(status_code=502, detail=str(e))
    return episodes
