"""Site version changes and fields the panel does not know, read from pages
already fetched. The network is faked with a saved, anonymised home page
(tests/fixtures/sc_home_page.json); no request ever leaves the process.
"""

import copy
import html
import json
from pathlib import Path

import pytest

from app import config
from app.core import catalog, home, metadata, page, source_budget, source_fields

FIXTURE = Path(__file__).parent / "fixtures" / "sc_home_page.json"
HOST = "s.test"


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.ok = status_code < 400

    def raise_for_status(self):
        pass


def _html(payload: dict) -> str:
    return ('<html><body><div id="app" data-page="'
            + html.escape(json.dumps(payload), quote=True) + '"></div></body></html>')


@pytest.fixture
def home_page():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def site(monkeypatch, home_page):
    """Every page of the fake site answers *payload* (the home page by default)."""
    state = {"payload": home_page, "calls": []}

    def fake_get(url, *args, **kwargs):
        state["calls"].append(url)
        return _Response(_html(state["payload"]))

    monkeypatch.setattr(page.requests, "get", fake_get)
    monkeypatch.setattr(home, "_spawn", lambda job: job())
    home.clear_cache()
    yield state
    home.clear_cache()


def _fields_file() -> Path:
    return Path(config.DATA_FILE).with_name(source_fields.FILE_NAME)


def _version_file() -> Path:
    return Path(config.DATA_FILE).with_name(source_budget.VERSION_FILE_NAME)


# ── The fixture itself ────────────────────────────────────────────────────────

def test_the_saved_home_page_parses_into_shelves(site):
    shelves = home.shelves("streamingcommunity", HOST)
    assert [s["key"] for s in shelves] == ["trending", "top10"]
    assert shelves[1]["title"] == "Top 10 di oggi"
    assert [i["id"] for i in shelves[1]["items"]] == [201, 202]


# ── Version change ────────────────────────────────────────────────────────────

def test_a_version_change_drops_search_home_catalog_and_title_caches(site, home_page, monkeypatch):
    monkeypatch.setattr(metadata, "configured_domain", lambda: HOST)
    from app.core import tv
    monkeypatch.setattr(tv, "get_title_props", lambda *a, **k: {"plot": "Trama", "type": "movie"})

    home.shelves("streamingcommunity", HOST)
    site["payload"] = {**home_page, "props": {"titles": home_page["props"]["sliders"][0]["titles"]}}
    page.search("titolo", HOST)
    catalog.browse(HOST, "trending")
    metadata.title_metadata("movie", "101", "titolo-uno", "fixture-version-1")
    for cache in (page._search_cache, home._cache, catalog._cache, metadata._cache):
        assert len(cache), cache.name
    sent = len(site["calls"])

    # The next page already being fetched carries a new deploy.
    assert source_budget.note_version(HOST, "fixture-version-2") is True
    assert len(site["calls"]) == sent            # no request to find that out
    for cache in (page._search_cache, home._cache, catalog._cache, metadata._cache):
        assert len(cache) == 0, cache.name
    assert json.loads(_version_file().read_text("utf-8")) == {
        "versions": {HOST: "fixture-version-2"}}


def test_a_version_change_spares_other_hosts(site):
    page._search_cache.put(("other.test", "q", 1, None), [], host="other.test")
    page._search_cache.put((HOST, "q", 1, None), [], host=HOST)
    source_budget.note_version(HOST, "a")
    source_budget.note_version(HOST, "b")
    assert page._search_cache.entry(("other.test", "q", 1, None)) is not None
    assert page._search_cache.entry((HOST, "q", 1, None)) is None


def test_the_same_version_keeps_the_caches(site):
    home.shelves("streamingcommunity", HOST)
    assert source_budget.note_version(HOST, "fixture-version-1") is False
    assert len(home._cache)


def test_the_last_version_survives_a_restart(site, home_page):
    home.shelves("streamingcommunity", HOST)
    assert json.loads(_version_file().read_text("utf-8"))["versions"] == {
        HOST: "fixture-version-1"}

    # A restart: memory is gone, the file is not.
    source_budget.reset()
    page._search_cache.put((HOST, "q", 1, None), [], host=HOST)
    assert source_budget.note_version(HOST, "fixture-version-1") is False
    assert page._search_cache.entry((HOST, "q", 1, None)) is not None
    assert source_budget.note_version(HOST, "fixture-version-2") is True
    assert page._search_cache.entry((HOST, "q", 1, None)) is None


def test_an_unreadable_version_file_does_not_break_a_page(site):
    _version_file().write_text("{not json", encoding="utf-8")
    assert home.shelves("streamingcommunity", HOST)
    assert json.loads(_version_file().read_text("utf-8"))["versions"][HOST] == "fixture-version-1"


# ── Fields the panel does not know ────────────────────────────────────────────

