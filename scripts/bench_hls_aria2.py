"""Benchmark: same HLS media-playlist segments fetched by two engines.

  A) requests + ThreadPoolExecutor (the same kind of connection pool the app uses)
  B) aria2c, one connection per segment, -j equal to A's concurrency

Only transfer speed is measured: segments are downloaded raw (no decryption),
into a temporary folder that is removed afterwards. Run it yourself, once, on
an episode you are allowed to download; it makes real requests to the URL you
pass. Use a small --limit first.

Example:
  python scripts/bench_hls_aria2.py --playlist "https://host/path/index.m3u8" \
      --referer "https://site/" --limit 40 --concurrency 8 \
      --aria2c "C:\\SW\\aria2\\bin\\aria2-1.37.0-win-64bit-build1\\aria2c.exe"
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urljoin

import requests

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def media_segments(playlist_url: str, headers: dict, limit: int) -> list[str]:
    response = requests.get(playlist_url, headers=headers, timeout=15)
    response.raise_for_status()
    text = response.text
    if "#EXT-X-STREAM-INF" in text:
        raise SystemExit("Master playlist: pass the media playlist (the one listing .ts/.m4s segments).")
    urls = [urljoin(playlist_url, line.strip()) for line in text.splitlines()
            if line.strip() and not line.startswith("#")]
    if not urls:
        raise SystemExit("No segments found in the playlist.")
    return urls[:limit]


def run_requests(urls, headers, concurrency):
    stats = {"bytes": 0, "ok": 0, "errors": {}}

    def fetch(url):
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            raise requests.HTTPError(str(r.status_code))
        return len(r.content)

    started = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(fetch, u) for u in urls]
        for fut in cf.as_completed(futures):
            try:
                stats["bytes"] += fut.result()
                stats["ok"] += 1
            except Exception as exc:  # noqa: BLE001 - we only count outcomes here
                key = str(exc)[:40]
                stats["errors"][key] = stats["errors"].get(key, 0) + 1
    stats["seconds"] = time.monotonic() - started
    return stats


def run_aria2(aria2c, urls, headers, concurrency):
    workdir = tempfile.mkdtemp(prefix="bench-aria2-")
    try:
        listing = os.path.join(workdir, "urls.txt")
        with open(listing, "w", encoding="utf-8") as fh:
            for i, url in enumerate(urls):
                fh.write(f"{url}\n")
                fh.write(f"  out=seg{i:05d}.bin\n")
                fh.write(f"  header=Referer: {headers.get('Referer', '')}\n")
                fh.write(f"  header=User-Agent: {headers.get('User-Agent', DEFAULT_UA)}\n")
        cmd = [aria2c, "-i", listing, "-d", workdir, "-j", str(concurrency), "-x", "1", "-s", "1",
               "--max-tries=3", "--retry-wait=2", "--allow-overwrite=true",
               "--summary-interval=0", "--console-log-level=warn", "--auto-file-renaming=false"]
        started = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        seconds = time.monotonic() - started
        got = [f for f in os.listdir(workdir) if f.startswith("seg")]
        total = sum(os.path.getsize(os.path.join(workdir, f)) for f in got)
        errors = {} if proc.returncode == 0 else {f"aria2c exit {proc.returncode}": 1}
        return {"bytes": total, "ok": len(got), "errors": errors, "seconds": seconds,
                "stderr_tail": (proc.stderr or "")[-400:]}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def report(name, s):
    mb = s["bytes"] / 1_000_000
    rate = mb / s["seconds"] if s["seconds"] else 0.0
    print(f"{name:10s} segments_ok={s['ok']:4d}  MB={mb:8.1f}  s={s['seconds']:7.1f}  MB/s={rate:6.2f}  errors={s['errors'] or '-'}")
    if s.get("stderr_tail"):
        print(f"           aria2c stderr tail: {s['stderr_tail'].strip()[-200:]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--playlist", required=True, help="media playlist URL (not a master playlist)")
    ap.add_argument("--referer", default="", help="Referer header sent with every request")
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    ap.add_argument("--limit", type=int, default=40, help="how many segments to fetch per run")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel transfers for both engines")
    ap.add_argument("--aria2c", default=r"C:\SW\aria2\bin\aria2-1.37.0-win-64bit-build1\aria2c.exe")
    ap.add_argument("--pause", type=float, default=20.0, help="seconds between the two runs")
    ap.add_argument("--order", choices=["requests-first", "aria2-first"], default="requests-first")
    args = ap.parse_args()

    headers = {"User-Agent": args.user_agent}
    if args.referer:
        headers["Referer"] = args.referer
    urls = media_segments(args.playlist, headers, args.limit)
    print(f"segments to fetch: {len(urls)}  concurrency: {args.concurrency}")

    runs = [("requests", lambda: run_requests(urls, headers, args.concurrency)),
            ("aria2c", lambda: run_aria2(args.aria2c, urls, headers, args.concurrency))]
    if args.order == "aria2-first":
        runs.reverse()
    results = {}
    for index, (name, fn) in enumerate(runs):
        if index:
            time.sleep(args.pause)
        results[name] = fn()
        report(name, results[name])
    print("(same segments, same concurrency; compare MB/s, and watch errors for 429/503)")


if __name__ == "__main__":
    main()
