[CmdletBinding()]
param([Parameter(Mandatory)][string]$SetupPath)
$ErrorActionPreference = 'Stop'
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('sc-setup-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$programs = Join-Path ([Environment]::GetFolderPath('Programs')) 'StreamingCommunity Downloader'
$shortcutBackup = Join-Path $fixture 'shortcuts'
if (Test-Path -LiteralPath $programs) { Copy-Item -LiteralPath $programs -Destination $shortcutBackup -Recurse }
$registryRoot = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
$existing = @(Get-ChildItem -LiteralPath $registryRoot | Get-ItemProperty | Where-Object { $_.DisplayName -eq 'StreamingCommunity Downloader' })
if ($existing.Count) { throw 'An installed edition already exists; refusing to overwrite its registration during testing.' }
$locations = @()
try {
    foreach ($mode in @('portable', 'installed')) {
        $location = Join-Path $fixture $mode
        $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS',
            ('/DIR="' + $location + '"'), ('/GROUP="SC release test ' + (Split-Path $fixture -Leaf) + '"'))
        if ($mode -eq 'portable') { $arguments += '/PORTABLE=1' }
        $process = Start-Process -FilePath $SetupPath -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Installer failed: $mode ($($process.ExitCode))" }
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
