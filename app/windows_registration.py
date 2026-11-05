"""Register extracted portable packages without changing the update ZIP format."""
import logging
import os
from pathlib import Path
import subprocess
import sys

from app import __version__, distribution

logger = logging.getLogger(__name__)


def register_portable():
    if os.name != "nt" or distribution.mode() != "portable":
        return
    root = Path(sys.executable).parent
    # Setup already provides and registers its own uninstaller.
    if (root / "unins000.exe").exists():
        return
    try:
        from app.portable_update import _path
        target = _path(root / "Register-Portable.ps1", exists=False)
        bundled = Path(sys._MEIPASS) / "portable" / "Register-Portable.ps1"
        content = bundled.read_bytes()
        if not target.exists() or target.read_bytes() != content:
            target.write_bytes(content)
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(target), "-Version", __version__],
            check=True, capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, KeyError, RuntimeError, subprocess.SubprocessError):
        logger.warning("Could not register the portable Windows uninstaller", exc_info=True)
