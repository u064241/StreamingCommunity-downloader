"""HTTP boundary shared by the provider-backed search, home and title routes."""
import asyncio
import json
import logging

from fastapi import HTTPException, Request

from app.providers import ProviderError, ProviderErrorCode, get_registry
from app.providers.contracts import NOT_DOWNLOADABLE_REASON

logger = logging.getLogger(__name__)


def provider_status(exc: ProviderError) -> int:
    return {
        ProviderErrorCode.NOT_CONFIGURED: 409,
        ProviderErrorCode.UNKNOWN_PROVIDER: 422,
        ProviderErrorCode.INVALID_REQUEST: 422,
        ProviderErrorCode.UNSUPPORTED: 422,
    }.get(exc.code, 502)


async def call_provider(operation, *args, **kwargs):
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except ProviderError as exc:
        status = provider_status(exc)
        if status == 502:
            logger.exception("Provider operation failed for %s", exc.source)
        raise HTTPException(status_code=status, detail=str(exc)) from exc


# ── Download gate ─────────────────────────────────────────────────────────────

def ensure_downloadable(source) -> None:
    """Raise 422 unless *source* is a registered source with ``download=True``.

    Fail-closed: an unknown source, a non-string, or a metadata-only catalogue
    (RaiPlay) is refused. Called before any job, request or download is made.
    """
    if not get_registry().is_downloadable(source):
        shown = source if isinstance(source, str) else type(source).__name__
        logger.warning("Refused a download/request for a non-downloadable source: %r", shown[:64])
        raise HTTPException(status_code=422, detail=f"{NOT_DOWNLOADABLE_REASON} (sorgente: {shown[:64]})")


async def require_downloadable_source(request: Request) -> None:
    """Dependency for every route that creates a download, job or request.

    Reads the JSON body before the route's own model does: a ``source`` that
    is present must be downloadable, whatever the model would make of it. A
    body that is not a JSON object is left to the model's validation (which
    rejects it); routes without a body (retry of an existing job) carry no
    source and pass. When ``source`` is absent the route's model default
    applies, and every such default is a download engine (streamingcommunity,
    animeunity); the models also pin ``source`` to those values (Literal /
    pattern), a second, independent refusal.
    """
    raw = await request.body()
    if not raw:
        return
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return  # not JSON: the route's model answers 422 itself
    if not isinstance(body, dict):
        return
    if "source" in body:
        ensure_downloadable(body["source"])
