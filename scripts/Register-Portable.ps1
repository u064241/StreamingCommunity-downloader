# Optional per-user registration for an extracted portable ZIP.
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param([switch]$Uninstall, [switch]$Quiet, [string]$Version)

$ErrorActionPreference = 'Stop'
$portableRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\')
foreach ($part in @((Get-Item -LiteralPath $portableRoot)) + @(
    for ($parent = [IO.Directory]::GetParent($portableRoot); $null -ne $parent; $parent = $parent.Parent) {
        Get-Item -LiteralPath $parent.FullName
    }
)) {
    if ($part.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are not supported.' }
}
$markerPath = Join-Path $portableRoot 'sc-distribution.json'
$marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
if ($marker.schema -ne 1 -or $marker.repository -ne 'u064241/StreamingCommunity-downloader' -or $marker.mode -ne 'portable') {
    throw 'This script requires a u064241 portable package.'
}
$sha = [Security.Cryptography.SHA256]::Create()
try { $folderId = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::Unicode.GetBytes($portableRoot.ToLowerInvariant())))).Replace('-', '').ToLowerInvariant() }
finally { $sha.Dispose() }
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamingCommunityDownloader-Portable-$folderId"
$appExe = Join-Path $portableRoot 'StreamingCommunityDownloader.exe'
if (-not $Uninstall) {
    if (-not (Test-Path -LiteralPath $appExe -PathType Leaf)) { throw 'Application executable not found.' }
    if ($PSCmdlet.ShouldProcess($portableRoot, 'Register portable application in Windows Installed apps')) {
        New-Item -Path $uninstallKey -Force | Out-Null
        $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $command = '"' + $windowsPowerShell + '" -NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Uninstall'
        $values = @{
            DisplayName = 'StreamingCommunity Downloader (Portable)'
            DisplayVersion = $(if ($Version) { $Version } else { [string]$marker.version })
            Publisher = 'u064241'
            InstallLocation = $portableRoot
            DisplayIcon = $appExe
            UninstallString = $command
            QuietUninstallString = "$command -Quiet"
        }
        foreach ($name in $values.Keys) { New-ItemProperty -LiteralPath $uninstallKey -Name $name -Value $values[$name] -PropertyType String -Force | Out-Null }
        foreach ($name in @('NoModify', 'NoRepair')) { New-ItemProperty -LiteralPath $uninstallKey -Name $name -Value 1 -PropertyType DWord -Force | Out-Null }
        Write-Host 'Portable application registered in Windows Installed apps.'
    }
    return
}
if (-not $Quiet -and -not $WhatIfPreference -and -not $PSCmdlet.ShouldContinue('Remove portable program files? Settings, databases and downloads will be preserved.', 'Uninstall portable application')) { return }
$knownFiles = @('StreamingCommunityDownloader.exe', 'StreamingCommunityDownloader-Updater.exe',
                'Register-Portable.cmd', 'sc-distribution.json', 'Register-Portable.ps1')
foreach ($name in $knownFiles) {
    $path = Join-Path $portableRoot $name
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing unexpected file: $path" }
    }
}
foreach ($process in Get-Process -Name 'StreamingCommunityDownloader','StreamingCommunityDownloader-Updater' -ErrorAction SilentlyContinue) {
    if (-not $process.Path -or [IO.Path]::GetDirectoryName($process.Path) -eq $portableRoot) {
        throw 'Close the portable application and updater before uninstalling.'
    }
}
if ($PSCmdlet.ShouldProcess($portableRoot, 'Remove portable program files and Windows registration (preserve user data)')) {
    foreach ($name in $knownFiles) {
        $path = Join-Path $portableRoot $name
        if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
    }
    if (Test-Path -LiteralPath $uninstallKey) { Remove-Item -LiteralPath $uninstallKey -Force }
    Write-Host 'Portable program removed. Settings, databases and downloads were preserved.'
}
