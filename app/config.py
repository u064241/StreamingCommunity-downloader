import json
import locale
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

logger = logging.getLogger(__name__)

VIDEOS_DIR = Path(os.getenv("VIDEOS_DIR", "videos"))
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
DATA_FILE = Path(os.getenv("DATA_FILE", "data.json"))
SCHEDULE_FILE = Path(os.getenv("SCHEDULE_FILE", "schedule.json"))
TMP_DIR = Path(os.getenv("TMP_DIR", "tmp"))

# ── Panel database (requests, notifications, settings) ────────────────────────
# Must live on a persistent volume when running in Docker, and must not be
# served to clients: it holds notification and hook URLs, which carry secrets.
DB_FILE = Path(os.getenv("DB_FILE", "panel.db"))

# Honour X-Forwarded-Host when checking the Host a request was made to. Only
# enable when the panel sits behind a reverse proxy you control — otherwise the
# header is attacker-controlled.
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "0") == "1"

# The panel is a local, single-user application with no login: it answers only
# to localhost and loopback addresses (127.0.0.0/8, ::1), which is what stops
# another site visited in the browser from reaching it through a DNS name of
# its own (DNS rebinding). Extra hostnames it may be reached under,
# comma-separated: ALLOWED_HOSTS=mypc.lan. See app.auth.deps.host_allowed().
ALLOWED_HOSTS = frozenset(
    h.strip().lower() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()
)

# Origins allowed to embed the panel in a frame, space- or comma-separated.
# Empty (the default): only the panel may frame itself. See
# app.auth.security_headers.
FRAME_ANCESTORS = tuple(os.getenv("FRAME_ANCESTORS", "").replace(",", " ").split())

# There is no login and no auth mode: earlier versions asked Jellyfin to sign
# users in (``auth_mode`` in the database, ``AUTH_ENABLED`` before that). Both
# are ignored now; migration v8 in app/db.py still reads the old variable once,
# harmlessly, for databases that predate it.

# ── Performance presets ───────────────────────────────────────────────────────
# How hard the panel pulls on the source and on this PC. Three fixed steps, so
# the hardware diagnosis and the automatic protection never invent numbers:
# they pick one of these, and never write anything more aggressive than it.
#
# source_max_connections is the global cap on segment requests in flight,
# across every job (app/core/m3u8.py); source_requests_per_hour is the sliding
# hourly budget of page requests (app/core/source_budget.py). Both are read
# from the settings: the cap at the start of each download, the budget at each
# request. SC_SOURCE_MAX_CONNECTIONS / SC_SOURCE_REQUESTS_PER_HOUR, when set,
# are explicit overrides and win over them; nothing sets them by default.
PERFORMANCE_KEYS = ("max_concurrent_downloads", "max_segment_workers",
                    "source_max_connections", "source_requests_per_hour")
PERFORMANCE_PRESETS = {
    "conservative": {"label": "Conservativo", "max_concurrent_downloads": 1,
                     "max_segment_workers": 4, "source_max_connections": 2,
                     "source_requests_per_hour": 60},
    "balanced": {"label": "Bilanciato", "max_concurrent_downloads": 1,
                 "max_segment_workers": 6, "source_max_connections": 3,
                 "source_requests_per_hour": 90},
    "fast": {"label": "Veloce", "max_concurrent_downloads": 2,
             "max_segment_workers": 8, "source_max_connections": 4,
             "source_requests_per_hour": 120},
}
# Least aggressive first: what "more aggressive than" is measured against.
PRESET_ORDER = ("conservative", "balanced", "fast")
DEFAULT_PERFORMANCE_PRESET = "conservative"

# Hard ceilings on what the settings may ask of the source. No preset goes
# above them, the settings API refuses more, and a stored value above them (an
# old data.json, a hand edit) is clamped when read. Only the environment
# overrides above can go past them, on purpose.
SOURCE_MAX_CONNECTIONS_CEILING = 4
SOURCE_REQUESTS_PER_HOUR_CEILING = 120