def test_known_fields_are_not_recorded(site):
    home.shelves("streamingcommunity", HOST)
    assert source_budget.status()["unknown_fields"] == {}
    assert not _fields_file().exists()


def test_a_new_field_is_recorded_by_path_and_never_its_value(site, home_page):
    changed = copy.deepcopy(home_page)
    row = changed["props"]["sliders"][1]["titles"][0]
    row["campo_nuovo"] = "VALORE-SEGRETO"
    row["images"][0]["formato"] = "VALORE-IMMAGINE"
    changed["props"]["sliders"][0]["extra"] = {"x": 1}
    site["payload"] = changed

    shelves = home.shelves("streamingcommunity", HOST)
    # What is shown does not change.
    assert [i["id"] for i in shelves[1]["items"]] == [201, 202]
    assert "campo_nuovo" not in shelves[1]["items"][0]

    expected = {
        "sliders[].titles[].campo_nuovo": 1,
        "sliders[].titles[].images[].formato": 1,
        "sliders[].extra": 1,
    }
    assert source_budget.status()["unknown_fields"] == expected
    saved = _fields_file().read_text("utf-8")
    assert json.loads(saved) == {"fields": expected}
    assert "VALORE" not in saved

    # Counted once per page it is seen on.
    home.clear_cache()
    home.shelves("streamingcommunity", HOST)
    assert source_budget.status()["unknown_fields"]["sliders[].titles[].campo_nuovo"] == 2


def test_search_browse_and_title_pages_are_scanned(site, home_page, monkeypatch):
    rows = copy.deepcopy(home_page["props"]["sliders"][0]["titles"])
    rows[0]["nuovo_in_ricerca"] = True
    site["payload"] = {**home_page, "props": {"titles": rows}}
    assert page.search("titolo", HOST)
    catalog.browse(HOST, "latest")

    from app.core import tv

    class _Json:
        ok = True
        status_code = 200

        def json(self):
            return {"version": "fixture-version-1",
                    "props": {"title": {"id": 1, "name": "T", "plot": "p",
                                        "trailers": [{"youtube_id": "x", "lingua": "it"}],
                                        "scheda_extra": 3}}}

    monkeypatch.setattr(tv, "get_localized", lambda *a, **k: _Json())
    assert tv.get_title_props(1, "t", "fixture-version-1", HOST)["plot"] == "p"

    fields = source_budget.status()["unknown_fields"]
    assert fields["titles[].nuovo_in_ricerca"] == 2       # search + browse
    assert fields["title.scheda_extra"] == 1
    assert fields["title.trailers[].lingua"] == 1


def test_odd_shapes_do_not_crash_the_scan():
    props = {"sliders": [{"name": "x", "titles": "non una lista"}, "non un dizionario",
                         {"name": "y", "titles": [None, 3, {"id": 9, "images": None,
                                                            7: "chiave non testuale",
                                                            "x" * 500: 1}]}],
             "genres": None}
    found = source_fields.unknown_paths("home", props)
    assert found == {"sliders[].titles[].<?>"}
    assert source_fields.unknown_paths("title", ["non un dizionario"]) == set()
    assert source_fields.unknown_paths("sconosciuto", props) == set()


def test_a_long_or_odd_key_is_named_generically(site, home_page):
    changed = copy.deepcopy(home_page)
    changed["props"]["sliders"][0]["titles"][0]["k" * 300] = 1
    site["payload"] = changed
    assert home.shelves("streamingcommunity", HOST)
    assert source_budget.status()["unknown_fields"] == {"sliders[].titles[].<?>": 1}


def test_the_field_limit_is_respected(site, home_page):
    many = copy.deepcopy(home_page)
    row = many["props"]["sliders"][0]["titles"][0]
    for n in range(source_fields.MAX_FIELDS + 50):
        row[f"campo_{n:03d}"] = n
    site["payload"] = many
    home.shelves("streamingcommunity", HOST)
    fields = source_budget.status()["unknown_fields"]
    assert len(fields) == source_fields.MAX_FIELDS
    assert len(json.loads(_fields_file().read_text("utf-8"))["fields"]) == source_fields.MAX_FIELDS

    # Past the limit a known path still counts, a new one is dropped.
    home.clear_cache()
    home.shelves("streamingcommunity", HOST)
    fields = source_budget.status()["unknown_fields"]
    assert len(fields) == source_fields.MAX_FIELDS
    assert set(fields.values()) == {2}


def test_the_status_route_reports_names_and_counts(client, site, home_page):
    changed = copy.deepcopy(home_page)
    changed["props"]["genres"][0]["colore"] = "#fff"
    site["payload"] = changed
    home.shelves("streamingcommunity", HOST)
    response = client.get("/api/source/status")
    assert response.status_code == 200
    assert response.json()["unknown_fields"] == {"genres[].colore": 1}
    assert "#fff" not in response.text
