import asyncio
import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request as HttpRequest
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import downloads_notify
from app.auth.deps import current_user, is_local_user, require
from app.auth.permissions import Permission
from app.config import configured_domain
from app.jobs import job_manager
from app.routers._providers import require_downloadable_source, call_provider
from app.providers import get_registry, ProviderRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/download", tags=["downloads"])

# One request can queue a whole series. The cap keeps a mis-click from filling
# the executor and the event stream with thousands of jobs.
MAX_BATCH_JOBS = 500

# Starting a download directly is the privilege this whole feature exists to
# gate: without it a user goes through the request queue instead.
# Then the source gate: a ``source`` in the body that is not downloadable
# (unknown, or a metadata-only catalogue such as RaiPlay) is refused with 422
# before any job is created. Fail-closed; see _providers.require_downloadable_source.
CAN_DOWNLOAD = [Depends(require(Permission.DOWNLOAD)), Depends(require_downloadable_source)]

# Job control is also reachable by approvers, who must be able to stop a
# download they approved without being allowed to start one themselves.
CAN_CONTROL_JOBS = [
    Depends(require(Permission.DOWNLOAD, Permission.MANAGE_REQUESTS, mode="or"))
]


def _domain() -> str:
    """The configured source host, never one supplied by the caller."""
    domain = configured_domain()
    if not domain:
        raise HTTPException(status_code=409, detail="Nessun dominio configurato")
    return domain


def _tmdb_id(media_type: str, title_id) -> int | None:
    """The TMDB id for this title, if the panel already happens to know it.

    Resolved server-side rather than accepted from the request body: it decides
    which third-party host a failed download falls back to, which is not a
    choice a client gets to make. A pure cache read, so a download that is about
    to work pays nothing — the detail modal has usually just filled it in, and a
    miss simply means no fallback, which is what there was before.
    """
    from app.core import metadata

    try:
        return metadata.cached_tmdb_id(media_type, title_id)
    except Exception:
        return None


class StreamingCommunityRequest(BaseModel):
    # Endpoint-specific engines remain explicit until provider download
    # adapters exist. Reject a different source instead of quietly ignoring it.
    source: Literal["streamingcommunity"] = "streamingcommunity"


class AnimeUnityRequest(BaseModel):
    source: Literal["animeunity"] = "animeunity"


class LA7DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["la7"] = "la7"
    url: str = Field(default="", max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    scope: Literal["single", "season", "series"] = "single"
    external_id: str | None = Field(default=None, max_length=300)
    season_number: int | None = Field(default=None, ge=1, le=999)

    @field_validator("url")
    @classmethod
    def official_episode_url(cls, value):
        if not value:
            return value
        from app.core.la7 import validate_episode_url
        validate_episode_url(value)
        return value

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value):
        if not value.strip():
            raise ValueError("Il titolo LA7 non può essere vuoto")
        return value.strip()


