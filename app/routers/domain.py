import asyncio
import json
import logging
import importlib.util
import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from app import config
from app.auth.deps import require
from app.auth.permissions import Permission
from app.config import get_settings, save_settings
from app.core import animeunity, domain_recovery, home, hosts, metadata, naming
from app.core.paths import validate_library_path
from app.core.page import get_domain_version

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/domain", tags=["domain"])

CAN_MANAGE = [Depends(require(Permission.MANAGE_SETTINGS))]

# Reading the current domain is not a settings operation: every signed-in user
# needs it to build poster URLs. Anyone who can search can already see it.
CAN_READ_DOMAIN = [
    Depends(require(Permission.REQUEST, Permission.DOWNLOAD, Permission.MANAGE_SETTINGS, mode="or"))
]


def _read_data() -> dict:
    # config.DATA_FILE is resolved at call time, not bound at import: the tests
    # point it at a temp file, and a stale binding here would have this module
    # reading one file while app.config.save_settings wrote another.
    try:
        with open(config.DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"domain": ""}


_update_data = config.update_data


def _domain_host(value: str) -> str:
    """Accept either a hostname or a pasted HTTP(S) URL; store only its host."""
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Domain cannot be empty")

    try:
        if "://" in value:
            parsed = urlsplit(value)
            if parsed.scheme.lower() not in {"http", "https"}:
                raise ValueError("Use an HTTP or HTTPS URL")
        else:
            parsed = urlsplit(f"//{value}")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Enter a valid domain or HTTP(S) URL")
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if ":" in hostname:  # Preserve IPv6 bracket syntax if a port is supplied.
        hostname = f"[{hostname}]"
    return f"{hostname}:{port}" if port is not None else hostname


class DomainUpdate(BaseModel):
    domain: str


@router.get("", dependencies=CAN_READ_DOMAIN)
async def get_domain():
    data = _read_data()
    domain = data.get("domain", "")
    version = None
    valid = False
    if domain:
        try:
            version = await asyncio.to_thread(get_domain_version, domain)
            valid = True
        except Exception:
            valid = False

    if not valid and get_settings().get("domain_auto_check_enabled", True):
        # The domain is dead, so start looking for its replacement now rather
        # than waiting up to six hours for the periodic check. This fires on
        # every page load of every user while the panel is broken, which is
        # exactly what the throttle inside run_check() is there to absorb.
        asyncio.create_task(asyncio.to_thread(domain_recovery.run_check))

    return {"domain": domain, "valid": valid, "version": version}


@router.get("/candidate", dependencies=CAN_READ_DOMAIN)
def get_domain_candidate():
    """The replacement domain waiting to be confirmed, if there is one.

    Readable by anyone who can read the domain: a hostname is not a secret, and
    a requester staring at a broken search benefits from being told the source
    has moved even though they cannot be the one to apply it.
    """
    return domain_recovery.recovery_state()


@router.post("/check", dependencies=CAN_MANAGE)
async def check_domain_now():
    return await asyncio.to_thread(domain_recovery.run_check, True)


@router.post("/candidate/apply", dependencies=CAN_MANAGE)
async def apply_domain_candidate(body: DomainUpdate):
    """Adopt the pending candidate.

    The domain in the body is a confirmation token, not an instruction: it must
    match what the server already found, and the value written is the server's
    own. Taking the host from the request would be exactly the "the source
    domain never comes from the client" rule this endpoint looks like it breaks.
    """
    candidate = domain_recovery.pending()
    if not candidate:
        raise HTTPException(status_code=409, detail="Nessun dominio proposto")
    if body.domain.strip() != candidate["host"]:
        raise HTTPException(
            status_code=409,
            detail="Il dominio proposto è cambiato: ricarica la pagina",
        )

    try:
        version = await asyncio.to_thread(domain_recovery.apply_candidate, candidate["host"])
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"domain": candidate["host"], "version": version, "valid": True}


