"""Standalone Windows portable EXE replacement; no installer or app imports.

Run from an external Python process (not the EXE being replaced)::

    python app/portable_update.py apply --pid 123 --exe C:/App/App.exe \
        --asset C:/Temp/App.exe --backup C:/App/App.previous.exe --sha256 HASH
    python app/portable_update.py rollback --pid 456 --exe C:/App/App.exe \
        --backup C:/App/App.previous.exe --failed-backup C:/App/App.failed.exe \
        --sha256 CURRENT_HASH --backup-sha256 OLD_HASH

The caller supplies a trusted SHA256 from its already verified download. ZIPs
must first be extracted/verified by the caller: this helper accepts EXEs only.
It never kills processes, changes data, downloads, or invokes Setup. On a
successful replacement it can optionally start the verified new application.
Rollback is automatic on a failed post-replacement check, or explicit later.
Backup and lock files intentionally survive a helper crash. Do not remove a
stale lock until the previous helper has been confirmed stopped.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


class PortableUpdateError(RuntimeError):
    """No update, or failed update; inspect backup before retrying recovery."""


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _hash(value: str) -> str:
    value = value.lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise PortableUpdateError("Invalid SHA256")
    return value


def _path(value: str | Path, *, exists: bool = True) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise PortableUpdateError("Absolute paths required")
    # Reject reparse points in every existing component, including junctions.
    for component in (path, *path.parents):
        if component.exists() or component.is_symlink():
            attributes = component.lstat()
            if component.is_symlink() or getattr(attributes, "st_file_attributes", 0) & 0x400:
                raise PortableUpdateError("Reparse points are not supported")
    if exists and (not path.is_file() or path.suffix.lower() != ".exe"):
        raise PortableUpdateError("Expected a regular EXE file")
    return path.resolve(strict=exists)


@contextmanager
def _process(pid: int, exe: Path):
    """Pin and check the Windows process handle, avoiding PID polling/reuse."""
    if os.name != "nt":
        raise PortableUpdateError("Windows only")
    if pid <= 0 or pid == os.getpid():
        raise PortableUpdateError("Invalid target PID")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        raise PortableUpdateError("Cannot open target process (it must still be running)")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise PortableUpdateError("Cannot identify target process")
        if not os.path.samefile(buffer.value, exe):
            raise PortableUpdateError("PID does not match target EXE")

        def wait(timeout: float) -> None:
            result = kernel.WaitForSingleObject(handle, math.ceil(timeout * 1000))
            if result != 0:
                raise PortableUpdateError("Process exit timeout" if result == 258 else "Process wait failed")

        yield wait
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _lock(exe: Path):
    path = exe.with_name(exe.name + ".portable-update.lock")
    # O_EXCL refuses existing locks, including orphaned locks after a crash.
    with path.open("x", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def _copy(source: Path, destination: Path) -> None:
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())


def _stage(source: Path, exe: Path, expected: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=".portable-update-", suffix=".exe", dir=exe.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if _digest(staged) != expected:
            raise PortableUpdateError("Asset SHA256 mismatch")
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def replace_portable(*, pid: int, exe: str | Path, asset: str | Path,
                     backup: str | Path, sha256: str, timeout: float = 60,
                     current_sha256: str | None = None,
                     ready_file: str | Path | None = None) -> Path:
    """Wait for PID, back up EXE without overwriting, atomically replace it.

    Returns retained backup path. The backup directory must already exist.
    Timeout never forces process exit. Existing backups are refused. Use only
    for a caller-confirmed portable installation in a trusted writable folder.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise PortableUpdateError("Timeout must be in (0, 3600] seconds")
    expected = _hash(sha256)
    current = _hash(current_sha256) if current_sha256 is not None else None
    target = _path(exe)
    source = _path(asset)
    saved = _path(backup, exists=False)
    if saved.suffix.lower() != ".exe" or not saved.parent.is_dir():
        raise PortableUpdateError("Backup requires an existing directory and .exe suffix")
    if len({target, source, saved}) != 3 or os.path.samefile(target, source):
        raise PortableUpdateError("EXE, asset and backup must be distinct")
    if saved.exists():
        raise PortableUpdateError("Backup already exists")
    staged = None
    try:
        with _lock(target), _process(pid, target) as wait:
            original = _digest(target)
            if current is not None and original != current:
                raise PortableUpdateError("Current EXE SHA256 mismatch")
            staged = _stage(source, target, expected)
            if ready_file is not None:
                ready = _path(ready_file, exists=False)
                with ready.open("x", encoding="ascii") as stream:
                    stream.write("ready")
            wait(timeout)
            if _digest(target) != original:
                raise PortableUpdateError("Target changed while waiting")
            _copy(target, saved)
            if _digest(saved) != original:
                raise PortableUpdateError("Backup verification failed; target unchanged")
            os.replace(staged, target)
            staged = None
            try:
                if _digest(target) != expected:
                    raise PortableUpdateError("Installed EXE SHA256 mismatch")
            except Exception as install_error:
                try:
                    staged = _stage(saved, target, original)
                    os.replace(staged, target)
                    staged = None
                    if _digest(target) != original:
                        raise PortableUpdateError("Restored EXE SHA256 mismatch")
                except Exception as recovery_error:
                    raise PortableUpdateError(f"Rollback failed; retained backup: {saved}") from recovery_error
                raise PortableUpdateError("Update failed; old EXE restored") from install_error
        return saved
    except OSError as exc:
        raise PortableUpdateError(f"Portable update failed: {exc}") from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def rollback_portable(*, pid: int, exe: str | Path, backup: str | Path,
                      failed_backup: str | Path, current_sha256: str,
                      backup_sha256: str, timeout: float = 60) -> Path:
    """Restore a verified backup, retaining the replaced version separately.

    The running updated process must exit first, just as for an update.
    Neither a running process nor a different current EXE is overwritten.
    """
    return replace_portable(pid=pid, exe=exe, asset=backup, backup=failed_backup,
                            sha256=backup_sha256, current_sha256=current_sha256,
                            timeout=timeout)


def _restart(exe: Path) -> None:
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    environment = os.environ.copy()
    environment["SC_PANEL_RESTART"] = "1"
    subprocess.Popen([str(exe)], cwd=exe.parent, creationflags=flags,
                     close_fds=True, env=environment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("apply", "rollback"))
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--backup", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--asset")
    parser.add_argument("--ready-file")
    parser.add_argument("--backup-sha256")
    parser.add_argument("--failed-backup")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "apply":
            if not args.asset:
                parser.error("apply requires --asset")
            target = Path(args.exe).resolve()
            replace_portable(pid=args.pid, exe=target, asset=args.asset,
                             backup=args.backup, sha256=args.sha256, timeout=args.timeout,
                             ready_file=args.ready_file)
            if args.restart:
                _restart(target)
        else:
            if not args.backup_sha256 or not args.failed_backup:
                parser.error("rollback requires --backup-sha256 and --failed-backup")
            rollback_portable(pid=args.pid, exe=args.exe, backup=args.backup,
                              failed_backup=args.failed_backup, current_sha256=args.sha256,
                              backup_sha256=args.backup_sha256, timeout=args.timeout)
    except PortableUpdateError as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
