"""Migration 12: the local user's jf_user row.

The request queue stores requester and approver by jf_user id, so the one local
user needs a row there. The migration only inserts it: nothing is altered or
dropped, legacy rows survive, and running it on a database that already has
the row changes nothing.
"""

from app import db
from app.auth import deps
from app.auth.permissions import ALL_PERMISSIONS


def _local_rows():
    return db.query("SELECT * FROM jf_user WHERE jellyfin_user_id = ?", (db.LOCAL_USER_KEY,))


def test_a_fresh_database_has_the_local_user_with_every_permission():
    (row,) = _local_rows()
    assert row["permissions"] == int(ALL_PERMISSIONS)
    assert row["enabled"] == 1
    assert row["username"] == "Pannello"


def test_the_local_user_is_bound_to_its_row(client):
    (row,) = _local_rows()
    user = deps.local_user()
    assert user.id == row["id"]
    assert deps.is_local_user(user)
    assert client.get("/api/auth/me").json()["user"]["id"] == row["id"]


def test_upgrading_keeps_legacy_rows_and_adds_the_local_one(tmp_path):
    """From schema v11 with a legacy account and a request of theirs."""
    full = db.MIGRATIONS
    db.configure(tmp_path / "old.db")
    try:
        db.MIGRATIONS = full[:11]
        db.run_migrations()
        db.execute(
            "INSERT INTO jf_user(jellyfin_user_id, username, device_id, created_at) "
            "VALUES('jf-old', 'old', '', '2024-01-01')"
        )
        legacy_id = db.query_one("SELECT id FROM jf_user WHERE jellyfin_user_id = 'jf-old'")["id"]
    finally:
        db.MIGRATIONS = full

    db.run_migrations()

    assert db.query_one("PRAGMA user_version")[0] == len(db.MIGRATIONS) == 12
    assert db.query_one("SELECT username FROM jf_user WHERE id = ?", (legacy_id,))["username"] == "old"
    (local,) = _local_rows()
    assert local["id"] != legacy_id
    assert deps.local_user().id == local["id"]


def test_the_migration_is_idempotent():
    """INSERT OR IGNORE on the UNIQUE key: re-applying it adds nothing."""
    for statement in db.MIGRATIONS[11]:
        db.execute(statement)
    assert len(_local_rows()) == 1


def test_permissions_come_from_the_code_not_the_row():
    """Tampering with the row cannot widen or narrow what the local user may do."""
    db.execute("UPDATE jf_user SET permissions = 0 WHERE jellyfin_user_id = ?", (db.LOCAL_USER_KEY,))
    assert deps.local_user().permissions == deps.LOCAL_PERMISSIONS
