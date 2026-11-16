"""Offline LA7 resolver/provider contract tests using synthetic fixtures."""
import json
from pathlib import Path
from urllib.parse import urljoin

import pytest
import requests

from app.core import la7
from app.providers.la7 import LA7Provider

FIXTURES = Path(__file__).parent / "fixtures" / "la7"
URL = "https://www.la7play.it/programma/puntata-di-prova-399460"
MANIFEST = (la7.CDN_HOST + "/local/hls/,/nk/content/entry/data/0/408/"
            "test_720,/nk/content/entry/data/0/408/test_sd,.mp4.urlset/master.m3u8")
SINGLE_MANIFEST = (la7.CDN_HOST + "/local/hls/,/nk/content/entry/data/0/408/"
                   "test_720.mp4.urlset/master.m3u8")


class Response:
    def __init__(self, text="", status=200, headers=None):
        self.body = text.encode("utf-8")
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


@pytest.fixture
def payload():
    return json.loads((FIXTURES / "episode.json").read_text(encoding="utf-8"))


@pytest.fixture
def network(payload, monkeypatch):
    """Only explicit fixture URLs can be fetched; no network or media reads."""
    class Network:
        def __init__(self):
            self.calls = []
            self.responses = {
                URL: Response(self.page(payload)),
                MANIFEST: Response((FIXTURES / "master.m3u8").read_text()),
            }
            for quality in ("720", "360"):
                self.responses[urljoin(MANIFEST, f"{quality}/index.m3u8")] = Response(
                    (FIXTURES / "media.m3u8").read_text())

        @staticmethod
        def page(data):
            return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'

        def set_page(self, data):
            self.responses[URL] = Response(self.page(data))

        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            assert kwargs["stream"] is True
            self.calls.append(url)
            assert url in self.responses, f"Unexpected request: {url}"
            return self.responses[url]

    fake = Network()
    monkeypatch.setattr(la7.requests, "get", fake.get)
    return fake


def test_resolve_episode_checks_metadata_and_all_variants(network):
    result = la7.resolve_episode(URL)
    assert result == {"id": "399460", "title": "Puntata di prova & ospiti",
                      "drm": False, "manifest": MANIFEST, "page_url": URL}
    assert set(network.calls) == set(network.responses)
    assert all(response.closed for response in network.responses.values())


def test_flight_json_escapes_and_correlated_content(network):
    network.responses[URL] = Response((FIXTURES / "flight.html").read_text())
    network.responses.update({key.replace(MANIFEST.rsplit("/", 1)[0], SINGLE_MANIFEST.rsplit("/", 1)[0]): value
                              for key, value in list(network.responses.items()) if key.startswith(la7.CDN_HOST)})
    result = la7.resolve_episode(URL)
    assert result["title"] == 'Puntata "speciale" &amp; ospiti'
    assert result["manifest"] == SINGLE_MANIFEST


def test_flight_record_split_across_chunks(network, payload):
    record = "a:" + json.dumps(payload) + "\n"
    chunks = [record[:90], record[90:]]
    network.responses[URL] = Response("".join(
        "<script>self.__next_f.push(" + json.dumps([1, chunk]) + ");</script>"
        for chunk in chunks))
    assert la7.resolve_episode(URL)["id"] == "399460"


@pytest.mark.parametrize("drm", [True, None, "false", 0, {}, []])
def test_reject_protected_or_unknown_drm(network, payload, drm):
    payload["props"]["related"]["drm"] = False
    payload["props"]["episode"]["drm"] = drm
    network.set_page(payload)
    with pytest.raises(la7.LA7Error, match="DRM"):
        la7.resolve_episode(URL)
    assert network.calls == [URL]


def test_missing_drm_cannot_borrow_related_flag(network, payload):
    del payload["props"]["episode"]["drm"]
    payload["props"]["related"]["drm"] = False
    network.set_page(payload)
    with pytest.raises(la7.LA7Error, match="DRM"):
        la7.resolve_episode(URL)


def test_duplicate_identity_with_conflicting_data_rejected(network, payload):
    payload["props"]["related"]["id"] = "399460"
    network.set_page(payload)
    with pytest.raises(la7.LA7Error, match="ambigui"):
        la7.resolve_episode(URL)


def test_wrong_identity_rejected(network, payload):
    payload["props"]["episode"]["id"] = "222222"
    network.set_page(payload)
    with pytest.raises(la7.LA7Error, match="non trovati"):
        la7.resolve_episode(URL)


@pytest.mark.parametrize("url", [
    "http://www.la7play.it/puntata-399460",
    "https://la7play.it.evil.test/puntata-399460",
    "https://evil.test/?url=https://la7play.it/puntata-399460",
    "https://la7play.it@evil.test/puntata-399460",
    "https://user@la7play.it/puntata-399460",
    "https://www.la7play.it:8443/puntata-399460",
    "https://www.la7play.it/puntata?fake=-399460",
    "https://www.la7play.it/puntata-399460#fragment",
    "https://www.la7play.it/\npuntata-399460",
    "https://www.la7play.it/\\evil.test/puntata-399460",
    None,
])
def test_invalid_url_rejected_before_request(network, url):
    with pytest.raises(la7.LA7Error):
        la7.resolve_episode(url)
    assert not network.calls


@pytest.mark.parametrize("path", [
    "https://evil.test/test.mp4", "//evil.test/test.mp4",
    "/nk/../secret.mp4", "/nk/%2e%2e/secret.mp4", "/nk/test.mp4?evil=1",
    "/nk/test.mp4,/nk/../secret.mp4", "/nk/test.mp4,", "",
])
def test_invalid_cdn_path_rejected(network, payload, path):
    payload["props"]["episode"]["uploadVideo"]["cf_remote_url"] = path
    network.set_page(payload)
    with pytest.raises(la7.LA7Error, match="Percorso"):
        la7.resolve_episode(URL)
    assert network.calls == [URL]


