[CmdletBinding()]
param(
    [switch]$SkipInnoSetup,
    [switch]$Release
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\')
$DistDir = Join-Path $ProjectRoot 'dist'
$WorkDir = Join-Path $ProjectRoot '.build-work'
$AppName = 'StreamingCommunityDownloader'
$Version = $null
$versionSource = Get-Content -LiteralPath (Join-Path $ProjectRoot 'app\__init__.py') -Raw
if ($versionSource -match '(?m)^__version__\s*=\s*"([^"]+)"') { $Version = $Matches[1] }
if (-not $Version) { throw 'Versione mancante in app\__init__.py.' }
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

    # Reused environments must also bundle the pinned FFmpeg version. Newer
    # imageio-ffmpeg releases require NVENC APIs absent from Pascal R580 drivers.
    & $venvPython -c "import importlib.metadata,sys; sys.exit(0 if importlib.metadata.version('imageio-ffmpeg') == '0.6.0' else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m pip install --disable-pip-version-check 'imageio-ffmpeg==0.6.0'
        if ($LASTEXITCODE -ne 0) { throw 'Installazione di FFmpeg compatibile fallita.' }
    }

    $runtimeHook = Join-Path $WorkDir 'runtime_hook.py'
    @'
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    executable_dir = Path(sys.executable).resolve().parent
    data_dir = executable_dir
    # Program Files is read-only for the application after installation.
    # Keep settings, databases and relative download paths in the user profile.
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        program_files = os.environ.get(variable)
        if program_files and executable_dir.is_relative_to(Path(program_files).resolve()):
            data_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "StreamingCommunityDownloader"
            data_dir.mkdir(parents=True, exist_ok=True)
            break
    os.chdir(data_dir)
