"""What the panel says back: stored secrets, and third-party text on the
Downloads page."""

import re
from pathlib import Path

import pytest

from app import downloads_hooks
from app.requests import apprise_channel

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


# ── 14. Stored secrets are write-only ──────────────────────────────────────────

def test_hooks_are_listed_without_their_url_or_header_values(client):
    headers = {}
    downloads_hooks.create_hook(
        name="Jellyfin", url="http://jf.local:8096/Library/Refresh?api_key=URLSECRET1234",
        headers={"X-Emby-Token": "HEADERSECRET", "Authorization": "Bearer HEADERSECRET"},
    )

    listed = client.get("/api/download-hooks")
    (hook,) = listed.json()["hooks"]

    assert "URLSECRET" not in listed.text and "HEADERSECRET" not in listed.text
    assert "url" not in hook
    assert hook["url_masked"] == "http://jf.local/…1234"
    assert set(hook["headers"]) == {"X-Emby-Token", "Authorization"}
    assert len(set(hook["headers"].values())) == 1

    created = client.post("/api/download-hooks", headers=headers, json={
        "name": "n", "url": "https://hooks.example/T0P/SECRETPATH", "headers": {"X-Key": "K3Y"},
    })
    patched = client.patch(f"/api/download-hooks/{hook['id']}", headers=headers,
                           json={"enabled": False})
    for response in (created, patched):
        assert response.status_code == 200
        assert "url" not in response.json()["hook"]
        for secret in ("SECRETPATH", "K3Y", "URLSECRET", "HEADERSECRET"):
            assert secret not in response.text


def test_redaction_does_not_touch_what_is_stored_or_sent(client):
    created = downloads_hooks.create_hook(
        name="x", url="http://jf.local/refresh?api_key=URLSECRET", headers={"X-Key": "K3Y"},
    )
    client.get("/api/download-hooks")
    stored = downloads_hooks.get_hook(created["id"])
    assert stored["url"].endswith("URLSECRET") and stored["headers"] == {"X-Key": "K3Y"}


def test_notification_channels_are_listed_with_a_masked_url(client, monkeypatch):
    headers = {}
    monkeypatch.setattr(apprise_channel, "url_is_valid", lambda url: "://" in url)
    secret = "discord://1234567890/WEBHOOKTOKENSECRETabcd"

    created = client.post("/api/notification-channels", headers=headers,
                          json={"name": "Discord", "apprise_url": secret})
    channel_id = created.json()["id"]
    listed = client.get("/api/notification-channels")
    patched = client.patch(f"/api/notification-channels/{channel_id}", headers=headers,
                           json={"enabled": False})

    for response in (created, listed, patched):
        assert response.status_code == 200
        assert "WEBHOOKTOKENSECRET" not in response.text
        assert "1234567890" not in response.text
    assert listed.json()["channels"][0]["apprise_url"] == "discord://…abcd"
    # Still the real one where it is used.
    assert apprise_channel.get_channel(channel_id)["apprise_url"] == secret


@pytest.mark.parametrize("url, masked", [
    ("tgram://123:ABCDEFGH/chat", "tgram://…chat"),
    ("ntfy://ab", "ntfy://…"),              # too short to show any of it
    ("not-a-url", "…"),
    ("", "…"),
])
def test_mask_url(url, masked):
    assert apprise_channel.mask_url(url) == masked


# ── 5. Third-party text on the Downloads page ──────────────────────────────────

def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}\n", start)]


def test_job_cards_escape_everything_that_comes_from_a_job():
    """j.error is whatever the source site or FFmpeg said, a phase can carry a
    language code read off a playlist, and the card is assembled as markup."""
    source = (STATIC / "page-downloads.js").read_text(encoding="utf-8")
    card = _function(source, "_buildJobCard")
    steps = _function(source, "_buildStepsHtml")

    # The retry line (j.error) and the progress line (progress.encoder).
    assert "${escapeHtml(infoStr) ||" in card
    assert "${infoStr" not in card
    assert "escapeHtml(j.error" in card
    assert "${escapeHtml(label)}" in card
    assert 'data-phase="${escapeHtml(p)}"' in steps and "${escapeHtml(_stepLabel(p))}" in steps

    # Nothing from a job reaches the card unescaped: every interpolation is
    # either escaped, or one of the values the card computes itself.
    safe = {
        "borderColor", "badgeClass", "barClass", "animated", "barWidth", "infoTitle",
        "stepsHtml", "fireBtn", "dateLabel", "j.job_id",
    }
    for expression in re.findall(r"\$\{([^{}]+)\}", card[card.index("return `<div"):]):
        expression = expression.strip()
        assert (
            expression in safe
            or expression.startswith("escapeHtml(")
            or expression.startswith(("isMovie?", "j.status===", "stopBtn ?"))
            or expression.startswith("stopBtn")
        ), expression


def test_escape_html_is_the_shared_helper():
    core = (STATIC / "core.js").read_text(encoding="utf-8")
    body = _function(core, "escapeHtml")
    for character in ("&amp;", "&lt;", "&gt;", "&quot;"):
        assert character in body
