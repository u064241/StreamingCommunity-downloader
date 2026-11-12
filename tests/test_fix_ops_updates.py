"""Signature gate of the in-app update, error hygiene and the desktop-only
lifecycle flag. No PowerShell, network or real executable is involved."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from app import distribution, portable_updates, update_download, updates
from app.routers import updates as routes
from app.update_download import SignatureError, UpdateDownloadError, verify_authenticode

SIGNER = "AB" * 20
POWERSHELL = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")


@pytest.fixture
def powershell(monkeypatch):
    """Replace the PowerShell call; returns the list of recorded invocations."""
    calls = []
    answer = {"stdout": json.dumps({"status": "Valid", "thumbprint": SIGNER}), "returncode": 0}
    monkeypatch.setattr(update_download, "system32_tool", lambda *parts: POWERSHELL)

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if answer.get("raise"):
            raise answer["raise"]
        return SimpleNamespace(stdout=answer["stdout"], returncode=answer["returncode"])

    monkeypatch.setattr(update_download.subprocess, "run", run)
    return calls, answer


def test_valid_signature_from_expected_signer_passes(powershell):
    calls, _ = powershell
    path = "C:/tmp/it's $(evil) `name`.exe"
    verify_authenticode(path, [SIGNER.lower()])
    args, kwargs = calls[0]
    # A list, an absolute interpreter, no shell, a timeout, and the path only
    # in the environment: nothing caller-controlled reaches the script text.
    assert args[0] == str(POWERSHELL) and isinstance(args, list)
    assert not any("evil" in part for part in args)
    assert kwargs["env"]["SC_VERIFY_FILE"] == path
    assert kwargs["shell"] is False and kwargs["timeout"] > 0
    assert not any(key.upper() == "PSMODULEPATH" for key in kwargs["env"])


@pytest.mark.parametrize("change", [
    {"stdout": json.dumps({"status": "Valid", "thumbprint": "CD" * 20})},
    {"stdout": json.dumps({"status": "NotSigned", "thumbprint": None})},
    {"stdout": json.dumps({"status": "HashMismatch", "thumbprint": SIGNER})},
    {"stdout": json.dumps({"status": "Valid", "thumbprint": SIGNER}), "returncode": 1},
    {"stdout": ""}, {"stdout": "not json"}, {"stdout": "[]"},
    {"raise": subprocess.TimeoutExpired("powershell", 60)},
    {"raise": OSError("cannot start")},
])
def test_anything_else_is_rejected(powershell, change):
    powershell[1].update(change)
    with pytest.raises(SignatureError):
        verify_authenticode("C:/tmp/file.exe", [SIGNER])


@pytest.mark.parametrize("signers", [(), [""], None])
def test_no_expected_signer_fails_closed_without_running_anything(powershell, signers):
    with pytest.raises(SignatureError, match="disattivata"):
        verify_authenticode("C:/tmp/file.exe", signers)
    assert powershell[0] == []


def test_missing_powershell_fails_closed(monkeypatch):
    monkeypatch.setattr(update_download, "system32_tool", lambda *parts: None)
    with pytest.raises(SignatureError):
        verify_authenticode("C:/tmp/file.exe", [SIGNER])


def test_portable_update_refuses_to_run_while_signer_is_not_configured(monkeypatch):
    monkeypatch.setattr(portable_updates, "EXPECTED_SIGNER_THUMBPRINTS", ())
    monkeypatch.setattr(portable_updates, "_running", None)
    for name in ("_runtime", "_idle", "download_verified"):
        monkeypatch.setattr(portable_updates, name,
                            lambda *a, **kw: pytest.fail("must stop before any work"))
    assert portable_updates.signature_check_enabled() is False
    with pytest.raises(UpdateDownloadError, match="Verifica firma disattivata"):
        portable_updates.prepare(1)


def test_panel_does_not_offer_an_update_it_would_refuse(monkeypatch):
    monkeypatch.setattr(distribution, "mode", lambda: "portable")
    monkeypatch.setattr(portable_updates, "EXPECTED_SIGNER_THUMBPRINTS", ())
    status = routes.portable_status()
    assert status["available"] is False and status["signature_verification"] is False
    monkeypatch.setattr(portable_updates, "EXPECTED_SIGNER_THUMBPRINTS", (SIGNER,))
    status = routes.portable_status()
    assert status["available"] is True and status["signature_verification"] is True


def test_staged_binaries_are_verified_before_being_prepared():
    source = (Path(__file__).parents[1] / "app/portable_updates.py").read_text("utf-8")
    verify = source.index("verify_authenticode(binary, EXPECTED_SIGNER_THUMBPRINTS)")
    assert "for binary in (target, staged_helper):" in source
    assert verify < source.index("_prepared = dict(")


def test_update_check_does_not_return_exception_text(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("HTTPSConnectionPool(host='api.github.com') secret detail")
    monkeypatch.setattr(updates.requests, "get", fail)
    monkeypatch.setattr(updates, "_cached", None)
    result = updates.check(force=True)
    assert result["status"] == "unavailable"
    assert "secret detail" not in json.dumps(result) and "error" not in result


def test_unexpected_errors_reach_the_client_as_a_generic_message():
    def broken():
        raise OSError(r"[WinError 5] C:\Users\someone\AppData\secret.zip")
    with pytest.raises(routes.HTTPException) as caught:
        routes._run(broken)
    assert caught.value.status_code == 409
    assert caught.value.detail == "Aggiornamento non disponibile"

    def refused():
        raise UpdateDownloadError("Completa o annulla i lavori prima di aggiornare")
    with pytest.raises(routes.HTTPException) as caught:
        routes._run(refused)
    assert "Completa o annulla" in caught.value.detail


def test_download_error_does_not_embed_the_cause(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("secret detail")
    monkeypatch.setattr(update_download.requests, "get", fail)
    with pytest.raises(UpdateDownloadError) as caught:
        update_download.download_verified(
            "https://github.com/u064241/StreamingCommunity-downloader/releases/download/v1/a.zip",
            "0" * 64, tmp_path / "staging")
    assert str(caught.value) == "Download aggiornamento fallito"
    assert list((tmp_path / "staging").iterdir()) == []


@pytest.mark.parametrize("os_name,frozen,host,expected", [
    ("nt", True, "127.0.0.1", True),
    ("nt", True, "localhost", True),
    ("nt", True, "::1", True),
    ("nt", True, "0.0.0.0", False),      # desktop build exposed on the LAN
    ("nt", True, "192.168.1.10", False),
    ("nt", True, "", False),
    ("nt", False, "127.0.0.1", True),    # python main.py from a checkout
    ("nt", False, "127.0.0.2", True),
    ("nt", False, "localhost", True),
    ("nt", False, "::1", True),
    ("nt", False, "[::1]", True),
    ("nt", False, "0.0.0.0", False),
    ("nt", False, "::", False),
    ("nt", False, "192.168.1.10", False),
    ("nt", False, "pc.lan", False),
    ("posix", False, "0.0.0.0", False),  # Docker / Linux server
    ("posix", False, "127.0.0.1", False),  # Linux checkout: not the desktop app
    ("posix", True, "127.0.0.1", True),
])
def test_last_tab_shutdown_is_desktop_only(monkeypatch, os_name, frozen, host, expected):
    monkeypatch.delenv("SCPANEL_INSTALL", raising=False)
    monkeypatch.setattr(distribution.os, "name", os_name)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    assert distribution.desktop_lifecycle(host) is expected


@pytest.mark.parametrize("os_name,frozen", [("nt", False), ("nt", True), ("posix", True)])
def test_last_tab_shutdown_never_applies_inside_docker(monkeypatch, os_name, frozen):
    monkeypatch.setenv("SCPANEL_INSTALL", "docker")
    monkeypatch.setattr(distribution.os, "name", os_name)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    assert distribution.desktop_lifecycle("127.0.0.1") is False


def test_entry_point_sets_the_portal_token_only_for_the_desktop_build():
    source = (Path(__file__).parents[1] / "main.py").read_text("utf-8")
    guard = source.index("if desktop_lifecycle(HOST):")
    assert guard < source.index('os.environ.setdefault("SC_PANEL_PORTAL_TOKEN"')
    assert 'os.environ.pop("SC_PANEL_PORTAL_TOKEN", None)' in source
    assert source.count("SC_PANEL_PORTAL_TOKEN") == 2