@router.post("/la7", status_code=202, dependencies=CAN_DOWNLOAD)
def download_la7(body: LA7DownloadRequest, http_request: HttpRequest,
                 registry: ProviderRegistry = Depends(get_registry)):
    if not registry.is_downloadable("la7"):
        raise HTTPException(status_code=422, detail="Download LA7 non disponibile")
    user_id = _acting_user_id(http_request)
    if body.scope == "single":
        if not body.url:
            raise HTTPException(422, "Per una puntata LA7 serve il link ufficiale")
        job_id = job_manager.submit_la7(body.url, body.title, user_id=user_id)
        return {"job_id": job_id, "status": "queued", "scope": "single"}
    if not body.external_id:
        raise HTTPException(422, "Per una serie LA7 serve l'identificativo del titolo")
    provider = registry.get("la7")
    try:
        details = provider.details(body.external_id)
        if details.ref.media_type != "tv":
            raise HTTPException(422, "Il titolo LA7 non è una serie")
        if body.scope == "season" and body.season_number is None:
            raise HTTPException(422, "Specificare la stagione LA7")
        seasons = [s["number"] for s in details.seasons]
        if body.scope == "season":
            seasons = [s for s in seasons if s == body.season_number]
        if not seasons:
            raise HTTPException(409, "Nessuna stagione LA7 disponibile")
        episodes = []
        for season in seasons:
            episodes.extend(provider.episodes(body.external_id, season_number=season))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Impossibile elencare gli episodi LA7: {exc}") from exc
    if not episodes:
        raise HTTPException(409, "Nessun episodio LA7 disponibile")
    batch_id = str(uuid.uuid4())
    jobs = []
    for ep in episodes[:MAX_BATCH_JOBS]:
        jobs.append(job_manager.submit_la7(ep["page_url"],
                     f"{body.title} S{ep['season']:02d}E{ep['n']}", user_id=user_id,
                     batch_id=batch_id, batch_kind=body.scope,
                     batch_label=body.title))
    return {"job_id": batch_id, "job_ids": jobs, "status": "queued",
            "scope": body.scope, "count": len(jobs)}


class RaiPlayDownloadRequest(BaseModel):
    source: Literal["raiplay"] = "raiplay"
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    year: str | None = None
    scope: Literal["auto", "film", "episode", "season", "series"] = "auto"
    season_number: int | None = Field(default=None, ge=1, le=999)
    episode_number: str | None = Field(default=None, max_length=20, pattern=r"^\d+$")
    external_id: str | None = Field(default=None, max_length=200)


class FilmDownloadRequest(StreamingCommunityRequest):
    id: int
    title: str
    year: str | None = None
    audio_languages: list[str] = ["ita"]
    subtitle_languages: list[str] = ["ita", "eng"]


class EpisodeDownloadRequest(StreamingCommunityRequest):
    tv_id: int
    eps: list[dict]
    ep_index: int
    token: str
    tv_name: str
    season: int
    year: str | None = None
    audio_languages: list[str] = ["ita"]
    subtitle_languages: list[str] = ["ita", "eng"]


class EpisodeInfo(BaseModel):
    id: int
    number: str | int


class AnimeDownloadRequest(AnimeUnityRequest):
    anime_id: str
    episode: EpisodeInfo
    anime_name: str
    anime_type: str = Field(default="tv", max_length=32)
    year: str | None = None
    audio_languages: list[str] = ["ita"]
    subtitle_languages: list[str] = ["ita", "eng"]


class SeasonDownloadRequest(StreamingCommunityRequest):
    """A whole season, enumerated server-side.

    Deliberately no episode list, token or domain: the browser used to send all
    three back on every episode, and the token was stale by the last one. The
    client only says which season of what.
    """
    tv_id: int
    slug: str
    tv_name: str
    season: int
    year: str | None = None
    audio_languages: list[str] = ["ita"]
    subtitle_languages: list[str] = ["ita", "eng"]
    scheduled_at: datetime | None = None
    # Skip episodes whose file is already in the library (#30).
    only_missing: bool = False


class SeriesDownloadRequest(StreamingCommunityRequest):
    tv_id: int
    slug: str
    tv_name: str
    year: str | None = None
    audio_languages: list[str] = ["ita"]
    subtitle_languages: list[str] = ["ita", "eng"]
    scheduled_at: datetime | None = None
    # Skip episodes whose file is already in the library (#30).
    only_missing: bool = False


class AnimeAllDownloadRequest(AnimeUnityRequest):
    anime_id: str
    anime_name: str
    anime_type: str = Field(default="tv", max_length=32)
    year: str | None = None
    audio_languages: list[str] = ["ita"]
    subtitle_languages: list[str] = ["ita", "eng"]
    scheduled_at: datetime | None = None
    # Skip episodes whose file is already in the library (#30).
    only_missing: bool = False


class FilmScheduleRequest(FilmDownloadRequest):
    scheduled_at: datetime


