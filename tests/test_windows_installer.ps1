[CmdletBinding()]
param([Parameter(Mandatory)][string]$SetupPath, [switch]$IsolateExistingProcesses)
$ErrorActionPreference = 'Stop'
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('sc-setup-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
if ($IsolateExistingProcesses) {
    # Compile the production installer recipe with a separate test identity/mutex.
    # Never bypass the running-app guard of the user's real installer.
    $ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    $buildAst = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $ProjectRoot 'build.ps1'), [ref]$null, [ref]$null)
    $recipe = $buildAst.Find({param($node)
        $node -is [System.Management.Automation.Language.ExpandableStringExpressionAst] -and
        $node.Value.StartsWith('#define MyAppName')
    }, $true)
    if (-not $recipe) { throw 'Installer recipe not found' }
    $AppName = 'StreamingCommunityDownloader'
    $Version = '2.8.4'
    $DistDir = $fixture
    $exePath = Join-Path $ProjectRoot 'dist\StreamingCommunityDownloader.exe'
    $helperExePath = Join-Path $ProjectRoot 'dist\StreamingCommunityDownloader-Updater.exe'
    $portableMarker = Join-Path $fixture 'portable.json'
    $installedMarker = Join-Path $fixture 'installed.json'
    [IO.File]::WriteAllText($portableMarker, '{"schema":1,"repository":"u064241/StreamingCommunity-downloader","mode":"portable"}')
    [IO.File]::WriteAllText($installedMarker, '{"schema":1,"repository":"u064241/StreamingCommunity-downloader","mode":"installed"}')
    $recipeText = Invoke-Expression $recipe.Extent.Text
    $identity = [guid]::NewGuid().ToString()
    $recipeText = $recipeText.Replace('4C51C76A-2F6D-4B0E-A0DD-39AC03C3F60C', $identity)
    $recipeText = $recipeText.Replace('StreamingCommunityDownloaderRunning', "StreamingCommunityDownloaderTest-$identity")
    $recipeText = $recipeText.Replace('StreamingCommunityDownloader-Portable-', "StreamingCommunityDownloader-Test-$identity-Portable-")
    $recipeText = [regex]::Replace($recipeText, '(?s)\[Icons\].*?\[Run\]', "[Icons]`r`n[Run]")
    $testRecipe = Join-Path $fixture 'test.iss'
    [IO.File]::WriteAllText($testRecipe, $recipeText)
    $compiler = Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'
    & $compiler /Qp $testRecipe
    if ($LASTEXITCODE -ne 0) { throw 'Isolated installer compilation failed' }
    $SetupPath = Join-Path $fixture 'StreamingCommunityDownloader-Setup.exe'
}
$programs = Join-Path ([Environment]::GetFolderPath('Programs')) 'StreamingCommunity Downloader'
$shortcutBackup = Join-Path $fixture 'shortcuts'
if (Test-Path -LiteralPath $programs) { Copy-Item -LiteralPath $programs -Destination $shortcutBackup -Recurse }
$registryRoot = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
$existing = @(Get-ChildItem -LiteralPath $registryRoot | Get-ItemProperty | Where-Object { $_.DisplayName -eq 'StreamingCommunity Downloader' })
if ($existing.Count -and -not $IsolateExistingProcesses) { throw 'An installed edition already exists; refusing to overwrite its registration during testing.' }
$locations = @()
try {
    foreach ($mode in @('portable', 'installed')) {
        $location = Join-Path $fixture $mode
        $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS',
            ('/DIR="' + $location + '"'), ('/GROUP="SC release test ' + (Split-Path $fixture -Leaf) + '"'),
            ('/LOG="' + (Join-Path $fixture "$mode.log") + '"'))
        if ($mode -eq 'portable') { $arguments += '/PORTABLE=1' }
        $process = Start-Process -FilePath $SetupPath -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Installer failed: $mode ($($process.ExitCode)); log: $(Join-Path $fixture "$mode.log")" }
        $locations += $location
        $marker = Get-Content -LiteralPath (Join-Path $location 'sc-distribution.json') -Raw | ConvertFrom-Json
        if ($marker.mode -ne $mode) { throw "Wrong distribution marker: $mode" }
        $uninstaller = Join-Path $location 'unins000.exe'
        if (-not (Test-Path -LiteralPath $uninstaller)) { throw "Missing uninstaller: $mode" }
        $roots = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall', $registryRoot)
        $entries = @(foreach ($root in $roots) {
            Get-ChildItem -LiteralPath $root | Get-ItemProperty | Where-Object { ([string]$_.InstallLocation).TrimEnd('\') -eq $location }
        })
        if ($entries.Count -ne 1 -or $entries[0].DisplayVersion -ne '2.8.4') { throw "Missing/duplicate Windows uninstall entry: $mode" }
        if ($mode -eq 'portable' -and $entries[0].DisplayName -notlike '*Portable*') { throw 'Portable entry is not distinguishable' }
        [IO.File]::WriteAllText((Join-Path $location 'data.json'), '{"keep":true}')
        Write-Output "PASS: $mode has an independent Windows uninstall entry"
    }
} finally {
    foreach ($location in $locations) {
        $uninstaller = Join-Path $location 'unins000.exe'
        if (Test-Path -LiteralPath $uninstaller) {
            $process = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -Wait -PassThru
            if ($process.ExitCode -ne 0) { throw "Test uninstaller failed: $location" }
            if (-not (Test-Path -LiteralPath (Join-Path $location 'data.json'))) { throw 'Uninstaller removed user data' }
            Write-Output "PASS: uninstall preserves data in $location"
        }
    }
    if (Test-Path -LiteralPath $shortcutBackup) {
        New-Item -ItemType Directory -Path $programs -Force | Out-Null
        foreach ($shortcut in Get-ChildItem -LiteralPath $shortcutBackup -File) { Copy-Item -LiteralPath $shortcut.FullName -Destination $programs -Force }
    }
}
