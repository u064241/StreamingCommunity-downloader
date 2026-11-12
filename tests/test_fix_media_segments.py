"""Segments: what is decrypted, with what, and what counts as present.

Three families of defect, all of which ended in a file that looked finished:

* the key in force was read once for the whole playlist — the *last*
  EXT-X-KEY — so a rotation, a key with no IV, METHOD=NONE and any method that
  is not AES-128 were all handled as "AES-128 with that one IV", and the PKCS7
  padding was left inside every segment;
* a segment file was opened before its content was known to be good, so a
  failed decryption or a full disk left ``N.ts`` at zero or partial length and
  every later check took the name for the segment;
* BYTERANGE and EXT-X-MAP were ignored, which downloads the wrong bytes.
"""

import inspect
import os

import pytest
import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core import hosts, m3u8
from app.core.m3u8 import M3U8_Parser, M3U8_Segments, UnsupportedStreamError

BASE = "https://cdn.example.test/hls/1080/playlist.m3u8"
KEY_A = bytes(range(16))
KEY_B = bytes(range(16, 32))
IV_1 = bytes([1] * 16)
IV_2 = bytes([2] * 16)


def encrypt(key: bytes, iv: bytes, plain: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


class _Response:
    def __init__(self, status_code=200, content=b"", headers=None, text=""):
        self.status_code = status_code
        self.content = content
        self.text = text
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}


class _Bar:
    def __init__(self, total=0, **kwargs):
        self.total, self.n = total, 0

    def update(self, n=1, bytes=0):
        self.n += n

    def close(self):
        pass

    def refresh(self):
        pass


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """No test here resolves a real name: every host is public unless it says so."""
    hosts._dns_cache.clear()
    monkeypatch.setattr(hosts, "_resolve", lambda host, port: ["93.184.216.34"])
    yield
    hosts._dns_cache.clear()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(m3u8.time, "sleep", lambda s: None)


@pytest.fixture
def server(monkeypatch):
    """Serve bodies by URL; record every request with the headers it carried."""
    bodies: dict[str, object] = {}
    calls: list[tuple[str, dict]] = []

    def fake_get(self, url, *args, **kwargs):
        calls.append((url, dict(kwargs.get("headers") or {})))
        reply = bodies.get(url, _Response(404))
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, _Response) else _Response(200, reply)

    monkeypatch.setattr(requests.Session, "get", fake_get)
    return bodies, calls


def _segments(tmp_path, playlist: str, key=KEY_A, **kwargs) -> M3U8_Segments:
    seg = M3U8_Segments(BASE, key, temp_dir=str(tmp_path / "segs"),
                        progress_factory=lambda **kw: _Bar(**kw), **kwargs)
    seg.parse_data(playlist)
    return seg


def _on_disk(seg, index) -> bytes:
    with open(os.path.join(seg.temp_folder, f"{index}.ts"), "rb") as handle:
        return handle.read()


# ── EXT-X-KEY, per segment ────────────────────────────────────────────────────

ROTATING = (
    "#EXTM3U\n"
    '#EXT-X-KEY:METHOD=AES-128,URI="https://vixcloud.test/keys/a.key",IV=0x' + IV_1.hex() + "\n"
    "#EXTINF:4,\nseg-0.ts\n"
    '#EXT-X-KEY:METHOD=AES-128,URI="https://vixcloud.test/keys/b.key",IV=0x' + IV_2.hex() + "\n"
    "#EXTINF:4,\nseg-1.ts\n"
)


def test_a_key_rotation_decrypts_each_segment_with_its_own_key(tmp_path, server):
    """The last EXT-X-KEY used to be applied to every segment, so everything
    before the final rotation came out as noise."""
    bodies, calls = server
    bodies["https://cdn.example.test/hls/1080/seg-0.ts"] = encrypt(KEY_A, IV_1, b"first" * 40)
    bodies["https://cdn.example.test/hls/1080/seg-1.ts"] = encrypt(KEY_B, IV_2, b"second" * 40)
    bodies["https://vixcloud.test/keys/b.key"] = KEY_B
    seg = _segments(tmp_path, ROTATING)

    seg.download_ts()

    assert _on_disk(seg, 0) == b"first" * 40
    assert _on_disk(seg, 1) == b"second" * 40
    # The key handed in is the first URI's; only the second is fetched, once.
    fetched = [url for url, _ in calls if url.endswith(".key")]
    assert fetched == ["https://vixcloud.test/keys/b.key"]


