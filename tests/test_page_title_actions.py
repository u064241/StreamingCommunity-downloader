"""Every data-action emitted by page-title.js must have a handler in its action map.

A mismatch (e.g. 'tp:raiplayEpisode' emitted, 'tp:raiEpisode' registered) leaves the
button silently inert, so this guards the wiring statically.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app" / "static" / "page-title.js"


def test_emitted_title_actions_are_registered():
    text = SRC.read_text(encoding="utf-8")
    emitted = set(re.findall(r"data-action=\"\$\{[^}]*?'(tp:[A-Za-z]+)'", text))
    emitted |= set(re.findall(r"data-action=\"(tp:[A-Za-z]+)\"", text))
    registered = set(re.findall(r"^\s*'(tp:[A-Za-z]+)'\s*:", text, re.MULTILINE))
    assert emitted, "no tp: actions found, regex out of date"
    missing = sorted(emitted - registered)
    assert not missing, f"data-action without handler: {missing}"


def test_raiplay_episode_button_uses_registered_action():
    text = SRC.read_text(encoding="utf-8")
    assert "'tp:raiplayEpisode'" not in text
    assert "'tp:raiEpisode'" in text
