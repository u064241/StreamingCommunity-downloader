"""Isolated stdlib tests; no app startup, network, installer or real app EXEs."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import portable_update as updater


def digest(data):
    return hashlib.sha256(data).hexdigest()


class PortableUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portable update test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.exe = self.root / "App.exe"
        self.asset = self.root / "New.exe"
        self.backup = self.root / "Previous.exe"
        self.exe.write_bytes(b"old exe")
        self.asset.write_bytes(b"verified new exe")
        self.waits = []

        @contextmanager
        def process(pid, exe):
            self.assertEqual(pid, 123)
            self.assertEqual(exe, self.exe)
            yield lambda timeout: self.waits.append(timeout)

        self.process_patch = patch.object(updater, "_process", process)
        self.process_patch.start()
        self.addCleanup(self.process_patch.stop)

    def apply(self, **overrides):
        options = dict(pid=123, exe=self.exe, asset=self.asset,
                       backup=self.backup, sha256=digest(b"verified new exe"))
        options.update(overrides)
        return updater.replace_portable(**options)

    def assert_clean(self):
        self.assertFalse(list(self.root.glob(".portable-update-*")))
        self.assertFalse(list(self.root.glob("*.lock")))

    def test_update_keeps_backup_asset_and_unrelated_data(self):
        data = self.root / "panel.db"
        data.write_bytes(b"untouched database")
        self.assertEqual(self.apply(), self.backup)
        self.assertEqual(self.exe.read_bytes(), b"verified new exe")
        self.assertEqual(self.backup.read_bytes(), b"old exe")
        self.assertEqual(self.asset.read_bytes(), b"verified new exe")
        self.assertEqual(data.read_bytes(), b"untouched database")
        self.assertEqual(self.waits, [60])
        self.assert_clean()

    def test_wrong_hash_never_replaces_or_backs_up(self):
        with self.assertRaisesRegex(updater.PortableUpdateError, "SHA256 mismatch"):
            self.apply(sha256="0" * 64)
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.waits, [])
        self.assert_clean()

    def test_existing_backup_is_never_overwritten(self):
        self.backup.write_bytes(b"precious backup")
        with self.assertRaisesRegex(updater.PortableUpdateError, "Backup already exists"):
            self.apply()
        self.assertEqual(self.backup.read_bytes(), b"precious backup")
        self.assertEqual(self.exe.read_bytes(), b"old exe")

    def test_lock_blocks_concurrent_update_and_is_retained(self):
        lock = self.exe.with_name(self.exe.name + ".portable-update.lock")
        lock.write_text("other helper")
        with self.assertRaises(updater.PortableUpdateError):
            self.apply()
        self.assertEqual(lock.read_text(), "other helper")
        self.assertFalse(self.backup.exists())

    def test_timeout_leaves_exe_unchanged(self):
        @contextmanager
        def timeout_process(*args):
            def wait(timeout):
                raise updater.PortableUpdateError("Process exit timeout")
            yield wait

        with patch.object(updater, "_process", timeout_process):
            with self.assertRaisesRegex(updater.PortableUpdateError, "timeout"):
                self.apply()
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assertFalse(self.backup.exists())
        self.assert_clean()

    def test_target_changed_during_wait_is_not_overwritten(self):
        @contextmanager
        def changed_process(*args):
            yield lambda timeout: self.exe.write_bytes(b"another version")

        with patch.object(updater, "_process", changed_process):
            with self.assertRaisesRegex(updater.PortableUpdateError, "changed"):
                self.apply()
        self.assertEqual(self.exe.read_bytes(), b"another version")
        self.assertFalse(self.backup.exists())
        self.assert_clean()

    def test_replace_permission_error_keeps_old_exe_and_backup(self):
        with patch.object(updater.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(updater.PortableUpdateError, "locked"):
                self.apply()
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assertEqual(self.backup.read_bytes(), b"old exe")
        self.assert_clean()

    def test_backup_copy_failure_leaves_target_unchanged(self):
        with patch.object(updater, "_copy", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(updater.PortableUpdateError, "disk full"):
                self.apply()
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assert_clean()

    def test_corrupted_backup_aborts_before_replace(self):
        def bad_copy(source, dest):
            dest.write_bytes(b"corrupt")

        with patch.object(updater, "_copy", bad_copy):
            with self.assertRaisesRegex(updater.PortableUpdateError, "Backup verification"):
                self.apply()
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assert_clean()

    def test_post_replace_failure_automatically_restores_old_exe(self):
        original_digest = updater._digest
        failed = False

        def corrupt_once(path):
            nonlocal failed
            if path == self.exe and path.read_bytes() == b"verified new exe" and not failed:
                failed = True
                return "0" * 64
            return original_digest(path)

        with patch.object(updater, "_digest", corrupt_once):
            with self.assertRaisesRegex(updater.PortableUpdateError, "old EXE restored"):
                self.apply()
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assertEqual(self.backup.read_bytes(), b"old exe")
        self.assert_clean()

    def test_failed_automatic_rollback_preserves_backup(self):
        original_digest = updater._digest
        original_replace = updater.os.replace
        count = 0

        def replace(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise PermissionError("rollback locked")
            original_replace(source, target)

        def invalid_installed(path):
            if path == self.exe and path.read_bytes() == b"verified new exe":
                return "0" * 64
            return original_digest(path)

        with patch.object(updater, "_digest", invalid_installed), patch.object(updater.os, "replace", replace):
            with self.assertRaisesRegex(updater.PortableUpdateError, "Rollback failed; retained backup"):
                self.apply()
        self.assertEqual(self.backup.read_bytes(), b"old exe")
        self.assert_clean()

    def test_explicit_rollback_retains_both_versions(self):
        self.apply()
        failed = self.root / "Failed.exe"
        updater.rollback_portable(pid=123, exe=self.exe, backup=self.backup,
                                  failed_backup=failed, current_sha256=digest(b"verified new exe"),
                                  backup_sha256=digest(b"old exe"))
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assertEqual(self.backup.read_bytes(), b"old exe")
        self.assertEqual(failed.read_bytes(), b"verified new exe")
        self.assert_clean()

    def test_rollback_rejects_unexpected_current_version(self):
        self.backup.write_bytes(b"old backup")
        with self.assertRaisesRegex(updater.PortableUpdateError, "Current EXE"):
            updater.rollback_portable(pid=123, exe=self.exe, backup=self.backup,
                                      failed_backup=self.root / "Failed.exe",
                                      current_sha256="0" * 64, backup_sha256=digest(b"old backup"))
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assert_clean()

    def test_invalid_arguments(self):
        for options in (dict(timeout=0), dict(timeout=float("nan")), dict(timeout=3601),
                        dict(sha256="not a hash"), dict(asset=self.exe),
                        dict(backup=self.exe), dict(exe=Path("relative.exe"))):
            with self.subTest(options=options), self.assertRaises(updater.PortableUpdateError):
                self.apply(**options)
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assert_clean()

    def test_zip_asset_is_rejected(self):
        archive = self.root / "Portable.zip"
        archive.write_bytes(b"not extracted")
        with self.assertRaisesRegex(updater.PortableUpdateError, "EXE"):
            self.apply(asset=archive)

    def test_missing_backup_directory_is_rejected(self):
        with self.assertRaisesRegex(updater.PortableUpdateError, "existing directory"):
            self.apply(backup=self.root / "missing" / "Backup.exe")
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assert_clean()

    def test_symbolic_link_asset_is_rejected(self):
        alias = self.root / "Linked.exe"
        try:
            alias.symlink_to(self.asset)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        with self.assertRaisesRegex(updater.PortableUpdateError, "Reparse points"):
            self.apply(asset=alias)
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assert_clean()

    def test_hardlinked_asset_is_rejected(self):
        alias = self.root / "Alias.exe"
        os.link(self.exe, alias)
        with self.assertRaisesRegex(updater.PortableUpdateError, "distinct"):
            self.apply(asset=alias)

    def test_process_mismatch_does_not_mutate_files(self):
        @contextmanager
        def mismatch(*args):
            raise updater.PortableUpdateError("PID does not match target EXE")
            yield  # pragma: no cover

        with patch.object(updater, "_process", mismatch):
            with self.assertRaisesRegex(updater.PortableUpdateError, "PID"):
                self.apply()
        self.assertFalse(self.backup.exists())
        self.assert_clean()


@unittest.skipUnless(os.name == "nt", "Windows process handle integration")
class WindowsProcessTests(unittest.TestCase):
    def setUp(self):
        self.child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                      stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(self.cleanup_child)

    def cleanup_child(self):
        if not self.child.stdin.closed:
            self.child.stdin.close()
        self.child.wait(timeout=10)

    def test_waits_for_real_process_exit(self):
        with updater._process(self.child.pid, Path(sys.executable)) as wait:
            self.child.stdin.close()
            wait(10)

    def test_timeout_does_not_terminate_process(self):
        with updater._process(self.child.pid, Path(sys.executable)) as wait:
            with self.assertRaisesRegex(updater.PortableUpdateError, "timeout"):
                wait(0.01)
        self.assertIsNone(self.child.poll())

    def test_rejects_real_pid_with_different_exe(self):
        with self.assertRaisesRegex(updater.PortableUpdateError, "does not match"):
            with updater._process(self.child.pid, Path(__file__)):
                self.fail("mismatched process accepted")

    def test_rejects_own_pid(self):
        with self.assertRaisesRegex(updater.PortableUpdateError, "Invalid target PID"):
            with updater._process(os.getpid(), Path(sys.executable)):
                self.fail("own PID accepted")


if __name__ == "__main__":
    unittest.main()
