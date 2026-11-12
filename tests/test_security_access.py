"""From where the local panel may be driven: the Host and Origin checks that
stand in for a login (DNS rebinding, CSRF), and the security response headers.

The panel has no accounts; tests/test_permissions.py covers what the local
user may do once a request is let through.
"""

import pytest

from app import config
from app.auth import deps, security_headers

FILM = {"id": 1, "title": "x"}


# ── Host ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", [
    "localhost", "localhost:8000", "127.0.0.1:8000", "127.0.0.2", "[::1]:8000",
    "testserver",  # in ALLOWED_HOSTS for the tests (conftest)
])
def test_answers_to_loopback_and_configured_hosts(client, host):
    assert client.get("/api/auth/me", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", [
    "attacker.example", "attacker.example:8000", "localhost.evil.test",
    # Not loopback: the panel is local-only unless ALLOWED_HOSTS says otherwise.
    "192.168.1.20:8000", "10.0.0.5", "[fe80::1]", "0.0.0.0:8000",
])
def test_refuses_any_other_host(client, host):
    """DNS rebinding: the attacker's own name, resolving to the panel — or an
    address on the network the panel was never meant to be reached from."""
    response = client.get("/api/auth/me", headers={"Host": host})
    assert response.status_code == 403


def test_allowed_hosts_extends_the_list(client, monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_HOSTS", frozenset({"testserver", "mypc.lan"}))
    assert client.get("/api/auth/me", headers={"Host": "mypc.lan:8000"}).status_code == 200
    assert client.get("/api/auth/me", headers={"Host": "other.lan"}).status_code == 403


def test_the_host_check_covers_pages_and_mcp_too(client):
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403
    assert client.post("/mcp", headers={"Host": "attacker.example"}, json={}).status_code == 403


def test_behind_a_trusted_proxy_the_forwarded_host_is_the_one_checked(client, monkeypatch):
    monkeypatch.setattr(deps, "TRUST_PROXY_HEADERS", True)
    refused = client.get("/api/auth/me", headers={"X-Forwarded-Host": "attacker.example"})
    assert refused.status_code == 403


def test_forwarded_host_is_ignored_unless_trusted(client):
    response = client.get("/api/auth/me", headers={"X-Forwarded-Host": "attacker.example"})
    assert response.status_code == 200


# ── Origin (CSRF) ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("headers", [
    {"Origin": "http://attacker.example"},
    {"Origin": "http://testserver:9999"},       # another service on the same machine
    {"Origin": "null"},                          # sandboxed frame, data: page
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},             # another port is same-site, not same-origin
    {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
])
def test_refuses_a_cross_origin_write(client, stub_jobs, headers):
    response = client.post("/api/download/film", json=FILM, headers=headers)
    assert response.status_code == 403
    assert stub_jobs == []


@pytest.mark.parametrize("headers", [
    {},                                                           # curl, an MCP bridge
    {"Origin": "http://testserver"},
    {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
])
def test_accepts_its_own_ui_and_plain_clients(client, stub_jobs, headers):
    assert client.post("/api/download/film", json=FILM, headers=headers).status_code == 202


def test_does_not_check_origin_on_reads(client):
    response = client.get("/api/auth/me", headers={"Origin": "http://attacker.example"})
    assert response.status_code == 200


# ── Security headers ───────────────────────────────────────────────────────────

def test_every_response_carries_the_security_headers(client):
    refused = client.get("/api/files", headers={"Host": "attacker.example"})
    for response in (client.get("/"), client.get("/api/files"), client.get("/static/core.js"), refused):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "same-origin"
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self';" in csp                # no inline <script>
        assert "frame-ancestors 'self'" in csp
        assert "object-src 'none'" in csp
    assert refused.status_code == 403                     # the middleware's own answer too


def test_frame_ancestors_can_be_set_explicitly(monkeypatch):
    monkeypatch.setattr(config, "FRAME_ANCESTORS", ("https://media.example.org", "javascript:x"))
    headers = security_headers.security_headers()
    assert headers["Content-Security-Policy"].endswith(
        "frame-ancestors 'self' https://media.example.org"
    )
    # X-Frame-Options cannot name a foreign origin: sending it would block the embed.
    assert "X-Frame-Options" not in headers
