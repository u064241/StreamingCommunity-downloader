"""Authorisation in the local, single-user panel.

There are no accounts: every request is the local user, who holds every
permission (app/auth/deps.py LOCAL_PERMISSIONS), the request queue's included.
Routes still declare what they need through ``require(...)``; the sweep at the
bottom fails when a new endpoint is added without that decision.
"""

import pytest
from fastapi.routing import APIRoute

from app.auth.deps import LOCAL_PERMISSIONS, LOCAL_USER
from app.auth.permissions import Permission


@pytest.fixture(autouse=True)
def _no_outbound_source(monkeypatch):
    """Keep the cases off the network: they assert who may reach an endpoint,
    not what it answers."""
    from app.core import animeunity, domain_recovery, home, page, tv

    monkeypatch.setattr(page, "search", lambda *a, **kw: [])
    monkeypatch.setattr(animeunity, "search", lambda *a, **kw: [])
    monkeypatch.setattr(home, "shelves", lambda *a, **kw: [])
    # The batch downloads and the domain check reach the source (and a public
    # page) on their own; none of that is what these cases are about.
    monkeypatch.setattr(page, "get_domain_version", lambda *a, **kw: "v1")
    monkeypatch.setattr(tv, "get_token", lambda *a, **kw: "token")
    monkeypatch.setattr(tv, "get_info_season", lambda *a, **kw: [])
    monkeypatch.setattr(tv, "get_season_numbers", lambda *a, **kw: [1])
    monkeypatch.setattr(animeunity, "get_episodes", lambda *a, **kw: [])
    monkeypatch.setattr(domain_recovery, "run_check", lambda *a, **kw: {"status": "ok"})


# (method, path, body) of endpoints the local user must be able to reach.
LOCAL_CASES = [
    ("POST", "/api/download/film", {"id": 1, "title": "x", "domain": "example.test"}),
    ("POST", "/api/download/season", {"tv_id": 1, "slug": "x", "tv_name": "x", "season": 1}),
    ("POST", "/api/download/series", {"tv_id": 1, "slug": "x", "tv_name": "x"}),
    ("POST", "/api/download/anime-all", {"anime_id": "1", "anime_name": "x"}),
    ("GET", "/api/search?q=abc&domain=example.test", None),
    ("GET", "/api/home", None),
    ("GET", "/api/domain/settings", None),
    ("GET", "/api/download-hooks", None),
    ("GET", "/api/domain/settings/naming-defaults", None),
    ("POST", "/api/domain/settings/naming-preview", {"templates": {"film_file": "{title}"}}),
    ("POST", "/api/download-hooks", {"name": "x", "url": "http://x/y"}),
    ("GET", "/api/metadata/tv/1", None),
    ("POST", "/api/domain/check", None),
    ("POST", "/api/domain/candidate/dismiss", None),
    ("GET", "/api/domain/candidate", None),
    ("POST", "/api/files/rename", {"path": "nope", "new_name": "other"}),
    ("GET", "/api/files", None),
    ("POST", "/api/download/does-not-exist/fire", None),
    ("POST", "/api/download/batch/does-not-exist/cancel", None),
    ("GET", "/api/mcp/status", None),
    ("POST", "/api/mcp/token", None),
    ("DELETE", "/api/mcp/token", None),
]


@pytest.mark.parametrize("case", LOCAL_CASES, ids=lambda c: f"{c[0]}-{c[1].split('?')[0]}")
def test_the_local_user_reaches_every_single_user_endpoint(client, case, stub_jobs):
    method, path, body = case
    response = client.request(method, path, json=body)
    # The call may still fail on its own merits (no such job, unreachable
    # source); what matters is that authorisation is not what stopped it.
    assert response.status_code != 403, f"{method} {path} refused to the local user"


def test_the_request_queue_belongs_to_the_local_user(client):
    """A request row references its requester in jf_user; the local user has a
    row there (migration 12), so the queue is reachable rather than refused."""
    assert client.get("/api/requests").status_code == 200
    assert client.get("/api/requests/mine").status_code == 200


def test_cancelling_a_batch_with_its_files_is_allowed_locally(client):
    """Deleting files takes MANAGE_FILES, which the local user holds."""
    response = client.post("/api/download/batch/does-not-exist/cancel?delete_files=true")
    assert response.status_code == 404


def test_the_local_permission_set():
    assert LOCAL_USER.permissions == LOCAL_PERMISSIONS
    for permission in Permission:
        if permission is not Permission.NONE:
            assert LOCAL_USER.has(permission), permission.name


