"""The release manifest written by build.ps1 must be the one the portable
update reads. The build itself is never run: only its New-UpdateManifest
function is extracted and executed on throw-away files."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

from app import distribution, portable_updates
from app.update_download import UpdateDownloadError

ROOT = Path(__file__).parents[1]
VERSION = "9.0.0"


def _manifest(assets):
    return dict(schema=1, variant="u064241", channel="stable", version=VERSION,
                repository=distribution.PRODUCT, assets=assets)


def _record(name, content=b"x"):
    return dict(name=name, size=len(content), sha256=hashlib.sha256(content).hexdigest())


def test_manifest_with_installer_and_portable_zip_is_accepted():
    portable = _record(portable_updates.portable_asset_name(VERSION))
    data = _manifest([_record(f"StreamingCommunityDownloader-Setup-v{VERSION}.exe"), portable])
    assert portable_updates.portable_record(data, VERSION) == portable


def test_installer_only_manifest_is_refused():
    # What build.ps1 published before the fix: the update could never start.
    data = _manifest([_record(f"StreamingCommunityDownloader-Setup-v{VERSION}.exe")])
    with pytest.raises(UpdateDownloadError, match="Pacchetto portable mancante"):
        portable_updates.portable_record(data, VERSION)


@pytest.mark.parametrize("key,value", [("schema", 2), ("variant", "upstream"), ("channel", "beta"),
                                       ("version", "9.0.1"), ("repository", "someone/else")])
def test_foreign_manifest_is_refused(key, value):
    data = _manifest([_record(portable_updates.portable_asset_name(VERSION))])
    data[key] = value
    with pytest.raises(UpdateDownloadError, match="incompatibile"):
        portable_updates.portable_record(data, VERSION)


def test_build_publishes_the_portable_zip_everywhere():
    build = (ROOT / "build.ps1").read_text("utf-8")
    assert "$assets = @($outputPath, $portablePath)" in build
    assert "New-UpdateManifest -Version $Version -AssetPaths $assets" in build
    # SHA256SUMS is derived from the same records as the manifest.
    assert "$checksums = $manifest.assets | ForEach-Object" in build
    release_files = build[build.index("$releaseFiles = @("):build.index("foreach ($releaseFile in $releaseFiles)")]
    for expected in ('Setup-v$Version.exe', "$portablePath", "update-manifest.json", "SHA256SUMS.txt"):
        assert expected in release_files
    # The ZIP is deleted after packaging only for a non-release build.
    assert "if (-not $Release -and (Test-Path -LiteralPath $portablePath -PathType Leaf))" in build
    assert re.search(r"pyinstaller==\$PyInstallerVersion", build)
    assert re.search(r"\$PyInstallerVersion = '\d+\.\d+\.\d+'", build)


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")),
                    reason="PowerShell not available")
def test_manifest_generated_by_build_function(tmp_path):
    setup = tmp_path / f"StreamingCommunityDownloader-Setup-v{VERSION}.exe"
    portable = tmp_path / portable_updates.portable_asset_name(VERSION)
    setup.write_bytes(b"installer bytes")
    portable.write_bytes(b"portable zip bytes")
    out = tmp_path / "update-manifest.json"
    # Parse build.ps1 without running it and define only the one function.
    script = tmp_path / "run.ps1"
    script.write_text("""
param($Build, $Version, $Setup, $Portable, $Out)
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Build, [ref]$null, [ref]$null)
$function = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'New-UpdateManifest' }, $false)
if (-not $function) { throw 'New-UpdateManifest not found' }
. ([scriptblock]::Create($function.Extent.Text))
New-UpdateManifest -Version $Version -AssetPaths @($Setup, $Portable) |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Out -Encoding utf8
""", encoding="utf-8")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(script), str(ROOT / "build.ps1"), VERSION,
                    str(setup), str(portable), str(out)],
                   check=True, timeout=120, capture_output=True)
    data = json.loads(out.read_text("utf-8-sig"))
    assert list(data) == ["schema", "variant", "channel", "version", "repository", "assets"]
    assert [a["name"] for a in data["assets"]] == [setup.name, portable.name]
    record = portable_updates.portable_record(data, VERSION)
    assert record == dict(name=portable.name, size=portable.stat().st_size,
                          sha256=hashlib.sha256(portable.read_bytes()).hexdigest())
    # Same checks the updater applies to the record before downloading.
    assert type(record["size"]) is int and re.fullmatch(r"[0-9a-f]{64}", record["sha256"])


# ── Docker and CI configuration ──────────────────────────────────────────────

def test_workflow_publishes_only_tags_and_default_branch():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text("utf-8"))
    jobs = workflow["jobs"]
    assert workflow["permissions"] == {"contents": "read"}
    writers = [name for name, job in jobs.items()
               if job.get("permissions", {}).get("packages") == "write"]
    assert writers == ["build-and-push"]
    condition = jobs["build-and-push"]["if"]
    assert "refs/tags/v" in condition and "default_branch" in condition
    assert "if" not in jobs["test"] and "if" not in jobs["test-windows"]
    assert jobs["test-windows"]["runs-on"] == "windows-latest"
    steps = [step for job in jobs.values() for step in job["steps"]]
    # No step builds the updater/ folder, which this fork does not have.
    assert all(step.get("with", {}).get("context", ".") == "." for step in steps)
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


def test_latest_moves_only_with_release_tags():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text("utf-8")
    assert "type=raw,value=latest,enable=${{ startsWith(github.ref, 'refs/tags/v')" in workflow
    script = "\n".join(yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text("utf-8"))
                       ["build-and-push"]["script"])
    assert "CI_DEFAULT_BRANCH" not in script
    assert script.index('if [ -n "$CI_COMMIT_TAG" ]') < script.index(":latest")


def test_docker_context_is_an_allowlist():
    lines = [line.strip() for line in (ROOT / ".dockerignore").read_text("utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    assert lines[0] == "*"
    assert {"!app/", "!main.py", "!requirements.txt"} <= set(lines)
    included = {line[1:].rstrip("/") for line in lines if line.startswith("!")}
    assert included <= {"app", "main.py", "requirements.txt", "LICENSE", "README.md"}


def test_image_runs_unprivileged_with_a_healthcheck():
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    assert dockerfile.startswith("FROM python:3.11-slim-bookworm")
    assert "\nUSER 10001:10001\n" in dockerfile
    assert dockerfile.index("COPY . .") < dockerfile.index("USER 10001:10001") < dockerfile.index("CMD [")
    assert "HEALTHCHECK" in dockerfile
    assert "chown 10001:10001 /app/tmp /app/config /app/videos" in dockerfile


def test_compose_templates_pull_the_fork_image():
    for name in ("docker-compose.template.yml", "docker-compose.dev.template.yml"):
        text = (ROOT / name).read_text("utf-8")
        image = yaml.safe_load(text)["services"]["web"]["image"]
        assert image.startswith("ghcr.io/u064241/streamingcommunity-downloader")
        assert "edoardofiore" not in text.lower()


def test_dead_upstream_updater_ui_is_gone():
    panel = (ROOT / "app/static/panel.js").read_text("utf-8")
    modals = (ROOT / "app/templates/partials/_modals.html").read_text("utf-8")
    for dead in ("/api/updates/install", "installUpdate", "openUpdateModal", "refreshUpdateBadge"):
        assert dead not in panel
    assert 'id="update-modal"' not in modals
    # The fork's own release dialog is a different modal and stays.
    assert any('id="updates-modal"' in page.read_text("utf-8")
               for page in (ROOT / "app/templates").rglob("*.html"))
