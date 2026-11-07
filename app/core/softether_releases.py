"""Offline selection of official stable Windows client releases; no I/O."""

from dataclasses import dataclass
from datetime import datetime
import re


REPOSITORY = "SoftEtherVPN/SoftEtherVPN_Stable"
_TAG = re.compile(r"v(\d{1,3})\.(\d{1,3})-(\d{1,9})-rtm", re.ASCII)


@dataclass(frozen=True)
class ClientRelease:
    version: tuple[int, int, int]
    tag: str
    asset_name: str
    asset_url: str
    release_url: str


def select_windows_client(releases: list) -> ClientRelease | None:
    """Select by version, never by upload order or GitHub's latest marker.

    The caller handles transport, size limits, caching and installed-version
    detection. Unknown/ambiguous data returns None, not 'already up to date'.
    """
    if not isinstance(releases, list) or len(releases) > 30:
        raise ValueError("Expected at most 30 release records")
    candidates = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        if release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not (match := _TAG.fullmatch(tag)):
            continue
        version = tuple(map(int, match.groups()))
        release_url = f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
        if release.get("html_url") != release_url:
            continue
        assets = release.get("assets")
        if not isinstance(assets, list) or len(assets) > 200:
            continue
        pattern = re.compile(
            rf"softether-vpnclient-{re.escape(tag)}-(\d{{4}}\.\d{{2}}\.\d{{2}})"
            r"-windows-x86_x64-intel\.exe", re.ASCII,
        )
        matching = []
        for asset in assets:
            if not isinstance(asset, dict) or asset.get("state") != "uploaded":
                continue
            name = asset.get("name")
            if not isinstance(name, str) or not (asset_match := pattern.fullmatch(name)):
                continue
            try:
                datetime.strptime(asset_match[1], "%Y.%m.%d")
            except ValueError:
                continue
            size = asset.get("size")
            if type(size) is not int or not 0 < size <= 256 * 1024 * 1024:
                continue
            url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
            if asset.get("browser_download_url") == url:
                matching.append(ClientRelease(version, tag, name, url, release_url))
        if len(matching) > 1:
            return None  # Never silently choose between repackaged same-tag binaries.
        candidates.extend(matching)
    if not candidates:
        return None
    newest = max(item.version for item in candidates)
    winners = [item for item in candidates if item.version == newest]
    return winners[0] if len(winners) == 1 else None
