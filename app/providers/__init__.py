"""Provider contracts and registry for search and front-page shelves."""
from .adapters import AnimeUnityProvider, StreamingCommunityProvider
from .contracts import ContentRef, HomeResult, Provider, ProviderCapabilities, ProviderError, ProviderErrorCode, SearchResult
from .registry import ProviderRegistry, create_default_registry, get_registry
__all__ = ["AnimeUnityProvider", "ContentRef", "HomeResult", "Provider", "ProviderCapabilities", "ProviderError", "ProviderErrorCode", "ProviderRegistry", "SearchResult", "StreamingCommunityProvider", "create_default_registry", "get_registry"]
