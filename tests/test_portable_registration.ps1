$ErrorActionPreference = 'Stop'
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('sc-portable-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$registration = Join-Path $fixture 'Register-Portable.ps1'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\scripts\Register-Portable.ps1') -Destination $registration
$marker = Join-Path $fixture 'sc-distribution.json'
[IO.File]::WriteAllText($marker, '{"schema":1,"repository":"u064241/StreamingCommunity-downloader","mode":"portable","version":"2.8.4"}')
[IO.File]::WriteAllText((Join-Path $fixture 'StreamingCommunityDownloader.exe'), 'test fixture, not executable')
[IO.File]::WriteAllText((Join-Path $fixture 'data.json'), '{"keep":true}')
$hash = [Security.Cryptography.SHA256]::Create()
try { $folderHash = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::Unicode.GetBytes($fixture.ToLowerInvariant()))).Replace('-', '').ToLowerInvariant() }
finally { $hash.Dispose() }
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamingCommunityDownloader-Portable-$folderHash"
try {
    & $registration -WhatIf
    if (Test-Path -LiteralPath $key) { throw 'WhatIf modified the registry' }
    & $registration
    $entry = Get-ItemProperty -LiteralPath $key
    if ($entry.DisplayVersion -ne '2.8.4' -or $entry.InstallLocation -ne $fixture -or
        $entry.UninstallString -notmatch 'Register-Portable.ps1.*-Uninstall') { throw 'Incorrect Windows registration' }
    Write-Output 'PASS: per-folder registration includes a working uninstall command'
    & $registration -Uninstall -Quiet -WhatIf
    if (-not (Test-Path -LiteralPath $key) -or -not (Test-Path -LiteralPath $marker)) { throw 'Uninstall WhatIf deleted data' }
    & $registration -Uninstall -Quiet -Confirm:$false
    if (Test-Path -LiteralPath $key) { throw 'Registration was not removed' }
    if (Test-Path -LiteralPath (Join-Path $fixture 'StreamingCommunityDownloader.exe')) { throw 'Program was not removed' }
    if (-not (Test-Path -LiteralPath (Join-Path $fixture 'data.json'))) { throw 'User data was removed' }
    Write-Output 'PASS: uninstall removes only program files and preserves user data'
} finally {
    if (Test-Path -LiteralPath $key) { Remove-Item -LiteralPath $key -Force }
    # Only fixed files in this unique fixture are removed; no recursive deletion.
    foreach ($name in @('data.json','Register-Portable.ps1','sc-distribution.json','StreamingCommunityDownloader.exe')) {
        $file = Join-Path $fixture $name
        if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file -Force }
    }
    if (-not (Get-ChildItem -LiteralPath $fixture -Force)) { Remove-Item -LiteralPath $fixture }
}
