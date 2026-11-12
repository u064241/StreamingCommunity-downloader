"""Download and verify a release asset without changing the running app."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests

MAX_ASSET_BYTES = 2 * 1024 * 1024 * 1024
ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}


logger = logging.getLogger(__name__)


class UpdateDownloadError(RuntimeError):
    pass


class SignatureError(UpdateDownloadError):
    """The file is not signed by an expected publisher: never execute it."""


def system32_tool(*parts: str) -> Path | None:
    """Absolute path of a Windows system tool, never resolved through PATH."""
    if os.name != "nt":
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(260)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        return None
    tool = Path(buffer.value, *parts)
    return tool if tool.is_file() else None


# Constant script: the file to inspect travels in the environment, so no path
# is ever interpolated into PowerShell source.
_SIGNATURE_SCRIPT = (
    "$s = Get-AuthenticodeSignature -LiteralPath $env:SC_VERIFY_FILE; "
    "[Console]::Out.Write((@{status = [string]$s.Status; "
    "thumbprint = [string]$s.SignerCertificate.Thumbprint} | ConvertTo-Json -Compress))"
)


def verify_authenticode(path: str | Path, thumbprints) -> None:
    """Require a valid Authenticode signature from one of ``thumbprints``.

    Fail-closed: no expected signer, no PowerShell, a timeout or any
    unexpected output all raise SignatureError.
    """
    trusted = {str(item).replace(" ", "").upper() for item in thumbprints or () if item}
    if not trusted:
        raise SignatureError("Verifica firma disattivata: nessun firmatario atteso configurato")
    powershell = system32_tool("WindowsPowerShell", "v1.0", "powershell.exe")
    if powershell is None:
        raise SignatureError("Verifica firma non disponibile su questo sistema")
    # A PSModulePath inherited from PowerShell 7 stops Windows PowerShell from
    # loading Microsoft.PowerShell.Security, which owns the cmdlet.
    env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    env["SC_VERIFY_FILE"] = str(path)
    try:
        completed = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", _SIGNATURE_SCRIPT],
            capture_output=True, text=True, timeout=60, check=False, env=env,
            stdin=subprocess.DEVNULL, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = json.loads(completed.stdout)
        status = result["status"]
        thumbprint = str(result["thumbprint"] or "").upper()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Authenticode check failed for %s: %r", path, exc)
        raise SignatureError("Verifica firma non riuscita") from exc
    if completed.returncode != 0 or status != "Valid" or thumbprint not in trusted:
        logger.warning("Authenticode rejected %s: status=%s signer=%s", path, status, thumbprint)
        raise SignatureError("Firma Authenticode non valida o firmatario inatteso")


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
        # The detail (paths, URLs) stays in the server log, not in the response.
        logger.warning("Update download failed: %r", exc)
        raise UpdateDownloadError("Download aggiornamento fallito") from exc
    finally:
        temp_path.unlink(missing_ok=True)

