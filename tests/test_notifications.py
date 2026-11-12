"""In-app notifications, and who is allowed to see or decide which request.

The panel is single-user: the local user (bound to its jf_user row, migration
12) files requests and approves them, and its bell shows its own notifications
plus the panel-wide ones (user_id NULL). What is still worth pinning:

* the queue's route guards still refuse a user without the permission
  (``restrict_local_user`` takes it away);
* a request or notification belonging to any other jf_user row (a legacy
  account left in an old database) is never shown, marked or deleted;
* writes still need to come from the panel's own origin.
"""

from app import db
from app.auth.permissions import ALL_PERMISSIONS, Permission
from app.requests import models as request_models, notify
from tests.conftest import do_setup, make_user, restrict_local_user
from tests.test_requests import FILM_BODY, _create, _login, _user


def _legacy_request(external_id="123", title="Test Film"):
    """A request owned by a legacy account row, not by the local user."""
    other = make_user("legacy", "jf-legacy-id", int(Permission.REQUEST))
    request, _ = request_models.create(
        source="streamingcommunity", media_type="film", external_id=external_id,
        title=title, audio_languages=["ita"], subtitle_languages=[],
        requested_by=other.id,
    )
    return other, request


# ── Scoping ────────────────────────────────────────────────────────────────────

def test_my_requests_shows_only_mine(client, admin_credentials, source, stub_jobs):
    do_setup(client, admin_credentials)
    _legacy_request("999", "Altro Film")
    csrf = _login(client, _user(client))
    _create(client, csrf, FILM_BODY)

    mine = client.get("/api/requests/mine").json()

    assert [r["title"] for r in mine] == ["Test Film"]


def test_a_requester_cannot_read_someone_elses_request(
    client, admin_credentials, source, stub_jobs, monkeypatch
):
    """Without MANAGE_REQUESTS, a request you do not subscribe to is a 404."""
    do_setup(client, admin_credentials)
    _, request = _legacy_request()
    restrict_local_user(monkeypatch, Permission.REQUEST)

    assert client.get(f"/api/requests/{request.id}").status_code == 404


def test_the_local_user_reads_any_request_as_approver(
    client, admin_credentials, source, stub_jobs
):
    do_setup(client, admin_credentials)
    _, request = _legacy_request()

    assert client.get(f"/api/requests/{request.id}").status_code == 200


def test_a_requester_cannot_see_the_queue(client, admin_credentials, source, stub_jobs, monkeypatch):
    do_setup(client, admin_credentials)
    restrict_local_user(monkeypatch, Permission.REQUEST)

    assert client.get("/api/requests").status_code == 403


