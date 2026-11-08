"""Independent provider contracts used while migrating source adapters."""
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

class ProviderErrorCode(str, Enum):
    UNKNOWN_PROVIDER = "unknown_provider"
    NOT_CONFIGURED = "not_configured"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED = "unsupported"
    UPSTREAM = "upstream"

class ProviderError(Exception):
    def __init__(self, source: str, code: ProviderErrorCode, message: str):
        super().__init__(message); self.source = source; self.code = code

@dataclass(frozen=True)
class ContentRef:
    source: str; external_id: str; media_type: str
    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in (self.source, self.external_id, self.media_type)):
            raise ValueError("Content references require non-empty strings")

@dataclass(frozen=True)
class ProviderCapabilities:
    search: bool = True; media_types: tuple[str, ...] = (); dubbed_filter: bool = False
    details: bool = False; episodes: bool = False; tracks: bool = False; download: bool = False

@dataclass(frozen=True)
class SearchResult:
    ref: ContentRef; title: str; poster: str | None; backdrop: str | None
    _legacy: dict[str, Any] = field(repr=False, compare=False)
    @classmethod
    def from_legacy(cls, source: str, row: dict[str, Any]):
        if row.get("id") is None or not str(row.get("name", "")).strip(): raise ValueError("Missing title identity")
        return cls(ContentRef(source, str(row["id"]), row["type"]), row["name"], row.get("poster"), row.get("backdrop"), deepcopy(row))
    def to_legacy(self): return deepcopy(self._legacy)

@runtime_checkable
class Provider(Protocol):
    source: str; capabilities: ProviderCapabilities
    def search(self, query: str, *, page: int = 1, media_type: str | None = None, dubbed: bool = False) -> list[SearchResult]: ...
