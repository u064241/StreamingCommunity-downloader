"""Bridges to existing search and home functions; backends load only on use."""
from collections.abc import Callable
from .contracts import HomeResult, ProviderCapabilities, ProviderError, ProviderErrorCode, search_results

class _SearchAdapter:
    def search(self, query, *, page=1, media_type=None, dubbed=False):
        if (not isinstance(query, str) or not query.strip() or len(query) > 200 or type(page) is not int or not 1 <= page <= 50 or type(dubbed) is not bool):
            raise ProviderError(self.source, ProviderErrorCode.INVALID_REQUEST, "Invalid query, page, or dubbed filter")
        if media_type is not None and media_type not in self.capabilities.media_types: raise ProviderError(self.source, ProviderErrorCode.UNSUPPORTED, "Unsupported media type")
        if dubbed and not self.capabilities.dubbed_filter: raise ProviderError(self.source, ProviderErrorCode.UNSUPPORTED, "Dubbed filter is unavailable")
        try: return search_results(self.source, self._search(query, page=page, media_type=media_type, dubbed=dubbed))
        except ProviderError: raise
        except Exception as exc: raise ProviderError(self.source, ProviderErrorCode.UPSTREAM, "Provider search failed") from exc

    def home(self):
        try:
            from app.core import home

            host = self._host()
            shelves = home.shelves(self.source, host)
            genres = home._cached((self.source, host, "genres")) or []
            return HomeResult.from_legacy(self.source, shelves, genres)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.source, ProviderErrorCode.UPSTREAM, "Provider home failed") from exc

class StreamingCommunityProvider(_SearchAdapter):
    # download=True: the download/request flow has an engine for this source
    # (app/source_identity.py). Checked by registry.is_downloadable at every
    # download and request entry point.
    source = "streamingcommunity"; capabilities = ProviderCapabilities(media_types=("movie", "tv"), home=True, download=True)
    def __init__(self, *, search_fn: Callable | None = None, domain_getter: Callable[[], str] | None = None): self._search_fn = search_fn; self._domain_getter = domain_getter
    def _host(self):
        get_domain = self._domain_getter
        if get_domain is None:
            from app.config import configured_domain; get_domain = configured_domain
        domain = get_domain()
        if not domain or not domain.strip(): raise ProviderError(self.source, ProviderErrorCode.NOT_CONFIGURED, "Nessun dominio configurato")
        return domain

    def _search(self, query, *, page, media_type, dubbed):
        domain = self._host()
        search_fn = self._search_fn
        if search_fn is None:
            from app.core.page import search; search_fn = search
        return search_fn(query, domain, page=page, media_type=media_type)

class AnimeUnityProvider(_SearchAdapter):
    source = "animeunity"; capabilities = ProviderCapabilities(media_types=("movie", "tv", "ova", "ona", "special"), dubbed_filter=True, home=True, download=True)
    def __init__(self, *, search_fn: Callable | None = None): self._search_fn = search_fn
    def _host(self):
        from app.core.animeunity import host
        return host()

    def _search(self, query, *, page, media_type, dubbed):
        search_fn = self._search_fn
        if search_fn is None:
            from app.core.animeunity import search; search_fn = search
        return search_fn(query, page=page, media_type=media_type, dubbed=dubbed)


# Metadata-only catalogue (no download). Importing it loads no HTTP library and
# makes no request: the client is created on first use.
from .raiplay.provider import RaiPlayProvider  # noqa: E402
