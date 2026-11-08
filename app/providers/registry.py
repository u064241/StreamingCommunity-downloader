"""Explicit per-instance provider registry."""
import re

from .contracts import HomeResult, Provider, ProviderCapabilities, ProviderError, ProviderErrorCode, SearchResult

class ProviderRegistry:
    def __init__(self): self._providers = {}
    def register(self, provider: Provider):
        if not isinstance(provider, Provider) or not callable(provider.search):
            raise TypeError("Provider does not implement the search contract")
        if not isinstance(provider.source, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider.source):
            raise ValueError("Provider source must be a lower-case identifier")
        if not isinstance(provider.capabilities, ProviderCapabilities):
            raise TypeError("Provider capabilities are required")
        if provider.capabilities.home and not callable(getattr(provider, "home", None)):
            raise TypeError("Provider does not implement the home contract")
        if provider.source in self._providers: raise ValueError(f"Provider already registered: {provider.source}")
        self._providers[provider.source] = provider
    def get(self, source: str):
        try: return self._providers[source]
        except KeyError as exc: raise ProviderError(source, ProviderErrorCode.UNKNOWN_PROVIDER, f"Unknown provider: {source}") from exc
    def sources(self): return tuple(self._providers)

    def search(self, source: str, query: str, **filters) -> list[SearchResult]:
        provider = self.get(source)
        if not provider.capabilities.search:
            raise ProviderError(source, ProviderErrorCode.UNSUPPORTED, "Provider search is unavailable")
        try:
            results = provider.search(query, **filters)
            if not isinstance(results, list) or any(
                not isinstance(row, SearchResult) or row.ref.source != source for row in results
            ):
                raise ValueError("Invalid search contract result")
            return results
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(source, ProviderErrorCode.UPSTREAM, "Provider search failed") from exc

    def home(self, source: str) -> HomeResult:
        provider = self.get(source)
        if not provider.capabilities.home:
            raise ProviderError(source, ProviderErrorCode.UNSUPPORTED, "Provider home is unavailable")
        try:
            result = provider.home()
            if not isinstance(result, HomeResult) or result.source != source:
                raise ValueError("Invalid home contract result")
            return result
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(source, ProviderErrorCode.UPSTREAM, "Provider home failed") from exc

def create_default_registry():
    from .adapters import AnimeUnityProvider, StreamingCommunityProvider
    registry = ProviderRegistry(); registry.register(StreamingCommunityProvider()); registry.register(AnimeUnityProvider()); return registry


# Registration is explicit and makes no network requests. Request values only
# look up these instances; they never select an import or a server hostname.
_registry = create_default_registry()


def get_registry() -> ProviderRegistry:
    return _registry