@router.post("/candidate/dismiss", dependencies=CAN_MANAGE)
def dismiss_domain_candidate():
    domain_recovery.clear_pending()
    return {"ok": True}


class HostsUpdate(BaseModel):
    """Either field may be omitted; an empty string resets it to automatic."""

    animeunity: str | None = None
    video_host: str | None = None


def _hosts_state() -> dict:
    data = _read_data()
    return {
        "animeunity": config.configured_animeunity_host(),
        "animeunity_saved": (data.get("animeunity_domain") or "").strip(),
        "animeunity_default": config.ANIMEUNITY_DEFAULT_HOST,
        "video_host": config.configured_video_host(),
    }


@router.get("/hosts", dependencies=CAN_MANAGE)
def get_hosts():
    return _hosts_state()


@router.put("/hosts", dependencies=CAN_MANAGE)
async def set_hosts(body: HostsUpdate):
    """Save the AnimeUnity host and the video host override.

    Each value is normalised to a bare hostname and verified against the
    service it claims to be before it is stored: a host saved here decides
    where the panel sends requests, so a typo should fail now, in front of the
    person who typed it, not as a broken download hours later.
    """
    changes = {}
    for field, key, verify in (
        ("animeunity", "animeunity_domain", hosts.verify_animeunity),
        ("video_host", "video_host", hosts.verify_video_host),
    ):
        value = getattr(body, field)
        if value is None:
            continue
        if not value.strip():
            changes[key] = ""
            continue
        try:
            host = hosts.normalize_host(value)
            await asyncio.to_thread(verify, host)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        changes[key] = host

    if changes:
        _update_data(changes)
    if "animeunity_domain" in changes:
        animeunity.forget_csrf()
        home.clear_cache("animeunity")
    return _hosts_state()


class LibraryItem(BaseModel):
    type: Literal["film", "tv", "anime"]
    path: str


class LibrariesUpdate(BaseModel):
    libraries: list[LibraryItem]
    excluded_folders: list[str]


@router.get("/libraries", dependencies=CAN_MANAGE)
def get_libraries():
    data = _read_data()
    return {
        "libraries": data.get("libraries", []),
        "excluded_folders": data.get("excluded_folders", []),
    }