def test_require_still_refuses_a_user_without_the_permission(monkeypatch):
    """The guard itself is not a no-op: a stand-in local user missing a bit is
    refused with 403, so a route's declared permission keeps meaning."""
    import dataclasses

    from fastapi import FastAPI, HTTPException
    from starlette.requests import Request

    from app.auth import deps

    restricted = dataclasses.replace(LOCAL_USER, permissions=int(Permission.VIEW_LIBRARY))
    monkeypatch.setattr(deps, "LOCAL_USER", restricted)
    request = Request({"type": "http", "method": "POST", "path": "/x", "headers": [],
                       "query_string": b"", "app": FastAPI()})
    with pytest.raises(HTTPException) as refused:
        deps.require(Permission.MANAGE_FILES)(request)
    assert refused.value.status_code == 403
    assert deps.require(Permission.VIEW_LIBRARY)(request).permissions == int(Permission.VIEW_LIBRARY)


def test_user_management_and_login_endpoints_are_gone(client):
    for method, path in [
        ("GET", "/api/users"),
        ("GET", "/api/auth/status"),
        ("POST", "/api/auth/setup"),
        ("POST", "/api/auth/skip"),
        ("POST", "/api/auth/jellyfin"),
        ("POST", "/api/auth/jellyfin-token"),
        ("POST", "/api/auth/jellyfin-connect"),
        ("POST", "/api/auth/logout"),
    ]:
        assert client.request(method, path, json={}).status_code in (404, 405), path


# ── Systematic sweep ───────────────────────────────────────────────────────────

# /api routes that deliberately carry no permission guard.
UNGUARDED = {
    # Who the frontend is acting as: the local user, always.
    "/api/auth/me",
    # Standalone lifecycle endpoints authenticate with the per-run portal token.
    "/api/portal/heartbeat",
    "/api/portal/close",
}


def _all_routes():
    """Every routed endpoint, with its effective path and merged dependencies.

    FastAPI no longer flattens ``include_router`` into ``app.routes`` — an
    included router appears as one opaque entry — so walking ``app.routes``
    alone sees only what was declared on the app itself, and any sweep over it
    silently passes.
    """
    from app.main import app

    routes = []
    for route in app.routes:
        effective = getattr(route, "effective_route_contexts", None)
        if callable(effective):
            for context in effective():
                routes.append((context.path, context.methods, context.dependant))
        elif isinstance(route, APIRoute):
            routes.append((route.path, route.methods, route.dependant))
    return routes


def _permission_guards(dependant) -> list[tuple]:
    """Permission requirements on a route, at any dependency depth."""
    found = []
    stack = list(dependant.dependencies)
    while stack:
        dependency = stack.pop()
        required = getattr(dependency.call, "__required_permissions__", None)
        if required is not None:
            found.append(required)
        stack.extend(dependency.dependencies)
    return found


def test_the_route_sweep_actually_sees_the_routers():
    """Guard against the sweep below passing because it found nothing to check."""
    paths = {path for path, _, _ in _all_routes()}
    assert "/api/download/film" in paths
    assert "/api/users" not in paths
    assert len([p for p in paths if p.startswith("/api/")]) > 20


def test_every_api_route_is_classified():
    """No /api route may exist without an explicit decision about what it needs."""
    unclassified = []
    for path, methods, dependant in _all_routes():
        if not path.startswith("/api/") or path in UNGUARDED:
            continue
        if not _permission_guards(dependant):
            unclassified.append(f"{sorted(methods)} {path}")

    assert not unclassified, (
        "These API routes carry no permission guard and are not in UNGUARDED:\n  "
        + "\n  ".join(sorted(unclassified))
    )


def test_every_permission_is_enforced_somewhere():
    """A permission nobody checks is a permission that does not exist.

    MANAGE_USERS is the exception: user management is gone, and the bit is
    kept only because stored values and the frontend still name it.
    """
    enforced = set()
    for _, _, dependant in _all_routes():
        for required in _permission_guards(dependant):
            enforced.update(required)

    defined = {p for p in Permission if p is not Permission.NONE} - {Permission.MANAGE_USERS}
    assert defined - enforced == set(), (
        "These permissions are never checked on any route: "
        + ", ".join(sorted(p.name for p in defined - enforced))
    )


def test_the_unguarded_list_stays_small():
    """Growth here should be deliberate."""
    assert UNGUARDED == {"/api/auth/me", "/api/portal/heartbeat", "/api/portal/close"}
