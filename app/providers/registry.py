"""Explicit per-instance provider registry."""
from .contracts import Provider, ProviderError, ProviderErrorCode

class ProviderRegistry:
    def __init__(self): self._providers = {}
    def register(self, provider: Provider):
        if not isinstance(provider, Provider): raise TypeError("Provider does not implement the search contract")
        if not isinstance(provider.source, str) or not provider.source.strip(): raise ValueError("Provider source must be a non-empty string")
        if provider.source in self._providers: raise ValueError(f"Provider already registered: {provider.source}")
        self._providers[provider.source] = provider
    def get(self, source: str):
        try: return self._providers[source]
        except KeyError as exc: raise ProviderError(source, ProviderErrorCode.UNKNOWN_PROVIDER, f"Unknown provider: {source}") from exc
    def sources(self): return tuple(self._providers)

def create_default_registry():
    from .adapters import AnimeUnityProvider, StreamingCommunityProvider
    registry = ProviderRegistry(); registry.register(StreamingCommunityProvider()); registry.register(AnimeUnityProvider()); return registry