class EpisodeScheduleRequest(EpisodeDownloadRequest):
    scheduled_at: datetime


class AnimeScheduleRequest(AnimeDownloadRequest):
    scheduled_at: datetime


# ── Batch downloads ────────────────────────────────────────────────────────────
#
# A season or a series is enumerated here rather than by the browser looping one
# POST per episode. The point is closure: knowing the expected total before the
# first job exists is what lets the notification layer say "season finished"
# instead of guessing with a timer.

def _acting_user_id(http_request: HttpRequest) -> int | None:
    """The in-app recipient. None for the local user: a direct download is the
    panel's own, and its notifications go to the panel-wide bell."""
    user = current_user(http_request)
    return None if is_local_user(user) else user.id


def _season_episodes(tv_id: int, slug: str, season: int, domain: str, version: str, token: str):
    from app.core.tv import get_info_season

    return get_info_season(tv_id, slug, domain, version, token, season) or []


def _tv_context(tv_id: int):
    from app.core.page import get_domain_version
    from app.core.tv import get_token

    domain = _domain()
    version = get_domain_version(domain) or ""
    return domain, version, get_token(tv_id, domain)


def _enumerate(work):
    """Run a server-side enumeration, turning an unreachable source into a 502.

    The domain rotates and links die; that is an expected answer, not a crash,
    and the caller gets a message it can show instead of a bare 500.
    """
    try:
        return work()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Batch enumeration failed: %s", exc)
        raise HTTPException(
            status_code=502, detail="Impossibile leggere gli episodi dalla fonte"
        )


def _check_size(count: int, skipped: int = 0):
    if count == 0 and skipped:
        raise HTTPException(status_code=409, detail="Tutti gli episodi sono già in libreria")
    if count == 0:
        raise HTTPException(status_code=409, detail="Nessun episodio disponibile sulla fonte")
    if count > MAX_BATCH_JOBS:
        raise HTTPException(
            status_code=400,
            detail=f"Troppi episodi in un'unica richiesta ({count}, massimo {MAX_BATCH_JOBS})",
        )


def _run_batch(kind: str, label: str, user_id: int | None, submits: list) -> dict:
    """Register the batch, then submit. Never the other way round.

    A job can fail in the instant it is created, and a result arriving before
    its batch exists would be counted against nothing.
    """
    batch_id = uuid.uuid4().hex
    downloads_notify.register(batch_id, kind=kind, label=label,
                              expected=len(submits), user_id=user_id)
    job_ids = []
    for index, submit in enumerate(submits):
        try:
            job_ids.append(submit(batch_id))
        except Exception:
            logger.exception("Batch %s: submit failed for item %s", batch_id, index)
            # Write off everything still unsubmitted, so the batch can close on
            # the jobs that did start.
            downloads_notify.abandon(batch_id, len(submits) - index)
            break
    return {"batch_id": batch_id, "job_ids": job_ids, "count": len(job_ids)}


def _missing_only(body, indices: list[int], present) -> tuple[list[int], int]:
    """The indices still to download, and how many were skipped as present."""
    if not body.only_missing:
        return indices, 0
    kept = [i for i in indices if not present(i)]
    return kept, len(indices) - len(kept)


def _episode_submits(body, tv_id, slug, tv_name, season, domain, version, token, user_id, kind, label):
    """One submit per episode to fetch, and how many were skipped as present."""
    from app.requests import resolver

    episodes = _season_episodes(tv_id, slug, season, domain, version, token)
    indices, skipped = _missing_only(body, list(range(len(episodes))), lambda i: (
        resolver.episode_on_disk(resolver.EPISODE, body.source, tv_id, tv_name,
                                 body.year, season, episodes[i]["n"])
    ))

    def make(index):
        def submit(batch_id):
            common = dict(
                source=body.source,
                year=body.year,
                audio_languages=body.audio_languages,
                subtitle_languages=body.subtitle_languages,
                user_id=user_id, batch_id=batch_id, batch_kind=kind, batch_label=label,
                tmdb_id=_tmdb_id("tv", tv_id),
            )
            if body.scheduled_at:
                return job_manager.schedule_episode(
                    tv_id, episodes, index, domain, token, tv_name, season,
                    body.scheduled_at, **common,
                )
            return job_manager.submit_episode(
                tv_id, episodes, index, domain, token, tv_name, season, **common,
            )
        return submit

    return [make(i) for i in indices], skipped


