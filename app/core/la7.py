"""Independent LA7 resolver: structured page data and clear HLS only.

No remote JavaScript is executed. Protection must be explicitly disabled on
the requested episode; playlists are inspected before returning metadata.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests

BASE_URL = "https://www.la7play.it"
CDN_HOST = "https://awsvodpkg.iltrovatore.it"
PAGE_HOSTS = frozenset({"la7play.it", "www.la7play.it"})
CDN_HOSTS = frozenset({"awsvodpkg.iltrovatore.it"})
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PLAYLISTS = 16
_ID_RE = re.compile(r"-([0-9]+)/*$")
_DECODER = json.JSONDecoder()


class LA7Error(ValueError):
    """Invalid input, unavailable content or unsupported protection state."""


def _validate_url(url: str, hosts=PAGE_HOSTS) -> str:
    if not isinstance(url, str) or not url or any(ord(c) <= 32 for c in url):
        raise LA7Error("URL LA7 Play non valido")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in hosts
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment
                 and "\\" not in url)
    except ValueError as exc:
        raise LA7Error("URL LA7 Play non valido") from exc
    if not valid:
        raise LA7Error("Host o URL LA7 Play non consentito")
    return url


def _fetch(url: str, hosts, http_get=None) -> str:
    """Bounded reads and manually checked redirects, including CDN requests."""
    get = http_get if http_get is not None else requests.get
    for _ in range(4):
        _validate_url(url, hosts)
        try:
            with get(url, timeout=(5, 20), allow_redirects=False, stream=True,
                     headers={"User-Agent": "Mozilla/5.0"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise LA7Error("Redirect LA7 Play privo di destinazione")
                    url = _validate_url(urljoin(url, location), hosts)
                    continue
                response.raise_for_status()
                if response.status_code != 200:
                    raise LA7Error("Risposta LA7 Play incompleta")
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise LA7Error("Risposta LA7 Play troppo grande")
                return data.decode("utf-8-sig")
        except (requests.RequestException, UnicodeError) as exc:
            raise LA7Error("LA7 Play non raggiungibile o risposta non valida") from exc
    raise LA7Error("Troppi redirect LA7 Play")


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def _objects(value):
    pending = [value]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            yield node
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)


def _json_values(text):
    # Each Flight JSON record has a hexadecimal row prefix. Ignore import,
    # text and error records instead of searching their contents for objects.
    for line in text.splitlines():
        match = re.match(r"^[0-9a-fA-F]+:\s*([\[{])", line)
        if not match:
            continue
        try:
            value = json.loads(line[match.start(1):])
        except (ValueError, RecursionError):
            continue
        yield value


def page_values(page: str) -> list:
    """Decode JSON/Flight data without evaluating page JavaScript."""
    parser = _Scripts()
    parser.feed(page)
    candidates = []
    flight_chunks = []
    for script in parser.scripts:
        if "self.__next_f.push(" in script:
            for match in re.finditer(r"self\.__next_f\.push\(\s*", script):
                try:
                    push, _ = _DECODER.raw_decode(script, match.end())
                except (ValueError, RecursionError):
                    continue
                if (isinstance(push, list) and len(push) == 2
                        and push[0] == 1 and isinstance(push[1], str)):
                    flight_chunks.append(push[1])
        else:
            try:
                candidates.append(json.loads(script))
            except (ValueError, RecursionError):
                pass
    candidates.extend(_json_values("".join(flight_chunks)))
    return candidates


def _episode_data(page: str, content_id: str, page_path: str | None = None) -> dict:
    candidates = page_values(page)
    videos = [obj for value in candidates for obj in _objects(value)
              if type(obj.get("id")) in (str, int) and str(obj["id"]).isdigit()
              and ("drm" in obj or "uploadVideo" in obj)]
    # Migrated LA7 URLs retain the old suffix, while their video node has a
    # new id. Match the requested official path before the legacy suffix.
    matches = [obj for obj in videos if page_path and obj.get("path") == page_path]
    if not matches:
        matches = [obj for obj in videos if not obj.get("path") and str(obj["id"]) == content_id]
    if not matches:
        raise LA7Error("Dati della puntata LA7 Play non trovati")
    if any(obj != matches[0] for obj in matches[1:]):
        raise LA7Error("Dati della puntata LA7 Play ambigui")
    episode = matches[0]
    if episode.get("drm") is not False:
        raise LA7Error("Contenuto LA7 Play con DRM o stato DRM sconosciuto")
    return episode


def _manifest_url(episode: dict) -> str:
    video = episode.get("uploadVideo")
    remote = video.get("cf_remote_url") if isinstance(video, dict) else None
    if not isinstance(remote, str) or not remote:
        raise LA7Error("Percorso video LA7 Play non trovato")
    paths = remote.split(",")
    for path in paths:
        # Older, migrated assets use /content/ (sometimes //content/) rather
        # than /nk/. These are CDN resource paths, never URL destinations.
        if (not re.fullmatch(r"(?:/nk/|/{1,2}content/)[A-Za-z0-9_./-]+\.mp4", path)
                or any(part in (".", "..", "") for part in path.lstrip("/").split("/"))):
            raise LA7Error("Percorso CDN LA7 Play non valido")
    # Preserve every rendition, using the same multi-resource syntax as LA7's
    # player instead of silently discarding all but the first video file.
    resources = paths[0] if len(paths) == 1 else ",".join(p[:-4] for p in paths) + ",.mp4"
    return f"{CDN_HOST}/local/hls/,{resources}.urlset/master.m3u8"


def _validate_manifest(url: str, http_get=None) -> dict[str, str]:
    pending = [(url, 0)]
    visited = set()
    snapshots = {}
    while pending:
        current, depth = pending.pop()
        if current in visited:
            continue
        if depth > 2 or len(visited) >= MAX_PLAYLISTS:
            raise LA7Error("Manifest LA7 Play troppo complesso")
        visited.add(current)
        snapshots[current] = _fetch(current, CDN_HOSTS, http_get)
        lines = [line.strip() for line in snapshots[current].splitlines()
                 if line.strip()]
        if not lines or lines[0] != "#EXTM3U":
            raise LA7Error("Manifest HLS LA7 Play non valido")
        child_urls = []
        stream_pending = False
        segment_pending = False
        target_duration = False
        segments = 0
        for line in lines[1:]:
            if line.startswith(("#EXT-X-KEY", "#EXT-X-SESSION-KEY")):
                # Clear HLS only. No key/licence requests are made.
                if line != "#EXT-X-KEY:METHOD=NONE":
                    raise LA7Error("Manifest LA7 Play cifrato o con DRM sconosciuto")
            if line.startswith("#EXT-X-STREAM-INF:"):
                if (stream_pending or not re.search(
                        r"(?:^|,)BANDWIDTH=[1-9][0-9]*(?:,|$)", line.partition(":")[2])):
                    raise LA7Error("Variante HLS LA7 Play incompleta")
                stream_pending = True
            if line.startswith("#EXT-X-TARGETDURATION:"):
                if not re.fullmatch(r"#EXT-X-TARGETDURATION:[1-9][0-9]*", line):
                    raise LA7Error("Durata HLS LA7 Play non valida")
                target_duration = True
            if line.startswith("#EXTINF:"):
                duration = re.fullmatch(r"#EXTINF:([0-9]+(?:\.[0-9]+)?),.*", line)
                if segment_pending or duration is None or float(duration[1]) <= 0:
                    raise LA7Error("Segmento HLS LA7 Play non valido")
                segment_pending = True
            for attribute in re.finditer(
                    r'(?<![A-Z0-9-])([A-Z0-9-]*URI)=("[^"]*"|[^,\s]*)', line):
                name, value = attribute.groups()
                if not (value.startswith('"') and value.endswith('"') and len(value) > 2):
                    raise LA7Error("URI HLS LA7 Play non valido")
                target = _validate_url(urljoin(current, value[1:-1]), CDN_HOSTS)
                if name == "URI" and line.startswith(("#EXT-X-MEDIA:", "#EXT-X-I-FRAME-STREAM-INF:")):
                    child_urls.append(target)
            if not line.startswith("#"):
                target = _validate_url(urljoin(current, line), CDN_HOSTS)
                if stream_pending:
                    child_urls.append(target)
                    stream_pending = False
                else:
                    if not segment_pending:
                        raise LA7Error("Segmento HLS LA7 Play privo di durata")
                    segments += 1
                    segment_pending = False
        if stream_pending or segment_pending:
            raise LA7Error("Variante HLS LA7 Play incompleta")
        if child_urls:
            if segments or len(set(child_urls)) > MAX_PLAYLISTS:
                raise LA7Error("Manifest HLS LA7 Play non valido")
            if any(child in visited for child in child_urls):
                raise LA7Error("Riferimento HLS LA7 Play ciclico")
            pending.extend((child, depth + 1) for child in set(child_urls))
        elif not (segments and target_duration):
            raise LA7Error("Manifest HLS LA7 Play privo di formati")
    return snapshots


def validate_episode_url(url: str) -> str:
    """Validate an official single-episode URL without making a request."""
    _validate_url(url)
    parsed = urlsplit(url)
    match = _ID_RE.search(parsed.path)
    if not match or parsed.query:
        raise LA7Error("URL della singola puntata LA7 Play non valido")
    return match.group(1)


def resolve_episode(url: str, *, http_get=None) -> dict:
    """Resolve an episode, fetching playlists but never media segments."""
    content_id = validate_episode_url(url)
    episode = _episode_data(_fetch(url, PAGE_HOSTS, http_get), content_id, urlsplit(url).path)
    title = episode.get("title")
    if not isinstance(title, str) or not title.strip():
        raise LA7Error("Titolo della puntata LA7 Play non trovato")
    manifest = _manifest_url(episode)
    _validate_manifest(manifest, http_get)
    return {"id": str(episode["id"]), "title": title.strip(), "drm": False,
            "manifest": manifest, "page_url": url}


def download(url: str, title: str, output_dir: str, *, temp_dir=None,
             progress_factory=None, cancel_event=None) -> str:
    """One queued episode, resolved at worker start, using only checked clear HLS.

    Native HLS receives the validated media-playlist snapshot. It cannot
    re-fetch a different playlist containing a key after the protection check.
    A job succeeds only after a nonempty media file is published to the library.
    """
    import os
    from pathlib import Path
    import shutil
    import tempfile

    from app.progress import DownloadCancelledError
    from app.providers.la7 import LA7Provider
    from app.core.ffmpeg_path import get_ffmpeg_exe

    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelledError()

    check_cancelled()
    try:
        import yt_dlp
    except ImportError as exc:
        raise LA7Error("Download LA7 non disponibile: yt-dlp non installato") from exc

    bar = None
    completed = 0
    last_key = None
    outputs = []

    def hook(data):
        nonlocal bar, completed, last_key
        check_cancelled()
        if progress_factory and data.get("status") == "downloading":
            done = data.get("downloaded_bytes", 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or done
            key = data.get("filename")
            if total:
                if bar is None or key != last_key:
                    if bar is not None:
                        bar.close()
                    bar = progress_factory(total=total, phase="downloading")
                    completed, last_key = 0, key
                bar.total = total
                delta = max(0, done - completed)
                bar.update(n=delta, bytes=delta)
                completed = done

    try:
        resolved = LA7Provider().direct_download_metadata(url)
        check_cancelled()
        if resolved.get("drm") is not False:
            raise LA7Error("Stato DRM LA7 non verificato")
        _validate_url(resolved["manifest"], CDN_HOSTS)
        if temp_dir is not None:
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="la7-", dir=temp_dir) as staging:
            stage = Path(staging).resolve()
            opts = {
                "format": "bv*+ba/b", "outtmpl": str(stage / "video.%(ext)s"),
                "noplaylist": True, "merge_output_format": "mp4",
                "hls_prefer_native": True, "allow_unplayable_formats": False,
                "skip_unavailable_fragments": False, "ignoreerrors": False,
                "socket_timeout": 20, "retries": 0, "fragment_retries": 0,
                "quiet": True, "no_warnings": True, "noprogress": True,
                "color": "never", "progress_hooks": [hook],
                "post_hooks": [outputs.append], "ffmpeg_location": str(get_ffmpeg_exe()),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(resolved["manifest"], download=False,
                                        process=False, ie_key="Generic")
                if (not isinstance(info, dict) or "entries" in info
                        or info.get("_type") not in (None, "video")
                        or info.get("is_live") or info.get("has_drm")
                        or info.get("license_url")):
                    raise LA7Error("LA7 non ha fornito un singolo video HLS in chiaro")
                snapshots = _validate_manifest(resolved["manifest"])
                formats = info.get("formats")
                if not isinstance(formats, list) or not formats:
                    raise LA7Error("LA7 non ha fornito formati HLS verificabili")
                # The checked snapshots describe VOD. Avoid Generic's live
                # sequence heuristic delegating a native download to FFmpeg.
                info.update(extractor_key="LA7", extractor="la7", id=resolved["id"])
                for fmt in formats:
                    if not isinstance(fmt, dict):
                        raise LA7Error("Formato LA7 non valido")
                    playlist = snapshots.get(fmt.get("url"))
                    if (fmt.get("protocol") != "m3u8_native" or fmt.get("has_drm")
                            or fmt.get("license_url") or fmt.get("hls_aes")
                            or playlist is None or "#EXTINF:" not in playlist
                            or "#EXT-X-ENDLIST" not in playlist):
                        raise LA7Error("Formato LA7 protetto, live o non verificabile")
                    fmt["hls_media_playlist_data"] = playlist
                check_cancelled()
                ydl.process_ie_result(info, download=True)
            check_cancelled()
            candidates = [Path(path).resolve() for path in outputs if path]
            candidates = [path for path in candidates if path.parent == stage
                          and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
                          and path.is_file() and path.stat().st_size > 0]
            if len(candidates) != 1:
                raise LA7Error("Download LA7 non riuscito: nessun singolo file completo prodotto")

            destination = Path(output_dir)
            destination.mkdir(parents=True, exist_ok=True)
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:140] or "LA7"
            target = destination / f"{name} [LA7-{resolved['id']}]{candidates[0].suffix}"
            # Copy across drives, then atomically publish without overwriting
            # an existing file. Unsupported destination filesystems fail explicitly.
            with tempfile.NamedTemporaryFile(dir=destination, prefix=".la7-", delete=False) as copy:
                pending = Path(copy.name)
                try:
                    with candidates[0].open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            check_cancelled()
                            copy.write(chunk)
                except BaseException:
                    copy.close()
                    pending.unlink(missing_ok=True)
                    raise
            try:
                check_cancelled()
                os.link(pending, target)
            finally:
                pending.unlink(missing_ok=True)
            return str(target)
    except (LA7Error, DownloadCancelledError):
        raise
    except Exception as exc:
        check_cancelled()
        message = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
        raise LA7Error(f"Download LA7 non riuscito: {message}") from exc
    finally:
        if bar is not None:
            bar.close()
