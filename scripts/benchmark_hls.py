"""Measure the standalone panel against a local, paced AES-128 HLS fixture.

Run with the project Python environment:
    python scripts/benchmark_hls.py --baseline ba5dfd8
The report and server logs are kept in a new directory under the system temp.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 ** 2
KEY = b"hls-fixture-key!"
CHUNK = 64 * 1024
HTTP = build_opener(ProxyHandler({}))

# Loaded only by the child interpreter. Provider discovery is synthetic;
# requests, segment decryption, job scheduling and FFmpeg use the app code.
BOOTSTRAP = """
import importlib.util
import logging
import os
import sys
import traceback

try:
    from app.core import hosts
    import requests

    origin = os.environ["HLS_BENCH_ORIGIN"]
    prefix = origin + "/"
    send = requests.adapters.HTTPAdapter.send

    def fixture_only(adapter, request, *args, **kwargs):
        if not request.url.startswith(prefix):
            raise RuntimeError("Benchmark HTTP is restricted to its fixture")
        return send(adapter, request, *args, **kwargs)

    def fixture_url(url):
        if not url.startswith(prefix):
            raise hosts.UnsafeURLError("Not the benchmark fixture")
        return url

    requests.adapters.HTTPAdapter.send = fixture_only
    hosts.assert_public_https = fixture_url
    baseline = os.environ.get("HLS_BENCH_BASELINE")
    if baseline:
        from app import core
        name = "app.core.m3u8"
        spec = importlib.util.spec_from_file_location(name, baseline)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        core.m3u8 = module

    from app.core import film
    film._get_iframe = lambda video_id, domain: (
        f"window.video = {{id: {video_id}}}; params: {{'token':'fixture','expires':'0'}}",
        f"https://fixture.invalid/embed/{video_id}",
    )
    film._get_m3u8_url = lambda video, params: f"{origin}/{video['id']}/master.m3u8"
    film._get_m3u8_key = lambda video, params, referer: None
    logging.basicConfig(level=logging.INFO, format="%(created).6f %(levelname)s %(name)s: %(message)s")
except Exception:
    traceback.print_exc()
    os._exit(120)
