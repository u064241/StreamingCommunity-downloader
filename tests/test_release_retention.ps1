$ErrorActionPreference = 'Stop'
$retentionScript = Join-Path $PSScriptRoot '..\scripts\github-release-retention.ps1'
$retentionAst = [System.Management.Automation.Language.Parser]::ParseFile($retentionScript, [ref]$null, [ref]$null)
foreach ($name in @('Get-ReleasePackageNames', 'Get-ReleaseRetentionPlan')) {
    $definition = $retentionAst.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    Invoke-Expression $definition.Extent.Text
}
function New-TestRelease([string]$Tag, [bool]$Preview = $false) {
    [pscustomobject]@{
        tag_name = $Tag; draft = $false; prerelease = $Preview
        assets = @(Get-ReleasePackageNames $Tag | ForEach-Object {
            [pscustomobject]@{ name = $_; state = 'uploaded'; size = 100; digest = 'sha256:' + ('a' * 64) }
        })
    }
}
$releases = @(New-TestRelease 'v2.8.9'; New-TestRelease 'v2.8.10'; New-TestRelease 'v2.8.11')
$plan = @(Get-ReleaseRetentionPlan $releases 'v2.8.11')
if ($plan.Count -ne 4 -or @($plan | Where-Object Tag -ne 'v2.8.9').Count) { throw 'Ordine numerico o limite di due release errato' }
Write-Output 'PASS: preservate due versioni con ordinamento numerico'
$releases[0].assets += [pscustomobject]@{name='custom-document.pdf';size=10}
$plan = @(Get-ReleaseRetentionPlan $releases 'v2.8.11')
if ($plan.Count -ne 4) { throw 'Allegato estraneo incluso nel piano' }
Write-Output 'PASS: allegati estranei preservati'
$releases[0].prerelease = $true
if (@(Get-ReleaseRetentionPlan $releases 'v2.8.11').Count) { throw 'Prerelease inclusa nella pulizia' }
Write-Output 'PASS: prerelease esclusa'
$releases[0].prerelease = $false
foreach ($scenario in @('incomplete', 'digest', 'latest')) {
    $fixtures = @(New-TestRelease 'v1.0.0'; New-TestRelease 'v1.0.1'; New-TestRelease 'v1.0.2')
    $tag = 'v1.0.2'
    if ($scenario -eq 'incomplete') { $fixtures[1].assets = @($fixtures[1].assets | Select-Object -First 1) }
    if ($scenario -eq 'digest') { $fixtures[2].assets[0].digest = $null }
    if ($scenario -eq 'latest') { $tag = 'v1.0.1' }
    $failed = $false
    try { Get-ReleaseRetentionPlan $fixtures $tag | Out-Null } catch { $failed = $true }
    if (-not $failed) { throw "Scenario $scenario non bloccato" }
    Write-Output "PASS: $scenario blocca la pulizia"
}
if (@(Get-ReleaseRetentionPlan @((New-TestRelease 'v1.0.0')) 'v1.0.0').Count) { throw 'Release unica non preservata' }
Write-Output 'PASS: release unica preservata'