@router.put("/libraries", dependencies=CAN_MANAGE)
def set_libraries(body: LibrariesUpdate):
    # Deduplicate: last entry per type wins
    seen: dict[str, dict] = {}
    for lib in body.libraries:
        # Rejected here rather than at download time. A host path saved on a
        # Linux deployment is accepted by every layer below — a backslash is a
        # legal filename character there — and only surfaces once FFmpeg is
        # handed the name at the end of a finished download.
        try:
            path = validate_library_path(lib.path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        seen[lib.type] = {"type": lib.type, "path": path}
    _update_data({
        "libraries": list(seen.values()),
        "excluded_folders": body.excluded_folders,
    })
    return {"ok": True}


class SettingsUpdate(BaseModel):
    """Every field optional: each settings section sends only what it owns.

    The merge in set_app_settings fills in the rest from what is stored, so the
    performance section and the recovery section can save independently without
    either one having to re-send the other's values and risk overwriting a
    change made seconds earlier in the same modal.
    """

    max_concurrent_downloads: int | None = None
    max_segment_workers: int | None = None
    # Global cap on segment requests to the source (app/core/m3u8.py) and the
    # hourly page-request budget (app/core/source_budget.py). Both apply at
    # once (the cap from the next segment request); SC_SOURCE_MAX_CONNECTIONS /
    # SC_SOURCE_REQUESTS_PER_HOUR win when set. Never above the ceilings in
    # app.config.
    source_max_connections: int | None = None
    source_requests_per_hour: int | None = None
    naming_templates: dict[str, str] | None = None
    domain_auto_check_enabled: bool | None = None
    domain_auto_apply: bool | None = None
    domain_check_interval_minutes: int | None = None
    update_check_enabled: bool | None = None
    # Literal rather than str + a validator: pydantic answers 422 for anything
    # off the list on its own. tests/test_output_container.py pins these two
    # against app.core.container, so the table and the API cannot drift.
    output_container: Literal["mkv", "mp4"] | None = None
    subtitle_mode: Literal["embed", "external"] | None = None
    video_conversion: Literal["copy", "h264", "hevc"] | None = None
    video_encoder: Literal["auto", "nvenc", "qsv", "amf", "cpu"] | None = None
    video_quality: Literal["high", "balanced", "compact"] | None = None
    video_cpu_fallback: bool | None = None
    mcp_enabled: bool | None = None
    confirmations_enabled: bool | None = None

    @field_validator("naming_templates")
    @classmethod
    def _check_templates(cls, value):
        """Validate templates before they are stored in application settings."""
        if value is None:
            return None
        return {slot: naming.validate(slot, template) for slot, template in value.items()}


# Deliberately exclude credentials, MCP tokens, notification URLs and the
# database. This backup is intended to be portable and safe to inspect.
_BACKUP_KEYS = {
    "domain", "animeunity_domain", "video_host", "download_dir",
    "libraries", "excluded_folders", "settings",
}


class SettingsBackup(BaseModel):
    schema_version: Literal[1] = Field(alias="schema")
    data: dict


def _safe_backup_data() -> dict:
    data = _read_data()
    return {key: data[key] for key in _BACKUP_KEYS if key in data}


@router.get("/diagnostics", dependencies=CAN_MANAGE)
def diagnostics():
    """Return bounded, local, read-only health information for the panel."""
    ffmpeg = shutil.which("ffmpeg")
    python_info = {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
    }
    optional = {
        name: bool(importlib.util.find_spec(name))
        for name in ("fastapi", "uvicorn", "httpx", "filelock", "pydantic")
    }
    paths = {
        "data": {"path": str(config.DATA_FILE.resolve()), "exists": config.DATA_FILE.exists()},
        "database": {"path": str(config.DB_FILE.resolve()), "exists": config.DB_FILE.exists()},
        "videos": {"path": str(config.download_dir().resolve()), "exists": config.download_dir().exists()},
        "temporary": {"path": str(config.TMP_DIR.resolve()), "exists": config.TMP_DIR.exists()},
    }
    domain = config.configured_domain()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "application": {"version": __import__("app").__version__, "python": python_info},
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "paths": paths,
        "domain": {"configured": bool(domain), "host": domain},
        "tools": {"ffmpeg": {"available": bool(ffmpeg), "path": ffmpeg}, "curl": bool(shutil.which("curl"))},
        "packages": optional,
        "writable": {
            "data": config.DATA_FILE.parent.exists() and os_access(config.DATA_FILE.parent),
            "temporary": config.TMP_DIR.exists() and os_access(config.TMP_DIR),
        },
    }


def os_access(path):
    import os
    return os.access(path, os.W_OK)


@router.get("/settings/backup", dependencies=CAN_MANAGE)
def export_settings_backup():
    payload = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(), "data": _safe_backup_data()}
    return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="streamingcommunity-settings.json"'})


