import asyncio
import logging
import os
import random
import shutil
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait as wait_for_futures
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app import config
from app.config import download_dir, read_data, VIDEOS_DIR, TMP_DIR, get_settings
from app.core import source_budget
from app.progress import DownloadCancelledError, PROGRESS_QUEUE_MAX, WebProgressBar, post
from app.source_identity import download_source


# Both read data.json through app.config, the one reader of that file: this
# module is imported by the routers and must not import them back.
def _get_library_path(type_: str) -> str:
    """Return the configured library path for content type, or VIDEOS_DIR as fallback."""
    for lib in read_data().get("libraries", []):
        if lib.get("type") == type_:
            return lib["path"]
    return str(download_dir(VIDEOS_DIR))

def _library_roots() -> list[str]:
    roots = [lib["path"] for lib in read_data().get("libraries", []) if lib.get("path")]
    return [os.path.realpath(p) for p in (*roots, str(download_dir(VIDEOS_DIR)))]


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # another drive, on Windows
        return False


def remove_download_output(path: str) -> list[str]:
    """Delete a finished download from the library: the video, the subtitles
    beside it ({stem}.{lang}.vtt), then every folder it leaves empty.

    Only inside a configured library, and never the library root itself - the
    path comes from a job, but what it points at is a person's library. A
    folder that still holds anything (the series' other episodes) stays.
    Returns what was removed.
    """
    roots = _library_roots()
    video = os.path.realpath(path)
    if not any(_inside(video, r) and video != r for r in roots):
        logger.warning("Not removing %s: outside every library", path)
        return []

    folder, name = os.path.split(video)
    stem = os.path.splitext(name)[0]
    try:
        siblings = [os.path.join(folder, f) for f in os.listdir(folder)
                    if f.startswith(stem + ".") and f.endswith(".vtt")]
    except OSError:
        siblings = []
    removed = []
    for target in (video, *siblings):
        try:
            os.remove(target)
            removed.append(target)
        except OSError:
            pass

    while folder not in roots and any(_inside(folder, r) for r in roots):
        try:
            os.rmdir(folder)  # fails on a folder not empty, which is the point
        except OSError:
            break
        removed.append(folder)
        folder = os.path.dirname(folder)
    for target in removed:
        logger.info("Removed with its cancelled batch: %s", target)
    return removed


# Coarser than any filesystem a library lives on: FAT and some SMB shares keep
# modification times to two seconds.
_TIMESTAMP_SLACK = 2.0


def _written_since(path: str, started: Optional[float]) -> bool:
    """Whether the file at ``path`` was created or modified from ``started`` on.

    How a download's cleanup tells its own output from a copy that was in the
    library before it began: a second download of a title already there, when
    it is cancelled, must not take the good copy with it. The job cannot record
    the file beforehand - where it lands is decided inside the download, after
    the source has been read - so it records when the attempt began and
    compares. When in doubt (no start time, a file that cannot be read) the
    answer is no, and the file stays.
    """
    if started is None:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    # The modification time alone: a file the download writes, or renames into
    # place from its staging copy, carries one from within the attempt. The
    # change time would also move for a chmod or a library scan - somebody
    # else's doing, and no reason to call the file ours.
    return st.st_mtime >= started - _TIMESTAMP_SLACK


def _looks_like_job_id(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


def purge_stale_temp_dirs(keep: frozenset = frozenset()) -> int:
    """Remove the staging directories no job will come back for.

    Called at startup: jobs live in memory, so whatever a previous run left in
    TMP_DIR belongs to nobody - the server has just started and has no job to
    resume from it - and it used to stay on disk for good. Only directories
    named like a job id are removed, which is everything this module ever
    creates there: TMP_DIR is a setting, and one pointed at a folder shared
    with something else must not cost that something its files. A link or a
    junction is never followed, and nothing outside TMP_DIR is touched.
    Returns how many directories were removed.
    """
    tmp_root = os.path.realpath(str(TMP_DIR))
    try:
        entries = list(os.scandir(tmp_root))
    except OSError:
        return 0
    removed = 0
    for entry in entries:
        if entry.name in keep or not _looks_like_job_id(entry.name):
            continue
        is_junction = getattr(os.path, "isjunction", lambda _p: False)(entry.path)
        if entry.is_symlink() or is_junction or not entry.is_dir(follow_symlinks=False):
            logger.warning("Not cleaning %s: not a plain directory", entry.path)
            continue
        target = os.path.realpath(entry.path)
        if not _inside(target, tmp_root) or target == tmp_root:
            logger.warning("Not cleaning temporary path outside TMP_DIR: %s", entry.path)
            continue
        shutil.rmtree(target, ignore_errors=True)
        removed += 1
    if removed:
        logger.info("Removed %d stale staging director%s from %s",
                    removed, "y" if removed == 1 else "ies", tmp_root)
    return removed


def _cleanup_temp_dir(path: str | None) -> None:
    """Remove a job's staging directory when it is no longer resumable."""
    if not path:
        return
    target = os.path.abspath(path)
    tmp_root = os.path.abspath(str(TMP_DIR))
    if not _inside(target, tmp_root) or target == tmp_root:
        logger.warning("Not cleaning temporary path outside TMP_DIR: %s", path)
        return
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
        logger.info("Cleaned up temp dir: %s", target)


def _schedule_external_id(params: dict) -> Optional[str]:
    """The source id a scheduled entry's params carry, whatever its type."""
    value = params.get("id") or params.get("tv_id") or params.get("anime_id")
    return str(value) if value not in (None, "") else None


logger = logging.getLogger(__name__)

SCHEDULER_INTERVAL = 30  # seconds

# A failed download is run again this many times, this long after each failure,
# before it is shown as failed. The source refuses a request now and then and
# serves the same one a minute later; a configuration mistake just fails fast
# three more times, which costs nothing.
AUTO_RETRIES = 3
# The wait before the first retry; each one after waits twice as long (60, 120,
# 240 seconds), give or take a fifth. Doubling gives a source that is down a
# real chance to come back within the three tries, and the jitter keeps a
# season that failed together from knocking again together.
AUTO_RETRY_DELAY = 60  # seconds
AUTO_RETRY_JITTER = 0.2

# Automatic protection (JobManager._on_source_backoff): more than this many
# source pauses within PROTECTION_WINDOW seconds switch to the conservative
# preset.
PROTECTION_MAX_BACKOFFS = 3
PROTECTION_WINDOW = 60 * 60


def _retry_delay(retry: int) -> float:
    """Seconds to wait before automatic retry number ``retry`` (from 1)."""
    base = AUTO_RETRY_DELAY * 2 ** (retry - 1)
    return base * random.uniform(1 - AUTO_RETRY_JITTER, 1 + AUTO_RETRY_JITTER)


# A scheduled job whose call cannot even be built (a library path gone, params
# that no longer make sense) is tried again at the next scheduler pass - but
# only this many times, then it fails for everyone to see.
MAX_FIRE_FAILURES = 3

# A finished job stays listed this long, then goes the way of a dismissed one.
JOB_RETENTION = timedelta(hours=24)

_ACTIVE = ("scheduled", "queued", "paused", "running")
_TERMINAL = ("done", "error", "cancelled")

# What one attempt came to (_attempt). Decided under the manager's lock and
# carried by value: the job's own fields can change the moment the lock is
# released - a parked job is resumed, and its ``parked`` cleared, by another
# thread - so nothing downstream may re-read them to find out what happened.
_FINISHED = "finished"   # reached a terminal state: clean up and notify
_RETRY = "retry"         # failed, and runs again after a wait
_PARKED = "parked"       # paused: left the executor, and is not over


@dataclass
class DownloadJob:
    job_id: str
    title: str
    type: str  # "film" | "episode" | "anime"
    status: str  # "scheduled" | "queued" | "paused" | "running" | "done" | "error" | "cancelled"
    created_at: datetime
    scheduled_at: Optional[datetime] = None
    schedule_id: Optional[str] = None
    error: Optional[str] = None
    output_path: Optional[str] = None
    progress: dict = field(default_factory=lambda: {
        "current": 0, "total": 0, "pct": 0, "speed": 0, "eta": None,
        "bytes_done": 0, "bytes_total": None, "bytes_total_estimated": True,
    })
    phases: list = field(default_factory=list)
    # Bounded, oldest dropped first (app.progress.post): it is read only while
    # someone has this job's own progress stream open.
    progress_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=PROGRESS_QUEUE_MAX))
    cancel_event: threading.Event = field(default_factory=threading.Event)

    # Set when this job is one episode of a season or series asked for in one go,
    # so the notification can be a single summary instead of one per episode.
    batch_id: Optional[str] = None
    batch_kind: Optional[str] = None    # "season" | "series" | "anime_all"
    batch_label: Optional[str] = None   # computed once at submit, e.g. "Serie — Stagione 2"

    # Who asked for it. None in open mode, where there is no account to notify.
    user_id: Optional[int] = None

    # Kept as separate fields rather than parsed back out of `title`, which is
    # already a composed string ("Nome Serie S02E05") and would misparse the
    # first time a title itself contains something like S01.
    media_label: Optional[str] = None
    year: Optional[str] = None
    season: Optional[int] = None
    episode_number: Optional[str] = None
    # The title's id at its source (film id, series id, AnimeUnity id). What
    # the library registry files the finished download under, so the title can
    # be found again whatever its folder ends up being called.
    external_id: Optional[str] = None
    source: Optional[str] = None

    # What was handed to the executor, minus the arguments bound to this job
    # object (_JOB_BOUND_KWARGS). Kept so a failed job can be run again without
    # the client re-sending anything — the domain and the requester's identity
    # must not come from a request body.
    call: Optional[tuple] = field(default=None, repr=False)

    # Automatic retries used so far, and when the next one starts (None when
    # none is pending). The job keeps its id and stays non-terminal throughout:
    # listeners hear about it once, when it is finally done or failed.
    retries: int = 0
    retry_at: Optional[datetime] = None
    retry_delay: float = field(default=0.0, repr=False)
    # Times its scheduled call could not be built (see MAX_FIRE_FAILURES).
    fire_failures: int = 0
    # When the running attempt began (time.time()), and whether the file the
    # job finished with was written by it rather than found in the library:
    # cleanup removes only what the download itself put there (_written_since).
    attempt_started: Optional[float] = field(default=None, repr=False)
    output_created: bool = False
    # When it reached its terminal state and its worker was done with it.
    finished_at: Optional[datetime] = None
    # Its ticket while it waits for a download slot (None otherwise): the
    # waiting jobs start in ticket order, so the frontend can number the line.
    queue_seq: Optional[int] = None
    # Held back with the rest of its batch (#43). A paused job gives up its
    # place in the line *and its executor thread*: ``parked`` keeps the exact
    # call, so resuming resubmits it. Only a job that has not started is ever
    # paused - one mid-download would come back to an expired stream token.
    paused: bool = False
    parked: Optional[tuple] = field(default=None, repr=False)
    # Cancelled with its batch and its files with it: a download that finishes
    # in the instant after the cancel removes what it just wrote.
    purge: bool = False


