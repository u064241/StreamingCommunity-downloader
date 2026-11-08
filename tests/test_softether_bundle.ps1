# Verification only: VPN Gate is downloaded at runtime; no local payload is expected.
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
if ((Get-SoftEtherFileEntries -ProjectRoot $ProjectRoot) -ne '') { throw 'Legacy payload shim is not empty.' }
if ((Get-Content (Join-Path $ProjectRoot 'build.ps1') -Raw) -match 'IncludeSoftEtherInstaller|localSoftEtherPayload') { throw 'build.ps1 still references local payload.' }
Write-Output 'PASS: runtime VPN Gate download and no local payload'

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