def _restored_settings(raw) -> dict:
    """The ``settings`` section of a backup, through the checks of PUT /settings."""
    if not isinstance(raw, dict):
        raise ValueError("deve essere un oggetto")
    # A backup taken by an older version may still carry a setting that has
    # since been removed: skipped, not a reason to refuse the whole section.
    raw = {k: v for k, v in raw.items() if k not in config.RETIRED_SETTINGS}
    unknown = sorted(set(raw) - set(SettingsUpdate.model_fields))
    if unknown:
        raise ValueError(f"chiavi non supportate: {', '.join(unknown)}")
    try:
        parsed = SettingsUpdate.model_validate(raw)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in error["loc"]) or "?" for error in exc.errors()})
        raise ValueError(f"valori non validi: {', '.join(fields)}") from exc
    provided = {k: v for k, v in parsed.model_dump().items() if v is not None}
    # A backup from before the ceilings may carry 6 connections: lowered to
    # the ceiling rather than refusing the whole section. Never raised.
    for field, ceiling in _SOURCE_CEILINGS:
        value = provided.get(field)
        if value is not None and value > ceiling:
            provided[field] = ceiling
    for field, low, high in _SETTING_RANGES:
        value = provided.get(field)
        if value is not None and not low <= value <= high:
            raise ValueError(f"{field} deve essere tra {low} e {high}")
    if provided.get("video_conversion", "copy") != "copy":
        from app.core.transcode import select_backend, TranscodeError
        try:
            select_backend({**config.SETTINGS_DEFAULTS, **provided})
        except (TranscodeError, OSError) as exc:
            raise ValueError(str(exc)) from exc
    return provided


def _restored_host(verify):
    def check(raw) -> str:
        if not isinstance(raw, str):
            raise ValueError("deve essere una stringa")
        if not raw.strip():
            return ""  # same meaning as in PUT /hosts: back to automatic
        try:
            host = hosts.normalize_host(raw)
            verify(host)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
        return host
    return check


def _restored_domain(raw) -> str:
    if not isinstance(raw, str):
        raise ValueError("deve essere una stringa")
    if not raw.strip():
        return ""
    try:
        return _domain_host(raw)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


def _restored_download_dir(raw) -> str:
    if not isinstance(raw, str):
        raise ValueError("deve essere una stringa")
    if not raw.strip():
        return ""  # falls back to VIDEOS_DIR, see config.download_dir()
    return str(_confined_download_dir(raw))


def _restored_libraries(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("deve essere un elenco")
    seen: dict[str, dict] = {}
    for entry in raw:
        try:
            lib = LibraryItem.model_validate(entry)
        except ValidationError as exc:
            raise ValueError("voce di libreria non valida") from exc
        seen[lib.type] = {"type": lib.type, "path": validate_library_path(lib.path)}
    return list(seen.values())


def _restored_excluded_folders(raw) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("deve essere un elenco di nomi")
    return raw


# One validator per backup key, each the same check its own PUT endpoint
# applies. A key in _BACKUP_KEYS with no entry here cannot be restored.
_RESTORE_VALIDATORS = {
    "domain": _restored_domain,
    "animeunity_domain": _restored_host(lambda host: hosts.verify_animeunity(host)),
    "video_host": _restored_host(lambda host: hosts.verify_video_host(host)),
    "download_dir": _restored_download_dir,
    "libraries": _restored_libraries,
    "excluded_folders": _restored_excluded_folders,
    "settings": _restored_settings,
}


@router.post("/settings/restore", dependencies=CAN_MANAGE)
def restore_settings_backup(body: SettingsBackup):
    """Restore a settings backup.

    A backup is a file somebody uploads, so it is input like any other: every
    value goes through the validator of the endpoint that normally sets it.
    All of them run before anything is written, and one rejected field
    rejects the whole file — a half-applied backup is worse than none.
    """
    if not isinstance(body.data, dict):
        raise HTTPException(status_code=400, detail="Backup non valido")
    unknown = set(body.data) - _BACKUP_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail="Il backup contiene campi non supportati")

    restored, rejected = {}, {}
    for key, value in body.data.items():
        validator = _RESTORE_VALIDATORS.get(key)
        try:
            if validator is None:
                raise ValueError("campo non ripristinabile")
            restored[key] = validator(value)
        except ValueError as exc:
            rejected[key] = str(exc)
    if rejected:
        logger.warning("Settings restore refused, invalid fields: %s", sorted(rejected))
        raise HTTPException(
            status_code=400,
            detail="Backup non valido. Campi rifiutati: "
                   + "; ".join(f"{key} ({reason})" for key, reason in sorted(rejected.items())),
        )

    # Merge, so a backup from an older version cannot erase unrelated state.
    config.update_data(restored)
    if "animeunity_domain" in restored:
        animeunity.forget_csrf()
        home.clear_cache("animeunity")
    if "download_dir" in restored:
        from app.routers.files import _disk_usage_cache
        _disk_usage_cache["data"] = None
    metadata.clear_cache()
    return {"ok": True, "restored": sorted(restored)}

