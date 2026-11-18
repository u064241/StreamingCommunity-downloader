"""RaiPlay catalogue provider and yt-dlp download entry point.

yt-dlp performs the final format/DRM check at download time; it never handles
licences or keys.
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
        details=True, episodes=True, tracks=False, download=True, home=True,
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

    def video_downloadable(self, page_url: str) -> tuple[bool, str | None]:
        """Fail closed: a video page is queued only when its own data declares no DRM."""
        relative = page_url.removeprefix(parser.BASE_URL).removesuffix(".html")
        try:
            payload = self.client.video(relative + ".json")
        except RaiPlayUnavailable as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "RaiPlay non raggiungibile", exc) from exc
        except Exception as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "Risposta RaiPlay non valida", exc) from exc
        if parser.video_drm(payload):
            return False, "Il contenuto RaiPlay è protetto da DRM"
        return True, None

    def stream(self, *_args, **_kwargs):
        """Streaming is deliberately not exposed; downloads use yt-dlp."""
        raise self._error(ProviderErrorCode.UNSUPPORTED, NOT_DOWNLOADABLE_REASON)

    def episodes(self, external_id, season_path=None, season_number=None):
        title_path = parser.path_for_id(external_id)
        root = title_path.removesuffix(".json") + "/" if title_path else None
        if (root is None or (season_path is not None and
                            (not isinstance(season_path, str) or not season_path.startswith(root)))
                or (season_number is not None and (type(season_number) is not int or season_number < 1))):
            raise self._error(ProviderErrorCode.INVALID_REQUEST, "Stagione RaiPlay non valida")
        try:
            # Resolve legacy callers by a known catalogue path or label, never
            # by array position. New clients need only the numeric season.
            if season_number is None:
                entries = parser.season_entries(self.client.title(title_path).get("blocks"))
                season_number = next((entry["number"] for entry in entries
                                      if season_path == entry["path"]), None)
                if season_number is None and season_path:
                    season_number = parser.season_number(season_path.rsplit("/", 1)[-1].replace("-", " "))
                if season_number is None:
                    raise ValueError("Numero stagione RaiPlay mancante")
            if season_path and (".." in season_path or "?" in season_path or "#" in season_path):
                raise ValueError("Percorso stagione RaiPlay non valido")
            return self.client.season_episodes(title_path, season_number, season_path)
        except RaiPlayUnavailable as exc:
            raise self._error(ProviderErrorCode.UPSTREAM, "RaiPlay non raggiungibile", exc) from exc
        except (ValueError, TypeError) as exc:
            raise self._error(ProviderErrorCode.INVALID_REQUEST, "Percorso stagione RaiPlay non valido", exc) from exc

