"""Register extracted portable packages without changing the update ZIP format."""
import logging
import os
from pathlib import Path
import subprocess
import sys
import hashlib

from app import __version__, distribution

logger = logging.getLogger(__name__)


def _uninstall_key(root: Path) -> str:
    folder_id = hashlib.sha256(str(root).lower().encode("utf-16le")).hexdigest()
    return r"Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamingCommunityDownloader-Portable-" + folder_id


def _registry_is_present(key_name: str) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0,
                           winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)):
            return True
    except OSError:
        return False


def _register_direct(root: Path, version: str, script: Path) -> None:
    import winreg
    key_name = _uninstall_key(root)
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = f'"{powershell}" -NoProfile -ExecutionPolicy Bypass -File "{script}" -Uninstall'
    values = {
        "DisplayName": "StreamingCommunity Downloader (Portable)",
        "DisplayVersion": version,
        "Publisher": "u064241",
        "InstallLocation": str(root),
        "DisplayIcon": str(root / "StreamingCommunityDownloader.exe"),
        "UninstallString": command,
        "QuietUninstallString": command + " -Quiet",
    }
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_name, 0,
                           winreg.KEY_SET_VALUE | getattr(winreg, "KEY_WOW64_64KEY", 0)) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def register_portable():
    if os.name != "nt" or distribution.mode() != "portable":
        return
    root = Path(sys.executable).parent
    # Setup already provides and registers its own uninstaller.
    if (root / "unins000.exe").exists():
        return
    target = root / "Register-Portable.ps1"
    try:
        from app.portable_update import _path
        target = _path(target, exists=False)
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
        if not _registry_is_present(_uninstall_key(root)):
            raise RuntimeError("PowerShell registration completed without creating the uninstall key")
    except (OSError, KeyError, RuntimeError, subprocess.SubprocessError):
        try:
            _register_direct(root, __version__, target)
        except (OSError, KeyError, RuntimeError):
            logger.warning("Could not register the portable Windows uninstaller", exc_info=True)