# What an installation that predates the presets was running with. Applied only
# when data.json exists but has never stored these keys: an existing install
# keeps its 3/16 until the user runs the diagnosis. A new one gets the preset
# above written at its first save (update_data), so it stays conservative.
# The source side is not kept as it was: the connection cap is at the ceiling
# (it used to be 6) and the hourly budget, absent here, is the conservative
# preset's (SETTINGS_DEFAULTS).
LEGACY_PERFORMANCE_DEFAULTS = {
    "max_concurrent_downloads": 3,
    "max_segment_workers": 16,
    "source_max_connections": SOURCE_MAX_CONNECTIONS_CEILING,
}


def _bounded_int(value, ceiling: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(1, min(ceiling, number))


def source_max_connections_setting(settings: dict | None = None) -> int:
    """The connection cap the settings ask for, clamped to the ceiling."""
    settings = get_settings() if settings is None else settings
    return _bounded_int(settings.get("source_max_connections"), SOURCE_MAX_CONNECTIONS_CEILING,
                        PERFORMANCE_PRESETS[DEFAULT_PERFORMANCE_PRESET]["source_max_connections"])


def source_requests_per_hour_setting(settings: dict | None = None) -> int:
    """The hourly request budget the settings ask for, clamped to the ceiling."""
    settings = get_settings() if settings is None else settings
    return _bounded_int(settings.get("source_requests_per_hour"), SOURCE_REQUESTS_PER_HOUR_CEILING,
                        PERFORMANCE_PRESETS[DEFAULT_PERFORMANCE_PRESET]["source_requests_per_hour"])


def preset_values(name: str) -> dict:
    """The setting values of a preset (no label). KeyError for an unknown one."""
    return {key: PERFORMANCE_PRESETS[name][key] for key in PERFORMANCE_KEYS}


def matching_preset(settings: dict) -> str | None:
    """The preset the performance settings equal, or None for a custom mix."""
    for name in PRESET_ORDER:
        if all(settings.get(key) == value for key, value in preset_values(name).items()):
            return name
    return None


SETTINGS_DEFAULTS = {
    **preset_values(DEFAULT_PERFORMANCE_PRESET),
    # Whether to look for a replacement when the source domain stops answering.
    "domain_auto_check_enabled": True,
    # Whether to adopt the replacement without asking. Off: a domain found on a
    # page we do not control is proposed to an administrator, never applied on
    # its own. See app.core.domain_recovery.
    "domain_auto_apply": False,
    "domain_check_interval_minutes": 1,
    # The container every download is written into, and whether subtitles are
    # muxed in or left beside the video as .vtt. The vocabulary and the
    # fallbacks live in app.core.container, which is the only thing that reads
    # these — app.config must not import app.core.
    "video_conversion": "copy",
    "video_encoder": "auto",
    "video_quality": "balanced",
    "video_cpu_fallback": True,
    "output_container": "mkv",
    "subtitle_mode": "embed",
    # The MCP endpoint at /mcp. Off by default: it lets an agent act as a user.
    "mcp_enabled": False,
    "confirmations_enabled": True,
}


class CorruptDataFile(RuntimeError):
    """data.json holds something that is not JSON and could not be set aside."""


# Held for the instant a JSON file is read or renamed into place (see
# atomic_write_text). Never across a parse or anything that can block.
_io_lock = threading.Lock()
_REPLACE_ATTEMPTS = 10


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` in one step.

    Written beside the target, flushed to disk, then renamed over it: a reader
    sees the old file or the new one, never a half-written one, and a crash or
    a full disk mid-write leaves the old file intact.

    Windows refuses the rename while anyone has the target open, even just to
    read it. ``_io_lock`` keeps this process's own readers out of the way for
    that instant; the short retry is for another process (an editor, a backup,
    an antivirus) holding it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            with _io_lock:
                os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


def quarantine_corrupt_file(path: Path) -> Path:
    """Move an unreadable JSON file aside as ``<name>.corrupt-<timestamp>``.

    Never deleted and never overwritten: starting from empty and saving over
    it is how one bad byte used to cost every setting. Returns the new path.
    """
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    with _io_lock:
        os.replace(path, target)
    return target


def _read_json_object(path: Path) -> dict:
    with _io_lock:
        raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Edited by hand in the system's ANSI code page, which is what this
        # file was opened with before it was read as UTF-8.
        text = raw.decode(locale.getpreferredencoding(False))
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, found {type(data).__name__}")
    return data


def read_data(*, strict: bool = False) -> dict:
    """The contents of data.json; ``{}`` only when there is no such file.

    The one reader of the file: everything else goes through here. A file that
    is not valid JSON is never treated as an empty configuration in silence -
    it is set aside as ``data.json.corrupt-<timestamp>`` and reported, so the
    next save cannot overwrite what might still be recovered by hand. Writes
    are atomic (``update_data``), so a reader needs no file lock to avoid a
    half-written file; that lock is taken only to set a corrupt one aside.

    ``strict`` is for the read-modify-write in ``update_data``: if the corrupt
    file cannot even be moved, raise instead of answering ``{}``.
    """
    try:
        return _read_json_object(DATA_FILE)
    except FileNotFoundError:
        return {}
    except ValueError as exc:  # json.JSONDecodeError, bad encoding, not an object
        first_error = exc
    with _data_lock():
        # Again under the lock: another thread may have set it aside already,
        # or a writer may have replaced it since.
        try:
            return _read_json_object(DATA_FILE)
        except FileNotFoundError:
            return {}
        except ValueError:
            pass
        try:
            moved = quarantine_corrupt_file(DATA_FILE)
        except OSError as exc:
            logger.error("%s is corrupt (%s) and could not be set aside: %s",
                         DATA_FILE, first_error, exc)
            if strict:
                raise CorruptDataFile(
                    f"{DATA_FILE} is corrupt and could not be set aside: {exc}") from exc
            return {}
    logger.error("%s is corrupt (%s): moved to %s, continuing with an empty configuration",
                 DATA_FILE, first_error, moved)
    return {}


def _data_lock() -> FileLock:
    return FileLock(str(DATA_FILE) + ".lock")


def configured_domain() -> str:
    """The source domain, as configured by an administrator.

    Every outbound request to the source resolves its host through here. The
    endpoints used to accept a `domain` from the client, which let any signed-in
    user make the server issue HTTP requests to a host of their choosing.
    """
    return (read_data().get("domain") or "").strip()


# Old aliases of the source. The source rotates its address, and the previous
# hosts keep answering for a while (often with 403). They are never adopted
# again and never probed, so the recovery loop does not keep knocking on them.
_MAX_RETIRED_DOMAINS = 20


def _retire_previous_domain(data: dict, new_domain) -> None:
    """Remember the domain being replaced. Called by update_data, the one writer."""
    old = (data.get("domain") or "").strip().lower()
    new = (new_domain or "").strip().lower()
    retired = [
        h.strip().lower() for h in (data.get("retired_domains") or [])
        if isinstance(h, str) and h.strip() and h.strip().lower() != new
    ]
    if old and old != new and old not in retired:
        retired.append(old)
    # Do not add an empty key to a file that never had one.
    if retired or "retired_domains" in data:
        data["retired_domains"] = retired[-_MAX_RETIRED_DOMAINS:]


def retired_domains() -> set[str]:
    """Hosts the panel must never adopt or probe again."""
    return {
        h.strip().lower() for h in (read_data().get("retired_domains") or [])
        if isinstance(h, str) and h.strip()
    }


# Where AnimeUnity lives unless an administrator says otherwise. The source moves
# TLD (.so to .to to .co); Settings › Sorgente overrides it without a release, and the
# ANIMEUNITY_HOST env var still works for deployments that set it.
ANIMEUNITY_DEFAULT_HOST = "www.animeunity.co"


def configured_animeunity_host() -> str:
    """AnimeUnity's host: the saved one, then ANIMEUNITY_HOST, then the default."""
    return ((read_data().get("animeunity_domain") or "").strip()
            or os.getenv("ANIMEUNITY_HOST", "").strip()
            or ANIMEUNITY_DEFAULT_HOST)


def configured_video_host() -> str:
    """A manual override for the video host, or "" to follow the embed URL.

    Empty is the normal state: the host is read off the embed URL the source
    hands over (app.core._shared.embed_host). The override is the escape hatch
    for when the source keeps pointing at a host that no longer answers.
    """
    return (read_data().get("video_host") or "").strip()


# Settings an older version stored that no longer mean anything. Dropped on
# read, so they neither reach the API nor travel into a settings backup.
# series_watch_interval_minutes went with the followed-series feature.
RETIRED_SETTINGS = frozenset({"jellyfin_refresh_on_download", "series_watch_interval_minutes"})


def get_settings() -> dict:
    data = read_data()
    stored = {
        k: v for k, v in (data.get("settings") or {}).items()
        if k not in RETIRED_SETTINGS
    }
    # An existing data.json without the performance keys is an installation
    # from before the presets: it keeps what it ran with (see
    # LEGACY_PERFORMANCE_DEFAULTS). No file at all is a new installation.
    defaults = {**SETTINGS_DEFAULTS, **LEGACY_PERFORMANCE_DEFAULTS} if data else SETTINGS_DEFAULTS
    return {**defaults, **stored}


def apply_performance_preset(name: str, *, only_lower: bool = False) -> dict:
    """Write a preset's performance values; returns the settings now stored.

    Never more aggressive than the preset: its values are written as they are.
    ``only_lower`` (the automatic protection) keeps any value the user had set
    even lower, so the switch can only ever reduce the load.
    """
    target = preset_values(name)
    current = get_settings()
    if only_lower:
        target = {key: min(value, int(current.get(key, value))) for key, value in target.items()}
    new_settings = {**current, **target}
    save_settings(new_settings)
    return new_settings


def update_data(changes: dict, *, _new_install_seed: bool = True):
    """Apply top-level changes to data.json under the shared lock.

    Read-modify-write inside the lock rather than "read it somewhere, mutate,
    write it back": the domain recovery loop writes ``domain`` from a background
    thread while an administrator may be saving libraries, and the last writer
    would otherwise drop the other's key wholesale.

    This lives here, not in the domain router, because ``app.core`` writes the
    domain too and importing a router from ``app.core`` would be a cycle.
    """
    with _data_lock():
        try:
            data = _read_json_object(DATA_FILE)
        except FileNotFoundError:
            # A new installation: its first save pins the default preset, so
            # it is not later mistaken for one that predates the presets.
            data = ({"settings": preset_values(DEFAULT_PERFORMANCE_PRESET)}
                    if _new_install_seed else {})
        except ValueError:
            data = None
        if data is not None:
            if "domain" in changes:
                _retire_previous_domain(data, changes["domain"])
            data.update(changes)
            atomic_write_text(DATA_FILE, json.dumps(data))
            return
    # Corrupt. Set aside by read_data, which takes the lock itself - released
    # here first, because two FileLock objects on one path do not nest - and
    # then applied to what is left. Raises rather than write over a file that
    # could not be moved. Not a new installation, so nothing is seeded.
    read_data(strict=True)
    update_data(changes, _new_install_seed=False)


def save_settings(new_settings: dict):
    update_data({"settings": new_settings})


def download_dir(default=None) -> Path:
    """Current fallback destination, resolved on each operation."""
    return Path(read_data().get("download_dir") or (VIDEOS_DIR if default is None else default))
