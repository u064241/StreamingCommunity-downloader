"""RaiPlay as a metadata-only provider: search, front page and title cards.

RaiPlay video is DRM-protected (Widevine/PlayReady). This provider therefore
declares ``download=False``, never resolves a stream, and marks every card
``downloadable: False`` with a reason the UI can show next to a disabled button.
"""
from __future__ import annotations

import logging

from ..contracts import (
    NOT_DOWNLOADABLE_REASON, HomeResult, ProviderCapabilities, ProviderError, ProviderErrorCode, TitleDetails, search_results,
)
from . import parser
from .client import RaiPlayClient, RaiPlayUnavailable

logger = logging.getLogger(__name__)

PAGE_SIZE = 30


class RaiPlayProvider:
    source = parser.SOURCE
    # No media-type filter: the search service does not say film vs series.
    capabilities = ProviderCapabilities(
        search=True, media_types=(), dubbed_filter=False,
        details=True, episodes=False, tracks=False, download=False, home=True,
    )

    def __init__(self, *, client: RaiPlayClient | None = None):
        self._client = client

    @property
    def client(self) -> RaiPlayClient:
        if self._client is None:
            self._client = RaiPlayClient()
        return self._client

    def _error(self, code, message, exc=None):
        if exc is not None:
            logger.warning("RaiPlay %s: %s", message, exc)
        return ProviderError(self.source, code, message)

    def search(self, query, *, page=1, media_type=None, dubbed=False):
        if (not isinstance(query, str) or not query.strip() or len(query) > 200
                or type(page) is not int or not 1 <= page <= 50 or type(dubbed) is not bool):
            raise self._error(ProviderErrorCode.INVALID_REQUEST, "Invalid query, page, or dubbed filter")
        if media_type is not None:
            raise self._error(ProviderErrorCode.UNSUPPORTED, "Unsupported media type")
        if dubbed:
            raise self._error(ProviderErrorCode.UNSUPPORTED, "Dubbed filter is unavailable")
        try:
            payload = self.client.search(query.strip(), offset=(page - 1) * PAGE_SIZE, size=PAGE_SIZE)
            return search_results(self.source, parser.search_rows(payload))
        except RaiPlayUnavailable as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "RaiPlay non raggiungibile", exc) from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "Risposta RaiPlay non valida", exc) from exc

    def home(self):
        try:
            shelves, genres = parser.home_shelves(self.client.home())
            return HomeResult.from_legacy(self.source, shelves, genres)
        except RaiPlayUnavailable as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "RaiPlay non raggiungibile", exc) from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "Risposta RaiPlay non valida", exc) from exc

    def details(self, external_id) -> TitleDetails:
        path = parser.path_for_id(external_id)
        if path is None:
            raise self._error(ProviderErrorCode.INVALID_REQUEST, "Identificativo RaiPlay non valido")
        try:
            return parser.title_details(external_id, self.client.title(path))
        except RaiPlayUnavailable as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "RaiPlay non raggiungibile", exc) from exc
        except Exception as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "Risposta RaiPlay non valida", exc) from exc

    def stream(self, *_args, **_kwargs):
        """Explicit refusal: there is no download or playback path for RaiPlay."""
        raise self._error(ProviderErrorCode.UNSUPPORTED, NOT_DOWNLOADABLE_REASON)

