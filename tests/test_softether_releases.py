"""Untrusted release metadata: no network or installer execution."""

from copy import deepcopy

import pytest

from app.core.softether_releases import REPOSITORY, select_windows_client


def release(tag="v4.44-9807-rtm"):
    name = f"softether-vpnclient-{tag}-2025.04.16-windows-x86_x64-intel.exe"
    return {
        "tag_name": tag, "draft": False, "prerelease": False,
        "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
        "assets": [{"name": name, "state": "uploaded", "size": 56249368,
                    "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"}],
    }


def test_numeric_version_order_and_universal_windows_package():
    result = select_windows_client([release("v4.9-9999-rtm"), release(), release("v4.43-9799-rtm")])
    assert result.version == (4, 44, 9807)
    assert result.asset_name.endswith("windows-x86_x64-intel.exe")


@pytest.mark.parametrize("change", [
    {"draft": True}, {"prerelease": True}, {"draft": None},
    {"tag_name": "v4.45-9999-beta"}, {"tag_name": "v4.45-9999-rc"},
    {"html_url": "https://github.com/other/repo/releases/tag/v4.44-9807-rtm"},
    {"assets": "invalid"},
])
def test_reject_invalid_release(change):
    assert select_windows_client([release() | change]) is None


@pytest.mark.parametrize("change", [
    {"browser_download_url": "https://evil.example/client.exe"},
    {"browser_download_url": "https://github.com.evil.example/client.exe"},
    {"name": "softether-vpnserver-v4.44-9807-rtm-2025.04.16-windows-x86_x64-intel.exe"},
    {"name": "softether-vpnclient-v4.44-9807-rtm-2025.02.30-windows-x86_x64-intel.exe"},
    {"state": "new"}, {"size": True}, {"size": 0}, {"size": 2**30},
])
def test_reject_invalid_asset(change):
    item = release()
    item["assets"][0].update(change)
    assert select_windows_client([item]) is None


def test_duplicate_assets_or_versions_are_ambiguous():
    item = release()
    item["assets"].append(deepcopy(item["assets"][0]))
    assert select_windows_client([item]) is None
    assert select_windows_client([release(), release()]) is None


def test_malformed_input_and_no_stable_candidate():
    assert select_windows_client([None, {}, {"prerelease": True}]) is None
    with pytest.raises(ValueError):
        select_windows_client({})
    with pytest.raises(ValueError):
        select_windows_client([release()] * 31)
