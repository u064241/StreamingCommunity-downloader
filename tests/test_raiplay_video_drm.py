"""A RaiPlay video page is queued only when its own data declares no DRM."""
import json as _json

import pytest

from app.providers.contracts import ProviderError
from app.providers.raiplay import parser
from app.providers.raiplay.client import RaiPlayClient, RaiPlayUnavailable
from app.providers.raiplay.provider import RaiPlayProvider

PAGE = "https://www.raiplay.it/video/2026/01/episodio-1.html"


class FakeTransport:
    """Answers only the video JSON twin of PAGE; anything else is a test failure."""

    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def __call__(self, method, url, *, json, headers, timeout):
        self.calls.append(url)
        if self.error:
            raise self.error
        assert url == "https://www.raiplay.it/video/2026/01/episodio-1.json", url

        class Response:
            status_code = 200

            def __init__(self, data):
                self.content = _json.dumps(data).encode("utf-8")

        return Response(self.payload)


def provider(transport):
    return RaiPlayProvider(client=RaiPlayClient(transport=transport, min_interval=0))


def test_video_without_drm_is_downloadable():
    transport = FakeTransport({"name": "Episodio 1", "rights_management": {"rights": {}}})
    assert provider(transport).video_downloadable(PAGE) == (True, None)
    assert transport.calls == ["https://www.raiplay.it/video/2026/01/episodio-1.json"]


def test_video_with_drm_declared_at_top_level_is_refused():
    transport = FakeTransport({"name": "Film", "drm": {"widevine": True}})
    downloadable, reason = provider(transport).video_downloadable(PAGE)
    assert downloadable is False
    assert "DRM" in reason


def test_video_with_drm_one_level_down_is_refused():
    assert parser.video_drm({"program_info": {"license_url": "https://lic.example"}}) is True


def test_unreadable_video_data_fails_closed_as_upstream_error():
    transport = FakeTransport(error=RaiPlayUnavailable("down"))
    with pytest.raises(ProviderError):
        provider(transport).video_downloadable(PAGE)


@pytest.mark.parametrize("path", ["/video/../programmi/x.json", "/programmi/x.json", "/video/x.html"])
def test_client_refuses_non_video_paths(path):
    client = RaiPlayClient(transport=FakeTransport({}), min_interval=0)
    with pytest.raises(ValueError):
        client.video(path)
