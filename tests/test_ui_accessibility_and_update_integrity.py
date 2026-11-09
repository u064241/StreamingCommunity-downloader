from pathlib import Path


ROOT = Path(__file__).parents[1]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_result_cards_are_keyboard_activatable():
    core = read("app/static/core.js")
    search = read("app/static/page-search.js")
    assert "el.click();" in core
    assert 'role="button" tabindex="0"' in search
    assert 'data-action="search:open"' in search


def test_focus_and_long_titles_have_responsive_rules():
    components = read("app/static/css/components.css")
    pages = read("app/static/css/pages.css")
    assert ":focus-visible" in components
    assert "overflow-wrap: anywhere" in pages
    assert ".result-card:focus-visible" in pages


def test_portable_update_keeps_manifest_and_sha256_verification():
    source = read("app/portable_updates.py")
    downloader = read("app/update_download.py")
    assert "Manifest incompatibile con questa release" in source
    assert "record[\"size\"] != asset[\"size\"]" in source
    assert "re.fullmatch(r\"[0-9a-fA-F]{64}\", record[\"sha256\"])" in source
    assert "download_verified(asset[\"url\"], record[\"sha256\"]" in source
    assert "Checksum SHA-256 non corrispondente" in downloader
