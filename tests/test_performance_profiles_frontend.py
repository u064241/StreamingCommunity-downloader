"""The profile table in page-settings.js must equal PERFORMANCE_PRESETS in app/config.py.

The server is authoritative; the page only mirrors its values so the form does not
lie before a save. A drift here is exactly the bug that showed Libero as Veloce.
"""
import re
from pathlib import Path

from app import config

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "page-settings.js"
KEYS = ("max_concurrent_downloads", "max_segment_workers", "source_max_connections",
        "source_requests_per_hour")


def _js_table():
    text = JS.read_text(encoding="utf-8")
    block = re.search(r"const PERFORMANCE_PROFILE_VALUES = \{(.*?)\n\};", text, re.S)
    assert block, "PERFORMANCE_PROFILE_VALUES not found in page-settings.js"
    table = {}
    for name, body in re.findall(r"(\w+): \{(.*?)\}", block.group(1)):
        table[name] = {key: int(value) for key, value in re.findall(r"(\w+): (\d+)", body)}
    return table


def test_js_profiles_match_server_presets():
    table = _js_table()
    assert set(table) == set(config.PERFORMANCE_PRESETS)
    for name, preset in config.PERFORMANCE_PRESETS.items():
        assert table[name] == {key: preset[key] for key in KEYS}, name


def test_free_profile_is_300_requests_not_unlimited():
    assert _js_table()["free"]["source_requests_per_hour"] == 300
