import logging
import os
import random
import sys
import shutil
import time
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
import ffmpeg
from requests.adapters import HTTPAdapter
from m3u8 import M3U8 as M3U8_Lib
from tqdm.rich import tqdm
from tqdm import TqdmExperimentalWarning
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.config import get_settings
from app.core import container, hosts, source_budget
from app.core._shared import AES128_KEY_BYTES, with_retry
from app.core.ffmpeg_path import run_ffmpeg, ffmpeg_file_arg, get_ffmpeg_exe
from app.core.headers import get_headers
from app.core.hosts import redact
from app.core.paths import windows_path_problem
from app.progress import DownloadCancelledError

warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)

logger = logging.getLogger(__name__)
DOWNLOAD_SUB = True
DOWNLOAD_DEFAULT_LANGUAGE = False

# Segment requests in flight toward the source, across every job at once. Each
# job has its own worker pool, so without this cap three jobs of sixteen workers
# are forty-eight connections to one CDN. SC_SOURCE_MAX_CONNECTIONS wins when it
# is set (an explicit override: nothing sets it by default); otherwise the
# ``source_max_connections`` setting, clamped to
# config.SOURCE_MAX_CONNECTIONS_CEILING. Re-read at the start of every download
# and whenever the settings change (apply_source_cap): the gate is resized in
# place, so requests already in flight finish and the new cap holds from the
# next one.
def source_cap_env() -> int | None:
    """The SC_SOURCE_MAX_CONNECTIONS override, or None when unset or invalid."""
    raw = os.getenv("SC_SOURCE_MAX_CONNECTIONS", "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("SC_SOURCE_MAX_CONNECTIONS=%r is not a number; using the setting", raw)
        return None


def _source_max_connections() -> int:
    override = source_cap_env()
    if override is not None:
        return override
    from app import config
    try:
        return config.source_max_connections_setting(get_settings())
    except (TypeError, ValueError, OSError, RuntimeError):
        return config.PERFORMANCE_PRESETS[config.DEFAULT_PERFORMANCE_PRESET]["source_max_connections"]


class SourceSlots:
    """A counting gate whose size can change while requests are in flight."""

    def __init__(self, maximum: int):
        self._cond = threading.Condition()
        self.maximum = max(1, int(maximum))
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        with self._cond:
            return self._in_flight

    def resize(self, maximum: int) -> None:
        with self._cond:
            self.maximum = max(1, int(maximum))
            self._cond.notify_all()

    def __enter__(self):
        with self._cond:
            while self._in_flight >= self.maximum:
                self._cond.wait(0.5)
            self._in_flight += 1
        return self

    def __exit__(self, *exc_info):
        with self._cond:
            self._in_flight -= 1
            self._cond.notify()
        return False


_SOURCE_MAX_CONNECTIONS = _source_max_connections()
_SOURCE_SLOTS = SourceSlots(_SOURCE_MAX_CONNECTIONS)


def apply_source_cap() -> int:
    """Re-read the cap (env, else setting) and resize the gate. Returns it."""
    global _SOURCE_MAX_CONNECTIONS
    cap = _source_max_connections()
    _SOURCE_MAX_CONNECTIONS = cap
    _SOURCE_SLOTS.resize(cap)
    return cap

# Segment failures worth another attempt. A 4xx that is not one of these is a
# verdict rather than congestion: repeating it across a few thousand segments
# only delays the error without changing it.
RETRIABLE_SEGMENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_SEGMENT_BACKOFF = 8

# When to stop rather than grind on against a source that is serving nothing.
#
# Counted as an unbroken run: any segment that arrives resets it. Losing some
# segments is the ordinary case — sixteen workers provoke exactly the rate
# limiting that produces it — and repairing them is what the sequential second
# pass is for, so the breaker must only catch a source refusing outright.
#
# Two earlier shapes were wrong. A budget of 5% of the playlist aborted downloads
# that used to complete, because it tripped before ever reaching the pass that
# recovers them. A cumulative failure *ratio* then failed differently: refusals
# clustered at the start of a playlist are all counted before a single success
# is, so the ratio reads 100% on a source that is merely slow to warm up. A run
# broken by any success is immune to both.
#
# Set well above any refusal run seen in practice — a report of this behaviour
# involved 84 in a row on a source that was otherwise serving fine. The cost of
# it being too high is bounded (a dead source takes a few more minutes to reach
# the same error) while the cost of it being too low is a download that would
# have completed.
FAILURE_ABORT_STREAK = 200


# One writer per destination file.
#
# Two requests for the same title in different languages are two requests on
# purpose, but they resolve to the same path — the layout carries no language —
# so downloading both at once had them writing the same file concurrently and
# left it corrupt. The lock is held for the whole download, so the second waits
# and then overwrites cleanly with its own complete file.
#
# In memory, which is the same assumption the rest of the panel makes: job state
# lives in JobManager and the process is single by design.
#
# Each entry is ``[lock, users]``: the lock, and how many downloads hold or are
# waiting for it. Counted so the entry can be dropped when the last one leaves —
# one lock per path ever downloaded, kept for the life of the process, was a
# dictionary that only grew.
_destination_locks: dict[str, list] = {}
_destination_locks_guard = threading.Lock()

# How often a download waiting for the destination looks at its cancel flag.
_LOCK_POLL = 0.5


def _acquire_destination(path: str, cancel_event=None) -> str:
    """Take the lock for *path*, giving up if the job is cancelled meanwhile.

    The wait used to be a bare ``acquire()``: a job queued behind another
    download of the same file could not be cancelled until that one finished.
    Returns what to hand back to ``_release_destination``.
    """
    key = os.path.normcase(os.path.abspath(path))
    with _destination_locks_guard:
        entry = _destination_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    lock = entry[0]
    acquired = lock.acquire(blocking=False)
    try:
        if not acquired:
            logger.info("Waiting for another download to finish writing %s", path)
        while not acquired:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelledError("Download annullato dall'utente")
            acquired = lock.acquire(timeout=_LOCK_POLL)
    except BaseException:
        _forget_destination(key, entry)
        raise
    return key, entry


def _forget_destination(key: str, entry: list) -> None:
    with _destination_locks_guard:
        entry[1] -= 1
        if entry[1] <= 0 and _destination_locks.get(key) is entry:
            del _destination_locks[key]


def _release_destination(held: tuple) -> None:
    key, entry = held
    entry[0].release()
    _forget_destination(key, entry)


def _segment_backoff(attempt: int) -> float:
    """Exponential, capped, and jittered.

    Sixteen worker threads back off in lockstep otherwise, and arrive back at a
    struggling CDN together — which is what produced the 503s in the first
    place.
    """
    return min(2 ** attempt, MAX_SEGMENT_BACKOFF) * (0.5 + random.random())


# Segment requests in flight across the whole process, not per download.
#
# ``max_segment_workers`` used to size each download's pool on its own, so the
# default queue of three concurrent downloads opened three pools of sixteen —
# 48 requests at once against the same CDN edge. That is what the reports of
# "one download in three fails" were: the edge shed the load with 503s, and the
# container's resolver started answering "Temporary failure in name resolution",
# which the retry loop cannot tell apart from a source that is simply gone.
#
# The setting keeps its meaning — how many segments to fetch at once — but now
# describes the panel rather than each download, so a queue of three is as
# polite to the source as a queue of one and merely takes longer to drain.
# A fixed number could not be right either. Sixteen at once is already more
# than vixcloud's edge will serve — a single download, with nothing else
# running, gets its first dozen segments and 503s from there on — while a
# healthy source handles far more and a cautious constant would leave it idle.
# The setting is therefore a ceiling to be discovered under, not a target: the
# limiter starts there, halves what it asks for whenever the source pushes
# back, and creeps up again while segments are arriving.
MIN_SEGMENT_ALLOWANCE = 2

# A 503 arrives in milliseconds, so a shrink per response would collapse the
# allowance on one burst of a hundred replies that all describe the same
# moment. One cut per this many seconds, and the rest of the burst is absorbed.
PENALTY_INTERVAL = 2.0

# Retry-After is the source saying exactly how long to wait. Honoured, but not
# unboundedly: a very long value is a fetch that will outlive the segment's
# attempts anyway, and the sequential pass is a better place to arrive late.
MAX_RETRY_AFTER = 30.0

# (connect, read) for one segment request.
SEGMENT_TIMEOUT = (10, 30)

# How long the parallel pass may go without a single segment arriving before
# the stall watchdog calls the source dead.
#
# It used to be thirty seconds — exactly MAX_RETRY_AFTER — so a source that
# answered "come back in 30" had every worker doing as it was told while the
# watchdog counted the same half-minute as a stall and killed the download.
# The floor is now a full honoured wait plus the slowest read that can follow
# it, with margin; and a worker sitting out a Retry-After also moves a
# heartbeat the watchdog reads, since waiting on request is not stalling.
PROGRESS_TIMEOUT = max(90, MAX_RETRY_AFTER + SEGMENT_TIMEOUT[1] + 30)

# The sequential second pass is deliberately slow, and used to have no end: on
# a source that had stopped serving, every lost segment cost its three attempts
# and their backoff, one after the other, for as many segments as were lost.
# It now stops on a run of failures with nothing recovered between them, or
# when it has used up its time, and says which.
SEQUENTIAL_MAX_STREAK = 20
SEQUENTIAL_BUDGET = 15 * 60


class UnsupportedStreamError(RuntimeError):
    """The playlist uses a part of HLS this downloader does not implement."""


class AdaptiveLimiter:
    """Concurrency that finds the source's tolerance instead of assuming it.

    Additive increase, multiplicative decrease — the same loop TCP uses, and
    for the same reason: nobody can be told the right number in advance, and
    guessing high is punished far harder than guessing low. Overshoot costs a
    burst of 503s and a halving; undershoot costs a few seconds of climbing.

    Process-wide, like the ceiling it works under, so three queued downloads
    share one view of how much the source is willing to serve rather than each
    rediscovering it. Requests in flight are what is counted, because that is
    what the edge counts too.
    """

    def __init__(self, maximum: int, now=time.monotonic):
        self._cond = threading.Condition()
        self._now = now
        self.maximum = max(1, int(maximum))
        self._allowance = self.maximum
        self._in_flight = 0
        self._served = 0
        self._last_penalty = None

    @property
    def allowance(self) -> int:
        with self._cond:
            return self._allowance

    @property
    def in_flight(self) -> int:
        with self._cond:
            return self._in_flight

    def resize(self, maximum: int) -> None:
        """Follow the setting, without discarding what has been learnt.

        An allowance sitting at the old ceiling was never cut — nothing has
        been learnt to preserve — so it moves with it, and raising the setting
        takes effect at once instead of waiting to be climbed to. One that is
        below the ceiling is there because the source put it there: it is only
        clamped, and earns its way back up as before.
        """
        with self._cond:
            maximum = max(1, int(maximum))
            if maximum == self.maximum:
                return
            untouched = self._allowance >= self.maximum
            self.maximum = maximum
            self._allowance = maximum if untouched else max(1, min(self._allowance, maximum))
            self._cond.notify_all()

    def __enter__(self):
        with self._cond:
            while self._in_flight >= self._allowance:
                # A timeout rather than a bare wait: a shrink can leave a
                # thread parked with no release left to wake it, and one that
                # rechecks every half second costs nothing next to a request.
                self._cond.wait(0.5)
            self._in_flight += 1
        return self

    def __exit__(self, *exc_info):
        with self._cond:
            self._in_flight -= 1
            self._cond.notify()
        return False

    def penalise(self) -> None:
        """The source pushed back. Ask it for half as much."""
        with self._cond:
            now = self._now()
            if self._last_penalty is not None and now - self._last_penalty < PENALTY_INTERVAL:
                return
            self._last_penalty = now
            self._served = 0
            floor = min(MIN_SEGMENT_ALLOWANCE, self.maximum)
            before = self._allowance
            self._allowance = max(floor, before // 2)
            after, ceiling = self._allowance, self.maximum
        if before != after:
            # Logged outside the lock, and only when the number really moved:
            # this is the line that says whether the source is throttling us or
            # refusing us, which the 503s alone never distinguished.
            logger.info("Source pushing back — segment concurrency %d → %d (max %d)",
                        before, after, ceiling)
            # The same signal pauses the page requests too (source_budget's
            # backoff): the source refusing segments is refusing us.
            source_budget.report_pushback("download pipeline")

    def reward(self) -> None:
        """A segment arrived. One extra slot per allowance served, no more.

        Climbing by one only after a full round of successes is what keeps the
        loop from oscillating: the allowance has to be *earned* at its current
        width before it widens.
        """
        with self._cond:
            if self._allowance >= self.maximum:
                return
            self._served += 1
            if self._served >= self._allowance:
                self._served = 0
                self._allowance += 1
                self._cond.notify()


_segment_budget_guard = threading.Lock()
_segment_budget: AdaptiveLimiter | None = None


def segment_budget(size: int) -> AdaptiveLimiter:
    """The process-wide limiter, following the configured ceiling."""
    global _segment_budget
    with _segment_budget_guard:
        if _segment_budget is None:
            _segment_budget = AdaptiveLimiter(size)
        else:
            _segment_budget.resize(size)
        return _segment_budget


def _retry_after(response) -> float | None:
    """The wait the source asked for, in seconds, when it named one.

    Only the delta-seconds form is read. The HTTP-date alternative needs a
    clock both ends agree on, and a CDN shedding load sends the number.
    """
    raw = (response.headers or {}).get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, min(float(str(raw).strip()), MAX_RETRY_AFTER))
    except (TypeError, ValueError):
        return None


def _new_session(pool_size: int) -> requests.Session:
    """One session per download, sized to the workers that will share it.

    Every segment used to go through a bare ``requests.get``, which opens a new
    TCP connection, a new TLS handshake and — the part that actually broke
    things — a new DNS lookup, for each of a playlist's thousands of segments.
    A session keeps the connection alive, so the host is resolved once per
    download instead of once per request.

    The pool is sized explicitly: urllib3's default of ten would leave sixteen
    workers discarding connections and reopening them, which is the same
    problem in miniature. Retries stay at zero because ``get_req_ts`` owns that
    policy, and urllib3's would silently multiply it.
    """
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size, max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


AES_128 = "AES-128"


def decrypt_aes128_cbc(key: bytes, iv: bytes, encrypted_data: bytes) -> bytes:
    """Decrypt one HLS segment and strip its PKCS7 padding.

    HLS pads every AES-128 segment (RFC 8216 §4.3.2.4). The padding used to be
    left in, so each segment ended in up to sixteen bytes that are not MPEG-TS,
    in the middle of the concatenated stream. Unpadding is also the check that
    was missing: a wrong key, a wrong IV or a truncated body ends in a
    ValueError here instead of in a file full of noise.

    No ``backend`` argument: it has been optional since cryptography 3.1 and
    ``default_backend()`` is gone from current releases.
    """
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(encrypted_data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def segment_iv(raw_iv: str | None, sequence: int) -> bytes:
    """The IV for one segment: the playlist's own, or its media sequence number.

    An EXT-X-KEY without an IV attribute means the segment's media sequence
    number, as a 16-byte big-endian integer (RFC 8216 §5.2). That case used to
    skip decryption altogether and write the ciphertext out as video.
    """
    if raw_iv:
        digits = str(raw_iv).strip()
        if digits[:2].lower() == "0x":
            digits = digits[2:]
        return bytes.fromhex(digits.zfill(32))
    return int(sequence).to_bytes(16, "big")


def _is_vtt(uri: str | None) -> bool:
    """Whether a segment URI names a WebVTT file, by its path's extension.

    Not by substring: a token, a directory or a host with "vtt" anywhere in it
    used to turn every video segment of that playlist into a subtitle.
    """
    return urlparse(uri or "").path.lower().endswith((".vtt", ".webvtt"))


class M3U8_Parser:
    def __init__(self):
        self.segments = []
        # One entry per item of ``segments``, same index: what is needed to
        # fetch and decrypt that segment beyond its URI. Kept beside the list
        # rather than in it because ``segments`` is a list of URLs everywhere
        # else in the code.
        self.segment_info: list[dict] = []
        self.video_playlist = []
        self.keys = []
        self.subtitle_playlist = []
        self.subtitle = []
        self.audio_ts = []

    def parse_data(self, m3u8_content, base_uri=None):
        """Parse a playlist; with *base_uri*, every URI in it comes out absolute.

        A playlist may name its variants, segments, keys and renditions
        relative to itself (RFC 8216 §4.1). Left as they were, those reached
        ``requests`` as "1080/playlist.m3u8" and failed with MissingSchema.
        """
        def absolute(uri):
            return urljoin(base_uri, uri) if base_uri and uri else uri

        try:
            m3u8_obj = M3U8_Lib(m3u8_content)

            for playlist in m3u8_obj.playlists:
                self.video_playlist.append({
                    "uri": absolute(playlist.uri),
                    "bandwidth": playlist.stream_info.bandwidth or 0,
                    "resolution": playlist.stream_info.resolution,
                })

            for key in m3u8_obj.keys:
                if key is not None:
                    self.keys = {
                        "method": key.method,
                        "uri": absolute(key.uri),
                        "iv": key.iv,
                    }

            for media in m3u8_obj.media:
                if media.type == "SUBTITLES":
                    self.subtitle_playlist.append({
                        "type": media.type,
                        "name": media.name,
                        "default": media.default,
                        "language": ("forced-" + media.language if media.language and str(media.forced).upper() == "YES" and not media.language.startswith("forced-") else media.language),
                        "uri": absolute(media.uri),
                    })
                elif media.type == "AUDIO":
                    self.audio_ts.append({
                        "type": media.type,
                        "name": media.name,
                        "default": media.default,
                        "language": media.language,
                        "uri": absolute(media.uri),
                    })

            first_sequence = m3u8_obj.media_sequence or 0
            range_end: dict[str, int] = {}
            for position, segment in enumerate(m3u8_obj.segments):
                uri = absolute(segment.uri)
                if _is_vtt(uri):
                    self.subtitle.append(uri)
                    continue

                # The key in force for *this* segment. EXT-X-KEY applies from
                # where it appears until the next one, so a playlist that
                # rotates keys has several, and reading the last of them for
                # every segment decrypted all but the final stretch wrongly.
                key = getattr(segment, "key", None)
                method = (getattr(key, "method", None) or "NONE").upper()
                init = getattr(segment, "init_section", None)

                byterange = None
                raw_range = getattr(segment, "byterange", None)
                if raw_range:
                    # "<length>[@<offset>]"; without an offset the range starts
                    # where the previous one into the same resource ended.
                    length, _, offset = str(raw_range).partition("@")
                    start = int(offset) if offset else range_end.get(uri, 0)
                    range_end[uri] = start + int(length)
                    byterange = (start, int(length))

                self.segments.append(uri)
                self.segment_info.append({
                    "sequence": first_sequence + position,
                    "method": method,
                    "key_uri": absolute(getattr(key, "uri", None)) if method != "NONE" else None,
                    "iv": getattr(key, "iv", None) if method != "NONE" else None,
                    "byterange": byterange,
                    "init": absolute(getattr(init, "uri", None)) if init is not None else None,
                    "discontinuity": bool(getattr(segment, "discontinuity", False)),
                })

        except Exception as e:
            logger.error(f"Error parsing M3U8 content: {redact(e)}")
            raise

    def get_best_quality(self):
        if self.video_playlist:
            best = max(self.video_playlist, key=lambda p: p.get("bandwidth") or 0)
            logger.info("Selected quality: %s bandwidth=%s", best.get("resolution"), best.get("bandwidth"))
            return best.get("uri")
        logger.warning("No video playlist found")
        return None

    def available_languages(self) -> dict:
        """Return available audio and subtitle language codes from the master playlist."""
        audio = [t.get("language") for t in self.audio_ts if t.get("language")]
        subtitles = [s.get("language") for s in self.subtitle_playlist if s.get("language")]
        return {"audio": audio, "subtitles": subtitles}

    def get_track_audio(self, language_name):
        if self.audio_ts:
            if language_name is not None:
                for obj_audio in self.audio_ts:
                    if obj_audio.get("name") == language_name:
                        return obj_audio.get("uri")
        return None


class M3U8_Segments:
    def __init__(self, url, key=None, temp_dir=None, progress_factory=None, referer=None, cancel_event=None, phase=None, emit_join_phase=True):
        self.url = url
        self.key = key
        self.referer = referer
        self._cancel = cancel_event
        self.phase = phase
        self.emit_join_phase = emit_join_phase

        # What each segment needs beyond its URL (see M3U8_Parser.segment_info);
        # empty when ``segments`` was assigned directly rather than parsed.
        self.segments = []
        self.segment_info: list[dict] = []
        # AES keys by the URI the playlist names them at. The key handed in
        # belongs to the first URI; any other is fetched when first needed.
        self._keys: dict[str | None, bytes] = {}
        self._keys_lock = threading.Lock()

        self.temp_folder = temp_dir if temp_dir is not None else os.path.join("tmp", "segments")
        os.makedirs(self.temp_folder, exist_ok=True)

        self.progress_factory = progress_factory
        self.progress_timeout = PROGRESS_TIMEOUT
        self.max_retry = 3
        self._failed_segments = set()
        self._saved_count = 0
        self._failure_streak = 0
        self._failed_lock = threading.Lock()
        self._abort = threading.Event()
        # Set by a worker that hit something no retry can change (a malformed
        # or refused URL); download_ts raises it instead of "incomplete".
        self._fatal: str | None = None
        # Moved by a worker waiting out a Retry-After, read by the watchdog.
        self._heartbeat = time.monotonic()

        # Read once here rather than per segment: get_settings() opens and
        # parses data.json on every call, and this is on the path taken a few
        # thousand times per download.
        self._workers = max(1, int(get_settings().get("max_segment_workers", 4)))
        # The source cap follows the settings from this download on.
        apply_source_cap()
        self._budget = segment_budget(self._workers)
        self._session = _new_session(self._workers)

        # One user-agent for the whole download, not one per request.
        # get_headers() builds a rotator and picks again on every call, so a
        # single film sent thousands of different agents down what is now one
        # connection — wasted work, and exactly the shape a CDN rate-limits.
        self._user_agent = get_headers()

    def parse_data(self, m3u8_content, base_uri=None):
        m3u8_parser = M3U8_Parser()
        m3u8_parser.parse_data(m3u8_content, base_uri=base_uri or self.url)
        self._adopt(m3u8_parser)

    def _adopt(self, parser: "M3U8_Parser"):
        """Take a media playlist's segments, refusing what cannot be downloaded.

        Checked here, before anything is fetched, so an unsupported stream is
        one clear error instead of a completed download of the wrong bytes.
        """
        methods = {info["method"] for info in parser.segment_info} - {"NONE"}
        unsupported = sorted(methods - {AES_128})
        if unsupported:
            # SAMPLE-AES and friends encrypt inside the elementary streams.
            # Running them through AES-128-CBC over the whole segment — which
            # is what "there is a key, so decrypt" amounted to — produces noise.
            raise UnsupportedStreamError(
                f"Cifratura HLS non supportata: {', '.join(unsupported)} "
                f"(è gestito solo {AES_128})"
            )
        if any(info["init"] for info in parser.segment_info):
            # EXT-X-MAP means fragmented MP4: an initialisation section plus
            # media fragments. The join below concatenates MPEG-TS and hands
            # the result to FFmpeg's TS demuxer, which is not a container that
            # an init section can be prepended to — supporting fMP4 is a
            # different join, not an extra request. Refused rather than
            # downloaded in full and then failed, or worse, joined wrong.
            raise UnsupportedStreamError(
                "fMP4 non supportato: la playlist usa EXT-X-MAP (segmenti MP4 "
                "frammentati), questo downloader gestisce solo MPEG-TS"
            )
        breaks = sum(1 for info in parser.segment_info if info["discontinuity"])
        if breaks:
            # Tolerated, not handled: the segments are concatenated as they
            # are and FFmpeg regenerates timestamps (+genpts) across the break.
            # Usually an advert or a re-encode boundary; the audio can drift.
            logger.warning("Playlist has %d EXT-X-DISCONTINUITY marker(s); joining across them", breaks)

        self.segments = parser.segments
        self.segment_info = parser.segment_info

        # The key the caller resolved is the one the playlist's first key URI
        # names; a playlist with a single URI — every one seen so far — never
        # fetches anything here.
        if self.key is not None:
            first = next((info["key_uri"] for info in self.segment_info
                          if info["method"] == AES_128), None)
            with self._keys_lock:
                self._keys.setdefault(first, self.key)

    def _info(self, index: int) -> dict:
        """Per-segment details, or none when ``segments`` was set by hand."""
        return self.segment_info[index] if index < len(self.segment_info) else {}

    def _key_for(self, key_uri: str | None) -> bytes:
        """The AES key published at *key_uri*, fetched once per download."""
        with self._keys_lock:
            if key_uri in self._keys:
                return self._keys[key_uri]
            if not key_uri:
                raise RuntimeError("La playlist è cifrata ma non indica la chiave (EXT-X-KEY senza URI)")
            response = with_retry(
                lambda: hosts.safe_get(self._session.get, key_uri,
                                       headers=self._headers(), timeout=15),
                what="segment key",
            )
            if not response.ok:
                raise RuntimeError(f"Cannot fetch encryption key: HTTP {response.status_code}")
            if len(response.content or b"") != AES128_KEY_BYTES:
                raise RuntimeError(
                    f"Chiave di cifratura non valida: attesi {AES128_KEY_BYTES} byte, "
                    f"ricevuti {len(response.content or b'')}"
                )
            self._keys[key_uri] = response.content
            return response.content

    def _decode(self, index: int, content: bytes) -> bytes:
        """The segment's plain bytes: decrypted when its EXT-X-KEY says so.

        ValueError (bad padding, a body that is not whole blocks) means the
        segment did not arrive intact and is the caller's to count as failed.
        """
        info = self._info(index)
        if info.get("method", "NONE") != AES_128:
            return content
        key = self._key_for(info.get("key_uri"))
        return decrypt_aes128_cbc(key, segment_iv(info.get("iv"), info.get("sequence", index)), content)

    def _headers(self):
        h = {"user-agent": self._user_agent}
        if self.referer:
            h["referer"] = self.referer
        return h

    def _wait(self, delay: float) -> bool:
        """Sleep *delay* seconds, or less if the job is cancelled. True if it was.

        ``time.sleep`` could not be interrupted: a cancelled download sat out
        every backoff still pending — up to MAX_RETRY_AFTER each — before any
        thread looked at the flag again.
        """
        if self._cancel is None:
            time.sleep(delay)
            return False
        return self._cancel.wait(delay)

    def _fetch_m3u8(self, url: str, what: str, max_retries: int = 3, retry_delay: int = 2):
        """Fetch one M3U8, riding out the errors this CDN hands out routinely.

        Shared by both fetches in ``get_info()``. They used to be written out
        separately and only the first one grew retries, so a transient 500 on
        the rendition playlist killed the whole download while the identical
        failure one request earlier would have been retried.

        Handles three things the bare call did not: a transient non-2xx, the
        ``?b=1`` quirk (some playlists are only served with it), and a
        connection that never completes.
        """
        current = url
        tried_with_b1 = "b=1" in url
        response = None

        for attempt in range(max_retries):
            last = attempt == max_retries - 1
            try:
                response = hosts.safe_get(self._session.get, current,
                                          headers=self._headers(), timeout=15)
            except _UNFETCHABLE as e:
                # Not an outage: the URL itself is unusable — no scheme, not
                # https, a private address. The third attempt would say exactly
                # what the first did, six seconds later.
                raise RuntimeError(f"Failed to fetch {what} M3U8: URL non valido ({redact(e)})") from e
            except requests.RequestException as e:
                response = None
                logger.warning("%s M3U8 fetch failed (%s) — tentativo %d/%d",
                               what, redact(e), attempt + 1, max_retries)
            else:
                if response.ok:
                    return response
                if response.status_code == 403 and not tried_with_b1:
                    # A different URL, so retry at once rather than waiting.
                    logger.warning("%s M3U8 fetch returned 403, retrying with ?b=1: %s",
                                   what, redact(current))
                    current = current + ("&b=1" if "?" in current else "?b=1")
                    tried_with_b1 = True
                    continue
                logger.warning("%s M3U8 fetch returned HTTP %d — tentativo %d/%d",
                               what, response.status_code, attempt + 1, max_retries)
            if not last and self._wait(retry_delay):
                raise DownloadCancelledError("Download annullato dall'utente")

        status = response.status_code if response is not None else "nessuna risposta"
        raise RuntimeError(f"Failed to fetch {what} M3U8: HTTP {status}")

    def get_info(self):
        response = self._fetch_m3u8(self.url, "playlist")

        parser = M3U8_Parser()
        parser.parse_data(response.text, base_uri=self.url)

        if parser.segments:
            # Direct segment playlist
            self._adopt(parser)
            logger.info("Direct segment playlist: %d segments, first=%s", len(self.segments),
                        redact(self.segments[0]) if self.segments else "none")
        elif parser.video_playlist:
            # Master playlist — resolve the best quality rendition
            best_url = parser.get_best_quality()
            logger.info("Master playlist detected (%d variants), fetching best rendition: %s",
                        len(parser.video_playlist), redact(best_url))
            rendition_resp = self._fetch_m3u8(best_url, "rendition")
            rp = M3U8_Parser()
            rp.parse_data(rendition_resp.text, base_uri=best_url)
            self._adopt(rp)
            logger.info("Rendition segments: %d, first=%s", len(self.segments),
                        redact(self.segments[0]) if self.segments else "none")
        else:
            # Status and size only: the body is a third party's response and
            # used to be quoted into job.error and the log.
            raise RuntimeError(
                f"M3U8 has no segments and no variant playlists "
                f"(HTTP {response.status_code}, {len(response.text)} characters)")

    def get_req_ts(self, ts_url, byterange=None):
        """Fetch one segment, retrying what is worth retrying.

        The loop ran three times but only a 429 ever reached the second
        iteration: every other status returned on the first attempt, so a CDN
        shedding load with 503s lost segments it would have served a moment
        later. An *exception* did retry, which meant the same outage was
        handled differently depending on whether the server answered badly or
        did not answer at all.

        *byterange* is ``(offset, length)`` for a segment that is a slice of a
        larger resource (EXT-X-BYTERANGE); it is asked for with a Range header.

        Returns the bytes, or None once the segment is given up on.
        """
        headers = self._headers()
        if byterange:
            start, length = byterange
            headers["range"] = f"bytes={start}-{start + length - 1}"

        for attempt in range(self.max_retry):
            if self._cancel and self._cancel.is_set():
                return None
            asked_for = None
            try:
                # The slot is held around the request alone. A thread waiting
                # out its backoff below must not occupy one the source is
                # willing to give to someone else.
                with self._budget:
                    response = hosts.safe_get(
                        self._session.get, ts_url, headers=headers, timeout=SEGMENT_TIMEOUT
                    )
            except _UNFETCHABLE as e:
                # The URL itself is unusable, and so is every other one this
                # playlist names the same way: stop the download rather than
                # spend three attempts on each of a few thousand segments.
                self._fatal = f"URL di un segmento non valido: {redact(e)}"
                self._abort.set()
                logger.error("Segment URL refused, aborting: %s", redact(e))
                return None
            except Exception as e:
                # A refused or timed-out connection under load says the same
                # thing a 503 does, and used to be the one form of pushback the
                # limiter never heard about.
                self._budget.penalise()
                logger.warning("Segment exception (attempt %d/%d): %s",
                               attempt + 1, self.max_retry, redact(e))
            else:
                content = self._segment_body(response, byterange)
                if content is not None:
                    self._budget.reward()
                    return content
                logger.warning("Segment HTTP %d (attempt %d/%d): ...%s", response.status_code,
                               attempt + 1, self.max_retry, redact(ts_url)[-60:])
                if response.status_code not in RETRIABLE_SEGMENT_STATUS | {200, 206}:
                    # A verdict, not congestion: nothing to back off from.
                    return None
                self._budget.penalise()
                asked_for = _retry_after(response)
            if attempt < self.max_retry - 1:
                # Waiting is pointless once the source has been written off, but
                # the attempt already made still counts: bailing out before ever
                # asking is what made an early abort cascade through every
                # segment still in flight.
                if self._abort.is_set():
                    return None
                if asked_for is not None:
                    # Doing what the source asked is not a stall: tell the
                    # watchdog, which would otherwise time this wait.
                    self._heartbeat = time.monotonic() + asked_for
                # The source's own number wins over our guess when it sent one.
                if self._wait(asked_for if asked_for is not None
                              else _segment_backoff(attempt)):
                    return None
        return None

    @staticmethod
    def _segment_body(response, byterange):
        """The segment's bytes out of *response*, or None when it is not whole.

        A 200 whose body is shorter than its own Content-Length is a connection
        that dropped mid-transfer; it used to be saved as the segment. A range
        request answered with 200 is the whole resource, so the slice is cut
        out of it here.
        """
        if response.status_code not in ((200, 206) if byterange else (200,)):
            return None
        content = response.content
        declared = (getattr(response, "headers", None) or {}).get("Content-Length")
        encoded = (getattr(response, "headers", None) or {}).get("Content-Encoding")
        if declared and not encoded and str(declared).isdigit() and int(declared) != len(content):
            return None
        if byterange:
            start, length = byterange
            if response.status_code == 200:
                content = content[start:start + length]
            if len(content) != length:
                return None
        return content

    def _write_ts(self, index, content):
        """Decrypt *content* and put it on disk as segment *index*, atomically.

        Decrypted before the file is opened, and written under another name
        until it is whole: a decryption error or a full disk used to leave
        ``N.ts`` behind at zero or partial length, and every later check — the
        resume, the completeness gate, the join — took a file with that name
        for the segment.
        """
        data = self._decode(index, content)
        if not data:
            raise ValueError("segmento vuoto dopo la decifratura")
        ts_filename = os.path.join(self.temp_folder, f"{index}.ts")
        staged = ts_filename + ".part"
        try:
            with open(staged, "wb") as ts_file:
                ts_file.write(data)
            os.replace(staged, ts_filename)
        except BaseException:
            try:
                os.remove(staged)
            except OSError:
                pass
            raise

    def _have_segment(self, index) -> bool:
        """Whether segment *index* is on disk with something in it."""
        try:
            return os.path.getsize(os.path.join(self.temp_folder, f"{index}.ts")) > 0
        except OSError:
            return False

    def _record_success(self):
        """One segment obtained. Breaks the failure run: the source is serving."""
        with self._failed_lock:
            self._saved_count += 1
            self._failure_streak = 0

    def _record_failure(self, index, ts_url):
        """Note a segment given up on, and stop if nothing is getting through.

        Losing segments is not by itself a reason to abandon the download: the
        sequential second pass exists to recover them, and it arrives later and
        one request at a time, which is what a throttling source was asking for.
        The breaker is for the other case — a source refusing outright — where
        every remaining segment would cost its full attempts and waits to reach
        a conclusion already visible.
        """
        with self._failed_lock:
            self._failed_segments.add(index)
            self._failure_streak += 1
            streak = self._failure_streak
            lost = len(self._failed_segments)
        logger.warning("Segment %d failed after all retries: ...%s", index, redact(ts_url)[-60:])

        if streak < FAILURE_ABORT_STREAK or self._abort.is_set():
            return
        self._abort.set()
        logger.error("Aborting: %d segments in a row failed with none arriving between "
                     "them (%d lost of %d) — the source is refusing, not throttling",
                     streak, lost, len(self.segments))

    def save_ts(self, index, progress_counter, quit_event):
        if self._cancel and self._cancel.is_set():
            return
        if self._abort.is_set():
            return
        ts_url = self.segments[index]
        ts_filename = os.path.join(self.temp_folder, f"{index}.ts")

        downloaded_bytes = 0
        # A resumed segment has to have something in it: a zero-byte file left
        # by an earlier attempt is not a segment that is present.
        if not self._have_segment(index):
            with _SOURCE_SLOTS:
                ts_content = self.get_req_ts(ts_url, self._info(index).get("byterange"))
            # Empty content counts as a failure: it would otherwise write a
            # zero-byte file, which then looks like a segment that is present.
            if not ts_content:
                self._record_failure(index, ts_url)
                return
            try:
                self._write_ts(index, ts_content)
            except Exception as e:
                # Bad padding, a body that is not whole AES blocks, a disk
                # error. It used to escape into the executor, where it was
                # logged and forgotten: the segment was in neither the failed
                # set nor on disk, so the sequential pass never retried it.
                logger.warning("Segment %d could not be written: %s", index, redact(e))
                self._record_failure(index, ts_url)
                return
            downloaded_bytes = len(ts_content)
        else:
            try:
                downloaded_bytes = os.path.getsize(ts_filename)
            except OSError:
                pass

        self._record_success()

        # Only a segment actually obtained counts. Counting attempts drove the
        # bar to 100% on a download that had saved barely half its segments,
        # and — worse — kept feeding the watchdog below, so a source failing
        # every single request still looked like it was making progress.
        progress_counter.update(1, bytes=downloaded_bytes)

    def download_ts(self):
        self._failed_segments = set()
        self._saved_count = 0
        self._failure_streak = 0
        self._abort.clear()
        self._fatal = None
        self._heartbeat = time.monotonic()
        bar_factory = self.progress_factory or (lambda **kw: tqdm(**kw))
        progress_counter = bar_factory(total=len(self.segments), unit="seg", desc="Downloading", phase=self.phase)
        self._bar = progress_counter

        quit_event = threading.Event()
        timeout_event = threading.Event()
        cancelled = False

        timer_thread = threading.Thread(
            target=self.timer, args=(progress_counter, quit_event, timeout_event.set)
        )
        timer_thread.start()

        try:
            with ThreadPoolExecutor(max_workers=self._workers) as executor:
                futures = []
                for index in range(len(self.segments)):
                    if timeout_event.is_set() or self._abort.is_set():
                        break
                    if self._cancel and self._cancel.is_set():
                        cancelled = True
                        break
                    futures.append(executor.submit(self.save_ts, index, progress_counter, quit_event))

                for future in as_completed(futures):
                    if timeout_event.is_set() or self._abort.is_set():
                        for f in futures:
                            f.cancel()
                        break
                    if self._cancel and self._cancel.is_set():
                        cancelled = True
                        for f in futures:
                            f.cancel()
                        break
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Segment error: {e}")

            # Stop the watchdog before the sequential pass. It measures stalls
            # against the parallel pass, and the retry below is deliberately
            # slow and one request at a time.
            quit_event.set()
            timer_thread.join()

            if cancelled:
                raise DownloadCancelledError("Download annullato dall'utente")

            if self._fatal:
                raise RuntimeError(f"Download interrotto: {self._fatal}")

            # Both of these used to fall through to the join, which then wrote
            # an output file out of whatever had arrived.
            if self._abort.is_set():
                raise RuntimeError(
                    f"Download interrotto: la fonte ha rifiutato {len(self._failed_segments)} "
                    f"segmenti su {len(self.segments)} servendone quasi nessuno. "
                    f"Riprova più tardi."
                )
            if timeout_event.is_set():
                raise RuntimeError(
                    f"Download interrotto: nessun segmento scaricato per "
                    f"{self.progress_timeout}s. La fonte non risponde, riprova più tardi."
                )

            self._retry_failed_sequentially(progress_counter)
            self._require_every_segment()
        finally:
            # Closed here and not before the retry pass: a segment recovered
            # sequentially has to reach the bar, or a download that lost some
            # segments early stops short of its total and never emits the event
            # that says the phase is finished.
            progress_counter.close()
            quit_event.set()
            timer_thread.join()
            # Nothing after this fetches anything — join() only reads the files
            # already on disk — so the pooled connections are released here
            # rather than waiting on the garbage collector.
            self._session.close()

    def _retry_failed_sequentially(self, progress_counter):
        """Second pass over the segments the parallel pass gave up on.

        Worth a try even after their attempts are exhausted: this arrives later
        and one request at a time, which is what a source shedding load was
        asking for.
        """
        if not self._failed_segments:
            return
        logger.warning("Retrying %d failed segments sequentially...", len(self._failed_segments))
        still_failed = set()
        streak = 0
        deadline = time.monotonic() + SEQUENTIAL_BUDGET
        pending = sorted(self._failed_segments)
        for position, index in enumerate(pending):
            if self._cancel and self._cancel.is_set():
                raise DownloadCancelledError("Download annullato dall'utente")
            # Bounded, and loudly: see SEQUENTIAL_MAX_STREAK. Each segment
            # tried here can cost three attempts and their backoff, so without
            # a limit a source that stopped serving kept the job "downloading"
            # for hours on its way to the same error.
            if streak >= SEQUENTIAL_MAX_STREAK or time.monotonic() > deadline:
                left = len(pending) - position + len(still_failed)
                reason = (f"{streak} segmenti consecutivi falliti" if streak >= SEQUENTIAL_MAX_STREAK
                          else f"superato il limite di {SEQUENTIAL_BUDGET // 60} minuti")
                self._failed_segments = still_failed | set(pending[position:])
                raise RuntimeError(
                    f"Download interrotto: recupero sequenziale abbandonato ({reason}), "
                    f"{left} segmenti su {len(self.segments)} ancora mancanti. "
                    f"La fonte non li sta servendo, riprova più tardi."
                )
            if self._wait(0.5):
                raise DownloadCancelledError("Download annullato dall'utente")
            ts_content = self.get_req_ts(self.segments[index], self._info(index).get("byterange"))
            if self._fatal:
                raise RuntimeError(f"Download interrotto: {self._fatal}")
            recovered = False
            if ts_content:
                try:
                    self._write_ts(index, ts_content)
                    recovered = True
                except Exception as e:
                    logger.warning("Segment %d could not be written: %s", index, redact(e))
            if recovered:
                streak = 0
                progress_counter.update(1, bytes=len(ts_content))
                logger.info("Segment %d recovered on retry", index)
            else:
                streak += 1
                still_failed.add(index)
                logger.error("Segment %d permanently failed", index)
        self._failed_segments = still_failed

    def _missing_segments(self) -> list[int]:
        """Which segments are not on disk.

        Asks the filesystem rather than trusting ``_failed_segments``, which
        only knows about segments that were attempted. A run cut short by the
        watchdog never submits the rest, so the bookkeeping would call a
        download whole precisely when most of it is absent.
        """
        present = set()
        try:
            names = os.listdir(self.temp_folder)
        except OSError:
            names = []
        for name in names:
            stem, ext = os.path.splitext(name)
            # Present means present with content: an empty N.ts is a segment
            # that failed in a way that still left its name behind. ("N.ts.part"
            # never matches — its extension is ".part".)
            if ext == ".ts" and stem.isdigit() and self._have_segment(int(stem)):
                present.add(int(stem))
        return [i for i in range(len(self.segments)) if i not in present]

    def _require_every_segment(self):
        """Refuse to hand on an incomplete set of segments.

        A gap here is not a glitch to be logged: TS concatenated with segments
        missing produces a file that opens, plays, and is wrong — and lands in
        the library looking exactly like a good one. Failing is recoverable,
        because the download can be run again; a silently truncated film is not,
        because nobody knows to.
        """
        missing = self._missing_segments()
        if not missing:
            return
        preview = ", ".join(str(i) for i in missing[:10])
        if len(missing) > 10:
            preview += ", …"
        logger.error("Incomplete download: %d/%d segments missing (%s)",
                     len(missing), len(self.segments), preview)
        raise RuntimeError(
            f"Download incompleto: {len(missing)} segmenti su {len(self.segments)} "
            f"non scaricati. Il file non è stato creato per non salvarlo corrotto."
        )

    def timer(self, progress_counter, quit_event, timeout_checker):
        start_time = time.monotonic()
        last_count = 0

        while not quit_event.is_set():
            current_count = progress_counter.n
            if current_count != last_count:
                start_time = time.monotonic()
                last_count = current_count

            # Measured from the last segment *or* the last honoured Retry-After,
            # whichever is later: a worker waiting as the source asked is not a
            # source that has stopped answering.
            if time.monotonic() - max(start_time, self._heartbeat) > self.progress_timeout:
                logger.warning(f"No progress for {self.progress_timeout}s, aborting download")
                timeout_checker()
                quit_event.set()
                break

            # Woken by quit_event rather than slept through, so the end of the
            # parallel pass does not wait out the rest of a second.
            quit_event.wait(1)

        progress_counter.refresh()

    def join(self, output_filename):
        if self._cancel and self._cancel.is_set():
            raise DownloadCancelledError("Download annullato dall'utente")
        # Checked before anything is written, not after. This used to warn about
        # missing segments on the line *following* the concatenation, so the
        # warning described a file that had already been assembled from a
        # partial set — and the download still ended as "done".
        self._require_every_segment()

        # "_combined.ts" is excluded by name: it has no digits, so it used to
        # raise ValueError out of the sort key if a previous attempt left one
        # behind. Reachable now that a failed join can leave the folder in place.
        # Exactly the playlist's segments, by index: a numbered file left over
        # from a longer earlier attempt in the same folder is not part of this
        # download, and listing the directory used to append it.
        ts_files = [f"{index}.ts" for index in range(len(self.segments))]

        # Byte-level concatenation: TS is a continuous stream format,
        # joining at byte level lets FFmpeg's TS demuxer handle PCR/PTS
        # continuity natively — avoids the timestamp drift of the concat demuxer.
        combined_ts = os.path.join(self.temp_folder, "_combined.ts")
        with open(combined_ts, "wb") as out:
            for ts_file in ts_files:
                with open(os.path.join(self.temp_folder, ts_file), "rb") as seg:
                    shutil.copyfileobj(seg, out)

        logger.info("Joining %d / %d segments...", len(ts_files), len(self.segments))
        if self.emit_join_phase and hasattr(self, '_bar') and hasattr(self._bar, 'emit_status'):
            self._bar.emit_status("joining")
        os.makedirs(os.path.dirname(os.path.abspath(output_filename)), exist_ok=True)

        # FFmpeg writes beside the destination and the result is renamed over
        # it only once it is whole. It used to write the destination itself,
        # with overwrite: a join that failed, or a job cancelled during it, had
        # already truncated whatever good file was there.
        from app.core.format import staging_path, JOIN_SUFFIX
        staged = staging_path(output_filename, JOIN_SUFFIX)
        try:
            try:
                self._run_join(combined_ts, staged, output_filename, reencode_audio=False)
            except ffmpeg.Error as copy_error:
                # The copy is refused when the audio is not AAC, or its ADTS
                # framing is too damaged to rewrap. Re-encoding the audio is
                # the fallback; the video is still copied.
                logger.warning("FFmpeg refused to copy the audio (%s); re-encoding it",
                               _ffmpeg_stderr(copy_error)[-200:])
                self._run_join(combined_ts, staged, output_filename, reencode_audio=True)
            if not os.path.exists(staged) or os.path.getsize(staged) == 0:
                raise RuntimeError("FFmpeg join error: nessun file prodotto")
            os.replace(staged, output_filename)
        except ffmpeg.Error as e:
            stderr = _ffmpeg_stderr(e)
            logger.error("FFmpeg join stderr: %s", stderr)
            raise RuntimeError(f"FFmpeg join error: {stderr}")
        finally:
            try:
                os.remove(staged)
            except OSError:
                pass

        logger.info("Cleaning temp segments...")
        shutil.rmtree(self.temp_folder, ignore_errors=True)

    def _run_join(self, combined_ts, staged, output_filename, reencode_audio):
        # Options are keyed on the extension of the file the join is *for*, not
        # on a parameter: this is called both for the destination (whatever
        # container is configured) and for the temp audio tracks (genuinely
        # mp4), and a parameter is a third thing every call site could get
        # wrong. movflags is mov/mp4-private — handing it to the matroska muxer
        # is not ignored, it is fatal, and it would fail after every byte is
        # fetched. The staging name keeps that extension, so FFmpeg picks the
        # same muxer for it.
        run_ffmpeg(
            ffmpeg.input(combined_ts, fflags="+genpts", avoid_negative_ts="make_zero").output(
                ffmpeg_file_arg(staged),
                **container.join_options(output_filename, reencode_audio=reencode_audio),
            ).overwrite_output(),
            cancel_event=self._cancel,
        )


class M3U8_Downloader:
    def __init__(self, m3u8_url, m3u8_audio=None, key=None, output_filename="output.mp4",
                 temp_dir=None, progress_factory=None, referer=None, cancel_event=None,
                 audio_languages: list[str] = None,
                 subtitle_languages: list[str] = None,
                 audio_track_urls: list[dict] = None,
                 subtitle_track_urls: list[dict] = None):
        self.m3u8_url = m3u8_url
        self.m3u8_audio = m3u8_audio
        self.key = key
        self.video_path = output_filename
        self.temp_dir = temp_dir or os.path.join("tmp", "segments")
        self.progress_factory = progress_factory
        self.referer = referer
        self.cancel_event = cancel_event
        self.audio_languages = audio_languages or ["ita"]
        self.subtitle_languages = subtitle_languages or []
        self.audio_track_urls = audio_track_urls or []
        self.subtitle_track_urls = subtitle_track_urls or []

        # A single audio playlist handed in directly is one more track to mux,
        # and goes down the same road as the ones collected per language. It
        # had a merge of its own (join_audio) whose -map options were placed
        # after the output file, where FFmpeg ignores them.
        if self.m3u8_audio is not None and not self.audio_track_urls:
            self.audio_track_urls = [{"url": self.m3u8_audio,
                                      "language": self.audio_languages[0]}]

        self.audio_paths: list[str] = []

    def start(self):
        from app.core.format import remux, staging_path, WORK_SUFFIX

        # Everything below is assembled under a hidden sibling of the
        # destination and renamed onto it at the very end. A title already in
        # the library is therefore never touched by a download that does not
        # complete: not by the join, not by a remux that fails, not by a
        # cancel between the two.
        final_path = self.video_path
        work_path = staging_path(final_path, WORK_SUFFIX)
        try:
            return self._assemble(work_path, final_path, remux)
        finally:
            try:
                os.remove(work_path)
            except OSError:
                pass

    def _assemble(self, work_path, final_path, remux):
        video_temp = os.path.join(self.temp_dir, "video")
        video_m3u8 = M3U8_Segments(self.m3u8_url, self.key,
                                    temp_dir=video_temp,
                                    progress_factory=self.progress_factory,
                                    referer=self.referer,
                                    cancel_event=self.cancel_event,
                                    phase="video")
        logger.info("Downloading video segments...")
        video_m3u8.get_info()
        video_m3u8.download_ts()
        bar = getattr(video_m3u8, "_bar", None)
        video_m3u8.join(work_path)

        if self.audio_track_urls:
            for i, track in enumerate(self.audio_track_urls):
                lang = track.get("language", "und")
                phase_label = f"audio_{lang}"
                if bar and hasattr(bar, "emit_status"):
                    bar.emit_status(phase_label)
                audio_temp = os.path.join(self.temp_dir, f"audio_{i}")
                audio_m3u8 = M3U8_Segments(track["url"], self.key,
                                            temp_dir=audio_temp,
                                            progress_factory=self.progress_factory,
                                            referer=self.referer,
                                            cancel_event=self.cancel_event,
                                            phase=phase_label,
                                            emit_join_phase=False)
                logger.info("Downloading audio track %d (%s)...", i + 1, lang)
                audio_m3u8.get_info()
                audio_m3u8.download_ts()
                audio_path = os.path.join(self.temp_dir, f"_audio_{i}.mp4")
                audio_m3u8.join(audio_path)
                self.audio_paths.append({"path": audio_path, "language": lang})

        # The remux is the merge, so the phase is announced here rather than in
        # the audio branch above. It used to be emitted only when separate audio
        # tracks had been downloaded, which left a film whose audio is already
        # muxed — remuxed all the same, to embed its subtitles — going straight
        # from "joining" to finished with the step never lighting up.
        # It is now also *not* emitted when the only extra content is subtitles
        # and they are configured to stay outside the container: there is no
        # merge to announce then, only a move.
        subtitle_mode = container.subtitle_mode()
        subtitle_tracks = self._downloaded_subtitles()
        embedded = subtitle_tracks if subtitle_mode == container.EMBED else []

        if self.audio_paths or embedded:
            if bar and hasattr(bar, "emit_status"):
                bar.emit_status("merging")
            remux(
                work_path,
                audio_tracks=self.audio_paths,
                subtitle_tracks=embedded,
                cancel_event=self.cancel_event,
            )
            for sub in embedded:
                try:
                    os.remove(sub["path"])
                    logger.info("Cleaned up subtitle: %s", sub["path"])
                except OSError:
                    pass

        if self.cancel_event is not None and self.cancel_event.is_set():
            raise DownloadCancelledError("Download annullato dall'utente")

        # The one moment the library changes: a complete file takes the place
        # of whatever was there, atomically.
        os.replace(work_path, final_path)
        self.video_path = final_path

        if subtitle_mode == container.EXTERNAL:
            self._place_subtitles_beside_video(subtitle_tracks)

        self._remove_superseded_sibling()
        return self.video_path

    def _remove_superseded_sibling(self):
        """Delete the same title in the other container, if one is sitting there.

        Changing the setting and re-downloading a title otherwise leaves
        Film.mkv and Film.mp4 side by side, which Jellyfin reports as two
        versions of one film. Only the exact sibling of the file just written
        is touched, and only once that file is complete — the same delete the
        remux has always done to the .mp4 it superseded, now that which
        extension supersedes which is a setting rather than a fixed rule.
        """
        stem, ext = os.path.splitext(self.video_path)
        if ext not in container.KNOWN_EXTENSIONS:
            return
        for other in container.KNOWN_EXTENSIONS:
            superseded = stem + other
            if other == ext or not os.path.exists(superseded):
                continue
            try:
                os.remove(superseded)
                logger.info("Removed the superseded copy: %s", superseded)
            except OSError as exc:
                logger.warning("Could not remove the superseded %s: %s", superseded, exc)

    def _downloaded_subtitles(self) -> list[dict]:
        """The subtitle files download_m3u8 actually wrote into temp_dir.

        Keyed on subtitle_languages, NOT subtitle_track_urls — the latter is
        collected via a 403-prone path and can be empty even when the vtts
        downloaded fine, which silently dropped subs from the output.
        """
        from app.core.format import LANG_MAP

        video_stem = os.path.splitext(os.path.basename(self.video_path))[0]
        found, seen = [], set()
        for lang_code in self.subtitle_languages:
            lang_short = LANG_MAP.get(lang_code, lang_code)
            sub_path = os.path.join(self.temp_dir, f"{video_stem}.{lang_short}.vtt")
            if sub_path not in seen and os.path.exists(sub_path):
                seen.add(sub_path)
                found.append({"path": sub_path, "language": lang_code})
        return found

    def _place_subtitles_beside_video(self, subtitle_tracks: list[dict]):
        """Move the vtts next to the finished video, Jellyfin's own convention.

        Moved at the end rather than downloaded straight there: written into
        the library up front they would sit beside a video that does not exist
        yet, and a Jellyfin scan running mid-download would index a title whose
        only content is subtitles. shutil.move rather than os.replace, because
        the temp directory is routinely a different volume.
        """
        video_dir = os.path.dirname(self.video_path) or "."
        for sub in subtitle_tracks:
            target = os.path.join(video_dir, os.path.basename(sub["path"]))
            try:
                if os.path.exists(target):
                    os.remove(target)
                shutil.move(sub["path"], target)
                logger.info("Subtitle placed beside the video: %s", target)
            except OSError as exc:
                logger.warning("Could not place subtitle %s: %s", target, exc)

_UNFETCHABLE = (
    hosts.UnsafeURLError,
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidSchema,
    requests.exceptions.InvalidURL,
)


def _ffmpeg_stderr(error) -> str:
    stderr = error.stderr.decode(errors="replace") if error.stderr else "(no stderr)"
    if len(stderr) > 500:
        stderr = f"...{stderr[-300:]}"
    return stderr


def _get_playlist(url, referer=None):
    """One playlist request outside a download's own session.

    These went out as a bare ``requests.get(url)``: no user-agent, no referer
    — which the video host checks, so they were the requests most likely to be
    refused — and no retry, so one flap during language detection or subtitle
    discovery failed what a second attempt would have answered. The host is
    validated like every other URL a playlist can name.
    """
    headers = {"user-agent": get_headers()}
    if referer:
        headers["referer"] = referer
    return with_retry(lambda: hosts.safe_get(requests.get, url, headers=headers, timeout=15),
                      what="playlist")


def _fetch_text(url, referer=None):
    response = _get_playlist(url, referer)
    if response.ok:
        return response.text
    raise RuntimeError(f"Failed to fetch {redact(url)}: HTTP {response.status_code}")


def _fetch_text_with_b1_fallback(url, referer=None):
    response = _get_playlist(url, referer)
    if response.status_code == 403:
        logger.warning("M3U8 fetch returned 403, retrying with ?b=1: %s", redact(url))
        b1_url = url + ("&b=1" if "?" in url else "?b=1")
        response = _get_playlist(b1_url, referer)
    if response.ok:
        return response.text
    raise RuntimeError(f"Failed to fetch {redact(url)}: HTTP {response.status_code}")


def fetch_master_languages(m3u8_url: str, referer: str) -> dict:
    """Fetch a master M3U8 playlist and return available audio/subtitle language codes."""
    text = _fetch_text_with_b1_fallback(m3u8_url, referer=referer)
    parser = M3U8_Parser()
    parser.parse_data(text, base_uri=m3u8_url)
    return parser.available_languages()


class DownloadResult(str):
    """The path of a finished download, plus what it had to do without.

    A ``str`` so every caller that treats the result as the output path —
    ``job.output_path``, the "done" event, the library registry — keeps
    working untouched. The two lists are optional reading:

    ``missing_tracks``  requested tracks that are not in the file, as
                        ``audio:<lang>`` / ``subtitle:<lang>``. The download
                        used to finish as a plain success without them.
    ``warnings``        things that went wrong after the file was complete and
                        did not invalidate it — today, a failed conversion.
    """

    missing_tracks: list[str]
    warnings: list[str]

    def __new__(cls, path, missing_tracks=None, warnings=None):
        self = super().__new__(cls, path)
        self.missing_tracks = list(missing_tracks or [])
        self.warnings = list(warnings or [])
        return self


def download_m3u8(**kwargs):
    """Download one title, with nobody else writing the same file meanwhile.

    Two requests for the same title in different languages are deliberately kept
    apart — the content key includes the chosen tracks — but they resolve to the
    same path, because the library layout carries no language. Approving both
    therefore had two downloads writing one file at the same time, and the
    result was a corrupt film that still looked like a film.

    Serialising them means the second waits and then writes its own complete
    file over the first. Combined with resolving every request to the union of
    what has been asked for, the file left behind is the one that satisfies
    everybody rather than whoever finished last.

    Every caller passes keyword arguments, which is what lets this wrap the real
    body without restating its signature.
    """
    destination = kwargs.get("output_filename") or os.path.join("videos", "output.mp4")
    # Tracks the caller already knows it could not find (an audio language the
    # source does not offer, on a non-strict download). Not the body's to know.
    missing_tracks = list(kwargs.pop("missing_tracks", None) or [])
    held = _acquire_destination(destination, kwargs.get("cancel_event"))
    try:
        from app.config import get_settings
        from app.core.transcode import convert_video, TranscodeError
        settings = dict(get_settings())
        result = _download_m3u8(**kwargs)
        missing_tracks += [t for t in getattr(result, "missing_tracks", []) if t not in missing_tracks]
        if missing_tracks:
            logger.warning("Download finished without the requested track(s): %s",
                           ", ".join(missing_tracks))
        path = str(result or destination)
        if settings.get("video_conversion", "copy") == "copy":
            return DownloadResult(path, missing_tracks) if result else result
        try:
            path = convert_video(path, settings, kwargs.get("progress_factory"), kwargs.get("cancel_event"))
        except TranscodeError as exc:
            # The download is complete and the original is intact — convert_video
            # only ever replaces it with a finished encode. Raising here made
            # the job fail, and a failed job is retried from the first segment:
            # hours of fetching repeated to arrive at the same encoder error.
            # The file is delivered as it was downloaded, with the reason.
            logger.warning("Conversion failed, keeping the downloaded file as it is: %s", exc)
            return DownloadResult(path, missing_tracks,
                                  [f"Conversione non riuscita, file originale conservato: {exc}"])
        return DownloadResult(path, missing_tracks)
    finally:
        _release_destination(held)


def _download_m3u8(
    m3u8_playlist=None,
    m3u8_index=None,
    m3u8_audio=None,
    m3u8_subtitle=None,
    key=None,
    output_filename=os.path.join("videos", "output.mp4"),
    temp_dir=None,
    progress_factory=None,
    referer=None,
    cancel_event=None,
    audio_languages: list[str] = None,
    subtitle_languages: list[str] = None,
    audio_track_urls: list[dict] = None,
    subtitle_track_urls: list[dict] = None,
):
    # Checked before a single segment is fetched. A library configured with a
    # Windows path on a Linux host used to get all the way here, create a
    # directory literally named "N:\Jellyfin\Anime" beside the app, download
    # the whole episode, and only then fail in FFmpeg with "Protocol not found"
    # — an error that says nothing about the actual mistake.
    problem = windows_path_problem(output_filename)
    if problem:
        raise RuntimeError(f"Percorso di destinazione non valido. {problem}")

    key = bytes.fromhex(key) if key is not None else None

    # Jellyfin convention: subtitles live next to the video as {stem}.{lang}.vtt
    video_dir = os.path.dirname(output_filename) or "."
    video_stem = os.path.splitext(os.path.basename(output_filename))[0]

    audio_languages = audio_languages or ["ita"]
    subtitle_languages = subtitle_languages or []
    audio_track_urls = audio_track_urls or []
    subtitle_track_urls = subtitle_track_urls or []

    # Track subtitle files created before the main download so they can be cleaned
    # up if the download is cancelled or fails mid-way.
    created_subtitle_files: list[str] = []

    # Languages whose subtitle file made it into temp. Whatever was asked for
    # and is not here by the time the video starts is reported on the result.
    subtitles_done: set[str] = set()

    def _fetch_subtitles(parser, base_url):
        from app.core.format import download_subtitle_tracks
        created = download_subtitle_tracks(parser, subtitle_languages, video_dir, video_stem,
                                           temp_dir=temp_dir, referer=referer, base_url=base_url)
        created_subtitle_files.extend(t["path"] for t in created)
        subtitles_done.update(t["language"] for t in created)

    if m3u8_playlist is not None:
        parse_class_m3u8 = M3U8_Parser()
        inline = "#EXTM3U" in m3u8_playlist
        content = m3u8_playlist if inline else _fetch_text(m3u8_playlist, referer=referer)
        playlist_base = None if inline else m3u8_playlist
        parse_class_m3u8.parse_data(content, base_uri=playlist_base)

        if DOWNLOAD_DEFAULT_LANGUAGE:
            m3u8_audio = parse_class_m3u8.get_track_audio("Italian")

        if m3u8_index is None:
            m3u8_index = parse_class_m3u8.get_best_quality()
            # The scheme, not the substring: "https" appears in plenty of
            # relative paths and query strings that are not https URLs.
            if not m3u8_index or urlparse(m3u8_index).scheme != "https":
                raise RuntimeError("Cannot find a valid M3U8 index URL")

        langs = parse_class_m3u8.available_languages()
        logger.info("Available languages — audio: %s | subtitles: %s", langs["audio"], langs["subtitles"])

        if DOWNLOAD_SUB and subtitle_languages:
            _fetch_subtitles(parse_class_m3u8, playlist_base)

    elif m3u8_index is not None and DOWNLOAD_SUB and subtitle_languages:
        # m3u8_index is a master playlist URL — parse it to extract subtitles
        try:
            master_content = _fetch_text_with_b1_fallback(m3u8_index, referer=referer)
            parse_master = M3U8_Parser()
            parse_master.parse_data(master_content, base_uri=m3u8_index)
            langs = parse_master.available_languages()
            logger.info("Available languages — audio: %s | subtitles: %s", langs["audio"], langs["subtitles"])
            _fetch_subtitles(parse_master, m3u8_index)
        except Exception as e:
            # Still not fatal — the video is what was asked for first — but no
            # longer silent: every subtitle requested is reported as missing.
            logger.warning("Could not parse subtitles from master playlist: %s", redact(e))

    if m3u8_subtitle is not None:
        parse_sub = M3U8_Parser()
        inline_sub = "#EXTM3U" in m3u8_subtitle
        content_sub = m3u8_subtitle if inline_sub else _fetch_text(m3u8_subtitle, referer=referer)
        subtitle_base = None if inline_sub else m3u8_subtitle
        parse_sub.parse_data(content_sub, base_uri=subtitle_base)
        if DOWNLOAD_SUB and subtitle_languages:
            _fetch_subtitles(parse_sub, subtitle_base)

    missing_tracks = [f"subtitle:{lang}" for lang in subtitle_languages
                      if DOWNLOAD_SUB and lang not in subtitles_done]

    os.makedirs(os.path.dirname(output_filename) or ".", exist_ok=True)

    from app.core import format as format_module

    try:
        final_path = M3U8_Downloader(
            m3u8_index,
            m3u8_audio,
            key=key,
            output_filename=output_filename,
            temp_dir=temp_dir,
            progress_factory=progress_factory,
            referer=referer,
            cancel_event=cancel_event,
            audio_languages=audio_languages,
            subtitle_languages=subtitle_languages,
            audio_track_urls=audio_track_urls,
            subtitle_track_urls=subtitle_track_urls,
        ).start()
        return DownloadResult(final_path or output_filename, missing_tracks)
    except (DownloadCancelledError, Exception) as exc:
        # Remove the subtitle files this attempt downloaded. They are still in
        # temp: the move beside the video happens only after the video itself
        # has been published, and nothing after that raises.
        for path in created_subtitle_files:
            try:
                os.remove(path)
                logger.info("Removed partial subtitle: %s", path)
            except OSError:
                pass
        # Remove what this attempt was assembling — and only that. The
        # destination itself is never deleted here, in any container: until the
        # final rename it is not this download's file but whatever was already
        # in the library, and deleting it on failure is how a failed
        # re-download used to cost the copy that worked. (A file a *job*
        # created and wants gone is app.jobs' decision, which knows whether it
        # existed beforehand.)
        stem = os.path.splitext(output_filename)[0]
        leftovers = []
        for ext in container.KNOWN_EXTENSIONS:
            work = format_module.staging_path(stem + ext, format_module.WORK_SUFFIX)
            leftovers += [
                work,
                format_module.staging_path(work, format_module.JOIN_SUFFIX),
                format_module.staging_path(work),
                format_module.staging_path(stem + ext),
            ]
        for candidate in dict.fromkeys(leftovers):
            try:
                os.remove(candidate)
                logger.info("Removed partial output: %s", candidate)
            except OSError:
                pass
        # Remove the output directory if it's now empty
        try:
            if video_dir and video_dir != "." and not os.listdir(video_dir):
                os.rmdir(video_dir)
        except OSError:
            pass
        raise
