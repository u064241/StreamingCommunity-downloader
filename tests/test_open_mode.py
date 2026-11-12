"""Local mode: the panel is a single-user application with no login.

Formerly "open mode", one of two choices in a setup wizard; now the only mode.
No cookie, no session, no CSRF token, no setup step: every request that passes
the Host / Origin guard (tests/test_security_access.py) acts as the local user.
"""

from app.auth import models


def test_root_reachable_with_no_cookie_at_all(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200


def test_a_fresh_install_needs_no_setup(client):
    assert models.get_setting(models.SETTING_AUTH_MODE) is None
    assert client.get("/api/domain/settings").status_code == 200


def test_old_login_bookmarks_land_on_the_panel(client):
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_me_reports_the_local_permission_set(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["auth_enabled"] is False
    assert body["csrf_token"] is None
    # Every permission, the request queue included: the local user files
    # requests and approves them.
    assert set(body["user"]["permission_names"]) == {
        "DOWNLOAD", "REQUEST", "MANAGE_REQUESTS", "MANAGE_USERS",
        "MANAGE_SETTINGS", "MANAGE_FILES", "VIEW_LIBRARY",
    }
    # Bound to its jf_user row (migration 12), not the id-0 template.
    assert body["user"]["id"] > 0


def test_no_session_cookie_is_ever_set(client):
    response = client.get("/api/auth/me")
    assert "set-cookie" not in response.headers


def test_download_endpoint_reachable_without_any_session(client, stub_jobs):
    response = client.post("/api/download/film", json={"id": 1, "title": "Movie"})
    assert response.status_code == 202


def test_no_csrf_token_required_for_state_changing_requests(client, stub_jobs):
    """No session means no X-CSRF-Token either; same-origin writes go through."""
    response = client.post("/api/download/film", json={"id": 2, "title": "Another"})
    assert response.status_code == 202


def test_request_queue_is_reachable_by_the_local_user(client):
    response = client.get("/api/requests")
    assert response.status_code == 200
    assert response.json() == []
