"""Provider scaffold; existing routers are migrated incrementally."""
from .adapters import AnimeUnityProvider, StreamingCommunityProvider
from .contracts import ContentRef, Provider, ProviderCapabilities, ProviderError, ProviderErrorCode, SearchResult
from .registry import ProviderRegistry, create_default_registry
__all__ = ["AnimeUnityProvider", "ContentRef", "Provider", "ProviderCapabilities", "ProviderError", "ProviderErrorCode", "ProviderRegistry", "SearchResult", "StreamingCommunityProvider", "create_default_registry"]