def test_a_requester_cannot_approve_or_deny(
    client, admin_credentials, source, stub_jobs, monkeypatch
):
    do_setup(client, admin_credentials)
    csrf = _login(client, _user(client))
    request_id = _create(client, csrf, FILM_BODY).json()["request"]["id"]
    restrict_local_user(monkeypatch, Permission.REQUEST)

    assert client.post(
        f"/api/requests/{request_id}/approve", headers={"X-CSRF-Token": csrf}
    ).status_code == 403
    assert client.post(
        f"/api/requests/{request_id}/deny",
        json={"reason": "no"},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 403
    assert stub_jobs == []


def test_settings_rights_do_not_open_the_queue(
    client, admin_credentials, source, stub_jobs, monkeypatch
):
    """Permissions are independent rather than hierarchical."""
    do_setup(client, admin_credentials)
    restrict_local_user(
        monkeypatch, Permission.MANAGE_SETTINGS | Permission.MANAGE_USERS | Permission.MANAGE_FILES
    )

    assert client.get("/api/domain/settings").status_code == 200
    assert client.get("/api/requests").status_code == 403


def test_card_status_shows_only_what_the_user_may_know(
    client, admin_credentials, source, stub_jobs, monkeypatch
):
    do_setup(client, admin_credentials)
    _legacy_request()
    status_body = {"source": "streamingcommunity", "external_ids": ["123"]}

    approvers_view = client.post("/api/requests/status", json=status_body).json()
    restrict_local_user(monkeypatch, Permission.REQUEST)
    requesters_view = client.post("/api/requests/status", json=status_body).json()

    assert requesters_view == {}
    assert approvers_view["123"]["status"] == "pending"


# ── Notifications ──────────────────────────────────────────────────────────────

def test_approvers_are_notified_of_a_new_request(client, admin_credentials, source, stub_jobs):
    """The local user is the approver, so its own request lands on its bell."""
    do_setup(client, admin_credentials)
    me = _user(client, "boss", ALL_PERMISSIONS)
    csrf = _login(client, me)

    _create(client, csrf, FILM_BODY)

    events = [n["event"] for n in notify.list_for_user(me.id)]
    assert notify.REQUEST_CREATED in events
    bell = [n["event"] for n in client.get("/api/notifications").json()["items"]]
    assert notify.REQUEST_CREATED in bell


def test_a_disabled_legacy_account_is_not_notified(client, admin_credentials, source, stub_jobs):
    """Only enabled rows holding MANAGE_REQUESTS are approvers."""
    do_setup(client, admin_credentials)
    disabled = make_user("old-boss", "jf-old-boss-id", int(ALL_PERMISSIONS), enabled=False)
    csrf = _login(client, _user(client))

    _create(client, csrf, FILM_BODY)

    assert disabled.id not in notify.approver_ids()
    addressed = db.query_one(
        "SELECT COUNT(*) AS n FROM jf_notification WHERE user_id = ?", (disabled.id,))["n"]
    assert addressed == 0


def test_notifications_are_listed_with_an_unread_count(
    client, admin_credentials, source, stub_jobs
):
    do_setup(client, admin_credentials)
    csrf = _login(client, _user(client))
    _create(client, csrf, FILM_BODY)
    _create(client, csrf, FILM_BODY)  # joins the open one

    payload = client.get("/api/notifications").json()

    assert payload["unread"] == len(payload["items"]) >= 1
    assert all(item["read_at"] is None for item in payload["items"])


def test_the_bell_includes_panel_wide_notifications(client):
    """Direct downloads notify the panel (user_id NULL): the one user sees them."""
    notify.notify(notify.DOWNLOAD_COMPLETED, "Film scaricato", [], panel_wide=True)

    payload = client.get("/api/notifications").json()

    assert [n["message"] for n in payload["items"]] == ["Film scaricato"]
    assert payload["unread"] == 1


def test_marking_read_only_touches_your_own_notifications(
    client, admin_credentials, source, stub_jobs
):
    do_setup(client, admin_credentials)
    other, request = _legacy_request()
    notify.notify(notify.REQUEST_APPROVED, "Per l'account legacy", [other.id], request.id)
    others_ids = [n["id"] for n in notify.list_for_user(other.id)]
    assert others_ids
    csrf = _login(client, _user(client))
    _create(client, csrf, {**FILM_BODY, "external_id": "555", "title": "Mio"})
    mine = client.get("/api/notifications").json()["items"]
    assert mine and not {n["id"] for n in mine} & set(others_ids)

    response = client.post(
        "/api/notifications/read",
        json={"ids": [n["id"] for n in mine] + others_ids},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.json()["unread"] == 0
    assert notify.unread_count(other.id) == len(others_ids)


def test_notification_writes_refuse_a_cross_site_origin(client):
    """No session to require any more: what stops a foreign page from clearing
    the bell is the Origin / Sec-Fetch-Site guard."""
    foreign = {"Origin": "https://evil.example"}
    assert client.post("/api/notifications/read", json={}, headers=foreign).status_code == 403
    assert client.post("/api/notifications/delete", json={}, headers=foreign).status_code == 403
    cross_site = {"Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/notifications/delete", json={}, headers=cross_site).status_code == 403


# ── Deleting ───────────────────────────────────────────────────────────────────

def _two_notifications(client):
    csrf = _login(client, _user(client))
    _create(client, csrf, {**FILM_BODY, "external_id": "123", "title": "Film"})
    _create(client, csrf, {**FILM_BODY, "external_id": "999", "title": "Altro Film"})
    return csrf


def test_deleting_one_notification_leaves_the_rest(
    client, admin_credentials, source, stub_jobs
):
    do_setup(client, admin_credentials)
    csrf = _two_notifications(client)
    mine = client.get("/api/notifications").json()["items"]
    assert len(mine) >= 2

    response = client.post(
        "/api/notifications/delete",
        json={"ids": [mine[0]["id"]]},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.json()["deleted"] == 1
    remaining = [n["id"] for n in client.get("/api/notifications").json()["items"]]
    assert mine[0]["id"] not in remaining
    assert len(remaining) == len(mine) - 1


def test_deleting_without_ids_clears_the_bell(
    client, admin_credentials, source, stub_jobs
):
    do_setup(client, admin_credentials)
    csrf = _two_notifications(client)

    response = client.post("/api/notifications/delete", json={},
                           headers={"X-CSRF-Token": csrf})

    assert response.json()["deleted"] >= 1
    payload = client.get("/api/notifications").json()
    assert payload["items"] == []
    assert payload["unread"] == 0


def test_deleting_cannot_reach_someone_elses_notifications(
    client, admin_credentials, source, stub_jobs
):
    """Scoped exactly as marking read is: another row's ids are not matched,
    so the count reports what actually went rather than claiming success."""
    do_setup(client, admin_credentials)
    other, request = _legacy_request()
    notify.notify(notify.REQUEST_APPROVED, "Per l'account legacy", [other.id], request.id)
    others_ids = [n["id"] for n in notify.list_for_user(other.id)]
    assert others_ids

    response = client.post("/api/notifications/delete", json={"ids": others_ids})

    assert response.json()["deleted"] == 0
    assert len(notify.list_for_user(other.id)) == len(others_ids)


def test_clearing_the_bell_leaves_other_rows_alone(
    client, admin_credentials, source, stub_jobs
):
    """The no-ids form deletes everything the *caller* sees, not everything."""
    do_setup(client, admin_credentials)
    other, request = _legacy_request()
    notify.notify(notify.REQUEST_APPROVED, "Per l'account legacy", [other.id], request.id)
    others = len(notify.list_for_user(other.id))
    csrf = _two_notifications(client)

    client.post("/api/notifications/delete", json={}, headers={"X-CSRF-Token": csrf})

    assert len(notify.list_for_user(other.id)) == others
    assert client.get("/api/notifications").json()["items"] == []
