"""VPN Gate profiles: real connection state, exact server match, honest creation.

vpncmd is always mocked: no SoftEther command and no network access runs here.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import deps
from app.routers import softether

VPNCMD = Path("C:/Program Files/SoftEther VPN Client/vpncmd_x64.exe")

ACCOUNT_LIST = """\
VPN Connection Setting Name |VPN Gate IT
Status                      |Offline
VPN Server Hostname         |1.2.3.4:443 (Direct TCP/IP Connection)
VPN Connection Setting Name |VPN Gate ITA-2
Status                      |Connected
VPN Server Hostname         |9.9.9.9:443 (Direct TCP/IP Connection)
"""

ACCOUNTS = {
    "VPN Gate IT": "Destination VPN Server Host Name |1.2.3.4\nDestination VPN Server Port Number |443\n",
    "VPN Gate ITA-2": "Destination VPN Server Host Name |9.9.9.9\nDestination VPN Server Port Number |1194\n",
}

CONNECTED = "Item |Value\nSession Status |Connection Completed (Session Established)\n"
STATUS_CONNECTED_WORD = "Item |Value\nStatus |Connected\n"
STATUS_STATO_CONNESSO = "Voce |Valore\nStato della sessione |Connesso\n"
DISCONNECTED = "Item |Value\nSession Status |Disconnected\n"
NOT_CONNECTED_ERROR = "Error occurred. (Error code: 37)\nThe specified VPN Connection Setting is not connected.\n"


def _done(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeVpncmd:
    """Answers vpncmd commands from tables; records every call."""

    def __init__(self, status=None, status_fail=None):
        self.calls = []
        self.status = status or {}
        self.status_fail = status_fail or set()
        self.accounts = dict(ACCOUNTS)
        self.account_list = ACCOUNT_LIST

    def __call__(self, args, **kwargs):
        assert args[0] == str(VPNCMD)
        command = args[args.index("/cmd") + 1:]
        self.calls.append(command)
        verb = command[0]
        if verb == "AccountList":
            return _done(self.account_list)
        if verb == "AccountGet":
            return _done(self.accounts.get(command[1], ""), 0 if command[1] in self.accounts else 1)
        if verb == "AccountStatusGet":
            if command[1] in self.status_fail:
                raise softether.subprocess.TimeoutExpired(args, 20)
            return _done(self.status.get(command[1], NOT_CONNECTED_ERROR),
                         0 if command[1] in self.status else 1)
        if verb in {"AccountCreate", "AccountPasswordSet"}:
            self.accounts[command[1]] = "Destination VPN Server Host Name |5.6.7.8\n"
            return _done("The command completed successfully.")
        raise AssertionError(f"unexpected vpncmd command {command}")


@pytest.fixture
def vpn(monkeypatch):
    fake = FakeVpncmd()
    monkeypatch.setattr(softether, "_find_vpncmd", lambda: VPNCMD)
    monkeypatch.setattr(softether, "_ensure_client_service", lambda: None)
    monkeypatch.setattr(softether.subprocess, "run", fake)
    monkeypatch.setattr(softether, "_vpngate_servers", lambda: [])

    def no_network(*args, **kwargs):
        raise OSError("network disabled in tests")

    monkeypatch.setattr(softether.socket, "create_connection", no_network)
    return fake


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.add_middleware(deps.AuthMiddleware)
    app.include_router(softether.router)
    user = SimpleNamespace(id=1, username="tester", has=lambda *a, **k: True)
    monkeypatch.setattr(deps, "LOCAL_USER", user)
    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))


def _headers():
    return {"Origin": "http://127.0.0.1:8000", "X-SoftEther-Token": softether._TOKEN}


# ── connection state ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("output, returncode, expected", [
    (CONNECTED, 0, True),
    (STATUS_CONNECTED_WORD, 0, True),
    (STATUS_STATO_CONNESSO, 0, True),
    (DISCONNECTED, 0, False),
    ("Stato |Non connesso\n", 0, False),
    ("Session Status |Connecting\n", 0, False),
    (NOT_CONNECTED_ERROR, 1, False),
    ("", 1, None),
    ("garbage without rows", 0, None),
])
def test_connection_state_parser(output, returncode, expected):
    assert softether._parse_connected(output, returncode) is expected


def test_profiles_report_registered_and_real_connected_state(vpn, client):
    vpn.status = {"VPN Gate IT": DISCONNECTED, "VPN Gate ITA-2": CONNECTED}
    data = client.get("/api/softether/vpngate/profiles").json()
    assert data["profiles"] == ["VPN Gate IT", "VPN Gate ITA-2"]
    it, ita = data["details"]["VPN Gate IT"], data["details"]["VPN Gate ITA-2"]
    assert it["registered"] is True and ita["registered"] is True
    assert it["connected"] is False
    assert ita["connected"] is True
    assert ["AccountStatusGet", "VPN Gate IT"] in vpn.calls


def test_failed_status_command_gives_null_and_keeps_the_list(vpn, client):
    vpn.status = {"VPN Gate IT": DISCONNECTED}
    vpn.status_fail = {"VPN Gate ITA-2"}
    response = client.get("/api/softether/vpngate/profiles")
    assert response.status_code == 200
    details = response.json()["details"]
    assert details["VPN Gate IT"]["connected"] is False
    assert details["VPN Gate ITA-2"]["connected"] is None


def test_unknown_status_output_is_null_not_offline(vpn, client, monkeypatch):
    # Empty output with a failing exit code: the state is unknown.
    vpn.account_list = "VPN Connection Setting Name |VPN Gate IT\n"
    original = vpn.__call__

    def empty_status(args, **kwargs):
        if "AccountStatusGet" in args:
            return _done("", 1)
        return original(args, **kwargs)

    monkeypatch.setattr(softether.subprocess, "run", empty_status)
    details = client.get("/api/softether/vpngate/profiles").json()["details"]
    assert details["VPN Gate IT"]["connected"] is None


# ── registered_profile on the public list ────────────────────────────────────

def test_registered_profile_matches_exact_ip_only():
    details = {"VPN Gate IT": {"host": "1.2.3.4"}, "VPN Gate ITA-2": {"host": "9.9.9.9"}}
    servers = [{"host": "1.2.3.45", "countryCode": "IT"}, {"host": "1.2.3.4", "countryCode": "IT"},
               {"host": "11.2.3.4", "countryCode": "IT"}, {"host": "9.9.9.9", "countryCode": "IT"}]
    annotated = softether._annotate_registered(servers, details)
    assert [s["registered_profile"] for s in annotated] == [None, "VPN Gate IT", None, "VPN Gate ITA-2"]
    # The shared server cache must not be mutated.
    assert all("registered_profile" not in s for s in servers)


def test_vpngate_endpoint_marks_registered_servers(vpn, client, monkeypatch):
    monkeypatch.setattr(softether, "_vpngate_servers", lambda: [
        {"host": "1.2.3.45", "port": 443, "countryCode": "IT"},
        {"host": "1.2.3.4", "port": 443, "countryCode": "IT"},
    ])
    servers = client.get("/api/softether/vpngate").json()["servers"]
    assert servers[0]["registered_profile"] is None
    assert servers[1]["registered_profile"] == "VPN Gate IT"


def test_vpngate_endpoint_survives_missing_client(client, monkeypatch):
    monkeypatch.setattr(softether, "_find_vpncmd", lambda: None)
    monkeypatch.setattr(softether, "_vpngate_servers", lambda: [{"host": "1.2.3.4", "port": 443}])
    servers = client.get("/api/softether/vpngate").json()["servers"]
    assert servers == [{"host": "1.2.3.4", "port": 443, "registered_profile": None}]


# ── creation ─────────────────────────────────────────────────────────────────

def _create(client, host="5.6.7.8"):
    return client.post("/api/softether/vpngate/profile", headers=_headers(),
                       json={"host": host, "port": 443, "name": "VPN Gate JP"})


class _Socket:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_created_but_not_connected_is_not_reported_as_success(vpn, client, monkeypatch):
    monkeypatch.setattr(softether.socket, "create_connection", lambda *a, **k: _Socket())
    response = _create(client)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["success"] is False
    assert body["connected"] is False
    assert body["detail"] == "softether.createdNotConnected"
    assert body["name"] == "VPN Gate JP"
    assert ["AccountStatusGet", "VPN Gate JP"] in vpn.calls
    # Never connected automatically.
    assert not any(call[0] == "AccountConnect" for call in vpn.calls)


def test_created_and_connected_is_a_success(vpn, client, monkeypatch):
    monkeypatch.setattr(softether.socket, "create_connection", lambda *a, **k: _Socket())
    vpn.status = {"VPN Gate JP": CONNECTED}
    body = _create(client).json()
    assert body["success"] is True and body["connected"] is True
    assert "detail" not in body


def test_unreachable_server_is_never_written(vpn, client):
    # The fixture's create_connection raises: the in-request check fails.
    response = _create(client)
    assert response.status_code == 409
    assert response.json()["detail"] == "softether.serverUnreachable"
    assert not any(call[0] == "AccountCreate" for call in vpn.calls)


# ── connect uses the same parser ─────────────────────────────────────────────

def test_connect_does_not_read_disconnected_as_connected(vpn, client, monkeypatch):
    vpn.status = {"VPN Gate IT": DISCONNECTED}
    original = vpn.__call__

    def with_connect(args, **kwargs):
        if "AccountConnect" in args:
            return _done("The command completed successfully.")
        return original(args, **kwargs)

    monkeypatch.setattr(softether.subprocess, "run", with_connect)
    body = client.post("/api/softether/vpngate/profile/connect", headers=_headers(),
                       json={"name": "VPN Gate IT"}).json()
    assert body["connected"] is False
