"""The MCP endpoint: who may reach it, and who its tools act as.

Driven over real JSON-RPC through the whole app — AuthMiddleware included —
rather than by calling tool functions, because the two things most worth
pinning live on that path: the bearer gate, and the acting user reaching the
same route handlers the browser uses.
"""

import asyncio
import dataclasses
import json

import httpx
import pytest

from app import config
from app.auth import deps, models
from app.auth.deps import OPEN_MODE_USER
from app.auth.permissions import Permission
from app.mcp import auth as mcp_auth, server as mcp_http
from app.requests import models as request_models
from tests.conftest import enable_open_mode, session_for


def _enable(on: bool = True):
    config.save_settings({**config.get_settings(), "mcp_enabled": on})


def _local_row_id() -> int:
    return deps.local_user().id


def _send(token: str | None, *calls, headers: dict | None = None, query: str = ""):
    """POST each ``(method, params)`` to /mcp; return the httpx responses."""
    from app.main import app

    async def run():
        async with mcp_http.running():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://panel") as c:
                sent = {"Accept": "application/json, text/event-stream", **(headers or {})}
                if token is not None:
                    sent["Authorization"] = f"Bearer {token}"
                return [
                    await c.post(f"/mcp{query}", headers=sent,
                                 json={"jsonrpc": "2.0", "id": i, "method": m, "params": p})
                    for i, (m, p) in enumerate(calls, 1)
                ]

    return asyncio.run(run())


def _tool(token: str, name: str, **arguments) -> tuple[bool, object]:
    """Call one tool; return ``(is_error, decoded payload or message)``."""
    (response,) = _send(token, ("tools/call", {"name": name, "arguments": arguments}))
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"]
    try:
        return result["isError"], json.loads(text)
    except ValueError:
        return result["isError"], text


# ── The gate ───────────────────────────────────────────────────────────────────

def test_switched_off_answers_404_even_with_a_valid_token(client):
    enable_open_mode()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    (response,) = _send(token, ("tools/list", {}))
    assert response.status_code == 404


def test_no_stored_token_refuses_instead_of_opening_up(client):
    enable_open_mode()
    _enable()
    (no_header,) = _send(None, ("tools/list", {}))
    (any_token,) = _send("anything", ("tools/list", {}))
    assert no_header.status_code == any_token.status_code == 401


def test_wrong_token_and_query_string_token_are_refused(client):
    enable_open_mode()
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    (wrong,) = _send("not-it", ("tools/list", {}))
    (in_query,) = _send(None, ("tools/list", {}), query=f"?token={token}")
    assert wrong.status_code == in_query.status_code == 401
    assert wrong.headers["www-authenticate"] == "Bearer"


def test_token_is_stored_hashed(client):
    enable_open_mode()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    stored = models.get_setting(mcp_auth.SETTING_TOKEN_HASH)
    assert stored and token not in stored


def test_lists_tools(client):
    enable_open_mode()
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    (response,) = _send(token, ("tools/list", {}))
    names = {t["name"] for t in response.json()["result"]["tools"]}
    assert {"search_content", "download_film", "submit_request",
            "approve_request", "list_downloads"} <= names
    # The followed-series feature was removed, and its tools with it.
    assert not {"follow_series", "list_followed_series", "unfollow_series",
                "check_series_now"} & names


# ── Who the agent is ───────────────────────────────────────────────────────────
#
# The panel is single-user: the agent acts as the local user, bound to its
# jf_user row (migration 12), exactly as the browser does.

def test_a_legacy_auth_mode_setting_does_not_kill_the_token(client):
    """auth_mode=jellyfin in an old database means nothing any more."""
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    models.set_setting(models.SETTING_AUTH_MODE, "jellyfin")
    assert _send(token, ("tools/list", {}))[0].status_code == 200


