"""Downloaded installers run only after verification; nothing is downloaded,
no PowerShell and no installer is started here."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import softether_trust
from app.auth import deps
from app.routers import softether
from app.update_download import SignatureError

INSTALLER = "vpngate-client-v4.44-9807-rtm-2025.04.16.exe"
PLUGIN = "VpnGatePlugin_x64.dll"


@pytest.mark.parametrize("url,trusted", [
    ("https://download.vpngate.jp/common/cd.aspx/vpngate-client-1.zip", True),
    ("https://www.vpngate.net/files/vpngate-client-1.zip", True),
    ("http://download.vpngate.jp/common/cd.aspx/vpngate-client-1.zip", False),
    ("https://evil.example/vpngate-client-1.zip", False),
    ("https://download.vpngate.jp.evil.example/vpngate-client-1.zip", False),
    ("https://user:pw@download.vpngate.jp/vpngate-client-1.zip", False),
    ("https://download.vpngate.jp:8443/vpngate-client-1.zip", False),
    ("https://[/vpngate-client-1.zip", False),
])
def test_only_https_allowlisted_vpngate_hosts(url, trusted):
    assert softether._trusted_vpngate_url(url) is trusted


def _page(monkeypatch, html):
    monkeypatch.setattr(softether.requests, "get", lambda *a, **kw: SimpleNamespace(
        text=html, raise_for_status=lambda: None))


def test_download_page_http_or_foreign_links_are_refused(monkeypatch):
    _page(monkeypatch, '<a href="http://download.vpngate.jp/x/vpngate-client-1.zip">'
                       '<a href="https://evil.example/vpngate-client-1.zip">')
    with pytest.raises(RuntimeError, match="not trusted"):
        softether._vpngate_download_url()
    good = "https://download.vpngate.jp/common/cd.aspx/vpngate-client-2.zip"
    _page(monkeypatch, f'<a href="http://mirror.example/vpngate-client-1.zip"><a href="{good}">')
    assert softether._vpngate_download_url() == good


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """A VPN Gate-shaped ZIP whose plug-in matches a (test) pin."""
    plugin = b"plugin code"
    monkeypatch.setattr(softether_trust, "VPNGATE_PLUGIN_SHA256",
                        {PLUGIN: frozenset({hashlib.sha256(plugin).hexdigest()})})
    path = tmp_path / "vpngate-client.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(INSTALLER, b"installer")
        z.writestr(PLUGIN, plugin)
        z.writestr("License.txt", b"text")
    verify = Mock()
    monkeypatch.setattr(softether, "verify_authenticode", verify)
    return path, verify


def test_verified_archive_returns_installer(archive, tmp_path):
    path, verify = archive
    installer = softether._extract_verified_vpngate(path, tmp_path / "out")
    assert installer == tmp_path / "out" / INSTALLER
    verify.assert_called_once_with(installer, softether_trust.SOFTETHER_SIGNER_THUMBPRINTS)


@pytest.mark.parametrize("name", ["version.dll", "setup.exe", "sub/" + PLUGIN, "../evil.dll",
                                  "vpngate-client-second.exe"])
def test_unexpected_archive_entry_is_never_extracted(archive, tmp_path, name):
    path, verify = archive
    with zipfile.ZipFile(path, "a") as z:
        z.writestr(name, b"planted")
    with pytest.raises(SignatureError):
        softether._extract_verified_vpngate(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    verify.assert_not_called()


def test_plugin_without_matching_pin_is_refused(archive, tmp_path, monkeypatch):
    path, verify = archive
    monkeypatch.setattr(softether_trust, "VPNGATE_PLUGIN_SHA256", {PLUGIN: frozenset()})
    with pytest.raises(SignatureError, match="pin"):
        softether._extract_verified_vpngate(path, tmp_path / "out")
    verify.assert_not_called()


def test_shipped_pins_are_well_formed():
    assert softether_trust.SOFTETHER_SIGNER_THUMBPRINTS
    assert all(len(t) == 40 and t == t.upper() for t in softether_trust.SOFTETHER_SIGNER_THUMBPRINTS)
    for pins in softether_trust.VPNGATE_PLUGIN_SHA256.values():
        assert pins and all(len(p) == 64 and p == p.lower() for p in pins)
    bundle = json.loads((Path(__file__).parents[1] / "tools/softether/bundle.json").read_text("utf-8"))
    assert bundle["signerThumbprint"] in softether_trust.SOFTETHER_SIGNER_THUMBPRINTS


def _install_job(monkeypatch, tmp_path, archive_path):
    temp_root = tmp_path / "job"
    monkeypatch.setattr(softether, "_vpngate_download_url",
                        lambda: "https://download.vpngate.jp/vpngate-client-1.zip")
    monkeypatch.setattr(softether.tempfile, "mkdtemp", lambda prefix: (temp_root.mkdir(), str(temp_root))[1])
    monkeypatch.setattr(softether, "_curl", lambda: None)
    monkeypatch.setattr(softether, "_download_with_requests",
                        lambda job, url, target: target.write_bytes(archive_path.read_bytes()))
    launch = Mock()
    monkeypatch.setattr(softether.subprocess, "Popen", launch)
    monkeypatch.setattr(softether.threading, "Thread", Mock())
    softether._vpngate_jobs["job"] = {"state": "queued"}
    return temp_root, launch


def test_failed_signature_deletes_download_and_never_runs_it(archive, tmp_path, monkeypatch):
    path, verify = archive
    verify.side_effect = SignatureError("Firma Authenticode non valida o firmatario inatteso")
    temp_root, launch = _install_job(monkeypatch, tmp_path, path)
    softether._run_vpngate_install("job")
    job = softether._vpngate_jobs.pop("job")
    launch.assert_not_called()
    assert not temp_root.exists()
    assert job["state"] == "error" and job["error"] == softether._UNVERIFIED


def test_verified_installer_is_started_from_its_own_folder(archive, tmp_path, monkeypatch):
    path, _ = archive
    temp_root, launch = _install_job(monkeypatch, tmp_path, path)
    softether._run_vpngate_install("job")
    job = softether._vpngate_jobs.pop("job")
    installer = temp_root / "extracted" / INSTALLER
    assert launch.call_args.args == ([str(installer)],)
    assert launch.call_args.kwargs["shell"] is False
    assert job["state"] == "installing"


# ── /download-update ─────────────────────────────────────────────────────────

RELEASE = {
    "status": "update_available", "version": "4.44.9807", "tag": "v4.44-9807-rtm",
    "asset_name": "softether-vpnclient-v4.44-9807-rtm-2025.04.16-windows-x86_x64-intel.exe",
    "asset_url": "https://github.com/SoftEtherVPN/SoftEtherVPN_Stable/releases/download/x/y.exe",
    "release_url": "https://github.com/SoftEtherVPN/SoftEtherVPN_Stable/releases/tag/x",
    "size_limit": 1024,
}


@pytest.fixture
def panel(monkeypatch, tmp_path):
    app = FastAPI()
    app.add_middleware(deps.AuthMiddleware)
    app.include_router(softether.router)
    user = SimpleNamespace(id=1, username="tester", has=lambda *a, **k: True)
    # No login: every request is the local user. Swapped for a stand-in so a
    # test can take a permission away.
    monkeypatch.setattr(deps, "LOCAL_USER", user)
    monkeypatch.setattr(softether.os, "name", "nt")
    monkeypatch.setattr(softether, "_release_status", lambda: dict(RELEASE))
    monkeypatch.setattr(softether, "_curl", lambda: Path("C:/Windows/System32/curl.exe"))
    temp_root = tmp_path / "update"
    monkeypatch.setattr(softether.tempfile, "mkdtemp", lambda prefix: (temp_root.mkdir(), str(temp_root))[1])

    def curl(args, **kwargs):
        assert args[0] == str(Path("C:/Windows/System32/curl.exe"))
        assert args[args.index("--proto") + 1] == "=https"
        assert args[-1] == RELEASE["asset_url"]
        Path(args[args.index("--output") + 1]).write_bytes(b"downloaded")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(softether.subprocess, "run", curl)
    launch = Mock()
    monkeypatch.setattr(softether.subprocess, "Popen", launch)
    monkeypatch.setattr(softether.threading, "Thread", Mock())
    verify = Mock()
    monkeypatch.setattr(softether, "verify_authenticode", verify)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    return client, launch, verify, temp_root


def _post(client):
    return client.post("/api/softether/download-update", headers={
        "Origin": "http://127.0.0.1:8000",
        "X-SoftEther-Token": softether._TOKEN})


def test_available_update_is_downloaded_verified_then_started(panel, monkeypatch):
    client, launch, verify, temp_root = panel
    roots = Mock(side_effect=AssertionError("bundle.json must not be touched"))
    monkeypatch.setattr(softether, "_softether_roots", roots)
    response = _post(client)
    assert response.json() == {"started": True, "version": RELEASE["version"]}
    target = temp_root / RELEASE["asset_name"]
    verify.assert_called_once_with(target, softether_trust.SOFTETHER_SIGNER_THUMBPRINTS)
    assert launch.call_args.args == ([str(target)],)


def test_unverified_update_is_deleted_and_never_started(panel):
    client, launch, verify, temp_root = panel
    verify.side_effect = SignatureError("Firma Authenticode non valida o firmatario inatteso")
    response = _post(client)
    assert response.status_code == 502
    assert response.json()["detail"] == softether._UNVERIFIED
    launch.assert_not_called()
    assert not temp_root.exists()


def test_update_needs_system32_curl(panel, monkeypatch):
    client, launch, _, temp_root = panel
    monkeypatch.setattr(softether, "_curl", lambda: None)
    assert _post(client).status_code == 503
    launch.assert_not_called()
    assert not temp_root.exists()


@pytest.mark.parametrize("status", ["up_to_date", "unavailable"])
def test_without_update_only_the_pinned_bundle_may_run(panel, monkeypatch, status):
    client, launch, verify, temp_root = panel
    monkeypatch.setattr(softether, "_release_status", lambda: {"status": status})
    monkeypatch.setattr(softether, "_bundled_installer", lambda: None)
    assert _post(client).status_code == 404
    launch.assert_not_called()
    verify.assert_not_called()
    assert not temp_root.exists()


def test_curl_is_never_resolved_through_path():
    source = (Path(__file__).parents[1] / "app/routers/softether.py").read_text("utf-8")
    assert "shutil.which" not in source
    assert '["curl.exe"' not in source
