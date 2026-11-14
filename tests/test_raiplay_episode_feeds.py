"""Offline regressions for editorial feeds, season selection and episode cards.

Run without optional pytest dependencies:
    python -m unittest tests.test_raiplay_episode_feeds
"""
import json
import unittest
from types import SimpleNamespace

from app.providers.contracts import ProviderError, ProviderErrorCode
from app.providers.raiplay.client import BASE_URL, RaiPlayClient, RaiPlayUnavailable
from app.providers.raiplay import parser
from app.providers.raiplay.provider import RaiPlayProvider


ROOT = "/programmi/esempio"
FEED = ROOT + "/PublishingBlock-episodes/ContentSet-six/episodes.json"


def block(name="Episodi", number=6, set_id="ContentSet-six", block_id="PublishingBlock-episodes"):
    return {"name": name, "id": block_id, "type": "RaiPlay Multimedia Block", "sets": [{
        "name": f"Stagione {number}", "id": set_id,
        "path_id": ROOT + "/" + set_id + ".json", "episode_size": {"number": 1},
    }]}


def card(season="6", name="Un episodio senza codice nel titolo"):
    return {"name": name, "episode_title": name, "season": season, "episode": "1",
            "forma": "Integrale", "weblink": "/video/2026/01/episodio.html"}


class EpisodeFeedTests(unittest.TestCase):
    def client(self, routes):
        calls = []

        def transport(method, url, **kwargs):
            path = url.removeprefix(BASE_URL)
            calls.append(path)
            payload = routes.get(path, (404, "missing"))
            status, body = payload if isinstance(payload, tuple) else (200, payload)
            content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            return SimpleNamespace(status_code=status, content=content)

        return RaiPlayClient(transport=transport, min_interval=0), calls

    def test_catalogue_resolves_legacy_editorial_url_without_html(self):
        client, calls = self.client({ROOT + ".json": {"blocks": [block(), block("Extra", set_id="ContentSet-extra")]},
                                     FEED: {"items": [card()]}})
        provider = RaiPlayProvider(client=client)
        for _ in range(2):
            rows = provider.episodes("programmi:esempio", ROOT + "/episodi/stagione-6", 6)
            self.assertEqual(len(rows), 1)
        self.assertEqual(calls, [ROOT + ".json", FEED])

    def test_season_blocks_and_card_title_are_preserved(self):
        payload = {"name": "Titolo serie", "blocks": [block("Stagione 6"),
                   block("Stagione 1", 1, "ContentSet-one"), block("Extra", set_id="ContentSet-extra")]}
        details = parser.title_details("programmi:esempio", payload)
        self.assertEqual(details.title, "Titolo serie")
        self.assertEqual([s["name"] for s in details.seasons], ["Stagione 1", "Stagione 6"])
        self.assertEqual(details.extra["season_paths"][1]["episodes_path"], FEED)
        self.assertEqual(details.extra["season_paths"][1]["number"], 6)
        self.assertNotIn("extra", details.extra["season_paths"][1]["path"])

    def test_explicit_episode_block_wins_even_after_extra(self):
        rows = parser.season_entries([block("Extra", set_id="ContentSet-extra"), block()])
        self.assertEqual([s["set_id"] for s in rows], ["ContentSet-six"])

    def test_empty_duplicate_season_does_not_hide_populated_feed(self):
        payload = {"seasons": [
            {"label": "Extra", "episodes": [{"label": "Stagione 6", "cards": [card(name="Extra")]}]},
            {"label": "Episodi", "episodes": [{"label": "Stagione 6", "cards": []}]},
            {"label": "Stagione 6", "episodes": [{"label": "Stagione 6", "cards": [card()]}]},
        ]}
        rows = parser.episode_rows(payload, 6)
        self.assertEqual([r["name"] for r in rows], [card()["name"]])

    def test_wrong_season_and_clips_are_excluded(self):
        clip = {**card(), "forma": "Clip"}
        rows = parser.episode_rows({"items": [card("7"), clip, card()]}, 6)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["season"], "6")

    def test_legacy_episode_name_without_numeric_fields(self):
        item = card(name="Serie - S6E1 - Un episodio")
        item.pop("season")
        item.pop("episode")
        self.assertEqual(parser.episode_rows({"items": [item]}, 6)[0]["n"], "1")

    def test_html_component_variants_use_common_transport_and_cache(self):
        variants = [
            f'<rai-episodes set="ContentSet-six" episode_path="episodes.json" block="PublishingBlock-episodes" base_path="{ROOT}">',
            f"<RAI-EPISODES BASE_PATH = '{ROOT}'\nBLOCK='PublishingBlock-episodes' SET='ContentSet-six' EPISODE_PATH='episodes.json'/>",
            f'<rai-episodes base_path={ROOT} block=PublishingBlock-episodes set=ContentSet-six episode_path=episodes.json>',
        ]
        for html in variants:
            with self.subTest(html=html):
                path = ROOT + "/puntate/tutte"
                client, calls = self.client({path: html, FEED: {"items": [card()]}})
                self.assertEqual(client.editorial_episodes(path), {"items": [card()]})
                client.editorial_episodes(path)
                self.assertEqual(calls, [path, FEED])

    def test_http_errors_are_not_reported_as_missing_component(self):
        path = ROOT + "/puntate/tutte"
        for status in (404, 559, 503):
            with self.subTest(status=status):
                client, _ = self.client({path: (status, "")})
                with self.assertRaisesRegex(RaiPlayUnavailable, f"HTTP {status}"):
                    client.editorial_episodes(path)

    def test_missing_component_reports_requested_path(self):
        path = ROOT + "/puntate/tutte"
        client, _ = self.client({path: "<html>Pagina senza episodi</html>"})
        with self.assertRaisesRegex(RaiPlayUnavailable, path):
            client.editorial_episodes(path)

    def test_invalid_paths_are_rejected_without_network(self):
        client, calls = self.client({})
        for path in ("//evil.example/a", ROOT + "/../stagione-6", ROOT + "/episodi/stagione-6?x=y"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                client.editorial_episodes(path)
        self.assertEqual(calls, [])

    def test_provider_rejects_another_programme_path(self):
        client, calls = self.client({})
        with self.assertRaises(ProviderError) as caught:
            RaiPlayProvider(client=client).episodes("programmi:esempio", "/programmi/altro/episodi/stagione-6", 6)
        self.assertIs(caught.exception.code, ProviderErrorCode.INVALID_REQUEST)
        self.assertEqual(calls, [])

    def test_content_set_fallback_when_catalogue_has_no_block_id(self):
        entry = block()
        entry.pop("id")
        client, _ = self.client({ROOT + ".json": {"blocks": [entry]},
                                 ROOT + "/ContentSet-six.json": {"items": [card()]}})
        self.assertEqual(len(parser.episode_rows(client.editorial_episodes(ROOT + "/episodi/stagione-6"), 6)), 1)


if __name__ == "__main__":
    unittest.main()
