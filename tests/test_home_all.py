"""/api/home?source=all: StreamingCommunity's shelves, then AnimeUnity's.

``home.shelves`` is mocked throughout: no request reaches any source (and
conftest blocks the network anyway)."""

import pytest

from app.auth.permissions import ALL_PERMISSIONS
from app.core import home
from tests.conftest import do_setup, make_user, session_for


@pytest.fixture(autouse=True)
def _empty_cache():
    home.clear_cache()
    yield
    home.clear_cache()


@pytest.fixture
def signed_in(client, admin_credentials):
    do_setup(client, admin_credentials)
    user = make_user("boss", "jf-boss-id", int(ALL_PERMISSIONS))
    client.cookies.clear()
    return session_for(client, user.id)


def _item(n, kind):
    return {"id": n, "name": f"T{n}", "slug": f"t-{n}", "type": kind}


_FAKE = {
    "streamingcommunity": [
        {"key": "trending", "title": "Di tendenza", "items": [_item(1, "movie")]},
        {"key": "top10", "title": "Top 10 di oggi", "items": [_item(2, "tv")]},
    ],
    "animeunity": [
        {"key": "latest", "title": "Ultimi episodi", "items": [_item(3, "anime")]},
    ],
}


@pytest.fixture
def fake_shelves(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "configured_domain", lambda: "source.test")
    calls = []

    def shelves(source, host):
        calls.append(source)
        value = _FAKE[source]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(home, "shelves", shelves)
    return calls


def test_all_stacks_sc_then_au_with_prefixed_titles(client, signed_in, fake_shelves):
    response = client.get("/api/home?source=all")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "all"
    assert [(s["source"], s["key"]) for s in body["shelves"]] == [
        ("streamingcommunity", "trending"), ("streamingcommunity", "top10"), ("animeunity", "latest")]
    assert [s["title"] for s in body["shelves"]] == [
        "StreamingCommunity · Di tendenza", "StreamingCommunity · Top 10 di oggi",
        "AnimeUnity · Ultimi episodi"]
    assert [s["base_title"] for s in body["shelves"]] == ["Di tendenza", "Top 10 di oggi", "Ultimi episodi"]
    # Every card carries its own source, so a click opens the right catalogue.
    assert [i["source"] for s in body["shelves"] for i in s["items"]] == [
        "streamingcommunity", "streamingcommunity", "animeunity"]
    # Exactly one front-page read per source: nothing beyond what each start page does.
    assert sorted(fake_shelves) == ["animeunity", "streamingcommunity"]


def test_a_failing_anime_source_still_answers_streamingcommunity(client, signed_in, fake_shelves, monkeypatch):
    monkeypatch.setitem(_FAKE, "animeunity", RuntimeError("down"))

    response = client.get("/api/home?source=all")

    assert response.status_code == 200
    assert [s["source"] for s in response.json()["shelves"]] == ["streamingcommunity"] * 2


def test_no_domain_still_answers_animeunity(client, signed_in, fake_shelves, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "configured_domain", lambda: "")

    response = client.get("/api/home?source=all")

    assert response.status_code == 200
    assert [s["source"] for s in response.json()["shelves"]] == ["animeunity"]
    assert fake_shelves == ["animeunity"]


def test_both_failing_is_an_empty_page_not_an_error(client, signed_in, fake_shelves, monkeypatch):
    monkeypatch.setitem(_FAKE, "animeunity", RuntimeError("down"))
    monkeypatch.setitem(_FAKE, "streamingcommunity", RuntimeError("down"))

    response = client.get("/api/home?source=all")

    assert response.status_code == 200
    assert response.json()["shelves"] == []


def test_combine_all_is_pure_and_keeps_keys():
    original = [{"key": "k", "title": "T", "items": []}]
    out = home.combine_all([("animeunity", original), ("streamingcommunity", [])])
    assert out == [{"key": "k", "title": "AnimeUnity · T", "base_title": "T",
                    "source": "animeunity", "items": []}]
    assert original == [{"key": "k", "title": "T", "items": []}]