def test_a_rotated_key_that_is_not_sixteen_bytes_fails_the_segment(tmp_path, server):
    bodies, _ = server
    bodies["https://cdn.example.test/hls/1080/seg-0.ts"] = encrypt(KEY_A, IV_1, b"first" * 40)
    bodies["https://cdn.example.test/hls/1080/seg-1.ts"] = encrypt(KEY_B, IV_2, b"second" * 40)
    bodies["https://vixcloud.test/keys/b.key"] = b"<html>not a key</html>"
    seg = _segments(tmp_path, ROTATING)

    with pytest.raises(RuntimeError, match="Download incompleto: 1 segmenti su 2"):
        seg.download_ts()


def test_a_key_without_an_iv_uses_the_media_sequence_number(tmp_path, server):
    """RFC 8216 §5.2. This case used to skip decryption altogether and write
    the ciphertext out as video."""
    playlist = (
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="https://vixcloud.test/keys/a.key"\n'
        "#EXTINF:4,\nseg-0.ts\n#EXTINF:4,\nseg-1.ts\n"
    )
    bodies, _ = server
    for position in (0, 1):
        iv = (7 + position).to_bytes(16, "big")
        bodies[f"https://cdn.example.test/hls/1080/seg-{position}.ts"] = encrypt(
            KEY_A, iv, f"plain-{position}".encode() * 30)
    seg = _segments(tmp_path, playlist)

    seg.download_ts()

    assert _on_disk(seg, 0) == b"plain-0" * 30
    assert _on_disk(seg, 1) == b"plain-1" * 30


def test_the_implicit_iv_is_sixteen_bytes_big_endian():
    assert m3u8.segment_iv(None, 7) == b"\x00" * 15 + b"\x07"
    assert m3u8.segment_iv(None, 258) == b"\x00" * 14 + b"\x01\x02"
    # An explicit IV wins, and a short one is left-padded rather than refused.
    assert m3u8.segment_iv("0x" + IV_1.hex(), 7) == IV_1
    assert m3u8.segment_iv("0X0A", 7) == b"\x00" * 15 + b"\x0a"


def test_method_none_writes_the_segment_as_it_arrived(tmp_path, server):
    """A playlist may switch encryption off part-way; with a key in hand the
    clear segments used to be "decrypted" all the same."""
    playlist = (
        "#EXTM3U\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="https://vixcloud.test/keys/a.key",IV=0x' + IV_1.hex() + "\n"
        "#EXTINF:4,\nseg-0.ts\n"
        "#EXT-X-KEY:METHOD=NONE\n"
        "#EXTINF:4,\nseg-1.ts\n"
    )
    bodies, _ = server
    bodies["https://cdn.example.test/hls/1080/seg-0.ts"] = encrypt(KEY_A, IV_1, b"secret" * 40)
    bodies["https://cdn.example.test/hls/1080/seg-1.ts"] = b"in the clear" * 10
    seg = _segments(tmp_path, playlist)

    seg.download_ts()

    assert _on_disk(seg, 0) == b"secret" * 40
    assert _on_disk(seg, 1) == b"in the clear" * 10


def test_an_unencrypted_playlist_ignores_the_key_it_was_given(tmp_path, server):
    bodies, _ = server
    bodies["https://cdn.example.test/hls/1080/seg-0.ts"] = b"plain ts" * 20
    seg = _segments(tmp_path, "#EXTM3U\n#EXTINF:4,\nseg-0.ts\n")

    seg.download_ts()

    assert _on_disk(seg, 0) == b"plain ts" * 20


