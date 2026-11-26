"""aria2c segment prefetch helper (app/core/hls_aria2.py): no network, no real aria2c."""
import os

from app.core import hls_aria2


def test_executable_is_none_when_neither_managed_nor_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(hls_aria2.aria2_update, "managed_path", lambda: tmp_path / "missing" / "aria2c.exe")
    monkeypatch.setattr(hls_aria2.aria2_update.shutil, "which", lambda name: None)
    assert hls_aria2.executable() is None


def test_connections_are_capped_by_source_and_ceiling():
    assert hls_aria2.connections_for(4) == 4
    assert hls_aria2.connections_for(999) == hls_aria2.MAX_CONNECTIONS
    assert hls_aria2.connections_for(0) == 1


def test_chunks_split_in_order():
    items = list(range(7))
    assert [list(c) for c in hls_aria2.chunks(items, 3)] == [[0, 1, 2], [3, 4, 5], [6]]


def test_command_carries_headers_and_user_agent_separately():
    cmd = hls_aria2._command("aria2c", "list.txt", "work", 8,
                             {"user-agent": "UA/1", "referer": "https://site/"})
    assert "--user-agent=UA/1" in cmd
    assert "--header=referer: https://site/" in cmd
    assert cmd[cmd.index("-j") + 1] == "8"
    assert "-x" in cmd and cmd[cmd.index("-x") + 1] == "1"


class _FakeProcess:
    """Stands in for aria2c: writes the listed files and a log with a 429 line."""

    def __init__(self, cmd, stdout=None, stderr=None):
        work_dir = cmd[cmd.index("-d") + 1]
        listing = cmd[cmd.index("-i") + 1]
        with open(listing, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("out=seg"):
                    name = line.strip()[len("out="):]
                    with open(os.path.join(work_dir, name), "wb") as out:
                        out.write(b"x" * 16)
        stdout.write(b"[ERROR] HTTP 429 Too Many Requests\n")

    def poll(self):
        return 0

    def wait(self):
        return 0

    def kill(self):
        pass


def test_fetch_chunk_stores_files_and_reports_pushback(monkeypatch, tmp_path):
    monkeypatch.setattr(hls_aria2.subprocess, "Popen", _FakeProcess)
    stored = []

    def store(index, path):
        with open(path, "rb") as fh:
            stored.append((index, fh.read()))

    pushback = hls_aria2.fetch_chunk("aria2c", [(0, "https://h/0.ts"), (1, "https://h/1.ts")],
                                     {"user-agent": "UA/1"}, 2, str(tmp_path / "w"), store)
    assert pushback is True
    assert [index for index, _ in stored] == [0, 1]
    assert all(data == b"x" * 16 for _, data in stored)
    assert not any(name.endswith(".bin") for name in os.listdir(tmp_path / "w"))


class _LiveProcess:
    """aria2c finishing one file per poll and announcing it, as its notice log does."""

    def __init__(self, cmd, stdout=None, stderr=None):
        self.work_dir = cmd[cmd.index("-d") + 1]
        self.names = []
        with open(cmd[cmd.index("-i") + 1], encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("out=seg"):
                    self.names.append(line.strip()[len("out="):])
        self.log = stdout
        self.polls = 0

    def poll(self):
        if self.polls < len(self.names):
            name = self.names[self.polls]
            with open(os.path.join(self.work_dir, name), "wb") as out:
                out.write(b"y" * 8)
            # The path carries a "503" folder name: not a pushback.
            self.log.write(f"11/26 02:05:03 [NOTICE] Download complete: "
                           f"C:/Film 503/tmp/aria2/{name}\r\n".encode())
            self.log.flush()
            self.polls += 1
            return None
        return 0

    def wait(self):
        return 0

    def kill(self):
        pass


def test_fetch_chunk_delivers_each_file_while_aria2c_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(hls_aria2.subprocess, "Popen", _LiveProcess)
    monkeypatch.setattr(hls_aria2.time, "sleep", lambda s: None)
    stored = []

    def store(index, path):
        with open(path, "rb") as fh:
            stored.append((index, fh.read()))

    items = [(i, f"https://h/{i}.ts") for i in range(3)]
    pushback = hls_aria2.fetch_chunk("aria2c", items, {}, 2, str(tmp_path / "w"), store)
    assert pushback is False
    # Each file exactly once, in the order aria2c finished them.
    assert [index for index, _ in stored] == [0, 1, 2]
    assert not any(name.endswith(".bin") for name in os.listdir(tmp_path / "w"))


def test_command_asks_for_completion_notices_without_colour():
    cmd = hls_aria2._command("aria2c", "list.txt", "work", 4, {})
    assert "--console-log-level=notice" in cmd
    assert "--enable-color=false" in cmd