@router.post("/season", status_code=202, dependencies=CAN_DOWNLOAD)
async def download_season(body: SeasonDownloadRequest, http_request: HttpRequest):
    user_id = _acting_user_id(http_request)
    label = f"{body.tv_name} — Stagione {body.season}"

    def work():
        domain, version, token = _tv_context(body.tv_id)
        submits, skipped = _episode_submits(body, body.tv_id, body.slug, body.tv_name,
                                            body.season, domain, version, token, user_id,
                                            "season", label)
        _check_size(len(submits), skipped)
        return {**_run_batch("season", label, user_id, submits), "skipped": skipped}

    result = await asyncio.to_thread(_enumerate, work)
    return {**result, "status": "scheduled" if body.scheduled_at else "queued"}


@router.post("/series", status_code=202, dependencies=CAN_DOWNLOAD)
async def download_series(body: SeriesDownloadRequest, http_request: HttpRequest):
    user_id = _acting_user_id(http_request)

    def work():
        from app.core.tv import get_season_numbers

        domain, version, token = _tv_context(body.tv_id)
        # The seasons come from the source, never from the client.
        seasons = get_season_numbers(body.tv_id, body.slug, version, domain)
        submits, skipped = [], 0
        for season in seasons:
            try:
                more, less = _episode_submits(body, body.tv_id, body.slug, body.tv_name, season,
                                              domain, version, token, user_id, "series",
                                              body.tv_name)
                submits += more
                skipped += less
            except Exception:
                # One unavailable season must not sink the whole series.
                logger.exception("Series %s: season %s could not be listed", body.tv_id, season)
        _check_size(len(submits), skipped)
        return {**_run_batch("series", body.tv_name, user_id, submits), "skipped": skipped}

    result = await asyncio.to_thread(_enumerate, work)
    return {**result, "status": "scheduled" if body.scheduled_at else "queued"}


@router.post("/anime-all", status_code=202, dependencies=CAN_DOWNLOAD)
async def download_anime_all(body: AnimeAllDownloadRequest, http_request: HttpRequest):
    user_id = _acting_user_id(http_request)

    def work():
        from app.core.animeunity import get_episodes
        from app.requests import resolver

        listed = get_episodes(body.anime_id) or []
        indices, skipped = _missing_only(body, list(range(len(listed))), lambda i: (
            resolver.episode_on_disk(resolver.ANIME, body.source, body.anime_id,
                                     body.anime_name, body.year, None,
                                     listed[i].get("number"), body.anime_type)
        ))
        episodes = [listed[i] for i in indices]
        _check_size(len(episodes), skipped)

        def make(episode):
            def submit(batch_id):
                common = dict(
                    source=body.source,
                    anime_type=body.anime_type, year=body.year,
                    audio_languages=body.audio_languages,
                    subtitle_languages=body.subtitle_languages,
                    user_id=user_id, batch_id=batch_id, batch_kind="anime_all",
                    batch_label=body.anime_name,
                )
                if body.scheduled_at:
                    return job_manager.schedule_anime_episode(
                        body.anime_id, episode, body.anime_name, body.scheduled_at, **common,
                    )
                return job_manager.submit_anime_episode(
                    body.anime_id, episode, body.anime_name, **common,
                )
            return submit

        return {**_run_batch("anime_all", body.anime_name, user_id,
                             [make(ep) for ep in episodes]), "skipped": skipped}

    result = await asyncio.to_thread(_enumerate, work)
    return {**result, "status": "scheduled" if body.scheduled_at else "queued"}


