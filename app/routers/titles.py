"""Read-only title cards from the provider registry (metadata-only sources).

``GET /api/titles/{source}/{external_id}`` answers ``registry.details`` as the
legacy card shape. Nothing is written: no job, no request, no cache of the
panel's own. A card from a source that cannot be downloaded (RaiPlay) carries
``downloadable: false`` and the reason, and holds no stream address (the
TitleDetails contract has no slot for one).
"""
from fastapi import APIRouter, Depends, Path

from app.auth.deps import require
from app.auth.permissions import Permission
from app.providers import ProviderRegistry, get_registry
from app.routers._providers import call_provider

router = APIRouter(
    prefix="/api/titles",
    tags=["titles"],
    dependencies=[Depends(require(Permission.REQUEST, Permission.DOWNLOAD, mode="or"))],
)


@router.get("/{source}/{external_id}")
async def title_details(
    source: str = Path(min_length=1, max_length=64, pattern="^[a-z][a-z0-9_-]{0,63}$"),
    # URL-encoded by the caller (RaiPlay ids carry a colon); the provider
    # validates the decoded value strictly before any request.
    external_id: str = Path(min_length=1, max_length=200),
    registry: ProviderRegistry = Depends(get_registry),
):
    details = await call_provider(registry.details, source, external_id)
    card = details.to_legacy()
    # Belt and braces: a source the registry does not let download never
    # reaches the UI as downloadable, whatever its details said.
    if not registry.is_downloadable(source):
        card["downloadable"] = False
        card["download_disabled_reason"] = details.download_disabled_reason or card.get(
            "download_disabled_reason") or "Contenuto non scaricabile dal pannello"
    return card