@pytest.mark.parametrize("body", ["<html>Access denied</html>", "#EXTM3U\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n",
    '#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://key"\n',
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttps://evil.test/video.m3u8\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nmaster.m3u8\n",
])
def test_invalid_or_protected_manifest_rejected(network, body):
    network.responses[MANIFEST] = Response(body)
    with pytest.raises(la7.LA7Error):
        la7.resolve_episode(URL)


@pytest.mark.parametrize("addition", [
    '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key"',
    '#EXT-X-KEY:METHOD=AES-128,URI="https://awsvodpkg.iltrovatore.it/key"',
    '#EXT-X-MAP:URI="https://evil.test/init.mp4"',
])
def test_media_playlist_protection_and_resource_hosts(network, addition):
    child = urljoin(MANIFEST, "720/index.m3u8")
    body = (FIXTURES / "media.m3u8").read_text().replace("#EXTM3U", "#EXTM3U\n" + addition)
    network.responses[child] = Response(body)
    with pytest.raises(la7.LA7Error):
        la7.resolve_episode(URL)


@pytest.mark.parametrize("target", [URL, MANIFEST])
def test_redirect_to_foreign_host_never_followed(network, target):
    network.responses[target] = Response(status=302, headers={"Location": "https://evil.test/video"})
    with pytest.raises(la7.LA7Error, match="consentito"):
        la7.resolve_episode(URL)
    assert not any("evil.test" in call for call in network.calls)


def test_safe_relative_redirect(network):
    child = urljoin(MANIFEST, "redirected.m3u8")
    network.responses[child] = network.responses[MANIFEST]
    network.responses[MANIFEST] = Response(status=302, headers={"Location": "redirected.m3u8"})
    assert la7.resolve_episode(URL)["manifest"] == MANIFEST
    assert child in network.calls


def test_response_size_limit(network, monkeypatch):
    monkeypatch.setattr(la7, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(la7.LA7Error, match="troppo grande"):
        la7.resolve_episode(URL)
    assert network.responses[URL].closed


def test_http_failure_is_la7_error(network):
    network.responses[MANIFEST] = Response(status=403)
    with pytest.raises(la7.LA7Error, match="non raggiungibile"):
        la7.resolve_episode(URL)


@pytest.mark.parametrize("method", ["resolve_url", "direct_download_metadata"])
def test_provider_returns_checked_metadata(network, method):
    provider = LA7Provider(http_get=network.get)
    result = getattr(provider, method)(URL)
    assert result["source"] == "la7"
    assert result["manifest"] == MANIFEST
    assert result["drm"] is False
    assert provider.capabilities.search
    assert provider.capabilities.details
    assert provider.capabilities.episodes
    assert provider.capabilities.download


def test_provider_propagates_protection_error(network, payload):
    payload["props"]["episode"]["drm"] = True
    network.set_page(payload)
    with pytest.raises(la7.LA7Error, match="DRM"):
        LA7Provider(http_get=network.get).direct_download_metadata(URL)


@pytest.mark.parametrize("body", [
    '#EXTM3U\n#EXT-X-KEY\n#EXT-X-TARGETDURATION:10\n#EXTINF:5,\na.ts\n',
    '#EXTM3U\n#EXT-X-TARGETDURATION:10\na.ts\n#EXTINF:5,\n',
    '#EXTM3U\n#EXT-X-TARGETDURATION:0\n#EXTINF:5,\na.ts\n',
    '#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:NaN,\na.ts\n',
    '#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:5,\nhttps://evil.test/a.ts\n',
    '#EXTM3U\n#EXT-X-STREAM-INF:INVALID=1\n720/index.m3u8\n',
    '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI=https://evil.test/a.m3u8\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=1\n720/index.m3u8\n',
    '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="audio.m3u8",URI=https://evil.test/a.m3u8\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=1\n720/index.m3u8\n',
])
def test_malformed_hls_cannot_produce_metadata(network, body):
    network.responses[MANIFEST] = Response(body)
    with pytest.raises(la7.LA7Error):
        LA7Provider(http_get=network.get).resolve_url(URL)


def test_playlist_limit_is_enforced(network, monkeypatch):
    monkeypatch.setattr(la7, "MAX_PLAYLISTS", 1)
    with pytest.raises(la7.LA7Error, match="complesso|non valido"):
        la7.resolve_episode(URL)


def test_redirect_loop_is_bounded(network):
    network.responses[URL] = Response(status=302, headers={"Location": URL})
    with pytest.raises(la7.LA7Error, match="Troppi redirect"):
        la7.resolve_episode(URL)
    assert len(network.calls) == 4


def test_connection_error_is_normalized(network):
    def failing_get(*args, **kwargs):
        raise requests.Timeout("fixture timeout")

    with pytest.raises(la7.LA7Error, match="non raggiungibile"):
        LA7Provider(http_get=failing_get).resolve_url(URL)


def test_valid_audio_rendition_is_checked(network):
    body = (FIXTURES / "master.m3u8").read_text().replace(
        "#EXTM3U", '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",'
        'NAME="Italiano",URI="audio/index.m3u8"')
    network.responses[MANIFEST] = Response(body)
    audio = urljoin(MANIFEST, "audio/index.m3u8")
    network.responses[audio] = Response((FIXTURES / "media.m3u8").read_text())
    assert la7.resolve_episode(URL)["drm"] is False
    assert audio in network.calls
