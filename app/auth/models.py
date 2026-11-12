"""Panel settings stored in the database, and the shape of the local user.

The panel is single-user and has no accounts. The ``jf_user`` / ``jf_session``
tables created by earlier versions are left in place (no destructive
migration). ``jf_user`` holds one row the panel uses, the local user's
(migration 12, key ``'local'``), which the request queue references as
requester and approver; legacy account rows are kept but never created.
``get_user`` reads a row by id.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app import db
from app.auth import permissions as perms

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Settings (jf_setting: a generic key/value store) ───────────────────────────

# Legacy key: which login mode the setup wizard chose. Ignored now — the panel
# is always local and single-user — but older databases still carry it.
SETTING_AUTH_MODE = "auth_mode"


def get_setting(key: str, default: str | None = None) -> str | None:
    row = db.query_one("SELECT value FROM jf_setting WHERE key = ?", (key,))
    if row is not None:
        return row["value"]
    return default


def set_setting(key: str, value: str, conn: sqlite3.Connection | None = None):
    (conn or db.get_conn()).execute(
        "INSERT INTO jf_setting(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def setup_done() -> bool:
    """Always true: a local panel has nothing to set up before it can be used."""
    return True


def runtime_open_mode() -> bool:
    """Always true: the panel runs without accounts, as the one local user.

    Kept as a function because the MCP module still asks it to choose its
    account-less behaviour.
    """
    return True


# ── The user shape ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class User:
    id: int
    jellyfin_user_id: str
    username: str
    is_jellyfin_admin: bool
    permissions: int
    enabled: bool
    device_id: str
    created_at: str
    last_login_at: str | None

    @property
    def permission_names(self) -> list[str]:
        return perms.to_names(self.permissions)

    def has(self, *required, mode: str = "and") -> bool:
        return perms.has(self.permissions, *required, mode=mode)

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "permissions": self.permissions,
            "permission_names": self.permission_names,
            "enabled": self.enabled,
        }


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        jellyfin_user_id=row["jellyfin_user_id"],
        username=row["username"],
        is_jellyfin_admin=bool(row["is_jellyfin_admin"]),
        permissions=row["permissions"],
        enabled=bool(row["enabled"]),
        device_id=row["device_id"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def get_user(user_id: int) -> User | None:
    """A legacy account row, read-only. Nothing creates these any more."""
    row = db.query_one("SELECT * FROM jf_user WHERE id = ?", (user_id,))
    return _row_to_user(row) if row else None


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
