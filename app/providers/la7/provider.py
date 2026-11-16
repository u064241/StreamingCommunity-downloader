"""LA7 direct episode adapter; catalogue capabilities remain unavailable."""
from __future__ import annotations

from ..contracts import ProviderCapabilities, ProviderError, ProviderErrorCode


class LA7Provider:
    source = "la7"
    capabilities = ProviderCapabilities(search=False, download=True)

    def __init__(self, *, http_get=None):
        self._http_get = http_get

    def resolve_url(self, url: str) -> dict:
        """Return checked metadata or raise LA7Error, without downloading media.

        Resolve again at job start: CDN URLs may expire.
        """
        from app.core import la7
        return {"source": self.source, **la7.resolve_episode(url, http_get=self._http_get)}

    def search(self, query, *, page=1, media_type=None, dubbed=False):
        raise ProviderError(self.source, ProviderErrorCode.UNSUPPORTED,
                            "LA7 supporta soltanto il download da URL ufficiale")

    def direct_download_metadata(self, url: str) -> dict:
        """Alias for consumers explicitly requesting download metadata."""
        return self.resolve_url(url)
