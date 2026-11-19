"""Telegram channel parsing: a blocked domain (written defanged, "[.]") is never
a candidate, and after the word "Nuovo" only the new domain counts."""
from types import SimpleNamespace

import pytest

from app.core import domain_recovery


def _candidates(monkeypatch, html):
    response = SimpleNamespace(text=html, raise_for_status=lambda: None)
    monkeypatch.setattr(domain_recovery.requests, "get", lambda *a, **kw: response)
    return domain_recovery.fetch_telegram_candidates()


def _message(text):
    return f'<div class="tgme_widget_message_text">{text}</div>'


def test_latest_post_picks_new_domain_not_the_blocked_one(monkeypatch):
    # Real post of 2026-11-16: the blocked host comes first in the text.
    html = _message("🔄 Dominio Aggiornato (16/11/2026) Bloccati: • streamingcommunityz[.]nexus "
                    "Nuovo: streamingcommunityz.dad")
    assert _candidates(monkeypatch, html) == ["streamingcommunityz.dad"]


def test_older_post_with_same_layout_still_picks_its_new_domain(monkeypatch):
    html = _message("🔄 Dominio Aggiornato (09/11/2026) Bloccati: • streamingcommunityz[.]boo "
                    "Nuovo: streamingcommunityz.nexus")
    assert _candidates(monkeypatch, html) == ["streamingcommunityz.nexus"]


def test_a_post_with_only_blocked_domains_yields_no_candidate(monkeypatch):
    html = _message("Bloccati: streamingcommunityz[.]nexus streamingcommunityz[.]dad")
    assert _candidates(monkeypatch, html) == []


def test_a_link_to_a_blocked_domain_is_not_taken_when_nuovo_is_present(monkeypatch):
    html = ('<div class="tgme_widget_message_text">Bloccati: '
            '<a href="https://streamingcommunityz.nexus">vecchio</a> '
            'Nuovo: streamingcommunityz.dad</div>')
    assert _candidates(monkeypatch, html) == ["streamingcommunityz.dad"]


def test_plain_post_without_nuovo_still_yields_its_domain(monkeypatch):
    html = _message("Il sito ora è su streamingcommunityz.day")
    assert _candidates(monkeypatch, html) == ["streamingcommunityz.day"]


@pytest.mark.parametrize("bad", ["streamingcommunityz[.]nexus", "streamingcommunityz[.]boo"])
def test_defanged_host_alone_is_never_a_candidate(monkeypatch, bad):
    assert _candidates(monkeypatch, _message(bad)) == []
