"""HTTP boundary shared by the provider-backed search and home routes."""
import asyncio
import logging

from fastapi import HTTPException

from app.providers import ProviderError, ProviderErrorCode

logger = logging.getLogger(__name__)


async def call_provider(operation, *args, **kwargs):
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except ProviderError as exc:
        status = {
            ProviderErrorCode.NOT_CONFIGURED: 409,
            ProviderErrorCode.UNKNOWN_PROVIDER: 422,
            ProviderErrorCode.INVALID_REQUEST: 422,
            ProviderErrorCode.UNSUPPORTED: 422,
        }.get(exc.code, 502)
        if status == 502:
            logger.exception("Provider operation failed for %s", exc.source)
        raise HTTPException(status_code=status, detail=str(exc)) from exc
