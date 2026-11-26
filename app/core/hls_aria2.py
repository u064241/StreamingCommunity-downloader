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

from app.core import aria2_update

logger = logging.getLogger(__name__)

CHUNK_SEGMENTS = 50          # one aria2c process per chunk: cancel and pushback checkpoints
MAX_CONNECTIONS = 16         # hard ceiling on -j, whatever the caller asks for
POLL_S = 0.25
_PUSHBACK = re.compile(rb"\b(429|503)\b")
# aria2c's notice for a finished file. Its path can hold any digits (a title
# folder named "503"), so these lines are dropped before the pushback search.
_COMPLETE = re.compile(rb"Download complete: (.*?)\s*$")


def executable() -> str | None:
    """The managed aria2c.exe, else aria2c on PATH, else None: the feature is then not used."""
    return aria2_update.executable()


def connections_for(cap: int) -> int:
    """The -j to use for a source cap: never above the cap, never above the ceiling."""
    return max(1, min(MAX_CONNECTIONS, int(cap)))


def _command(binary: str, listing: str, work_dir: str, connections: int, headers: dict) -> list[str]:
    cmd = [binary, "-i", listing, "-d", work_dir, "-j", str(connections), "-x", "1", "-s", "1",
           "--max-tries=3", "--retry-wait=2", "--allow-overwrite=true", "--auto-file-renaming=false",
           "--console-log-level=notice", "--enable-color=false", "--show-console-readout=false",
           "--summary-interval=0", "--download-result=hide"]
    for name, value in headers.items():
        if name.lower() == "user-agent":
            cmd.append(f"--user-agent={value}")
        else:
            cmd.append(f"--header={name}: {value}")
    return cmd


def fetch_chunk(binary: str, items: list[tuple[int, str]], headers: dict, connections: int,
                work_dir: str, store, cancel_event=None) -> bool:
    """Download *items* ((segment index, absolute URL) pairs) and pass each whole file to *store*.

    *store(index, path)* is called once for every file aria2c delivered: while
    aria2c runs, as soon as it reports the file complete (so the caller's
    progress moves segment by segment), and after it exits for any file not
    reported yet. The caller reads it; it is removed here. Returns True when a
    429 or 503 was seen (pushback). Cancelling kills aria2c and returns False;
    the caller checks the flag itself.
    """
    os.makedirs(work_dir, exist_ok=True)
    listing = os.path.join(work_dir, "segments.txt")
    with open(listing, "w", encoding="utf-8") as fh:
        for index, url in items:
            fh.write(f"{url}\n  out=seg{index}.bin\n")
    log_path = os.path.join(work_dir, "aria2c.log")
    wanted = {f"seg{index}.bin": index for index, _url in items}
    delivered: set[int] = set()
    offset = 0
    pending = b""

    def deliver(index: int) -> None:
        if index in delivered:
            return
        path = os.path.join(work_dir, f"seg{index}.bin")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            delivered.add(index)
            store(index, path)
        if index in delivered and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def deliver_reported() -> None:
        """Hand over the files aria2c's log says are complete so far."""
        nonlocal offset, pending
        try:
            with open(log_path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return
        offset += len(data)
        lines = (pending + data).split(b"\n")
        pending = lines.pop()          # a line still being written
        for line in lines:
            match = _COMPLETE.search(line)
            if match:
                name = os.path.basename(match.group(1).decode("utf-8", "replace").replace("\\", "/"))
                if name in wanted:
                    deliver(wanted[name])

    # Refused while aria2c.exe is being replaced (the caller falls back to the built-in downloader).
    with aria2_update.running(), open(log_path, "wb") as log:
        # No console window for aria2c on Windows (it is a console program).
        hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        proc = subprocess.Popen(_command(binary, listing, work_dir, min(connections, len(items)), headers),
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **hidden)
        try:
            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    return False
                time.sleep(POLL_S)
                deliver_reported()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    with open(log_path, "rb") as log:
        output = b"\n".join(line for line in log.read().split(b"\n") if not _COMPLETE.search(line))
    pushback = bool(_PUSHBACK.search(output))
    for index, _url in items:
        deliver(index)
        path = os.path.join(work_dir, f"seg{index}.bin")
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