class NamingPreviewRequest(BaseModel):
    templates: dict[str, str]


@router.post("/settings/naming-preview", dependencies=CAN_MANAGE)
def preview_naming(body: NamingPreviewRequest):
    """What each template would produce, rendered by the real engine.

    Server-side rather than reimplemented in JavaScript: two renderers drift,
    and this way an invalid template shows its actual validation error while it
    is being typed instead of at save time.
    """
    result = {}
    for slot, template in body.templates.items():
        if slot not in naming.SLOT_TOKENS:
            continue
        try:
            naming.validate(slot, template)
            result[slot] = {"preview": naming.preview(slot, template), "error": None}
        except ValueError as exc:
            result[slot] = {"preview": None, "error": str(exc)}
    return {"slots": result, "tokens": {s: list(t) for s, t in naming.SLOT_TOKENS.items()}}


@router.get("/settings/naming-defaults", dependencies=CAN_MANAGE)
def naming_defaults():
    return {"templates": dict(naming.DEFAULT_TEMPLATES),
            "tokens": {s: list(t) for s, t in naming.SLOT_TOKENS.items()}}


@router.get("/settings", dependencies=CAN_MANAGE)
def get_app_settings():
    # naming_templates is always returned complete, defaults filled in: the UI
    # renders nine fields and a partially stored dict would leave some blank,
    # which reads as "no rule" rather than "the default rule".
    return {**get_settings(), "naming_templates": naming.templates()}


# Range checks, as (field, low, high). A settings key with no sensible bounds
# is simply absent here.
_SETTING_RANGES = (
    ("max_concurrent_downloads", 1, 32),
    ("max_segment_workers", 1, 128),
    ("source_max_connections", 1, config.SOURCE_MAX_CONNECTIONS_CEILING),
    ("source_requests_per_hour", 1, config.SOURCE_REQUESTS_PER_HOUR_CEILING),
    # Floored well above the throttle in domain_recovery: a check any more often
    # than this is hammering somebody else's page for a domain that rotates
    # every few weeks.
    ("domain_check_interval_minutes", 1, 1440),
)
_SOURCE_CEILINGS = (
    ("source_max_connections", config.SOURCE_MAX_CONNECTIONS_CEILING),
    ("source_requests_per_hour", config.SOURCE_REQUESTS_PER_HOUR_CEILING),
)


