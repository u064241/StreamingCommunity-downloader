"""Download and verify a release asset without changing the running app."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests

MAX_ASSET_BYTES = 2 * 1024 * 1024 * 1024
ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}


class UpdateDownloadError(RuntimeError):
    pass


def _safe_url(url: str) -> None:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise UpdateDownloadError("URL asset non autorizzato")


@contextmanager
def github_response(url):
    """Validate each redirect before making the next request."""
    for _ in range(6):
        _safe_url(url)
        response = requests.get(url, stream=True, timeout=(5, 30), allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise UpdateDownloadError("Redirect senza destinazione")
            url = urljoin(url, location)
            continue
        try:
            response.raise_for_status()
            yield response
        finally:
            response.close()
        return
    raise UpdateDownloadError("Troppi redirect")


def download_verified(url: str, expected_sha256: str, destination_dir: str | Path) -> Path:
    """Stream an HTTPS GitHub asset to a temporary file, then atomically publish it."""
    _safe_url(url)
    expected = expected_sha256.strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise UpdateDownloadError("SHA-256 non valido")
    target_dir = Path(destination_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="sc-update-", suffix=".download", dir=target_dir)
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as output:
            with github_response(url) as response:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_ASSET_BYTES:
                        raise UpdateDownloadError("Asset troppo grande")
                    digest.update(chunk)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest().lower() != expected:
            raise UpdateDownloadError("Checksum SHA-256 non corrispondente")
        final_path = target_dir / Path(urlparse(url).path).name
        if not final_path.name:
            raise UpdateDownloadError("Nome asset mancante")
        os.replace(temp_path, final_path)
        return final_path
    except (requests.RequestException, OSError) as exc:
        raise UpdateDownloadError(f"Download aggiornamento fallito: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)