def test_tools_use_the_local_users_live_permissions(client, stub_jobs, monkeypatch):
    """Route dependencies do not run on a direct call, so each tool checks the
    permission itself — against the local user's permissions at call time."""
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    restricted = dataclasses.replace(deps.LOCAL_USER, permissions=int(Permission.REQUEST))
    monkeypatch.setattr(deps, "LOCAL_USER", restricted)

    is_error, message = _tool(token, "download_film", film_id=7, title="Film")
    assert is_error and "DOWNLOAD" in message
    assert stub_jobs == []

    monkeypatch.setattr(deps, "LOCAL_USER", dataclasses.replace(
        restricted, permissions=deps.LOCAL_PERMISSIONS))
    is_error, body = _tool(token, "download_film", film_id=7, title="Film")
    assert not is_error and body["status"] == "queued"
    (name, _, kwargs), = stub_jobs
    # A direct download is the panel's own: its notifications are panel-wide.
    assert name == "submit_film" and kwargs["user_id"] is None


def test_request_is_filed_by_the_local_user(client, source):
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)

    is_error, body = _tool(token, "submit_request", source="streamingcommunity",
                           media_type="film", external_id="7", title="Film")
    assert not is_error, body
    request = request_models.get(body["request"]["id"])
    assert request.requested_by == _local_row_id()


def test_request_audio_is_checked_against_the_source(client, source):
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)

    is_error, message = _tool(token, "submit_request", source="streamingcommunity",
                              media_type="film", external_id="7", title="Film",
                              audio_languages=["jpn"])
    assert is_error and "jpn" in message


def test_queue_tools_work_for_the_local_user(client, source):
    """The local user owns the request queue: it files and approves."""
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    is_error, body = _tool(token, "submit_request", source="streamingcommunity",
                           media_type="film", external_id="7", title="Film")
    assert not is_error, body

    is_error, listed = _tool(token, "list_requests")
    assert not is_error, listed
    assert body["request"]["id"] in {r["id"] for r in listed["requests"]}


def test_season_goes_through_the_batch(client, source, stub_jobs):
    enable_open_mode()
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    is_error, body = _tool(token, "download_season", tv_id=1, slug="s", tv_name="Serie",
                           season_number=1)
    assert not is_error, body
    assert body["count"] == len(source.episodes)
    assert {kwargs["batch_id"] for _, _, kwargs in stub_jobs} == {body["batch_id"]}


def test_episode_is_picked_by_its_number(client, source, stub_jobs):
    enable_open_mode()
    _enable()
    token = mcp_auth.generate_token(OPEN_MODE_USER)
    is_error, _ = _tool(token, "download_episode", tv_id=1, slug="s", tv_name="Serie",
                        season_number=1, episode_number=2)
    assert not is_error
    (_, args, _), = stub_jobs
    assert args[2] == 1  # ep_index of episode 2

    is_error, message = _tool(token, "download_episode", tv_id=1, slug="s", tv_name="Serie",
                              season_number=1, episode_number=9)
    assert is_error and "9" in message


# ── Settings API ───────────────────────────────────────────────────────────────

@pytest.fixture
def settings_admin(client):
    return deps.LOCAL_USER, session_for(client, 0)


def test_token_is_shown_once_and_owned_by_the_panel(client, settings_admin):
    user, csrf = settings_admin
    minted = client.post("/api/mcp/token", headers={"X-CSRF-Token": csrf})
    assert minted.status_code == 200
    assert minted.json()["owner"] == "Pannello"

    status = client.get("/api/mcp/status").json()
    assert status["has_token"] is True and status["owner"] == "Pannello"
    assert minted.json()["token"] not in json.dumps(status)

    assert client.delete("/api/mcp/token", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/mcp/status").json()["has_token"] is False


def test_switch_is_saved_through_the_settings_endpoint(client, settings_admin):
    _, csrf = settings_admin
    response = client.put("/api/domain/settings", json={"mcp_enabled": True},
                          headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert config.get_settings()["mcp_enabled"] is True