# ── Immediate downloads ────────────────────────────────────────────────────────

@router.post("/film", status_code=202, dependencies=CAN_DOWNLOAD)
def download_film(body: FilmDownloadRequest, http_request: HttpRequest):
    job_id = job_manager.submit_film(
        body.id, body.title, _domain(), year=body.year,
        source=body.source,
        audio_languages=body.audio_languages,
        subtitle_languages=body.subtitle_languages,
        user_id=_acting_user_id(http_request),
        tmdb_id=_tmdb_id("movie", body.id),
    )
    return {"job_id": job_id, "status": "queued"}


@router.post("/raiplay", status_code=202, dependencies=CAN_DOWNLOAD)
async def download_raiplay(body: RaiPlayDownloadRequest, http_request: HttpRequest,
                           registry: ProviderRegistry = Depends(get_registry)):
    """Expand a programme with the same resolver used by episode previews.

    yt-dlp's programme playlist includes Extra and duplicates. Each job here
    receives exactly one public video page instead, including for films.
    """
    from urllib.parse import urlsplit
    from app.providers.raiplay import parser

    parsed = urlsplit(body.url)
    if (parsed.scheme != "https" or parsed.netloc not in {"www.raiplay.it", "raiplay.it"}
            or parsed.query or parsed.fragment):
        raise HTTPException(422, "URL RaiPlay non valido")
    page = parser.video_page(parser.BASE_URL + parsed.path)
    user_id = _acting_user_id(http_request)
    if page:
        if body.scope not in {"auto", "film", "episode"}:
            raise HTTPException(422, "Per la serie o la stagione serve il link del programma RaiPlay")
        job_id = job_manager.submit_raiplay(
            page, body.title, year=body.year, user_id=user_id,
            season=body.season_number, episode_number=body.episode_number,
            external_id=body.external_id,
        )
        return {"job_id": job_id, "count": 1, "status": "queued"}
    path = parsed.path.rstrip("/")
    segments = path.strip("/").split("/")
    external_id = parser.external_id_from_path("/" + "/".join(segments[:2]))
    if external_id is None or len(segments) not in (2, 4):
        raise HTTPException(422, "Link programma RaiPlay non valido")

    def enumerate_items():
        provider = registry.get("raiplay")
        details = provider.details(external_id)
        if not details.downloadable:
            raise HTTPException(409, details.download_disabled_reason)
        if details.ref.media_type == "movie":
            if body.scope in {"series", "season", "episode"}:
                raise HTTPException(422, "Il titolo RaiPlay è un film")
            video = details.extra.get("video_page_url")
            if not video:
                raise HTTPException(409, "RaiPlay non ha fornito il video del film")
            return details, "film", [{"page_url": video}], []
        if body.scope in {"film", "episode"}:
            raise HTTPException(422, "Per un episodio serve il link completo del video")
        number = body.season_number
        scope = "season" if body.scope == "season" or len(segments) == 4 else "series"
        if scope == "season" and number is None and len(segments) == 4:
            number = parser.season_number(segments[-1].replace("-", " "))
        if scope == "season" and number is None:
            raise HTTPException(422, "Specificare la stagione RaiPlay")
        seasons = details.extra.get("season_paths", [])
        if scope == "season":
            seasons = [s for s in seasons if s["number"] == number]
        if not seasons:
            raise HTTPException(409, "Nessuna stagione disponibile nel catalogo RaiPlay")
        items, unavailable, seen = [], [], set()
        for season in seasons:
            try:
                rows = provider.episodes(external_id, season_number=season["number"])
            except Exception:
                # Continue the series but explicitly tell the caller which
                # seasons were unavailable; never claim all were queued.
                if scope == "season":
                    raise
                logger.exception("RaiPlay %s: season %s unavailable", external_id, season["number"])
                rows = []
            if not rows:
                unavailable.append(season["number"])
            for ep in rows:
                if ep["page_url"] not in seen:
                    seen.add(ep["page_url"])
                    items.append(ep)
        _check_size(len(items))
        return details, scope, items, unavailable

    details, scope, items, unavailable = await call_provider(enumerate_items)
    if scope == "film":
        job_id = job_manager.submit_raiplay(items[0]["page_url"], details.title,
            year=details.year, user_id=user_id, external_id=external_id)
        return {"job_id": job_id, "count": 1, "scope": scope, "status": "queued"}
    label = details.title + (f" — Stagione {items[0]['season']}" if scope == "season" else "")

    def make(ep):
        return lambda batch_id: job_manager.submit_raiplay(
            ep["page_url"], details.title, year=details.year, user_id=user_id,
            season=int(ep["season"]), episode_number=ep["n"],
            episode_title=ep.get("name"), external_id=external_id,
            batch_id=batch_id, batch_kind=scope, batch_label=label,
        )
    result = _run_batch(scope, label, user_id, [make(ep) for ep in items])
    return {**result, "status": "queued", "scope": scope, "unavailable_seasons": unavailable}