"""


def fixture(ffmpeg, directory, seconds):
    subprocess.run([
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
        "-threads", "4", "-pix_fmt", "yuv420p", "-b:v", "6M",
        "-maxrate", "7M", "-bufsize", "12M", "-g", "50", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "128k", "-f", "hls", "-hls_time", "2",
        "-hls_playlist_type", "vod", "-hls_segment_filename", "seg-%03d.ts", "media.m3u8",
    ], cwd=directory, check=True, timeout=180)
    media = (directory / "media.m3u8").read_text(encoding="utf-8")
    names = re.findall(r"(?m)^seg-\d+\.ts$", media)
    if not names:
        raise RuntimeError("The generated HLS playlist has no segments")
    tag = '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
    files = {
        "master.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=7500000,RESOLUTION=1920x1080\nmedia.m3u8\n",
        "media.m3u8": media.replace("#EXTINF:", tag + "#EXTINF:", 1).encode(),
        "key.bin": KEY,
    }
    for sequence, name in enumerate(names):
        pad = padding.PKCS7(128).padder()
        body = pad.update((directory / name).read_bytes()) + pad.finalize()
        enc = Cipher(algorithms.AES(KEY), modes.CBC(sequence.to_bytes(16, "big"))).encryptor()
        files[name] = enc.update(body) + enc.finalize()
    return files, names


class FixtureServer(ThreadingHTTPServer):
    def __init__(self, files, retry):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.files = files
        self.retry = retry
        self.attempts = Counter()
        self.events = []
        self.lock = threading.Lock()


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        if len(parts) != 2 or parts[1] not in self.server.files:
            self.send_error(404)
            return
        job, name = parts
        segment = name.endswith(".ts")
        event = None
        status = 200
        body = self.server.files[name]
        if segment:
            with self.server.lock:
                self.server.attempts[job, name] += 1
                if (self.server.retry and name in ("seg-000.ts", "seg-001.ts")
                        and self.server.attempts[job, name] == 1):
                    status, body = 503, b""
                event = {"job": job, "segment": name, "start": time.time(),
                         "status": status, "bytes": len(body)}
                self.server.events.append(event)
            index = int(name[4:-3])
            time.sleep(0.110 + (index * 17 % 61) / 1000)
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if status == 503:
                self.send_header("Retry-After", "2")
            self.end_headers()
            if segment:
                for offset in range(0, len(body), CHUNK):
                    chunk = body[offset:offset + CHUNK]
                    time.sleep(len(chunk) / (5 * MIB))
                    self.wfile.write(chunk)
            else:
                self.wfile.write(body)
        finally:
            if event is not None:
                with self.server.lock:
                    event["end"] = time.time()


def api(port, route, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(f"http://127.0.0.1:{port}{route}", data=data,
                  headers={"Content-Type": "application/json"})
    with HTTP.open(req, timeout=2) as resp:
        return json.load(resp)


def run_panel(directory, environment, count):
    launcher = ["py"] if os.name == "nt" else [sys.executable]
    port = int(environment["PORT"])
    with (directory / "panel.log").open("wb") as log:
        panel = subprocess.Popen([*launcher, "./main.py"], cwd=ROOT, env=environment,
                                 stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 35
            while True:
                if panel.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"Panel did not start; see {log.name}")
                try:
                    api(port, "/api/jobs")
                    break
                except OSError:
                    time.sleep(0.15)

            submitted, done, peaks, ids = {}, {}, Counter(), []
            for job in range(1, count + 1):
                submitted[job] = time.time()
                reply = api(port, "/api/download/film",
                            {"id": job, "title": f"HLS fixture {job}", "subtitle_languages": []})
                ids.append(reply["job_id"])

            deadline = time.monotonic() + 180
            while len(done) < count:
                for row in api(port, "/api/jobs"):
                    job = ids.index(row["job_id"]) + 1
                    peaks[job] = max(peaks[job], row["progress"].get("bytes_speed") or 0)
                    if row["status"] == "done" and job not in done:
                        done[job] = (time.time(), row["output_path"])
                    if row["status"] in ("error", "cancelled"):
                        raise RuntimeError(f"Job {job} failed: {row.get('error')}; see {log.name}")
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Jobs did not finish; see {log.name}")
                time.sleep(0.12)
            return submitted, done, peaks
        finally:
            if panel.poll() is None:
                panel.terminate()
                try:
                    panel.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    panel.kill()
                    panel.wait()


def idle_seconds(events):
    events = sorted(events, key=lambda event: event["start"])
    end = events[0]["end"]
    gaps = []
    for event in events[1:]:
        gaps.append(max(0, event["start"] - end))
        end = max(end, event["end"])
    return round(max(gaps, default=0), 3)


def codec_and_duration(ffmpeg, output):
    probe = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(output)],
                           capture_output=True, text=True, errors="replace", timeout=20).stderr
    codec = re.search(r"Video:\s*(\w+)", probe)
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe)
    if not codec or not duration or "Audio: aac" not in probe:
        raise RuntimeError(f"File lacks expected video/audio streams: {output}\n{probe}")
    return codec[1], 3600 * int(duration[1]) + 60 * int(duration[2]) + float(duration[3])


def measure(case, directory, files, names, ffmpeg, seconds, baseline, bootstrap):
    label, workers, slots, conversion, count, retry, old = case
    directory.mkdir()
    data = {
        "domain": "fixture.invalid", "download_dir": str(directory / "videos"),
        "settings": {
            "domain_auto_check_enabled": False, "max_concurrent_downloads": 1,
            "max_segment_workers": workers, "source_max_connections": slots,
            "source_requests_per_hour": 90, "output_container": "mp4",
            "video_conversion": conversion, "video_encoder": "auto",
        },
    }
    config = directory / "data.json"
    config.write_text(json.dumps(data), encoding="utf-8")
    with FixtureServer(files, retry) as server:
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        env = {
            **os.environ, "HOST": "127.0.0.1", "PORT": str(port),
            "DATA_FILE": str(config), "DB_FILE": str(directory / "panel.db"),
            "SCHEDULE_FILE": str(directory / "schedule.json"),
            "TMP_DIR": str(directory / "staging"), "VIDEOS_DIR": data["download_dir"],
            "LOCALAPPDATA": str(directory / "localappdata"),
            "APPDATA": str(directory / "appdata"),
            "SC_PANEL_OPEN_BROWSER": "0", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(map(str, (bootstrap, ROOT))),
            "HLS_BENCH_ORIGIN": f"http://127.0.0.1:{server.server_port}",
            "HLS_BENCH_BASELINE": str(baseline) if old else "",
            "FFMPEG_PATH": str(ffmpeg), "NO_PROXY": "*",
        }
        # Exercise the fixture's persisted profile without inheriting host overrides.
        for override in ("SC_SOURCE_MAX_CONNECTIONS", "SC_SOURCE_REQUESTS_PER_HOUR"):
            env.pop(override, None)
        if os.name == "nt" and (Path(sys.prefix) / "pyvenv.cfg").is_file():
            env["VIRTUAL_ENV"] = sys.prefix
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            submitted, done, peaks = run_panel(directory, env, count)
        finally:
            server.shutdown()
            thread.join()

    events = server.events
    (directory / "http.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    log = (directory / "panel.log").read_text(encoding="utf-8", errors="replace")
    results = []
    fetched = re.findall(r"^(\d+\.\d+) INFO app\.core\.m3u8: Segments .*?, (\d+) lost", log, re.M)
    starts = re.findall(r"^\d+\.\d+ INFO app\.core\.m3u8: Segment fetch .*?: (\d+) segments, "
                        r"connections cap (\d+), workers (\d+)", log, re.M)
    if starts != [(str(len(names)), str(slots), str(workers))] * count:
        raise RuntimeError(f"Unexpected runtime settings for {label}: {starts}")
    if len(fetched) != count or any(lost != "0" for _, lost in fetched):
        raise RuntimeError(f"Unexpected HLS phase result for {label}: {fetched}")
    for job in range(1, count + 1):
        served = [event for event in events if event["job"] == str(job)]
        successful = [event for event in served if event["status"] == 200]
        if sorted(event["segment"] for event in successful) != sorted(names):
            raise RuntimeError(f"Missing or duplicate segments for {label}, job {job}")
        rejected = len(served) - len(successful)
        if rejected != (2 if retry else 0):
            raise RuntimeError(f"Unexpected HTTP error count for {label}, job {job}: {rejected}")
        start = min(event["start"] for event in served)
        end = max(event["end"] for event in served)
        size = sum(event["bytes"] for event in successful)
        peak = max(sum(other["start"] <= event["start"] < other["end"] for other in served)
                   for event in served)
        if peak > min(workers, slots):
            raise RuntimeError(f"Observed concurrency {peak} exceeds limits for {label}")
        done_at, output = done[job]
        codec, duration = codec_and_duration(ffmpeg, output)
        expected_codec = "hevc" if conversion == "hevc" else "h264"
        if codec != expected_codec or abs(duration - seconds) > 0.5:
            raise RuntimeError(f"Unexpected output for {label}: {codec}, {duration:.2f}s")
        results.append({
            "case": label, "job": job, "slots": slots, "workers": workers,
            "segment_count": len(names), "wire_mib": round(size / MIB, 2),
            "wire_seconds": round(end - start, 3), "mib_per_second": round(size / MIB / (end - start), 3),
            "max_in_flight": peak, "max_no_http_seconds": idle_seconds(served),
            "reported_ui_peak_mib_s": round(peaks[job] / MIB, 3),
            "http_503": rejected, "queue_to_first_segment_seconds": round(start - submitted[job], 3),
            "after_fetch_seconds": round(done_at - float(fetched[job - 1][0]), 3),
            "submit_to_done_seconds": round(done_at - submitted[job], 3),
            "codec": codec, "output_mib": round(Path(output).stat().st_size / MIB, 2),
            "duration_seconds": duration,
            "encoder": re.findall(r"Converting .*? with (.*?), codec=", log),
        })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="Git revision to compare for app/core/m3u8.py")
    parser.add_argument("--seconds", type=int, default=72)
    parser.add_argument("--repeats", type=int, default=2, help="Repeats of each clean copy case")
    parser.add_argument("--ffmpeg", type=Path, default=ROOT / "ffmpeg" / "ffmpeg.exe")
    args = parser.parse_args()
    if args.seconds < 8 or args.repeats < 1:
        parser.error("Use at least 8 seconds and one repeat")
    ffmpeg = args.ffmpeg.resolve(strict=True)
    scratch = Path(tempfile.mkdtemp(prefix="sc-hls-benchmark-"))
    print(f"Artifacts: {scratch}", flush=True)
    bootstrap = scratch / "bootstrap"
    bootstrap.mkdir()
    (bootstrap / "sitecustomize.py").write_text(BOOTSTRAP, encoding="utf-8")
    baseline = None
    if args.baseline:
        baseline = scratch / "baseline_m3u8.py"
        baseline.write_bytes(subprocess.check_output(
            ["git", "show", f"{args.baseline}:app/core/m3u8.py"], cwd=ROOT))
    fixture_dir = scratch / "fixture"
    fixture_dir.mkdir()
    files, names = fixture(ffmpeg, fixture_dir, args.seconds)
    clean = [
        ("current-2s-4w", 4, 2, "copy", 1, False, False),
        ("current-2s-6w", 6, 2, "copy", 1, False, False),
        ("current-3s-6w", 6, 3, "copy", 1, False, False),
        ("current-4s-6w", 6, 4, "copy", 1, False, False),
    ]
    special = [
        ("current-retry-2s-6w", 6, 2, "copy", 1, True, False),
        ("current-copy-queue", 6, 3, "copy", 2, False, False),
        ("current-hevc-queue", 6, 3, "hevc", 2, False, False),
    ]
    if baseline:
        clean.append(("baseline-3s-6w", 6, 3, "copy", 1, False, True))
        special.insert(1, ("baseline-retry-2s-6w", 6, 2, "copy", 1, True, True))
    all_rows = []
    for index, case in enumerate(clean * args.repeats + special):
        rows = measure(case, scratch / f"{index + 1:02d}-{case[0]}",
                       files, names, ffmpeg, args.seconds, baseline, bootstrap)
        all_rows.extend(rows)
        print(json.dumps(rows), flush=True)
    report = {"seconds": args.seconds, "baseline": args.baseline, "runs": all_rows,
              "clean_median_mib_s": {case[0]: round(median(
                  row["mib_per_second"] for row in all_rows if row["case"] == case[0]), 3)
                  for case in clean}}
    (scratch / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["clean_median_mib_s"]), flush=True)


if __name__ == "__main__":
    main()
