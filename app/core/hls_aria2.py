"""Segment prefetch with aria2c (opt-in: setting ``hls_use_aria2c``, off by default).

aria2c downloads the segments a media playlist still lacks, in chunks. Each
file it delivers is handed back to the built-in writer, which decrypts and
stores it exactly as a segment fetched by the built-in downloader. Anything
aria2c does not deliver — and every byte-range segment — is left to the
built-in downloader, which keeps its retries, pushback handling and source cap.

Pushback (a 429 or 503 seen in aria2c's output) stops the prefetch at once: the
built-in downloader takes over and its limiter decides how fast to go.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

CHUNK_SEGMENTS = 50          # one aria2c process per chunk: cancel and pushback checkpoints
MAX_CONNECTIONS = 16         # hard ceiling on -j, whatever the caller asks for
POLL_S = 0.25
_PUSHBACK = re.compile(rb"\b(429|503)\b")


def executable() -> str | None:
    """aria2c on PATH, or None: the feature is then simply not used."""
    return shutil.which("aria2c")


def connections_for(cap: int) -> int:
    """The -j to use for a source cap: never above the cap, never above the ceiling."""
    return max(1, min(MAX_CONNECTIONS, int(cap)))


def _command(binary: str, listing: str, work_dir: str, connections: int, headers: dict) -> list[str]:
    cmd = [binary, "-i", listing, "-d", work_dir, "-j", str(connections), "-x", "1", "-s", "1",
           "--max-tries=3", "--retry-wait=2", "--allow-overwrite=true", "--auto-file-renaming=false",
           "--console-log-level=warn", "--show-console-readout=false", "--summary-interval=0",
           "--download-result=hide"]
    for name, value in headers.items():
        if name.lower() == "user-agent":
            cmd.append(f"--user-agent={value}")
        else:
            cmd.append(f"--header={name}: {value}")
    return cmd


def fetch_chunk(binary: str, items: list[tuple[int, str]], headers: dict, connections: int,
                work_dir: str, store, cancel_event=None) -> bool:
    """Download *items* ((segment index, absolute URL) pairs) and pass each whole file to *store*.

    *store(index, path)* is called for every file aria2c delivered; the caller
    reads and removes it. Returns True when a 429 or 503 was seen (pushback).
    Cancelling kills aria2c and returns False; the caller checks the flag itself.
    """
    os.makedirs(work_dir, exist_ok=True)
    listing = os.path.join(work_dir, "segments.txt")
    with open(listing, "w", encoding="utf-8") as fh:
        for index, url in items:
            fh.write(f"{url}\n  out=seg{index}.bin\n")
    log_path = os.path.join(work_dir, "aria2c.log")
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(_command(binary, listing, work_dir, min(connections, len(items)), headers),
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    return False
                time.sleep(POLL_S)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    with open(log_path, "rb") as log:
        pushback = bool(_PUSHBACK.search(log.read()))
    for index, _url in items:
        path = os.path.join(work_dir, f"seg{index}.bin")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            store(index, path)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    if pushback:
        logger.warning("aria2c: la fonte ha risposto 429/503, passo al downloader interno")
    return pushback


def chunks(items: list, size: int = CHUNK_SEGMENTS):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def cleanup(work_dir: str) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)
