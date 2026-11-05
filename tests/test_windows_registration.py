import os
import subprocess
import sys

import pytest

from app import __version__, windows_registration

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows portable registration')


def test_portable_registration_uses_bundled_script_and_current_version(tmp_path, monkeypatch):
    root = tmp_path / 'portable'
    root.mkdir()
    bundle = tmp_path / 'bundle' / 'portable'
    bundle.mkdir(parents=True)
    (bundle / 'Register-Portable.ps1').write_text('# registration', encoding='utf-8')
    monkeypatch.setattr(windows_registration.distribution, 'mode', lambda: 'portable')
    monkeypatch.setattr(sys, 'executable', str(root / 'StreamingCommunityDownloader.exe'))
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle.parent), raising=False)
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    windows_registration.register_portable()
    assert (root / 'Register-Portable.ps1').read_text() == '# registration'
    command = calls[0][0][0]
    assert command[-2:] == ['-Version', __version__]
    assert calls[0][1]['creationflags'] == subprocess.CREATE_NO_WINDOW


def test_setup_portable_keeps_its_existing_uninstaller(tmp_path, monkeypatch):
    (tmp_path / 'unins000.exe').write_bytes(b'existing uninstaller')
    monkeypatch.setattr(windows_registration.distribution, 'mode', lambda: 'portable')
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'StreamingCommunityDownloader.exe'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not register twice')))
    windows_registration.register_portable()
    assert not (tmp_path / 'Register-Portable.ps1').exists()


def test_source_checkout_does_not_register(monkeypatch):
    monkeypatch.setattr(windows_registration.distribution, 'mode', lambda: 'source')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not run')))
    windows_registration.register_portable()