@router.post("/episode", status_code=202, dependencies=CAN_DOWNLOAD)
def download_episode(body: EpisodeDownloadRequest, http_request: HttpRequest):
    if body.ep_index < 0 or body.ep_index >= len(body.eps):
        raise HTTPException(status_code=400, detail="ep_index out of range")
    job_id = job_manager.submit_episode(
        body.tv_id, body.eps, body.ep_index,
        _domain(), body.token, body.tv_name, body.season,
        year=body.year,
        source=body.source,
        audio_languages=body.audio_languages,
        subtitle_languages=body.subtitle_languages,
        user_id=_acting_user_id(http_request),
        tmdb_id=_tmdb_id("tv", body.tv_id),
    )
    return {"job_id": job_id, "status": "queued"}


@router.post("/anime", status_code=202, dependencies=CAN_DOWNLOAD)
def download_anime(body: AnimeDownloadRequest, http_request: HttpRequest):
    job_id = job_manager.submit_anime_episode(
        body.anime_id, body.episode.model_dump(), body.anime_name, body.anime_type, year=body.year,
        source=body.source,
        audio_languages=body.audio_languages,
        subtitle_languages=body.subtitle_languages,
        user_id=_acting_user_id(http_request),
    )
    return {"job_id": job_id, "status": "queued"}


# ── Scheduled downloads ────────────────────────────────────────────────────────

@router.post("/schedule/film", status_code=202, dependencies=CAN_DOWNLOAD)
def schedule_film(body: FilmScheduleRequest, http_request: HttpRequest):
    job_id = job_manager.schedule_film(
        body.id, body.title, _domain(), body.scheduled_at, year=body.year,
        source=body.source,
        audio_languages=body.audio_languages,
        subtitle_languages=body.subtitle_languages,
        user_id=_acting_user_id(http_request),
    )
    return {"job_id": job_id, "status": "scheduled", "scheduled_at": body.scheduled_at.isoformat()}


@router.post("/schedule/episode", status_code=202, dependencies=CAN_DOWNLOAD)
def schedule_episode(body: EpisodeScheduleRequest, http_request: HttpRequest):
    if body.ep_index < 0 or body.ep_index >= len(body.eps):
        raise HTTPException(status_code=400, detail="ep_index out of range")
    job_id = job_manager.schedule_episode(
        body.tv_id, body.eps, body.ep_index,
        _domain(), body.token, body.tv_name, body.season,
        body.scheduled_at, year=body.year,
        source=body.source,
        audio_languages=body.audio_languages,
        subtitle_languages=body.subtitle_languages,
        user_id=_acting_user_id(http_request),
    )
    return {"job_id": job_id, "status": "scheduled", "scheduled_at": body.scheduled_at.isoformat()}