@pytest.mark.parametrize("method", ["SAMPLE-AES", "SAMPLE-AES-CTR", "AES-256"])
def test_a_method_that_is_not_aes_128_is_refused_before_any_download(tmp_path, server, method):
    """Decrypting SAMPLE-AES as AES-128-CBC over the whole segment produces a
    complete download of noise. Refused instead, by name."""
    _, calls = server
    playlist = (
        f'#EXTM3U\n#EXT-X-KEY:METHOD={method},URI="https://vixcloud.test/k",IV=0x{IV_1.hex()}\n'
        "#EXTINF:4,\nseg-0.ts\n"
    )

    with pytest.raises(UnsupportedStreamError, match=method):
        _segments(tmp_path, playlist)

    assert calls == []


# ── PKCS7 ─────────────────────────────────────────────────────────────────────

def test_the_padding_is_removed():
    """Left in, every segment ended in up to sixteen bytes that are not MPEG-TS,
    in the middle of the concatenated stream."""
    plain = b"G" * 188  # one TS packet: not a multiple of the block size
    encrypted = encrypt(KEY_A, IV_1, plain)

    assert len(encrypted) == 192
    assert m3u8.decrypt_aes128_cbc(KEY_A, IV_1, encrypted) == plain


def test_a_whole_block_of_padding_is_removed_too():
    plain = b"G" * 160
    assert m3u8.decrypt_aes128_cbc(KEY_A, IV_1, encrypt(KEY_A, IV_1, plain)) == plain


@pytest.mark.parametrize("damage", ["wrong-key", "truncated"])
def test_a_segment_that_does_not_unpad_is_a_failed_segment(tmp_path, server, damage):
    """ValueError out of the unpadder is the integrity check HLS gives for free:
    a wrong key or a body cut short is counted, retried on the second pass, and
    never written."""
    playlist = (
        '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://vixcloud.test/k",IV=0x' + IV_1.hex() + "\n"
        "#EXTINF:4,\nseg-0.ts\n"
    )
    good = encrypt(KEY_A, IV_1, b"payload" * 50)
    bodies, _ = server
    bodies["https://cdn.example.test/hls/1080/seg-0.ts"] = (
        encrypt(KEY_B, IV_1, b"payload" * 50) if damage == "wrong-key" else good[:-7]
    )
    seg = _segments(tmp_path, playlist)
    bar = _Bar(total=1)

    seg.save_ts(0, bar, None)

    assert seg._failed_segments == {0}
    assert bar.n == 0
    assert os.listdir(seg.temp_folder) == []


def test_no_backend_argument_and_no_process_wide_warning_filter():
    """``default_backend()`` is gone from current cryptography, and the filter
    that hid its deprecation silenced every UserWarning the library raises."""
    source = inspect.getsource(m3u8)
    assert "default_backend" not in source.replace("``default_backend()``", "")
    assert 'module="cryptography"' not in source


# ── A segment is on disk when it is whole ─────────────────────────────────────

def test_a_zero_byte_segment_is_not_a_segment(tmp_path, server):
    bodies, calls = server
    bodies["https://cdn.example.test/hls/1080/seg-0.ts"] = b"real" * 50
    seg = _segments(tmp_path, "#EXTM3U\n#EXTINF:4,\nseg-0.ts\n")
    open(os.path.join(seg.temp_folder, "0.ts"), "wb").close()

    assert seg._missing_segments() == [0]

    seg.save_ts(0, _Bar(total=1), None)

    assert len(calls) == 1, "the empty file must not be taken for a resumed segment"
    assert _on_disk(seg, 0) == b"real" * 50


def test_join_refuses_a_set_with_an_empty_segment_in_it(tmp_path, server):
    seg = _segments(tmp_path, "#EXTM3U\n#EXTINF:4,\nseg-0.ts\n#EXTINF:4,\nseg-1.ts\n")
    with open(os.path.join(seg.temp_folder, "0.ts"), "wb") as handle:
        handle.write(b"data")
    open(os.path.join(seg.temp_folder, "1.ts"), "wb").close()

    with pytest.raises(RuntimeError, match="Download incompleto: 1 segmenti su 2"):
        seg.join(str(tmp_path / "out.mkv"))