'@ | Set-Content -LiteralPath $runtimeHook -Encoding utf8

    $pyinstallerArgs = @(
        '--noconfirm', '--clean', '--onefile', '--windowed',
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
        # random-user-agent loads this archive at runtime from package data;
        # without it the frozen app cannot build request headers, so saving a
        # source URL fails even though the same code works from Python.
        '--collect-data', 'random_user_agent',
        '--collect-submodules', 'mcp.server',
        '--collect-submodules', 'mcp.types',
        (Join-Path $ProjectRoot 'main.py')
    )
    & $venvPython -m PyInstaller @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) { throw 'Build PyInstaller fallita.' }

    $exePath = Join-Path $DistDir "$AppName.exe"
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { throw "Eseguibile non trovato: $exePath" }
    $helperExePath = Join-Path $DistDir "$AppName-Updater.exe"
    & $venvPython -m PyInstaller --noconfirm --clean --onefile --console `
        --name "$AppName-Updater" --distpath $DistDir --workpath (Join-Path $WorkDir 'updater') `
        --specpath $WorkDir (Join-Path $ProjectRoot 'app\portable_update.py')
    if ($LASTEXITCODE -ne 0) { throw 'Build helper aggiornamento fallita.' }
    if (-not (Test-Path -LiteralPath $helperExePath -PathType Leaf)) { throw "Helper aggiornamento non trovato: $helperExePath" }
    $outputPath = $exePath

    $portablePath = Join-Path $DistDir "$AppName-$Version-Portable.zip"
    $portableMarker = Join-Path $WorkDir 'sc-distribution.json'
    $installedMarker = Join-Path $WorkDir 'installed-distribution.json'
    [IO.File]::WriteAllText($portableMarker, '{"schema":1,"repository":"u064241/StreamingCommunity-downloader","mode":"portable"}', [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($installedMarker, '{"schema":1,"repository":"u064241/StreamingCommunity-downloader","mode":"installed"}', [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $portablePath) { Remove-Item -LiteralPath $portablePath -Force }
    Compress-Archive -LiteralPath @($exePath, $helperExePath, $portableMarker) -DestinationPath $portablePath -CompressionLevel Optimal

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
DefaultDirName={commonpf64}\StreamingCommunity Downloader
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultGroupName=StreamingCommunity Downloader
Uninstallable=yes
AppMutex=Local\StreamingCommunityDownloaderRunning
CreateUninstallRegKey=not IsPortable
OutputDir="$DistDir"
OutputBaseFilename=StreamingCommunityDownloader-Setup
PrivilegesRequired=admin
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\$AppName.exe
LicenseFile="$ProjectRoot\LICENSE"
[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[CustomMessages]
italian.ModeTitle=Modalità di installazione
italian.ModeSubtitle=Scegli come vuoi usare StreamingCommunity Downloader.
italian.ModeInstall=&Installa (menu Start, disinstallazione da Impostazioni)
italian.ModePortable=&Solo versione portable (file in una cartella scelta)
italian.PortableDirName=StreamingCommunity Downloader
italian.DesktopIcon=Crea un collegamento sul &desktop
italian.LinkTitle=Collegamenti portable
italian.LinkSubtitle=Vuoi creare anche collegamenti per la versione portable?
italian.UninstallName=Disinstalla StreamingCommunity Downloader
italian.LaunchApp=Avvia StreamingCommunity Downloader
italian.RemoveData=Vuoi eliminare log e file temporanei di StreamingCommunity Downloader? Impostazioni e database saranno conservati.
italian.RemoveDownloads=Vuoi eliminare anche i file scaricati nelle cartelle videos dell'applicazione e del suo profilo utente? Questa operazione è definitiva. Le cartelle esterne a queste due posizioni non saranno toccate.
italian.CloseBeforeUninstall=StreamingCommunity Downloader è ancora in esecuzione. Chiudi tutte le schede del portale e attendi il termine dei download, poi ripeti la disinstallazione.
english.ModeTitle=Installation mode
english.ModeSubtitle=Choose how you want to use StreamingCommunity Downloader.
english.ModeInstall=&Install (Start menu, uninstall from Settings)
english.ModePortable=&Portable only (files in a folder of your choice)
english.PortableDirName=StreamingCommunity Downloader
english.DesktopIcon=Create a &desktop shortcut
english.LinkTitle=Portable shortcuts
english.LinkSubtitle=Would you also like shortcuts for the portable version?
english.UninstallName=Uninstall StreamingCommunity Downloader
english.LaunchApp=Launch StreamingCommunity Downloader
english.RemoveData=Delete StreamingCommunity Downloader logs and temporary files? Settings and the database will be kept.
english.RemoveDownloads=Also permanently delete downloaded files in the application and user profile videos folders? Folders outside these two locations will not be touched.
english.CloseBeforeUninstall=StreamingCommunity Downloader is still running. Close all portal tabs and wait for downloads to finish, then run the uninstaller again.
[Files]
Source: "$exePath"; DestDir: "{app}"; Flags: ignoreversion
Source: "$helperExePath"; DestDir: "{app}"; Flags: ignoreversion
Source: "$portableMarker"; DestDir: "{app}"; DestName: "sc-distribution.json"; Flags: ignoreversion; Check: IsPortable
Source: "$installedMarker"; DestDir: "{app}"; DestName: "sc-distribution.json"; Flags: ignoreversion; Check: not IsPortable
[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked
[Icons]
Name: "{group}\StreamingCommunity Downloader"; Filename: "{app}\$AppName.exe"; WorkingDir: "{app}"; Check: not IsPortable
Name: "{group}\{cm:UninstallName}"; Filename: "{uninstallexe}"; Check: not IsPortable
Name: "{autodesktop}\StreamingCommunity Downloader"; Filename: "{app}\$AppName.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Check: not IsPortable
Name: "{userprograms}\StreamingCommunity Downloader\StreamingCommunity Downloader"; Filename: "{app}\$AppName.exe"; WorkingDir: "{app}"; Check: IsPortable
Name: "{userprograms}\StreamingCommunity Downloader\{cm:UninstallName}"; Filename: "{uninstallexe}"; Check: IsPortable
Name: "{userdesktop}\StreamingCommunity Downloader"; Filename: "{app}\$AppName.exe"; WorkingDir: "{app}"; Check: PortableDesktop
[Run]
Filename: "{app}\$AppName.exe"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent runascurrentuser
[UninstallDelete]
Type: filesandordirs; Name: "{app}\tmp"; Check: ShouldRemoveTemporaryData
Type: filesandordirs; Name: "{app}\logs"; Check: ShouldRemoveTemporaryData
Type: filesandordirs; Name: "{app}\__pycache__"; Check: ShouldRemoveTemporaryData
Type: filesandordirs; Name: "{app}\videos"; Check: ShouldRemoveLocalDownloads
Type: dirifempty; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\StreamingCommunityDownloader\tmp"; Check: ShouldRemoveTemporaryData
Type: filesandordirs; Name: "{localappdata}\StreamingCommunityDownloader\logs"; Check: ShouldRemoveTemporaryData
Type: filesandordirs; Name: "{localappdata}\StreamingCommunityDownloader\__pycache__"; Check: ShouldRemoveTemporaryData
Type: filesandordirs; Name: "{localappdata}\StreamingCommunityDownloader\videos"; Check: ShouldRemoveLocalDownloads
Type: dirifempty; Name: "{localappdata}\StreamingCommunityDownloader"
Type: dirifempty; Name: "{userprograms}\StreamingCommunity Downloader"
Type: dirifempty; Name: "{userprograms}\StreamingCommunity Downloader (portable)"
Type: dirifempty; Name: "{autoprograms}\StreamingCommunity Downloader"
[Code]
var
  ModePage: TInputOptionWizardPage;
  LinkPage: TInputOptionWizardPage;
  InstallDir: String;
  LastPortable: Boolean;
  RemoveTemporaryData: Boolean;
  RemoveLocalDownloads: Boolean;

function ShouldRemoveTemporaryData: Boolean;
begin
  Result := RemoveTemporaryData;
end;

function ShouldRemoveLocalDownloads: Boolean;
begin
  Result := RemoveLocalDownloads;
end;

function InitializeUninstall: Boolean;
begin
  Result := not CheckForMutexes('Local\StreamingCommunityDownloaderRunning');
  if not Result then begin
    MsgBox(CustomMessage('CloseBeforeUninstall'), mbInformation, MB_OK);
    Exit;
  end;
  RemoveTemporaryData := False;
  RemoveLocalDownloads := False;
  if not UninstallSilent then begin
    RemoveTemporaryData := MsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    RemoveLocalDownloads := MsgBox(CustomMessage('RemoveDownloads'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  end;
end;

function IsPortable: Boolean;
begin
  Result := Assigned(ModePage) and ModePage.Values[1];
end;

function PortableDesktop: Boolean;
begin
  Result := IsPortable and LinkPage.Values[0];
end;

procedure ApplyModeDir;
begin
  if IsPortable = LastPortable then Exit;
  if ExpandConstant('{param:DIR|}') <> '' then Exit;
  LastPortable := IsPortable;
  if IsPortable then begin
    InstallDir := WizardForm.DirEdit.Text;
    WizardForm.DirEdit.Text := ExpandConstant('{src}\') + CustomMessage('PortableDirName');
  end else begin
    WizardForm.DirEdit.Text := InstallDir;
  end;
end;

procedure InitializeWizard;
begin
  ModePage := CreateInputOptionPage(wpWelcome, CustomMessage('ModeTitle'), CustomMessage('ModeSubtitle'), '', True, False);
  ModePage.Add(CustomMessage('ModeInstall'));
  ModePage.Add(CustomMessage('ModePortable'));
  ModePage.Values[0] := True;
  if ExpandConstant('{param:PORTABLE|0}') = '1' then begin
    ModePage.Values[0] := False;
    ModePage.Values[1] := True;
  end;

  LinkPage := CreateInputOptionPage(wpSelectDir, CustomMessage('LinkTitle'), CustomMessage('LinkSubtitle'), '', False, False);
  LinkPage.Add(CustomMessage('DesktopIcon'));
  LinkPage.Values[0] := Pos('desktopicon', ExpandConstant('{param:TASKS|}')) > 0;
  InstallDir := WizardForm.DirEdit.Text;
  LastPortable := False;
  ApplyModeDir;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ModePage.ID then ApplyModeDir;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectDir then ApplyModeDir;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = wpSelectTasks) and IsPortable then Result := True;
  if Assigned(LinkPage) and (PageID = LinkPage.ID) and not IsPortable then Result := True;
end;
"@
            [IO.File]::WriteAllText($issPath, $iss, [Text.UTF8Encoding]::new($false))
            & $iscc /Qp $issPath
            if ($LASTEXITCODE -ne 0) { throw 'Compilazione installer Inno Setup fallita.' }
            $outputPath = Join-Path $DistDir 'StreamingCommunityDownloader-Setup.exe'
            if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) { throw 'Installer Inno Setup non trovato.' }
            if (-not $Release) { Remove-Item -LiteralPath $exePath -Force }
        } else {
            Write-Warning 'Inno Setup non trovato: creato solo eseguibile PyInstaller.'
        }
    }

    if ($Release) {
        $assets = @($portablePath)
        if ((Test-Path -LiteralPath $outputPath -PathType Leaf) -and $outputPath -ne $exePath) {
            $assets += $outputPath
        }
        $assetRecords = foreach ($asset in $assets) {
            $file = Get-Item -LiteralPath $asset
            $hash = (Get-FileHash -LiteralPath $asset -Algorithm SHA256).Hash.ToLowerInvariant()
            [ordered]@{ name = $file.Name; size = $file.Length; sha256 = $hash }
        }
        $manifest = [ordered]@{
            schema = 1
            variant = 'u064241'
            channel = 'stable'
            version = $Version
            repository = 'u064241/StreamingCommunity-downloader'
            assets = @($assetRecords)
        }
        $manifestPath = Join-Path $DistDir 'update-manifest.json'
        $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
        $checksums = $assetRecords | ForEach-Object { "$($_.sha256)  $($_.name)" }
        $checksums | Set-Content -LiteralPath (Join-Path $DistDir 'SHA256SUMS.txt') -Encoding ascii
        Write-Host "Release manifest: $manifestPath" -ForegroundColor Green
    }
    Write-Host "Artefatto: $outputPath" -ForegroundColor Green
} finally {
    Remove-SafeTemporaryPath $WorkDir
    Remove-SafeTemporaryPath (Join-Path $ProjectRoot 'build')
    Remove-PythonCaches $ProjectRoot
    if ($null -eq $OldBytecodeSetting) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
    else { $env:PYTHONDONTWRITEBYTECODE = $OldBytecodeSetting }
    Write-Host "Cartelle temporanee rimosse; dist conservata: $DistDir" -ForegroundColor Green
}