@router.post("/schedule/anime", status_code=202, dependencies=CAN_DOWNLOAD)
def schedule_anime(body: AnimeScheduleRequest, http_request: HttpRequest):
    job_id = job_manager.schedule_anime_episode(
        body.anime_id, body.episode.model_dump(), body.anime_name, body.scheduled_at,
        anime_type=body.anime_type, year=body.year,
        source=body.source,
        audio_languages=body.audio_languages,
        subtitle_languages=body.subtitle_languages,
        user_id=_acting_user_id(http_request),
    )
    return {"job_id": job_id, "status": "scheduled", "scheduled_at": body.scheduled_at.isoformat()}


# ── Job management ─────────────────────────────────────────────────────────────

@router.post("/{job_id}/fire", status_code=200, dependencies=CAN_CONTROL_JOBS)
def fire_now(job_id: str):
    if job_manager.fire_now(job_id):
        return {"job_id": job_id, "status": "queued"}
    raise HTTPException(status_code=404, detail="Job non trovato o non in stato programmato")


# Pausing holds back what has not started; resuming only lets go what was
# already approved and queued, so both are job control, like stopping (#43).
@router.post("/batch/{batch_id}/pause", status_code=200, dependencies=CAN_CONTROL_JOBS)
def pause_batch(batch_id: str):
    count = job_manager.pause_batch(batch_id)
    if not count:
        raise HTTPException(status_code=404, detail="Nessun download in attesa in questo gruppo")
    return {"batch_id": batch_id, "paused": count}


@router.post("/batch/{batch_id}/resume", status_code=200, dependencies=CAN_CONTROL_JOBS)
def resume_batch(batch_id: str):
    count = job_manager.resume_batch(batch_id)
    if not count:
        raise HTTPException(status_code=404, detail="Nessun download in pausa in questo gruppo")
    return {"batch_id": batch_id, "resumed": count}


# Stopping a whole batch is stopping each of its jobs, so it takes what
# stopping one takes. Deleting what it already put in the library is deleting
# files, so that part also takes what the file manager's delete takes.
@router.post("/batch/{batch_id}/cancel", status_code=200, dependencies=CAN_CONTROL_JOBS)
def cancel_batch(batch_id: str, request: HttpRequest, delete_files: bool = False):
    if delete_files and not current_user(request).has(Permission.MANAGE_FILES):
        raise HTTPException(status_code=403, detail="Permesso negato")
    if delete_files:
        downloads_notify.silence(batch_id)
    cancelled, removed = job_manager.cancel_batch(batch_id, delete_files=delete_files)
    if not cancelled and not removed:
        raise HTTPException(status_code=404, detail="Nessun download da annullare in questo gruppo")
    return {"batch_id": batch_id, "cancelled": cancelled, "removed": removed}


@router.post("/all/cancel", status_code=200, dependencies=CAN_CONTROL_JOBS)
def cancel_all_downloads():
    return {"cancelled": job_manager.cancel_all()}


# Starts a download, so it takes what starting one takes — not the job-control
# permission approvers hold for stopping what they approved.
@router.post("/{job_id}/retry", status_code=202, dependencies=CAN_DOWNLOAD)
def retry(job_id: str):
    new_id = job_manager.retry(job_id)
    if new_id is None:
        raise HTTPException(status_code=404, detail="Job non trovato o non fallito")
    return {"job_id": new_id, "replaces": job_id, "status": "queued"}


@router.delete("/{job_id}", status_code=200, dependencies=CAN_CONTROL_JOBS)
def cancel_or_dismiss(job_id: str):
    """Cancel a running/queued/scheduled job, or dismiss a finished one (also cleans schedule store)."""
    if job_manager.dismiss(job_id):
        return {"job_id": job_id, "status": "dismissed"}
    if job_manager.cancel(job_id):
        return {"job_id": job_id, "status": "cancelled"}
    raise HTTPException(status_code=404, detail="Job non trovato")