def test_a_write_that_fails_leaves_nothing_behind_and_is_recorded(tmp_path, server, monkeypatch):
    """The file used to be opened first and filled second, so a failure in
    between left N.ts at zero length — and the exception escaped into the
    executor, where the segment was in neither the failed set nor on disk."""
    bodies, _ = server
    bodies["https://cdn.example.test/hls/1080/seg-0.ts"] = b"real" * 50
    seg = _segments(tmp_path, "#EXTM3U\n#EXTINF:4,\nseg-0.ts\n")

    def full_disk(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(m3u8.os, "replace", full_disk)

    seg.save_ts(0, _Bar(total=1), None)

    assert seg._failed_segments == {0}
    assert os.listdir(seg.temp_folder) == []


def test_a_partial_file_never_carries_the_segments_name(tmp_path, server, monkeypatch):
    bodies, _ = server
    bodies["https://cdn.example.test/hls/1080/seg-0.ts"] = b"real" * 50
    seg = _segments(tmp_path, "#EXTM3U\n#EXTINF:4,\nseg-0.ts\n")
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((os.path.basename(src), os.path.basename(dst),
                     os.path.exists(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(m3u8.os, "replace", spy)

    seg.save_ts(0, _Bar(total=1), None)

    # Written whole under another name, and only then given the real one.
    assert seen == [("0.ts.part", "0.ts", False)]


def test_a_body_shorter_than_its_content_length_is_retried(tmp_path, server):
    """A connection dropped mid-transfer answers 200 with part of the segment."""
    bodies, calls = server
    url = "https://cdn.example.test/hls/1080/seg-0.ts"
    bodies[url] = [
        _Response(200, b"half", headers={"Content-Length": "200"}),
        _Response(200, b"x" * 200, headers={"Content-Length": "200"}),
    ]
    seg = _segments(tmp_path, "#EXTM3U\n#EXTINF:4,\nseg-0.ts\n")

    assert seg.get_req_ts(url) == b"x" * 200
    assert len(calls) == 2


# ── BYTERANGE, EXT-X-MAP, DISCONTINUITY ───────────────────────────────────────

BYTERANGE = (
    "#EXTM3U\n#EXT-X-VERSION:4\n"
    "#EXTINF:4,\n#EXT-X-BYTERANGE:100@0\nall.ts\n"
    "#EXTINF:4,\n#EXT-X-BYTERANGE:50\nall.ts\n"
    "#EXTINF:4,\n#EXT-X-BYTERANGE:25@400\nall.ts\n"
)


def test_a_byterange_is_asked_for_with_a_range_header(tmp_path, monkeypatch):
    """Ignored, every segment of such a playlist downloaded the whole resource
    — the same file, once per segment."""
    resource = bytes(range(256)) * 2
    ranges = []

    def ranged(self, url, *args, **kwargs):
        ranges.append(kwargs["headers"]["range"])
        first, last = kwargs["headers"]["range"].removeprefix("bytes=").split("-")
        return _Response(206, resource[int(first):int(last) + 1])

    monkeypatch.setattr(requests.Session, "get", ranged)
    seg = _segments(tmp_path, BYTERANGE)

    seg.download_ts()

    # Sorted: the three are fetched by a pool, in whatever order it likes.
    assert sorted(ranges) == ["bytes=0-99", "bytes=100-149", "bytes=400-424"]
    assert _on_disk(seg, 0) == resource[0:100]
    # No offset: it starts where the previous range into that resource ended.
    assert _on_disk(seg, 1) == resource[100:150]
    assert _on_disk(seg, 2) == resource[400:425]


def test_a_server_that_ignores_the_range_still_yields_the_slice(tmp_path, server):
    resource = bytes(range(256)) * 2
    bodies, _ = server
    bodies["https://cdn.example.test/hls/1080/all.ts"] = _Response(200, resource)
    seg = _segments(tmp_path, BYTERANGE)

    seg.download_ts()

    assert _on_disk(seg, 1) == resource[100:150]


def test_a_range_answer_of_the_wrong_length_is_not_accepted(tmp_path, server):
    bodies, _ = server
    bodies["https://cdn.example.test/hls/1080/all.ts"] = _Response(206, b"short")
    seg = _segments(tmp_path, BYTERANGE)

    assert seg.get_req_ts("https://cdn.example.test/hls/1080/all.ts", (0, 100)) is None


def test_fmp4_is_refused_by_name_before_any_download(tmp_path, server):
    """EXT-X-MAP means fragmented MP4. The join concatenates MPEG-TS, so the
    choice made is to say so up front rather than fetch a film it cannot join."""
    _, calls = server
    playlist = (
        '#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-MAP:URI="init.mp4"\n'
        "#EXTINF:4,\nseg-0.m4s\n#EXTINF:4,\nseg-1.m4s\n"
    )

    with pytest.raises(UnsupportedStreamError, match="fMP4 non supportato"):
        _segments(tmp_path, playlist)

    assert calls == []


def test_a_discontinuity_is_tolerated_and_logged(tmp_path, server, caplog):
    playlist = (
        "#EXTM3U\n#EXTINF:4,\nseg-0.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\nseg-1.ts\n"
    )
    with caplog.at_level("WARNING", logger="app.core.m3u8"):
        seg = _segments(tmp_path, playlist)

    assert len(seg.segments) == 2
    assert "EXT-X-DISCONTINUITY" in caplog.text


# ── What the parser hands on ──────────────────────────────────────────────────

def test_relative_uris_come_out_absolute():
    """A playlist may name everything relative to itself; those reached
    requests as "1080/playlist.m3u8" and failed with MissingSchema."""
    master = M3U8_Parser()
    master.parse_data(
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Italiano",LANGUAGE="it",URI="audio/it.m3u8"\n'
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="Eng",LANGUAGE="eng",URI="/subs/en.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,AUDIO="a"\n'
        "1080/playlist.m3u8\n",
        base_uri="https://vixcloud.test/playlist/42?token=t",
    )

    assert master.get_best_quality() == "https://vixcloud.test/playlist/1080/playlist.m3u8"
    assert master.audio_ts[0]["uri"] == "https://vixcloud.test/playlist/audio/it.m3u8"
    assert master.subtitle_playlist[0]["uri"] == "https://vixcloud.test/subs/en.m3u8"

    media = M3U8_Parser()
    media.parse_data(
        '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="/storage/enc.key"\n'
        "#EXTINF:4,\nseg-0.ts\n#EXTINF:4,\nhttps://other.test/seg-1.ts\n",
        base_uri=BASE,
    )

    assert media.segments == ["https://cdn.example.test/hls/1080/seg-0.ts",
                              "https://other.test/seg-1.ts"]
    assert media.segment_info[0]["key_uri"] == "https://cdn.example.test/storage/enc.key"


def test_without_a_base_the_uris_are_left_as_written():
    parser = M3U8_Parser()
    parser.parse_data("#EXTM3U\n#EXTINF:4,\nseg-0.ts\n")
    assert parser.segments == ["seg-0.ts"]


@pytest.mark.parametrize("uri,is_subtitle", [
    ("https://cdn.test/vtt-store/seg-0.ts", False),
    ("https://cdn.test/seg-0.ts?format=vtt", False),
    ("https://svtt.cdn.test/seg-0.ts", False),
    ("https://cdn.test/subs/eng.vtt", True),
    ("https://cdn.test/subs/eng.VTT?token=abc", True),
    ("subs/eng.webvtt", True),
])
def test_a_subtitle_is_a_path_ending_in_vtt(uri, is_subtitle):
    """"vtt" anywhere in the URL used to be enough: a CDN directory or a query
    parameter turned every video segment of that playlist into a subtitle, and
    the video playlist came back empty."""
    parser = M3U8_Parser()
    parser.parse_data(f"#EXTM3U\n#EXTINF:4,\n{uri}\n")

    assert (parser.subtitle == [uri]) is is_subtitle
    assert (parser.segments == [uri]) is not is_subtitle
