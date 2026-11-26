"""The downloader in use is announced on the job (engine event): no network, no real downloads."""
import asyncio

from app.progress import WebProgressBar, announce_engine


def test_web_bar_announces_engine_to_its_job_callback():
    seen = []
    bar = WebProgressBar(total=3, job_queue=asyncio.Queue(), loop=None, phase="video",
                         on_event=seen.append)
    announce_engine(bar, "aria2c")
    assert {"type": "engine", "engine": "aria2c"} in seen


def test_announce_is_a_no_op_for_a_plain_bar():
    class PlainBar:
        def update(self, n=1, bytes=0):
            pass

    announce_engine(PlainBar(), "yt-dlp")  # must not raise


def test_each_engine_name_is_passed_through():
    seen = []
    bar = WebProgressBar(total=1, job_queue=asyncio.Queue(), loop=None, phase="audio_ita",
                         on_event=seen.append)
    for name in ("aria2c", "yt-dlp", "interno"):
        announce_engine(bar, name)
    assert [m["engine"] for m in seen if m.get("type") == "engine"] == ["aria2c", "yt-dlp", "interno"]
