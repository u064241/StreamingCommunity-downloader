"""Independent provider contracts used while migrating source adapters."""
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

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
    home: bool = False

@dataclass(frozen=True)
class SearchResult:
    ref: ContentRef; title: str; poster: str | None; backdrop: str | None
    _legacy: dict[str, Any] = field(repr=False, compare=False)
    @classmethod
    def from_legacy(cls, source: str, row: dict[str, Any]):
        if (not isinstance(row, dict) or type(row.get("id")) not in (str, int)
                or not isinstance(row.get("name"), str) or not row["name"].strip()):
            raise ValueError("Missing title identity")
        return cls(ContentRef(source, str(row["id"]), row["type"]), row["name"], row.get("poster"), row.get("backdrop"), deepcopy(row))
    def to_legacy(self):
        row = deepcopy(self._legacy)
        row["source"] = self.ref.source
        return row


def search_results(source: str, rows: list[dict]) -> list[SearchResult]:
    """Keep usable cards when an upstream response contains broken rows."""
    if not isinstance(rows, list):
        raise ProviderError(source, ProviderErrorCode.UPSTREAM, "Invalid provider results")
    results = []
    for row in rows:
        try:
            results.append(SearchResult.from_legacy(source, row))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed title from %s", source)
    return results


@dataclass(frozen=True)
class HomeResult:
    source: str
    shelves: list[dict[str, Any]]
    genres: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_legacy(cls, source: str, shelves: list[dict], genres: list[dict] | None = None):
        if not isinstance(shelves, list):
            raise ProviderError(source, ProviderErrorCode.UPSTREAM, "Invalid provider shelves")
        normalized = []
        for shelf in shelves:
            if (not isinstance(shelf, dict) or not shelf.get("key")
                    or not isinstance(shelf.get("items"), list)):
                logger.warning("Skipping malformed shelf from %s", source)
                continue
            copied = deepcopy(shelf)
            copied["items"] = [item.to_legacy() for item in search_results(source, shelf["items"])]
            normalized.append(copied)
        return cls(source, normalized, deepcopy(genres or []))

    def to_legacy(self):
        return {"source": self.source, "shelves": deepcopy(self.shelves), "genres": deepcopy(self.genres)}

#: Shown on a metadata-only card instead of the download/request button.
NOT_DOWNLOADABLE_REASON = "Contenuto non scaricabile dal pannello"


@dataclass(frozen=True)
class TitleDetails:
    """One title's card for a metadata-only provider (no stream, no download).

    Only descriptive fields live here: there is deliberately no slot for a
    stream, manifest or licence URL, so a provider cannot leak one by accident.
    """
    ref: ContentRef
    title: str
    description: str | None = None
    year: str | None = None
    genres: tuple[str, ...] = ()
    duration: str | None = None
    poster: str | None = None
    backdrop: str | None = None
    page_url: str | None = None
    seasons: tuple[dict[str, Any], ...] = ()
    extra: dict[str, Any] = field(default_factory=dict, compare=False)
    downloadable: bool = False
    download_disabled_reason: str | None = NOT_DOWNLOADABLE_REASON

    def __post_init__(self):
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("Title details require a title")
        if not self.downloadable and not self.download_disabled_reason:
            raise ValueError("A non-downloadable title must say why")

    def to_legacy(self):
        return {
            "id": self.ref.external_id, "source": self.ref.source, "type": self.ref.media_type,
            "name": self.title, "description": self.description, "year": self.year,
            "genres": list(self.genres), "duration": self.duration,
            "poster": self.poster, "backdrop": self.backdrop, "page_url": self.page_url,
            "seasons": deepcopy(list(self.seasons)), **deepcopy(self.extra),
            "downloadable": self.downloadable,
            "download_disabled_reason": None if self.downloadable else self.download_disabled_reason,
        }


@runtime_checkable
class Provider(Protocol):
    source: str; capabilities: ProviderCapabilities
    def search(self, query: str, *, page: int = 1, media_type: str | None = None, dubbed: bool = False) -> list[SearchResult]: ...