@router.put("/settings", dependencies=CAN_MANAGE)
def set_app_settings(body: SettingsUpdate):
    # save_settings() replaces the whole `settings` dict rather than merging, so
    # every key the caller did not send has to be carried over here or it is
    # lost. Merging over get_settings() makes that structural: a key added to
    # SETTINGS_DEFAULTS later survives a PUT from an older client without
    # anyone having to remember to re-emit it.
    provided = {k: v for k, v in body.model_dump().items() if v is not None}

    for field, low, high in _SETTING_RANGES:
        value = provided.get(field)
        if value is not None and not low <= value <= high:
            raise HTTPException(
                status_code=400, detail=f"{field} must be between {low} and {high}"
            )

    new_settings = {**get_settings(), **provided}
    if new_settings.get("video_conversion", "copy") != "copy" and any(
        key in provided for key in ("video_conversion", "video_encoder", "video_quality", "video_cpu_fallback")
    ):
        from app.core.transcode import select_backend, TranscodeError
        try:
            select_backend(new_settings)
        except (TranscodeError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_settings(new_settings)
    _sync_job_limit(new_settings)
    return new_settings


@router.put("", dependencies=CAN_MANAGE)
async def set_domain(body: DomainUpdate):
    requested_domain = _domain_host(body.domain)

    # The Telegram channel is authoritative.  A manually entered domain may
    # still be valid for a short time after a rotation, so do the forced
    # recovery check before accepting it.  If the channel publishes a verified
    # replacement, run_check() applies it and that value must win over the
    # value in the form.  When Telegram is unavailable, keep the normal manual
    # verification path as a safe fallback.
    try:
        recovery = await asyncio.to_thread(domain_recovery.run_check, True)
    except Exception as exc:
        logger.warning("Telegram domain check during save failed: %s", exc)
        recovery = {"applied": False}

    domain = config.configured_domain() if recovery.get("applied") else requested_domain
    try:
        version = await asyncio.to_thread(get_domain_version, domain)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _update_data({"domain": domain})
    home.clear_cache("streamingcommunity")
    metadata.clear_cache()
    return {"domain": domain, "version": version, "valid": True}


class DownloadDirectoryUpdate(BaseModel):
    path: str


# The download folder is the root of the file manager: whatever is under it can
# be listed, streamed, renamed and deleted from the browser. Pointed at the
# folder that holds panel.db it served the hook and notification secrets to
# anyone allowed to browse; pointed at a system folder it offered that for deletion.
_POSIX_SYSTEM_DIRS = (
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys", "/usr",
)


def _system_dirs() -> list[Path]:
    if os.name != "nt":
        return [Path(p) for p in _POSIX_SYSTEM_DIRS]
    drive = os.environ.get("SystemDrive") or "C:"
    candidates = [
        os.environ.get("SystemRoot") or os.environ.get("windir") or f"{drive}\\Windows",
        os.environ.get("ProgramFiles") or f"{drive}\\Program Files",
        os.environ.get("ProgramFiles(x86)") or f"{drive}\\Program Files (x86)",
        os.environ.get("ProgramW6432") or f"{drive}\\Program Files",
    ]
    return [Path(c) for c in candidates if c]


def _same_or_inside(path: Path, parent: Path) -> bool:
    """``path`` is ``parent`` or below it. Case-insensitive where the OS is."""
    child = os.path.normcase(str(path))
    root = os.path.normcase(str(parent))
    return child == root or child.startswith(root.rstrip("\\/") + os.sep)


def _confined_download_dir(raw: str) -> Path:
    """Validate a download folder; return it resolved, or raise ValueError.

    Has no side effects — nothing is created — so settings/restore can run it
    before deciding whether to write anything.
    """
    from app import db

    path = Path(validate_library_path(raw)).expanduser()
    if not path.is_absolute():
        raise ValueError("Indica un percorso assoluto per la cartella download.")
    path = path.resolve()

    if path.parent == path:
        raise ValueError("La radice di un disco non può essere la cartella download.")
    for system_dir in _system_dirs():
        if _same_or_inside(path, system_dir.resolve()):
            raise ValueError("Una cartella di sistema non può essere la cartella download.")

    # The panel's own state must be neither the folder, nor inside it.
    for protected in (db.current_path(), config.DB_FILE, config.DATA_FILE, config.SCHEDULE_FILE):
        protected = Path(protected).resolve()
        if _same_or_inside(protected, path) or _same_or_inside(path, protected):
            raise ValueError(
                "La cartella download non può contenere i file di configurazione del pannello."
            )
    app_dir = Path(config.__file__).resolve().parent
    if _same_or_inside(path, app_dir) or _same_or_inside(app_dir, path):
        raise ValueError("La cartella download non può coincidere con la cartella dell'applicazione.")
    return path


@router.get("/download-directory", dependencies=CAN_MANAGE)
def get_download_directory():
    from app.windows_folder_picker import available
    return {"path": str(config.download_dir().resolve()), "can_browse": available()}


@router.put("/download-directory", dependencies=CAN_MANAGE)
def set_download_directory(body: DownloadDirectoryUpdate):
    import tempfile
    try:
        path = _confined_download_dir(body.path)
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
        path = path.resolve()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"Cartella download non utilizzabile: {exc}") from exc
    _update_data({"download_dir": str(path)})
    from app.routers.files import _disk_usage_cache
    _disk_usage_cache["data"] = None
    return {"path": str(path)}


