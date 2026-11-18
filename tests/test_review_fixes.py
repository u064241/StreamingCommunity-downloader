"""Regression tests for the pre-release review of 2026-11-18. All offline."""
import threading
from pathlib import Path

import pytest

from app.core import ffmpeg_update, raiplay
from app.core import animeunity_catalog as cat


# ── ffmpeg_update: only PyPI's own file host is trusted ──────────────────────

def test_ffmpeg_wheel_from_foreign_host_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(ffmpeg_update, "_wheel_info", lambda: {"urls": []})
    monkeypatch.setattr(ffmpeg_update, "_select_wheel", lambda info: {
        "filename": "imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl",
        "url": "https://evil.example/ffmpeg.whl",
        "digests": {"sha256": "0" * 64},
    })
    monkeypatch.setattr(ffmpeg_update, "_root", lambda: tmp_path)

    def must_not_download(*_a, **_k):
        raise AssertionError("download attempted from a foreign host")

    monkeypatch.setattr(ffmpeg_update, "urlopen", must_not_download)
    with pytest.raises(RuntimeError, match="non attendibile"):
        ffmpeg_update.update()


# ── AnimeUnity cache: a slow fetch must not block other keys ─────────────────

def test_slow_fetch_does_not_block_other_catalogue_keys():
    cat.clear_cache()
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return {"slow": True}

    worker = threading.Thread(target=lambda: cat._cached(("slow-key",), slow))
    worker.start()
    try:
        assert started.wait(5)
        result = {}
        other = threading.Thread(target=lambda: result.update(v=cat._cached(("fast-key",), lambda: {"fast": 1})))
        other.start()
        other.join(3)
        assert not other.is_alive(), "a different key waited for the slow fetch"
        assert result["v"] == {"fast": 1}
    finally:
        release.set()
        worker.join(5)
        cat.clear_cache()


def test_repeated_identical_request_is_served_from_cache():
    cat.clear_cache()
    calls = []

    def fetch():
        calls.append(1)
        return {"n": len(calls)}

    first = cat._cached(("same",), fetch)
    second = cat._cached(("same",), fetch)
    assert first == second == {"n": 1}
    assert len(calls) == 1
    cat.clear_cache()


# ── RaiPlay output template: '%' escaped in the directory too ────────────────

def test_outtmpl_escapes_percent_in_directory_and_title():
    import os
    template = raiplay._outtmpl(Path("D:/Libreria/100% Anime"), "Serie 50%")
    expected_dir = str(Path("D:/Libreria/100% Anime")).replace("%", "%%")
    assert template == os.path.join(expected_dir, "Serie 50%%.%(ext)s")


# ── HTTP routes: refusals are fail-closed ────────────────────────────────────

def test_unlimited_source_requests_need_free_profile_consent(client):
    response = client.put("/api/domain/settings", json={"source_requests_per_hour": 0})
    assert response.status_code == 400
    assert "profilo libero" in response.text


def test_explicit_false_ack_cannot_bypass_consent_already_stored(client, monkeypatch):
    from app.routers import domain
    monkeypatch.setattr(domain, "get_settings", lambda: {"source_requests_per_hour": 60,
                                                         "free_profile_acknowledged": True})
    response = client.put("/api/domain/settings", json={"source_requests_per_hour": 0,
                                                        "free_profile_acknowledged": False})
    assert response.status_code == 400


def test_portable_prepare_and_apply_stay_closed_without_a_published_zip(monkeypatch):
    from fastapi import HTTPException
    from app.routers import updates as routes

    assert routes.PORTABLE_ZIP_PUBLISHED is False
    monkeypatch.setattr(routes.portable_updates, "prepare", lambda *a: pytest.fail("prepare reached"))
    with pytest.raises(HTTPException) as exc:
        routes.prepare_portable(user=type("U", (), {"id": 1})())
    assert exc.value.status_code == 409
