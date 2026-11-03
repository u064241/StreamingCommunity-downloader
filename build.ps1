[CmdletBinding()]
param(
    [switch]$SkipInnoSetup
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\')
$DistDir = Join-Path $ProjectRoot 'dist'
$WorkDir = Join-Path $ProjectRoot '.build-work'
$AppName = 'StreamingCommunityDownloader'
$Version = '1.0.0'
$versionSource = Get-Content -LiteralPath (Join-Path $ProjectRoot 'app\__init__.py') -Raw
if ($versionSource -match '(?m)^__version__\s*=\s*"([^"]+)"') { $Version = $Matches[1] }
$OldBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE

function Resolve-Python {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('3.12', '3.13', '3.11', '3.14')) {
            $candidate = & $launcher.Source "-$version" -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) { return ([string]$candidate).Trim() }
        }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $version = & $python.Source -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]'3.11' -and [version]$version -lt [version]'3.15') {
            return $python.Source
        }
    }
    throw 'Python 3.11–3.14 non trovato. Installa Python 3.12 oppure aggiungilo al PATH.'
}

function Find-ReadyBuildPython {
    $modules = 'fastapi,uvicorn,mcp,jinja2,filelock,cloudscraper,apprise,requests,PyInstaller'
    foreach ($environment in @('.venv', '.venv312')) {
        $candidate = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "..\..\$environment\Scripts\python.exe"))
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        & $candidate -c "import importlib.util,sys; names='$modules'.split(','); sys.exit(0 if all(importlib.util.find_spec(n) for n in names) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $null
}

function Find-InnoCompiler {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($programRoot in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        if (-not $programRoot) { continue }
        foreach ($version in @('7', '6', '5')) {
            $candidate = Join-Path $programRoot "Inno Setup $version\ISCC.exe"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    return $null
}

function Remove-SafeTemporaryPath([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $resolved = (Resolve-Path -LiteralPath $Path).Path.TrimEnd('\')
    $prefix = $ProjectRoot + '\'
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved -eq $DistDir -or $resolved -eq $ProjectRoot) {
        throw "Rifiuto di rimuovere un percorso fuori dall'area temporanea: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Remove-PythonCaches([string]$Directory) {
    foreach ($child in Get-ChildItem -LiteralPath $Directory -Directory -Force -ErrorAction SilentlyContinue) {
        if ($child.Name -in @('.git', '.build-work', 'dist', '.venv', 'venv', 'env', 'node_modules', '.cache', 'tmp', 'videos')) {
            continue
        }
        if ($child.Name -in @('__pycache__', '.pytest_cache', '.mypy_cache')) {
            Remove-SafeTemporaryPath $child.FullName
            continue
        }
        Remove-PythonCaches $child.FullName
    }
}

try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
    New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null

    $venvPython = Find-ReadyBuildPython
    if ($venvPython) {
        Write-Host "Uso l'ambiente Python già pronto: $venvPython"
    } else {
        $basePython = Resolve-Python
        $venvPath = Join-Path $WorkDir 'venv'
        $venvPython = Join-Path $venvPath 'Scripts\python.exe'
        & $basePython -m venv $venvPath
        if ($LASTEXITCODE -ne 0) { throw 'Creazione ambiente temporaneo Python fallita.' }
        & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Installazione delle dipendenze del progetto fallita.' }
        & $venvPython -m pip install --disable-pip-version-check pyinstaller
        if ($LASTEXITCODE -ne 0) { throw 'Installazione di PyInstaller fallita.' }
    }

    $runtimeHook = Join-Path $WorkDir 'runtime_hook.py'
    @'
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    os.chdir(Path(sys.executable).resolve().parent)
'@ | Set-Content -LiteralPath $runtimeHook -Encoding utf8

    $pyinstallerArgs = @(
        '--noconfirm', '--clean', '--onefile', '--console',
        '--name', $AppName,
        '--distpath', $DistDir,
        '--workpath', (Join-Path $WorkDir 'pyinstaller'),
        '--specpath', $WorkDir,
        '--runtime-hook', $runtimeHook,
        '--add-data', "$(Join-Path $ProjectRoot 'app\templates');app/templates",
        '--add-data', "$(Join-Path $ProjectRoot 'app\static');app/static",
        '--collect-all', 'app',
        '--collect-all', 'uvicorn',
        '--collect-all', 'fastapi',
        '--collect-all', 'starlette',
        '--collect-submodules', 'mcp.server',
        '--collect-submodules', 'mcp.types',
        (Join-Path $ProjectRoot 'main.py')
    )
    & $venvPython -m PyInstaller @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) { throw 'Build PyInstaller fallita.' }

    $exePath = Join-Path $DistDir "$AppName.exe"
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { throw "Eseguibile non trovato: $exePath" }

    if (-not $SkipInnoSetup) {
        $iscc = Find-InnoCompiler
        if ($iscc) {
            $issPath = Join-Path $WorkDir 'StreamingCommunityDownloader.iss'
            $iss = @"
#define MyAppName "StreamingCommunity Downloader"
#define MyAppVersion "$Version"
[Setup]
AppId={{4C51C76A-2F6D-4B0E-A0DD-39AC03C3F60C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\StreamingCommunityDownloader
DefaultGroupName=StreamingCommunity Downloader
OutputDir="$DistDir"
OutputBaseFilename=StreamingCommunityDownloader-Setup
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\$AppName.exe
LicenseFile="$ProjectRoot\LICENSE"
[Files]
Source: "$exePath"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\StreamingCommunity Downloader"; Filename: "{app}\$AppName.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\StreamingCommunity Downloader"; Filename: "{app}\$AppName.exe"; WorkingDir: "{app}"
"@
            [IO.File]::WriteAllText($issPath, $iss, [Text.UTF8Encoding]::new($false))
            & $iscc /Qp $issPath
            if ($LASTEXITCODE -ne 0) { throw 'Compilazione installer Inno Setup fallita.' }
        } else {
            Write-Warning 'Inno Setup non trovato: creato solo eseguibile PyInstaller.'
        }
    }

    Write-Host "Eseguibile: $exePath" -ForegroundColor Green
} finally {
    Remove-SafeTemporaryPath $WorkDir
    Remove-SafeTemporaryPath (Join-Path $ProjectRoot 'build')
    Remove-PythonCaches $ProjectRoot
    if ($null -eq $OldBytecodeSetting) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
    else { $env:PYTHONDONTWRITEBYTECODE = $OldBytecodeSetting }
    Write-Host "Cartelle temporanee rimosse; dist conservata: $DistDir" -ForegroundColor Green
}