@router.get("/logs", dependencies=CAN_MANAGE)
def get_logs():
    from app.log_view import buffer, install_log_buffer
    install_log_buffer()
    return {"lines": buffer.snapshot()}


@router.post("/download-directory/browse", dependencies=CAN_MANAGE)
async def browse_download_directory(body: DownloadDirectoryUpdate):
    from app.windows_folder_picker import choose_folder, available
    if not available():
        raise HTTPException(status_code=400, detail="La scelta cartella nativa è disponibile solo su Windows.")
    try:
        selected = await asyncio.to_thread(choose_folder, body.path or str(config.download_dir().resolve()))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"path": selected, "cancelled": selected is None}


@router.get("/hardware", dependencies=CAN_MANAGE)
async def hardware():
    from app.core.hardware_probe import probe
    return await asyncio.to_thread(probe)


def _sync_job_limit(settings: dict) -> None:
    """Make the running queue and the source gate follow the stored settings."""
    from app.jobs import job_manager
    job_manager.update_max_concurrent(settings["max_concurrent_downloads"])
    from app.core import m3u8
    m3u8.apply_source_cap()


def _source_limits() -> dict:
    """The source limits in force and whether an env override set them."""
    from app.core import m3u8, source_budget
    cap_env = m3u8.source_cap_env()
    budget_env = source_budget.env_limit()
    return {
        "source_cap_from_env": cap_env is not None,
        "effective_source_max_connections": m3u8._SOURCE_MAX_CONNECTIONS,
        "budget_from_env": budget_env is not None,
        "effective_requests_per_hour": source_budget.limit(),
    }


@router.post("/hardware/diagnose", dependencies=CAN_MANAGE)
async def diagnose_hardware():
    """Run the diagnosis and apply the preset it chooses (performance only)."""
    from app.core.hardware_probe import diagnose
    result = await asyncio.to_thread(diagnose, True)
    _sync_job_limit(config.get_settings())
    return result


class PresetRequest(BaseModel):
    preset: Literal["conservative", "balanced", "fast"] = "conservative"


@router.post("/performance-preset", dependencies=CAN_MANAGE)
def set_performance_preset(body: PresetRequest):
    """Write one preset's values as they are ("Ripristina Conservativo")."""
    settings = config.apply_performance_preset(body.preset)
    _sync_job_limit(settings)
    return {"preset": body.preset, "label": config.PERFORMANCE_PRESETS[body.preset]["label"],
            "settings": {key: settings.get(key) for key in config.PERFORMANCE_KEYS}}


@router.get("/source-protection", dependencies=CAN_MANAGE)
def source_protection():
    """The automatic protection: is the queue held, until when, and the preset."""
    from app.jobs import job_manager
    settings = config.get_settings()
    return {**job_manager.protection_status(),
            "preset": config.matching_preset(settings),
            "settings": {key: settings.get(key) for key in config.PERFORMANCE_KEYS},
            **_source_limits()}


@router.get("/video-encoders", dependencies=CAN_MANAGE)
async def video_encoders():
    from app.core.transcode import capabilities
    try:
        return await asyncio.to_thread(capabilities)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/video-encoders/refresh", dependencies=CAN_MANAGE)
async def refresh_video_encoders():
    from app.core.transcode import capabilities
    try:
        return await asyncio.to_thread(capabilities, True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
