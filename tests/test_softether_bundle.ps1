# Verification only: never execute the VPN setup or generated Inno installer.
[CmdletBinding()]
param([switch]$CompileRecipe)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
foreach ($file in @('build.ps1', 'tools\softether\Bundle-SoftEther.ps1')) {
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $ProjectRoot $file), [ref]$null, [ref]$errors)
    if ($errors.Count) { throw "PowerShell parse failure: $file $errors" }
    if ($file -eq 'build.ps1') { $buildAst = $ast }
}
. (Join-Path $ProjectRoot 'tools\softether\Bundle-SoftEther.ps1')
$SoftEtherFileEntries = Get-SoftEtherFileEntries -ProjectRoot $ProjectRoot
if (@($SoftEtherFileEntries -split "`r`n").Count -ne 6 -or
    $SoftEtherFileEntries -match '(?im)^\s*(Filename:|\[Run\])') {
    throw 'Expected six copy-only file entries'
}
Write-Output 'PASS: original binary hash, size, signature and bundled notices'

& {
    function Get-FileHash { param($LiteralPath, $Algorithm) [pscustomobject]@{ Hash = '0' * 64 } }
    $rejected = $false
    try { Get-SoftEtherFileEntries -ProjectRoot $ProjectRoot | Out-Null }
    catch { if ($_.Exception.Message -notlike '*SHA256*') { throw }; $rejected = $true }
    if (-not $rejected) { throw 'Tampered hash was accepted' }
}
& {
    function Get-AuthenticodeSignature {
        param($LiteralPath)
        [pscustomobject]@{ Status = 'NotSigned'; SignerCertificate = $null }
    }
    $rejected = $false
    try { Get-SoftEtherFileEntries -ProjectRoot $ProjectRoot | Out-Null }
    catch { if ($_.Exception.Message -notlike '*Authenticode*') { throw }; $rejected = $true }
    if (-not $rejected) { throw 'Invalid signature was accepted' }
}
Write-Output 'PASS: tampered hash and invalid signature rejected'

if ($CompileRecipe) {
    # Compile the production recipe with empty app fixtures; this is not a release.
    $recipe = $buildAst.Find({ param($node)
        $node -is [System.Management.Automation.Language.ExpandableStringExpressionAst] -and
        $node.Value.StartsWith('#define MyAppName')
    }, $true)
    if (-not $recipe) { throw 'Inno recipe not found' }
    $compiler = Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'
    if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'Inno Setup 7 not found' }
    $DistDir = Join-Path $ProjectRoot ('tools\softether\.verification\' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $DistDir | Out-Null
    $AppName = 'StreamingCommunityDownloader'
    $Version = '0.0.0'
    $exePath = Join-Path $DistDir 'empty-app.exe'
    $helperExePath = Join-Path $DistDir 'empty-helper.exe'
    $portableMarker = Join-Path $DistDir 'empty-portable.json'
    $installedMarker = Join-Path $DistDir 'empty-installed.json'
    foreach ($fixture in @($exePath, $helperExePath, $portableMarker, $installedMarker)) {
        New-Item -ItemType File -Path $fixture | Out-Null
    }
    # Only evaluate the trusted local recipe expression, never the whole build.
    $recipeText = Invoke-Expression $recipe.Extent.Text
    $recipeText = $recipeText.Replace('StreamingCommunityDownloader-Setup-v0.0.0', 'COMPILE-TEST-DO-NOT-INSTALL')
    $recipePath = Join-Path $DistDir 'compile-only.iss'
    [IO.File]::WriteAllText($recipePath, $recipeText, [Text.UTF8Encoding]::new($false))
    & $compiler /Qp $recipePath
    if ($LASTEXITCODE -ne 0) { throw 'Inno recipe compilation failed' }
    Write-Output "PASS: recipe compiled only; test artifacts at $DistDir"
}