# Rebuilt for every attempt: each belongs to one job id.
_JOB_BOUND_KWARGS = ("temp_dir", "progress_factory", "cancel_event")


def _worth_retrying(exc: Exception) -> bool:
    """Everything but what no retry can fix.

    A missing audio track waits for a person to choose, and on the request path
    parks the request. A permission error is a deployment problem (#28): the
    same uid meets the same mode bits a minute later. Only on POSIX, though —
    on Windows a sharing violation (a file held open by Jellyfin or an
    antivirus) is also a PermissionError, carries a ``winerror``, and does go
    away. Never all of OSError: ``requests.ConnectionError`` is one too.
    """
    from app.core._shared import MissingAudioTrackError
    from app.core.transcode import TranscodeError
    if isinstance(exc, TranscodeError):
        return False
    if isinstance(exc, MissingAudioTrackError):
        return False
    if isinstance(exc, PermissionError) and getattr(exc, "winerror", None) is None:
        return False
    return True


class DownloadSlots:
    """At most ``limit`` downloads at once, granted strictly in arrival order.

    Replaces a ``threading.BoundedSemaphore``, which promises nothing about
    which waiter goes next — so the "In attesa" list could not honestly say
    what starts next (#26). It also fixes what changing the limit did: the
    semaphore was *replaced*, leaving every job already waiting on the old one
    under the old limit while new jobs used the new, so going from 3 to 5 could
    run 8 at once. Here the limit changes in place, for everyone.

    A waiter leaves the line as soon as its job is cancelled instead of
    holding its place until a slot frees up.

    Automatic protection: while the source is in backoff
    (``source_budget.backoff_until()``, after a 403/429/503 or a segment
    pushback) no slot is granted. The line keeps its order and its threads -
    the head simply waits, with a timeout set to the end of the pause, and
    starts by itself when it is over. Nothing is cancelled or failed; a
    download already running carries on (its own pipeline already slows down
    on pushback). The backoff lives in memory: after a restart it starts from
    zero, which is safe because the jobs that were waiting live in memory too,
    and the first new refusal opens a new pause at once.
    """

    # Longest single wait while the source is paused: re-checks now and then,
    # so a pause ended early (a reset, a clock change) is noticed.
    HOLD_RECHECK = 30.0

    def __init__(self, limit: int):
        self._cond = threading.Condition()
        self._limit = limit
        self._busy = 0
        self._line: deque = deque()
        self._tickets = 0

    def acquire(self, job: "DownloadJob", on_queued=None) -> bool:
        """Wait for a slot. False when the job was cancelled or paused while
        waiting; the caller tells the two apart."""
        with self._cond:
            if job.paused:
                return False
            self._tickets += 1
            job.queue_seq = self._tickets
            self._line.append(job)
        if on_queued is not None:
            on_queued(job)
        with self._cond:
            try:
                while True:
                    if job.cancel_event.is_set() or job.paused:
                        return False
                    if self._line[0] is job and self._busy < self._limit:
                        held = source_budget.backoff_remaining()
                        if not held:
                            break
                        # Its turn, but the source is paused: wait it out.
                        self._cond.wait(min(held, self.HOLD_RECHECK))
                        continue
                    self._cond.wait()
                self._busy += 1
                return True
            finally:
                self._line.remove(job)
                job.queue_seq = None
                # The next in line may now be at the head with a slot free.
                self._cond.notify_all()

    def release(self) -> None:
        with self._cond:
            self._busy -= 1
            self._cond.notify_all()

    def resize(self, limit: int) -> None:
        with self._cond:
            self._limit = limit
            self._cond.notify_all()

    def wake(self) -> None:
        """Let waiters re-check: a cancelled or paused one leaves the line."""
        with self._cond:
            self._cond.notify_all()


