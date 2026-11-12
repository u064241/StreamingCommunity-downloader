"""Load on the source: one user agent per process, artwork kept in memory."""

from app.core import headers, m3u8
from app.routers import images


def test_the_user_agent_is_the_same_on_every_call(monkeypatch):
    monkeypatch.setattr(headers, "_user_agent", None)
    picks = []
    monkeypatch.setattr(headers, "_pick_user_agent", lambda: picks.append(1) or f"UA-{len(picks)}")

    first = headers.get_headers()
    second = headers.get_headers()

    assert first == second == "UA-1"
    assert len(picks) == 1


def test_source_connections_are_capped_across_jobs():
    # The semaphore is the single gate for segment requests from every job.
    assert m3u8._SOURCE_MAX_CONNECTIONS >= 1
    assert m3u8._SOURCE_SLOTS.maximum == m3u8._SOURCE_MAX_CONNECTIONS


def test_a_cached_image_is_not_fetched_again(monkeypatch):
    images._cache.clear()
    key = ("streamingcommunityz.nexus", "a.webp")
    images._cache_put(key, (b"img", "image/webp"))

    assert images._cache_get(key) == (b"img", "image/webp")


def test_the_image_cache_is_bounded(monkeypatch):
    images._cache.clear()
    monkeypatch.setattr(images, "_CACHE_MAX_ENTRIES", 3)
    for i in range(10):
        images._cache_put(("d", f"{i}.webp"), (b"x", "image/webp"))

    assert len(images._cache) == 3
    assert images._cache_get(("d", "0.webp")) is None
    assert images._cache_get(("d", "9.webp")) is not None


def test_an_oversized_image_is_not_kept(monkeypatch):
    images._cache.clear()
    monkeypatch.setattr(images, "_CACHE_MAX_BYTES", 4)
    images._cache_put(("d", "big.webp"), (b"12345", "image/webp"))

    assert images._cache_get(("d", "big.webp")) is None
