import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth.deps import require
from app.auth.permissions import Permission
from app.config import configured_domain
from app.core import page as sc_page
from app.core.film import get_film_languages
from app.core.tv import get_tv_languages
from app.providers import ProviderRegistry, get_registry
from app.routers._providers import call_provider

logger = logging.getLogger(__name__)

# Searching is the entry point to both flows, so either privilege grants it.
# The language endpoint is part of the same flow: a requester has to see the
# real audio and subtitle tracks before choosing them.
router = APIRouter(
    prefix="/api/search",
    tags=["search"],
    dependencies=[Depends(require(Permission.REQUEST, Permission.DOWNLOAD, mode="or"))],
)


def _domain() -> str:
    domain = configured_domain()
    if not domain:
        raise HTTPException(status_code=409, detail="Nessun dominio configurato")
    return domain


@router.get("")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    # ``all`` is an orchestration mode, not a provider: it queries every
    # registered catalogue in parallel and keeps each result's source.
    source: str = Query(default="streamingcommunity", min_length=1, max_length=64),
    # 1-based, and the same number for both sources even though their pages are
    # different sizes (60 and 30): the caller asks for "the next lot" and each
    # source works out what that means. Bounded so nobody asks the source for a
    # preposterous row offset.
    page: int = Query(default=1, ge=1, le=50),
    # One canonical vocabulary on the wire, lower case; the mapping to
    # AnimeUnity's own capitalisation lives in animeunity.TYPES.
    media_type: str | None = Query(default=None, pattern="^(movie|tv|ova|ona|special)$"),
    dubbed: bool = Query(default=False),
    registry: ProviderRegistry = Depends(get_registry),
    # Last and defaulted: tests call these handlers directly, positionally.
    # FastAPI still injects it, by annotation.
    response: Response = None,
):
    started = time.time()
    if source != "all":
        results = await call_provider(
            registry.search, source, q, page=page, media_type=media_type, dubbed=dubbed)
        if source == "streamingcommunity":
            _cache_headers(response, q, page, media_type, started)
        return [result.to_legacy() for result in results]

    # A missing/broken source must not hide results from the other catalogues:
    # each one is asked on its own, and one that fails (RaiPlay answering
    # badly, a domain not configured) contributes no rows and is named in
    # ``X-Source-Errors`` (``raiplay=502``) instead of failing the whole search.
    # The media type goes only to the sources that declare it
    # (capabilities.media_types); the others get None, so a metadata catalogue
    # with no type filter (RaiPlay) is not excluded. The dubbed filter is
    # AnimeUnity's own and is ignored here.
    def _media_for(provider_source: str):
        try:
            supported = registry.get(provider_source).capabilities.media_types
        except Exception:
            return None
        return media_type if media_type in supported else None

    errors: dict[str, int] = {}

    async def search_one(provider_source: str):
        try:
            return await call_provider(
                registry.search, provider_source, q, page=page,
                media_type=_media_for(provider_source), dubbed=False)
        except HTTPException as exc:
            logger.warning("Unified search source %s unavailable: %s", provider_source, exc.detail)
            errors[provider_source] = exc.status_code
            return []

    sources = registry.sources()
    batches = await asyncio.gather(*(search_one(name) for name in sources))
    _cache_headers(response, q, page,
                   _media_for("streamingcommunity") if "streamingcommunity" in sources else None,
                   started)
    if response is not None and errors:
        response.headers["X-Source-Errors"] = ",".join(
            f"{name}={status}" for name, status in sorted(errors.items()))
    merged = []
    for batch in batches:
        merged.extend(result.to_legacy() for result in batch)
    return merged


def _cache_headers(response: Response | None, q: str, page: int,
                   media_type: str | None, started: float) -> None:
    """Say whether the StreamingCommunity answer came from the panel's cache.

    ``X-Cache`` is ``HIT`` when the answer was not fetched for this request,
    ``X-Cache-Updated`` when it was fetched from the source (epoch seconds).
    ``X-Stale: 1`` means its lifetime is over and it was served anyway (the
    source is in backoff or the hourly budget is spent). No I/O.
    """
    domain = configured_domain()
    if response is None or not domain:
        return
    info = sc_page.search_info(q, domain, page=page, media_type=media_type)
    if not info:
        return
    cached = info["updated_at"] < started
    response.headers["X-Cache"] = "HIT" if cached else "MISS"
    response.headers["X-Cache-Updated"] = str(int(info["updated_at"]))
    if not info["fresh"]:
        response.headers["X-Stale"] = "1"


@router.get("/languages/{title_id}")
async def title_languages(
    title_id: int,
    type: str = Query(..., pattern="^(movie|tv)$"),
    slug: str = Query(default=None),
    version: str = Query(default=""),
):
    domain = _domain()
    try:
        if type == "movie":
            langs = await asyncio.to_thread(get_film_languages, title_id, domain)
        else:
            if not slug:
                raise ValueError("slug is required for tv type")
            langs = await asyncio.to_thread(get_tv_languages, title_id, slug, domain, version)
    except Exception as e:
        logger.warning("Languages fetch error for %s %d: %s", type, title_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    return langs