class JobManager:
    def __init__(self):
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=64)
        self._workers = set()
        self._slots = DownloadSlots(get_settings().get("max_concurrent_downloads", 3))
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: list[asyncio.Queue] = []
        self._schedule_store = None  # set via set_schedule_store()
        self._listeners: list = []
        # When the automatic protection last switched to the conservative
        # preset (ISO time), for the interface. In memory, like the backoff.
        self._protection_downgraded_at: Optional[str] = None
        source_budget.add_backoff_listener(self._on_source_backoff)

    @staticmethod
    def _compute_phases(audio_languages: list) -> list:
        """Return the ordered list of phase names that will be emitted for this job."""
        steps = ["video", "joining"] + [f"audio_{l}" for l in (audio_languages or ["ita"])] + ["merging", "done"]
        if get_settings().get("video_conversion", "copy") != "copy":
            steps.insert(-1, "converting")
        return steps

    def update_max_concurrent(self, n: int):
        # In place: running downloads keep their slot, waiting ones get the new
        # limit too (see DownloadSlots).
        self._slots.resize(n)

    # ── Automatic protection ───────────────────────────────────────────────────

    def _on_source_backoff(self, until: float, reason: str) -> None:
        """A new source pause started: hold the line, tell the user, and after
        too many pauses in an hour fall back to the conservative preset.

        The hold itself needs nothing from here - DownloadSlots asks
        source_budget at every grant - so this only announces it and wakes the
        line, so the head starts its timed wait now rather than at the next
        release. Runs on the thread that saw the refusal; never raises.
        """
        self._slots.wake()
        downgraded = False
        if source_budget.backoffs_since(PROTECTION_WINDOW) > PROTECTION_MAX_BACKOFFS:
            try:
                before = get_settings()
                after = config.apply_performance_preset("conservative", only_lower=True)
                downgraded = any(after.get(k) != before.get(k) for k in config.PERFORMANCE_KEYS)
                # Always, not only when this call wrote: the settings are the
                # truth, and the line must follow them whoever lowered them.
                self.update_max_concurrent(after["max_concurrent_downloads"])
                from app.core import m3u8
                m3u8.apply_source_cap()
                self._protection_downgraded_at = datetime.now(timezone.utc).isoformat()
                if downgraded:
                    logger.warning("Source paused more than %d times in an hour: "
                                   "switched to the conservative preset",
                                   PROTECTION_MAX_BACKOFFS)
            except Exception:
                logger.exception("Could not switch to the conservative preset")
        self._broadcast({"type": "source_paused", **self.protection_status(),
                         "reason": reason, "downgraded": downgraded})

    def protection_status(self) -> dict:
        """What the interface shows about the automatic protection."""
        until = source_budget.backoff_until()
        with self._lock:
            waiting = sum(1 for j in self._jobs.values() if j.status == "queued")
        message = (f"Sorgente in pausa fino alle {datetime.fromtimestamp(until):%H:%M}, "
                   "la coda riprenderà automaticamente" if until else None)
        return {
            "paused": until is not None,
            "until": datetime.fromtimestamp(until, timezone.utc).isoformat() if until else None,
            "until_hhmm": f"{datetime.fromtimestamp(until):%H:%M}" if until else None,
            "message": message,
            "queued_jobs": waiting,
            "backoffs_last_hour": source_budget.backoffs_since(PROTECTION_WINDOW),
            "max_backoffs_per_hour": PROTECTION_MAX_BACKOFFS,
            "downgraded_at": self._protection_downgraded_at,
        }

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        loop.create_task(self._scheduler_loop())

    def set_schedule_store(self, store):
        from app.schedule import ScheduleStore
        self._schedule_store: Optional[ScheduleStore] = store

    def get(self, job_id: str) -> Optional[DownloadJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [self._job_to_dict(j) for j in self._jobs.values()]

    def _submit_worker(self, fn, *args, **kwargs):
        future = self._executor.submit(fn, *args, **kwargs)
        with self._lock:
            self._workers.add(future)
        def finished(completed):
            with self._lock:
                self._workers.discard(completed)
        future.add_done_callback(finished)
        return future

    def has_active_work(self) -> bool:
        """Whether stopping the process now would lose something.

        Workers count past their job's "done", for the final notifications and
        cleanup. So do jobs that hold no worker at all: a scheduled one fires
        from this process's scheduler and a paused one is resumed from its
        memory, and the idle shutdown used to take both down as "nothing
        running".
        """
        with self._lock:
            return bool(self._workers) or any(job.status in _ACTIVE for job in self._jobs.values())

    def _job_to_dict(self, job: DownloadJob) -> dict:
        return {
            "job_id": job.job_id,
            "title": job.title,
            "type": job.type,
            "source": job.source,
            "external_id": job.external_id,
            "status": job.status,
            "created_at": job.created_at.isoformat(),
            "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
            "schedule_id": job.schedule_id,
            "error": job.error,
            "output_path": job.output_path,
            "progress": job.progress,
            "phases": job.phases,
            # user_id is deliberately absent: the jobs list is readable by anyone
            # who can download, and the field has no use in the interface.
            "batch_id": job.batch_id,
            "batch_kind": job.batch_kind,
            # Composed once at submit and carried, rather than left to the
            # client: ``title`` is already a composed string ("Nome Serie
            # S02E05"), and reparsing a group heading back out of it misreads
            # the first title that contains something like S01 itself.
            "batch_label": job.batch_label,
            "media_label": job.media_label,
            "season": job.season,
            "episode_number": job.episode_number,
            "year": job.year,
            "retries": job.retries,
            "queue_seq": job.queue_seq,
            "max_retries": AUTO_RETRIES,
            "retry_at": job.retry_at.isoformat() if job.retry_at else None,
        }

    # ── Global pub/sub ─────────────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, event: dict):
        """Thread-safe push to all global SSE subscribers."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._fanout(event), self._loop)

    def broadcast(self, event: dict):
        """Public entry point for other modules pushing to the SSE stream."""
        self._broadcast(event)

    # ── Terminal-state listeners ───────────────────────────────────────────────

    def add_listener(self, callback):
        """Register a callback invoked when a job reaches a terminal state.

        Used by the request system to move a request to completed or failed
        without the job manager knowing that requests exist.
        """
        self._listeners.append(callback)

    def _notify_listeners(self, job: "DownloadJob"):
        for callback in list(self._listeners):
            try:
                callback(job)
            except Exception:
                logger.exception("Job listener failed for job %s", job.job_id)

    def _finish(self, job: "DownloadJob"):
        """A job is over, and nothing will run for it again: tell everyone.

        The one place a terminal state is announced from, called exactly once
        per job by whoever ends it - its worker, or cancel() for a job that has
        none. Never for a parked job, which is not over.
        """
        with self._lock:
            job.finished_at = datetime.now(timezone.utc)
            if job.status != "error":
                # Only a failed job can be run again; the call holds a whole
                # season's episode list, for as long as the job is listed.
                job.call = None
        if self._schedule_store is not None and job.schedule_id and job.status in _TERMINAL:
            # So a restart can tell a schedule that ran from one it interrupted.
            self._schedule_store.set_outcome(job.schedule_id, job.status)
        self._notify_listeners(job)

    def _emit(self, job: "DownloadJob", event: dict):
        """Push a terminal/progress event to the job's own queue and to everyone.

        The loop is only set once the app has started; a job finishing before
        that (or in a test) must not leave an un-awaited coroutine behind.
        """
        if not post(job.progress_queue, self._loop, event):
            logger.debug("No event loop; dropping %s for job %s",
                         event.get("type"), job.job_id)
        self._broadcast({**event, "job_id": job.job_id})

    async def _fanout(self, event: dict):
        """Push an event to every subscriber, dropping the oldest under pressure.

        A backgrounded tab, a slow proxy, or a season starting twenty downloads
        at once can all fill a queue. Dropping the *subscriber* there — which is
        what used to happen — left the browser holding an open connection nobody
        would ever write to again: the page looked live and silently stopped
        updating until it was reloaded. Discarding the oldest event instead
        costs a stale frame of progress and lets the next one repair the view.
        """
        for q in list(self._subscribers):
            while True:
                try:
                    q.put_nowait(event)
                    break
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        # Drained from under us by its own reader; try again.
                        continue

    # ── Progress factory ───────────────────────────────────────────────────────

    def _make_progress_factory(self, job: DownloadJob):
        loop = self._loop
        manager = self
        # A bar covers one phase; a job spans several. Kept here because this
        # closure is the only thing that is per-job and sees every bar.
        bars: list = []

        def on_event(ev: dict):
            if ev.get("type") == "status" and ev.get("phase") == "converting":
                job.progress = {**job.progress, "phase": "converting", "encoder": None,
                                "pct": 0, "speed": 0, "bytes_speed": 0, "eta": None}
            if ev.get("type") == "progress":
                job.progress = {
                    "phase": ev.get("phase"),
                    "encoder": ev.get("encoder"),
                    "current": ev["current"],
                    "total": ev["total"],
                    "pct": ev["pct"],
                    "speed": ev.get("speed", 0),
                    "bytes_speed": ev.get("bytes_speed", 0),
                    "eta": ev.get("eta"),
                    "bytes_done": ev.get("bytes_done", 0),
                    "bytes_total": ev.get("bytes_total"),
                    "bytes_total_estimated": ev.get("bytes_total_estimated", True),
                }
            manager._broadcast({**ev, "job_id": job.job_id})

        def factory(**kwargs):
            total = kwargs.get("total", 0)
            phase = kwargs.get("phase")
            bar = WebProgressBar(total, job.progress_queue, loop, phase=phase, on_event=on_event)
            # Carry what the earlier phases already downloaded, so the readout
            # climbs across the whole job instead of restarting at each one.
            bar.prior_bytes = bars[-1].bytes_done if bars else 0
            bars.append(bar)
            return bar

        return factory

    # ── Scheduler loop ─────────────────────────────────────────────────────────

    async def _scheduler_loop(self):
        while True:
            await asyncio.sleep(SCHEDULER_INTERVAL)
            try:
                # On a thread: firing reads data.json and writes schedule.json,
                # and neither belongs on the event loop.
                await asyncio.to_thread(self._scheduler_tick)
            except Exception:
                logger.exception("Scheduler pass failed")

    def _scheduler_tick(self, now: Optional[datetime] = None):
        """One pass: fire what is due, forget what finished long ago."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status != "scheduled" or job.scheduled_at is None or job.schedule_id is None:
                continue
            sa = job.scheduled_at
            if sa.tzinfo is None:
                sa = sa.replace(tzinfo=timezone.utc)
            if sa > now:
                continue
            if self._schedule_store is None:
                continue
            entry = self._schedule_store.get_by_schedule_id(job.schedule_id)
            if entry is None:
                continue
            logger.info("Scheduler firing job_id=%s schedule_id=%s", job.job_id, job.schedule_id)
            try:
                self._fire_job(job, entry["type"], entry["params"])
            except Exception as e:
                logger.error("Failed to fire scheduled job %s: %s", job.job_id, e)
        self.expire_finished(now)

    def expire_finished(self, now: Optional[datetime] = None) -> int:
        """Dismiss the jobs that ended more than JOB_RETENTION ago.

        Nothing else ever removed a finished job the user did not dismiss by
        hand, so a panel left running kept every download it had ever made.
        Only jobs _finish() has seen: one cancelled a moment ago whose worker
        is still unwinding has no ``finished_at`` yet, and is left alone.
        """
        now = now or datetime.now(timezone.utc)
        with self._lock:
            expired = [j.job_id for j in self._jobs.values()
                       if j.status in _TERMINAL and j.finished_at is not None
                       and now - j.finished_at > JOB_RETENTION]
        return sum(self.dismiss(job_id) for job_id in expired)

    def _fire_job(self, job: DownloadJob, type_: str, params: dict) -> bool:
        """Hand a scheduled job to the download queue. False when it is not
        scheduled any more - fired already, or cancelled.

        The check and the change of status are one step under the lock, so the
        scheduler and "Avvia ora" - or two clicks on it - cannot both start it:
        whoever comes second finds it queued and leaves.
        """
        with self._lock:
            if job.status != "scheduled":
                return False
            job.status = "queued"
        try:
            fn, args, kwargs = self._build_call(type_, params, job)
        except Exception as exc:
            with self._lock:
                job.fire_failures += 1
                cancelled = job.cancel_event.is_set()
                failed = not cancelled and job.fire_failures > MAX_FIRE_FAILURES
                if failed:
                    job.status = "error"
                    job.error = str(exc)
                elif not cancelled:
                    # Back where it was: the next scheduler pass tries again.
                    job.status = "scheduled"
            if failed:
                logger.error("Scheduled job %s could not be started %d times; giving up: %s",
                             job.job_id, job.fire_failures, exc)
                self._emit(job, {"type": "error", "message": str(exc)})
            if failed or cancelled:
                # No worker was started, so nothing else would ever say so.
                # (A cancel that found it "queued" left the telling to one.)
                self._finish(job)
            raise
        self._remember_call(job, fn, args, kwargs)
        # Outside the lock - it writes a file - and before the worker starts,
        # so the outcome a quick job records cannot be overwritten by this.
        if self._schedule_store is not None and job.schedule_id:
            self._schedule_store.mark_fired(job.schedule_id)
        self._broadcast({"type": "job_status", "job_id": job.job_id, "status": "queued"})
        # Even if it was cancelled meanwhile: the worker sees that before it
        # downloads anything, and it is what tells the listeners.
        self._submit_worker(self._run_download, job, fn, *args, **kwargs)
        return True

    def _build_call(self, type_: str, params: dict, job: DownloadJob):
        source = download_source(type_, params.get("source"))
        if download_source(type_, job.source) != source:
            raise ValueError("La fonte della programmazione non corrisponde al job")
        pf = self._make_progress_factory(job)
        td = str(TMP_DIR / job.job_id)
        if type_ == "film":
            from app.core.film import download_film
            return download_film, (params["id"], params["title"], params["domain"]), dict(
                output_dir=_get_library_path("film"), temp_dir=td, progress_factory=pf,
                year=params.get("year"), cancel_event=job.cancel_event,
                audio_languages=params.get("audio_languages", ["ita"]),
                subtitle_languages=params.get("subtitle_languages", ["ita", "eng"]),
                tmdb_id=params.get("tmdb_id"), source=source,
            )
        if type_ == "episode":
            from app.core.tv import download_episode
            return download_episode, (
                params["tv_id"], params["eps"], params["ep_index"],
                params["domain"], params["token"], params["tv_name"], params["season"],
            ), dict(
                output_dir=_get_library_path("tv"), temp_dir=td, progress_factory=pf,
                cancel_event=job.cancel_event, year=params.get("year"),
                audio_languages=params.get("audio_languages", ["ita"]),
                subtitle_languages=params.get("subtitle_languages", ["ita", "eng"]),
                tmdb_id=params.get("tmdb_id"), source=source,
            )
        if type_ == "anime":
            from app.core.animeunity import download_anime_episode
            return download_anime_episode, (
                params["anime_id"], params["episode"],
                params["anime_name"], params.get("anime_type", "tv"),
            ), dict(
                output_dir=_get_library_path("anime"), temp_dir=td, progress_factory=pf,
                cancel_event=job.cancel_event, year=params.get("year"),
                audio_languages=params.get("audio_languages", ["ita"]),
                subtitle_languages=params.get("subtitle_languages", ["ita", "eng"]),
                source=source,
            )
        raise ValueError(f"Unknown schedule type: {type_!r}")

    # ── Job lifecycle ──────────────────────────────────────────────────────────

    def fire_now(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "scheduled" or job.schedule_id is None:
                return False
        entry = self._schedule_store.get_by_schedule_id(job.schedule_id) if self._schedule_store else None
        if entry is None:
            return False
        # Decides again, under the lock: a second click loses here.
        return self._fire_job(job, entry["type"], entry["params"])

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in ("scheduled", "queued", "paused", "running"):
                return False
            # A scheduled job was never handed to the executor, and a parked one
            # has left it, so _run_download will not run for either and nothing
            # else would ever tell listeners it is over. Queued and running ones
            # go through _run_download, which notifies on every path.
            orphaned = job.status == "scheduled" or job.parked is not None
            job.parked = None
            job.paused = False
            job.cancel_event.set()
            job.status = "cancelled"
        self._slots.wake()
        self._emit(job, {"type": "error", "message": "Annullato"})
        if orphaned:
            self._finish(job)
        return True

    def pause_batch(self, batch_id: str) -> int:
        """Hold back every job of a batch still waiting to start (#43).

        What is already downloading finishes: its stream token was issued when
        it started and would not outlive a long pause, while a job that has not
        started asks for its own when it does. Returns how many were paused.
        """
        with self._lock:
            jobs = [j for j in self._jobs.values()
                    if j.batch_id == batch_id and j.status == "queued" and not j.paused]
            for job in jobs:
                job.paused = True
                job.status = "paused"
                job.queue_seq = None
                # One between automatic retries parks when its wait ends.
                job.retry_at = None
                # Under the lock, so it cannot overtake the "running" of a job
                # that won its slot a moment before (_attempt).
                self._broadcast({"type": "job_status", "job_id": job.job_id, "status": "paused"})
        # Waiters re-check and leave the line; each parks on its own thread.
        self._slots.wake()
        return len(jobs)

    def resume_batch(self, batch_id: str) -> int:
        """Put a paused batch back in the line, behind whatever joined it since."""
        with self._lock:
            jobs = [j for j in self._jobs.values()
                    if j.batch_id == batch_id and j.status == "paused"]
            parked = []
            for job in jobs:
                job.paused = False
                job.status = "queued"
                if job.parked is not None:
                    parked.append((job, job.parked))
                    job.parked = None
        for job in jobs:
            self._broadcast({"type": "job_status", "job_id": job.job_id, "status": "queued"})
        # A job not parked yet is still on its thread and goes back in line by
        # itself (_attempt); a parked one needs a thread again.
        for job, (fn, args, kwargs) in parked:
            self._submit_worker(self._run_download, job, fn, *args, **kwargs)
        return len(jobs)

    def cancel_batch(self, batch_id: str, delete_files: bool = False) -> tuple[int, int]:
        """Cancel every job of a batch not over yet, the running one included.

        Through cancel(), one job at a time, so each still reaches its listeners
        exactly once and the batch closes. The waiting ones go first: cancelling
        the running one frees its slot, and a sibling still in line would take
        it and start downloading before its own turn to be cancelled came.

        With ``delete_files`` the episodes the batch already finished leave the
        library too, and turn "cancelled" on the page; their listeners heard
        "done" already and are not told again. The running one's partial file
        is removed by the download itself as it stops. Returns how many jobs
        were cancelled and how many finished ones were removed.
        """
        with self._lock:
            batch = [j for j in self._jobs.values() if j.batch_id == batch_id]
            jobs = [j for j in batch if j.status in ("scheduled", "queued", "paused", "running")]
            finished = [j for j in batch if j.status == "done"] if delete_files else []
            for job in jobs:
                job.purge = delete_files
        jobs.sort(key=lambda j: j.status == "running")
        cancelled = sum(self.cancel(j.job_id) for j in jobs)

        removed = 0
        for job in finished:
            # Only what the batch itself wrote: an episode that was already in
            # the library, and that the download merely found there, stays.
            if job.output_path and job.output_created:
                remove_download_output(job.output_path)
            with self._lock:
                job.status = "cancelled"
                job.output_path = None
                self._broadcast({"type": "job_status", "job_id": job.job_id, "status": "cancelled"})
            removed += 1
        return cancelled, removed

    def cancel_all(self) -> int:
        """Cancel every unfinished job, without touching completed files."""
        with self._lock:
            jobs = [j for j in self._jobs.values()
                    if j.status in ("scheduled", "queued", "paused", "running")]
        return sum(self.cancel(job.job_id) for job in jobs)

    def shutdown(self, timeout: float = 20.0) -> bool:
        """Stop every download and wait for the workers to wind down.

        Called once, as the server stops. Without it the downloads kept
        running on their non-daemon threads - against an event loop that was
        gone - and the process could not exit until the last one finished.
        Cancelled first, so each worker ends by itself: it removes its partial
        output, cleans its staging directory and tells its listeners, which is
        why the wait comes before the executor is closed and not after.
        Bounded: a worker stuck in a call that does not look at its
        cancel_event is left behind with a warning. True when all ended.
        """
        cancelled = self.cancel_all()
        with self._lock:
            workers = list(self._workers)
        _, still_running = wait_for_futures(workers, timeout=timeout)
        self._executor.shutdown(wait=False, cancel_futures=True)
        if still_running:
            logger.warning("Shutdown: %d download worker(s) still running after %.0fs",
                           len(still_running), timeout)
        else:
            logger.info("Shutdown: %d job(s) cancelled, all download workers stopped", cancelled)
        return not still_running

    def purge_stale_temp(self) -> int:
        """Clear what earlier runs left in TMP_DIR (see purge_stale_temp_dirs)."""
        with self._lock:
            keep = frozenset(self._jobs)
        return purge_stale_temp_dirs(keep)

    def _park(self, job: DownloadJob, fn, args, kwargs) -> bool:
        """Leave the executor for a paused job. False when it was resumed or
        cancelled in the meantime, which the caller handles."""
        with self._lock:
            if not job.paused or job.cancel_event.is_set():
                return False
            job.parked = (fn, args, kwargs)
            job.status = "paused"
            return True

    def dismiss(self, job_id: str) -> bool:
        """Remove a finished/cancelled job and clean it from the schedule store."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in ("done", "error", "cancelled"):
                return False
            del self._jobs[job_id]
        _cleanup_temp_dir(str(TMP_DIR / job.job_id))
        if self._schedule_store is not None:
            self._schedule_store.remove_by_job_id(job_id)
        self._broadcast({"type": "job_dismissed", "job_id": job_id})
        return True

    def retry(self, job_id: str) -> Optional[str]:
        """Run a failed job again as a new job; returns the new job id.

        The failed entry goes away in the same step, so a second failure makes a
        fresh entry instead of a duplicate. The new job leaves the batch it came
        from: that batch counted this episode already, and closes (or has
        closed) on its own count — reporting into it again would close it early
        or land in a batch that no longer exists. A retry succeeding is
        therefore announced on its own.
        """
        with self._lock:
            old = self._jobs.get(job_id)
            if not old or old.status != "error" or old.call is None:
                return None
            del self._jobs[job_id]
        if self._schedule_store is not None:
            self._schedule_store.remove_by_job_id(job_id)
        self._broadcast({"type": "job_dismissed", "job_id": job_id})

        job = self._make_job(
            old.title, old.type, phases=list(old.phases),
            user_id=old.user_id, media_label=old.media_label, year=old.year,
            season=old.season, episode_number=old.episode_number,
            external_id=old.external_id, source=old.source,
        )
        fn, args, kwargs = old.call
        # Keep the failed job's staging directory. The segment downloader can
        # reuse the .ts files already present there instead of starting over.
        resume_dir = str(TMP_DIR / old.job_id)
        return self._submit_job(
            job, fn, *args, **kwargs,
            temp_dir=resume_dir,
            progress_factory=self._make_progress_factory(job),
            cancel_event=job.cancel_event,
        )

    @staticmethod
    def _remember_call(job: DownloadJob, fn, args, kwargs):
        job.call = (fn, args, {k: v for k, v in kwargs.items() if k not in _JOB_BOUND_KWARGS})

    def _run_download(self, job: DownloadJob, fn, *args, **kwargs):
        # Listeners fire outside the semaphore: a listener does DB writes and
        # blocking HTTP (external notification channels), and holding a download
        # slot for that would cost real throughput. The outer try/finally makes
        # every path notify exactly once — including the job cancelled before it
        # ever started, which used to return early and notify nobody, leaving
        # anything counting completions waiting forever. An automatic retry is
        # not a path out: it loops here, so a batch still counts the episode
        # once, with its final outcome.
        outcome = _FINISHED
        try:
            while True:
                outcome = self._attempt(job, fn, args, kwargs)
                if outcome != _RETRY:
                    break
                # The wait holds no download slot, and a cancel ends it at once.
                if job.cancel_event.wait(job.retry_delay):
                    with self._lock:
                        job.retry_at = None
                        job.status = "cancelled"
                    self._emit(job, {"type": "error", "message": "Annullato"})
                    outcome = _FINISHED
                    break
                job.retry_at = None
                if "progress_factory" in kwargs:
                    # The factory carries byte totals from phase to phase; the
                    # failed attempt's must not count toward the new one.
                    kwargs = {**kwargs, "progress_factory": self._make_progress_factory(job)}
        finally:
            # A parked job is not over: it is resubmitted on resume, and
            # notifies then - or cancel() does, if it never comes back. What
            # _attempt returned decides, not job.parked: a resume can have
            # cleared that already, and this thread would then announce a job
            # that is queued again, on another thread, as finished.
            if outcome != _PARKED:
                with self._lock:
                    if job.status not in _TERMINAL:
                        # Only by an exception out of the code above, never by
                        # a download failing. Listeners must still hear of it,
                        # and must not hear "queued" or "running" as an ending.
                        job.status = "error"
                        job.error = job.error or "Errore interno del gestore dei download"
                # Keep a failed staging directory so the explicit "Riprova"
                # action can continue from the segments already downloaded.
                # Successful and cancelled jobs have no reason to retain it.
                if job.status in ("done", "cancelled"):
                    _cleanup_temp_dir(kwargs.get("temp_dir") or str(TMP_DIR / job.job_id))
                self._finish(job)

    def _remove_own_output(self, job: DownloadJob, path: str) -> None:
        """Remove what a cancelled attempt left in the library - if it is its."""
        if _written_since(path, job.attempt_started):
            remove_download_output(path)
        else:
            logger.info("Kept %s: it was in the library before job %s started",
                        path, job.job_id)

    def _attempt(self, job: DownloadJob, fn, args, kwargs) -> str:
        """Run the download once. Returns _FINISHED, _RETRY or _PARKED."""
        while not self._slots.acquire(job, on_queued=self._announce_queued):
            if self._park(job, fn, args, kwargs):
                return _PARKED
            with self._lock:
                cancelled = job.cancel_event.is_set()
                if cancelled:
                    job.status = "cancelled"
            if cancelled:
                self._emit(job, {"type": "error", "message": "Annullato"})
                return _FINISHED
            # Resumed before it could park: straight back in line.
        try:
            with self._lock:
                # It won its slot, so a pause arriving now is too late for it:
                # it runs, and must not be resumed later as if still waiting.
                job.paused = False
                job.status = "running"
                job.attempt_started = time.time()
                self._broadcast({"type": "job_status", "job_id": job.job_id, "status": "running"})

            try:
                result = fn(*args, **kwargs)
                created = bool(result) and _written_since(result, job.attempt_started)
                # Under the lock cancel() and cancel_batch() decide with: either
                # they find this job done (and cancel() then refuses, while
                # cancel_batch() removes its files), or this finds their mark.
                # A cancel that lands as the last bytes do wins - the job was
                # told "Annullato" and its status already says so; writing
                # "done" over it would bring it back to life.
                with self._lock:
                    cancelled = job.purge or job.cancel_event.is_set()
                    if not cancelled:
                        job.status = "done"
                        job.output_path = result
                        job.output_created = created
                if cancelled:
                    # What it wrote goes the way of a download stopped midway;
                    # a file that was there before it began is not its to take.
                    if result:
                        self._remove_own_output(job, result)
                    raise DownloadCancelledError("Download annullato dall'utente")
                self._emit(job, {"type": "done", "output_path": result})
            except DownloadCancelledError:
                with self._lock:
                    job.status = "cancelled"
                self._emit(job, {"type": "error", "message": "Annullato"})
            except Exception as e:
                with self._lock:
                    cancelled = job.cancel_event.is_set()
                    retry = (not cancelled and job.retries < AUTO_RETRIES and _worth_retrying(e))
                    if cancelled:
                        # A download torn down by its own cancellation fails
                        # any way it likes; it is still a cancelled one.
                        job.status = "cancelled"
                    elif retry:
                        job.retries += 1
                        job.retry_delay = _retry_delay(job.retries)
                        job.status = "queued"
                        job.error = str(e)
                        job.retry_at = datetime.now(timezone.utc) + timedelta(seconds=job.retry_delay)
                        job.progress = {**job.progress, "current": 0, "pct": 0, "speed": 0,
                                        "eta": None, "bytes_done": 0}
                    else:
                        job.status = "error"
                        job.error = str(e)
                if cancelled:
                    self._emit(job, {"type": "error", "message": "Annullato"})
                elif retry:
                    logger.warning("Job %s failed (%s); retry %d/%d in %ds",
                                   job.job_id, e, job.retries, AUTO_RETRIES, job.retry_delay)
                    self._broadcast({"type": "job_retrying", "job": self._job_to_dict(job)})
                    return _RETRY
                else:
                    logger.exception(f"Job {job.job_id} failed: {e}")
                    self._emit(job, {"type": "error", "message": str(e)})
        finally:
            self._slots.release()
        return _FINISHED

    def _announce_queued(self, job: DownloadJob) -> None:
        """A job joined the line for a slot — first time, or back from a retry
        wait. Its ticket is what orders the "In attesa" list."""
        self._broadcast({"type": "job_queued", "job_id": job.job_id, "queue_seq": job.queue_seq})

    def _submit_job(self, job: DownloadJob, fn, *args, **kwargs) -> str:
        with self._lock:
            self._jobs[job.job_id] = job
        self._remember_call(job, fn, args, kwargs)
        self._broadcast({"type": "job_created", "job": self._job_to_dict(job)})
        self._submit_worker(self._run_download, job, fn, *args, **kwargs)
        return job.job_id

    def _make_job(self, title: str, type_: str, scheduled_at: Optional[datetime] = None,
                  schedule_id: Optional[str] = None, phases: list = None,
                  **meta) -> DownloadJob:
        """Build a job. ``meta`` carries the notification fields declared on
        DownloadJob (batch_id, user_id, season, …); unknown keys raise, which is
        the point — a typo must not silently vanish."""
        meta["source"] = download_source(type_, meta.get("source"))
        now = datetime.now(timezone.utc)
        sa = scheduled_at.replace(tzinfo=timezone.utc) if scheduled_at and scheduled_at.tzinfo is None else scheduled_at
        status = "scheduled" if sa and sa > now else "queued"
        return DownloadJob(
            job_id=str(uuid.uuid4()),
            title=title,
            type=type_,
            status=status,
            # Same instant `status` was decided against, and timezone-aware like
            # scheduled_at so the two stay comparable.
            created_at=now,
            scheduled_at=scheduled_at,
            schedule_id=schedule_id,
            phases=phases or [],
            **meta,
        )

    # ── Submit (immediate) ─────────────────────────────────────────────────────

    def submit_film(self, id_film: int, title: str, domain: str, year: str = None,
                    schedule_id: str = None,
                    audio_languages: list[str] = None,
                    subtitle_languages: list[str] = None,
                    strict_audio: bool = False,
                    user_id: int = None,
                    tmdb_id: int = None, *, source: str = "streamingcommunity") -> str:
        from app.core.film import download_film

        job = self._make_job(title, "film", schedule_id=schedule_id,
                             phases=self._compute_phases(audio_languages or ["ita"]),
                             user_id=user_id, media_label=title, year=year,
                             external_id=str(id_film), source=source)
        return self._submit_job(
            job, download_film,
            id_film, title, domain,
            output_dir=_get_library_path("film"),
            temp_dir=str(TMP_DIR / job.job_id),
            progress_factory=self._make_progress_factory(job),
            year=year,
            cancel_event=job.cancel_event,
            audio_languages=audio_languages or ["ita"],
            subtitle_languages=subtitle_languages or ["ita", "eng"],
            strict_audio=strict_audio,
            tmdb_id=tmdb_id,
            source=job.source,
        )

    def submit_episode(self, tv_id: int, eps: list[dict], ep_index: int, domain: str,
                       token: str, tv_name: str, season: int, year: str = None,
                       schedule_id: str = None,
                       audio_languages: list[str] = None,
                       subtitle_languages: list[str] = None,
                       strict_audio: bool = False,
                       user_id: int = None, batch_id: str = None,
                       batch_kind: str = None, batch_label: str = None,
                       tmdb_id: int = None, *, source: str = "streamingcommunity") -> str:
        from app.core.tv import download_episode, fmt_ep

        ep = eps[ep_index]
        episode_title = ep.get("name") or ep.get("title") or ep.get("episode_name") or ""
        title = f"{tv_name} S{season:02d}E{fmt_ep(ep['n'])}"
        if episode_title:
            title += f" - {episode_title}"
        job = self._make_job(title, "episode", schedule_id=schedule_id,
                             phases=self._compute_phases(audio_languages or ["ita"]),
                             user_id=user_id, batch_id=batch_id, batch_kind=batch_kind,
                             batch_label=batch_label, media_label=tv_name, year=year,
                             season=season, episode_number=str(ep["n"]),
                             external_id=str(tv_id), source=source)
        return self._submit_job(
            job, download_episode,
            tv_id, eps, ep_index, domain, token, tv_name, season,
            output_dir=_get_library_path("tv"),
            temp_dir=str(TMP_DIR / job.job_id),
            progress_factory=self._make_progress_factory(job),
            cancel_event=job.cancel_event,
            year=year,
            audio_languages=audio_languages or ["ita"],
            subtitle_languages=subtitle_languages or ["ita", "eng"],
            strict_audio=strict_audio,
            tmdb_id=tmdb_id,
            source=job.source,
        )

    # AnimeUnity deliberately gains no tmdb_id: an anime here has no TMDB
    # identifier and its embed lives on a different host, so a fallback
    # provider would have nothing to resolve against.
    def submit_anime_episode(self, anime_id: str, episode: dict, anime_name: str,
                             anime_type: str = "tv", year: str = None,
                             schedule_id: str = None,
                             audio_languages: list[str] = None,
                             subtitle_languages: list[str] = None,
                             strict_audio: bool = False,
                             user_id: int = None, batch_id: str = None,
                             batch_kind: str = None, batch_label: str = None,
                             *, source: str = "animeunity") -> str:
        from app.core.animeunity import download_anime_episode

        ep_num = episode.get("number", "?")
        title = f"{anime_name} E{ep_num}"
        job = self._make_job(title, "anime", schedule_id=schedule_id,
                             phases=self._compute_phases(audio_languages or ["ita"]),
                             user_id=user_id, batch_id=batch_id, batch_kind=batch_kind,
                             batch_label=batch_label, media_label=anime_name, year=year,
                             episode_number=str(ep_num), external_id=str(anime_id), source=source)
        return self._submit_job(
            job, download_anime_episode,
            anime_id, episode, anime_name, anime_type,
            output_dir=_get_library_path("anime"),
            temp_dir=str(TMP_DIR / job.job_id),
            progress_factory=self._make_progress_factory(job),
            cancel_event=job.cancel_event,
            year=year,
            audio_languages=audio_languages or ["ita"],
            subtitle_languages=subtitle_languages or ["ita", "eng"],
            strict_audio=strict_audio,
            source=job.source,
        )

    # ── Schedule (future) ──────────────────────────────────────────────────────

    def schedule_film(self, id_film: int, title: str, domain: str,
                      scheduled_at: datetime, year: str = None,
                      audio_languages: list[str] = None,
                      subtitle_languages: list[str] = None,
                      user_id: int = None,
                      tmdb_id: int = None, *, source: str = "streamingcommunity") -> str:
        params = {
            "id": id_film, "title": title, "domain": domain, "year": year,
            "source": source,
            "audio_languages": audio_languages or ["ita"],
            "subtitle_languages": subtitle_languages or ["ita", "eng"],
            # Persisted with the schedule: the cache it came from will not
            # survive until the job fires.
            "tmdb_id": tmdb_id,
        }
        return self._add_schedule("film", scheduled_at, params, title,
                                  user_id=user_id, media_label=title, year=year)

    def schedule_episode(self, tv_id: int, eps: list[dict], ep_index: int, domain: str,
                         token: str, tv_name: str, season: int,
                         scheduled_at: datetime, year: str = None,
                         audio_languages: list[str] = None,
                         subtitle_languages: list[str] = None,
                         user_id: int = None, batch_id: str = None,
                         batch_kind: str = None, batch_label: str = None,
                         tmdb_id: int = None, *, source: str = "streamingcommunity") -> str:
        from app.core.tv import fmt_ep
        ep = eps[ep_index]
        title = f"{tv_name} S{season:02d}E{fmt_ep(ep['n'])}"
        params = {
            "tv_id": tv_id, "eps": eps, "ep_index": ep_index,
            "source": source,
            "domain": domain, "token": token, "tv_name": tv_name,
            "season": season, "year": year,
            "audio_languages": audio_languages or ["ita"],
            "subtitle_languages": subtitle_languages or ["ita", "eng"],
            "tmdb_id": tmdb_id,
        }
        return self._add_schedule("episode", scheduled_at, params, title,
                                  user_id=user_id, batch_id=batch_id, batch_kind=batch_kind,
                                  batch_label=batch_label, media_label=tv_name, year=year,
                                  season=season, episode_number=str(ep["n"]))

    def schedule_anime_episode(self, anime_id: str, episode: dict, anime_name: str,
                               scheduled_at: datetime, anime_type: str = "tv",
                               year: str = None,
                               audio_languages: list[str] = None,
                               subtitle_languages: list[str] = None,
                               user_id: int = None, batch_id: str = None,
                               batch_kind: str = None, batch_label: str = None,
                               *, source: str = "animeunity") -> str:
        ep_num = episode.get("number", "?")
        title = f"{anime_name} E{ep_num}"
        params = {
            "anime_id": anime_id, "episode": episode, "anime_name": anime_name,
            "source": source,
            "anime_type": anime_type, "year": year,
            "audio_languages": audio_languages or ["ita"],
            "subtitle_languages": subtitle_languages or ["ita", "eng"],
        }
        return self._add_schedule("anime", scheduled_at, params, title,
                                  user_id=user_id, batch_id=batch_id, batch_kind=batch_kind,
                                  batch_label=batch_label, media_label=anime_name, year=year,
                                  episode_number=str(ep_num))

    def _add_schedule(self, type_: str, scheduled_at: datetime, params: dict, title: str,
                      **meta) -> str:
        params = {**params, "source": download_source(type_, params.get("source"))}
        meta["source"] = params["source"]
        if self._schedule_store is None:
            raise RuntimeError("ScheduleStore not configured")
        schedule_id = self._schedule_store.add(type_, scheduled_at, params)
        # Firing reuses this same job object, so notification metadata set here
        # survives until the download actually runs. It does not survive a
        # restart (load_scheduled_from_store rebuilds from the JSON params), and
        # neither does the in-memory batch it would belong to.
        meta.setdefault("external_id", _schedule_external_id(params))
        job = self._make_job(title, type_, scheduled_at=scheduled_at, schedule_id=schedule_id,
                             phases=self._compute_phases(params.get("audio_languages") or ["ita"]),
                             **meta)
        # Whatever the hour: _make_job calls a time already past "queued", but
        # nothing is running it - only the scheduler starts a job that has a
        # schedule entry, and it looks at scheduled ones alone.
        job.status = "scheduled"
        with self._lock:
            self._jobs[job.job_id] = job
        self._schedule_store.set_job_id(schedule_id, job.job_id)
        self._broadcast({"type": "job_created", "job": self._job_to_dict(job)})
        return job.job_id

    def load_scheduled_from_store(self):
        """Re-hydrate the schedule from the JSON store on startup.

        A pending entry becomes a scheduled job again - one whose hour passed
        while the server was down included, which the next scheduler pass
        starts. An entry a previous run fired and never saw the end of was
        interrupted by the restart: it comes back as a failed job, so the
        download is not lost without a word. One that ended well, or was
        cancelled, has nothing left to say and is removed.
        """
        if self._schedule_store is None:
            return
        for entry in self._schedule_store.list_all():
            sid = entry["schedule_id"]
            interrupted = False
            if entry.get("fired"):
                if "outcome" not in entry:
                    continue  # fired by a version that kept no outcome — cannot tell
                if entry["outcome"] in ("done", "cancelled"):
                    self._schedule_store.remove_by_schedule_id(sid)
                    continue
                interrupted = True
            type_ = entry["type"]
            params = entry["params"]
            try:
                source = download_source(type_, params.get("source"))
            except ValueError:
                logger.error("Unsupported source for scheduled entry %s; left pending", sid)
                continue
            sa_raw = datetime.fromisoformat(entry["scheduled_at"])
            scheduled_at = sa_raw if sa_raw.tzinfo else sa_raw.replace(tzinfo=timezone.utc)
            title = params.get("title") or params.get("tv_name") or params.get("anime_name", "?")
            # What the registry needs to file the download survives in the
            # params; the rest of the notification metadata does not.
            job = self._make_job(title, type_, scheduled_at=scheduled_at, schedule_id=sid,
                                 phases=self._compute_phases(params.get("audio_languages") or ["ita"]),
                                 media_label=title, year=params.get("year"),
                                 source=source,
                                 external_id=_schedule_external_id(params))
            if interrupted:
                self._restore_interrupted(job, entry)
            else:
                job.status = "scheduled"  # even if overdue: see _add_schedule
            with self._lock:
                self._jobs[job.job_id] = job
            self._schedule_store.set_job_id(sid, job.job_id)
            logger.info("Restored %s job %s (schedule_id=%s) for %s",
                        job.status, job.job_id, sid, scheduled_at)

    def _restore_interrupted(self, job: DownloadJob, entry: dict):
        """Make ``job`` the visible remains of a schedule a restart cut short."""
        if entry.get("outcome") is None:
            self._schedule_store.set_outcome(entry["schedule_id"], "interrupted")
        job.status = "error"
        job.error = "Interrotto dal riavvio del server prima del completamento"
        job.finished_at = datetime.now(timezone.utc)
        try:
            # So "Riprova" works on it like on any other failed download.
            self._remember_call(job, *self._build_call(entry["type"], entry["params"], job))
        except Exception as exc:
            logger.warning("Interrupted scheduled job %s cannot be retried: %s", job.job_id, exc)


job_manager = JobManager()
