# Keep two complete stable Windows releases on the fork; retain tags and notes.
[CmdletBinding(SupportsShouldProcess)]
param([string]$PublishedTag)

$ErrorActionPreference = 'Stop'
$Repository = 'u064241/StreamingCommunity-downloader'

function Get-ReleasePackageNames([string]$Tag) {
    if ($Tag -notmatch '^v(\d+\.\d+\.\d+)$') { throw "Tag non stabile: $Tag" }
    @("StreamingCommunityDownloader-Setup-v$($Matches[1]).exe",
      "StreamingCommunityDownloader-$($Matches[1])-Portable.zip",
      'update-manifest.json', 'SHA256SUMS.txt')
}

function Get-ReleaseRetentionPlan($Releases, [string]$LatestTag) {
    $stable = @($Releases | Where-Object {
        -not $_.draft -and -not $_.prerelease -and $_.tag_name -match '^v\d+\.\d+\.\d+$' -and
        @($_.assets | Where-Object { $_.name -match '^StreamingCommunityDownloader-Setup-v\d+\.\d+\.\d+\.exe$' }).Count -gt 0
    } | Sort-Object { [version]$_.tag_name.Substring(1) } -Descending)
    if ($stable.Count -lt 2) { return }
    if ($stable[0].tag_name -ne $LatestTag) {
        throw 'Latest non coincide con la versione stabile piu recente: pulizia annullata.'
    }
    foreach ($release in $stable | Select-Object -First 2) {
        foreach ($name in Get-ReleasePackageNames $release.tag_name) {
            $assets = @($release.assets | Where-Object { $_.name -eq $name })
            if ($assets.Count -ne 1 -or $assets[0].state -ne 'uploaded' -or $assets[0].size -le 0 -or
                $assets[0].digest -notmatch '^sha256:[a-fA-F0-9]{64}$') {
                throw "Release da conservare incompleta: $($release.tag_name), $name. Pulizia annullata."
            }
        }
    }
    foreach ($release in $stable | Select-Object -Skip 2) {
        $names = @(Get-ReleasePackageNames $release.tag_name)
        foreach ($asset in $release.assets | Where-Object { $_.name -in $names }) {
            [pscustomobject]@{Tag = $release.tag_name; Name = $asset.name; Bytes = $asset.size}
        }
    }
}

$gh = Get-Command gh.exe -ErrorAction Stop
$previousToken = $env:GH_TOKEN
if ($env:SC_GITHUB_TOKEN) { $env:GH_TOKEN = $env:SC_GITHUB_TOKEN }
try {
    $latestJson = & $gh.Source api "repos/$Repository/releases/latest"
    if ($LASTEXITCODE -ne 0) { throw 'Impossibile verificare Latest: nessun allegato rimosso.' }
    $latest = ($latestJson -join "`n") | ConvertFrom-Json
    if ($PublishedTag -and $latest.tag_name -ne $PublishedTag) {
        throw 'Latest e cambiata: pulizia annullata.'
    }
    $releaseJson = & $gh.Source api "repos/$Repository/releases?per_page=100" --paginate --slurp
    if ($LASTEXITCODE -ne 0) { throw 'Impossibile elencare le release: nessun allegato rimosso.' }
    $pages = ($releaseJson -join "`n") | ConvertFrom-Json
    $releases = @($pages | ForEach-Object { foreach ($release in $_) { $release } })
    $plan = @(Get-ReleaseRetentionPlan $releases $latest.tag_name)
    # Check the retained binaries against their release manifest before deleting anything.
    $retained = @($releases | Where-Object { $_.tag_name -eq $latest.tag_name })
    $previous = @($releases | Where-Object {
        -not $_.draft -and -not $_.prerelease -and $_.tag_name -match '^v\d+\.\d+\.\d+$' -and
        [version]$_.tag_name.Substring(1) -lt [version]$latest.tag_name.Substring(1) -and
        @($_.assets | Where-Object { $_.name -match '^StreamingCommunityDownloader-Setup-v\d+\.\d+\.\d+\.exe$' }).Count -gt 0
    } | Sort-Object { [version]$_.tag_name.Substring(1) } -Descending | Select-Object -First 1)
    if ($plan.Count) {
        foreach ($release in @($retained) + @($previous)) {
            $manifestJson = & $gh.Source release download $release.tag_name --repo $Repository --pattern 'update-manifest.json' --output '-'
            if ($LASTEXITCODE -ne 0) { throw 'Manifest non leggibile: pulizia annullata.' }
            $manifest = (($manifestJson -join "`n").TrimStart([char]0xfeff)) | ConvertFrom-Json
            if ($manifest.variant -ne 'u064241' -or $manifest.repository -ne $Repository -or
                "v$($manifest.version)" -ne $release.tag_name) { throw 'Manifest non coerente: pulizia annullata.' }
            foreach ($name in (Get-ReleasePackageNames $release.tag_name | Select-Object -First 2)) {
                $asset = @($release.assets | Where-Object { $_.name -eq $name })
                $record = @($manifest.assets | Where-Object { $_.name -eq $name })
                if ($record.Count -ne 1 -or $asset.Count -ne 1 -or $record[0].size -ne $asset[0].size -or
                    "sha256:$($record[0].sha256)" -ne $asset[0].digest) { throw 'Checksum del manifest non coerente: pulizia annullata.' }
            }
        }
    }
    foreach ($asset in $plan) {
        if ($PSCmdlet.ShouldProcess("$Repository/$($asset.Tag)/$($asset.Name)", 'Elimina allegato release precedente')) {
            & $gh.Source release delete-asset $asset.Tag $asset.Name --repo $Repository --yes
            if ($LASTEXITCODE -ne 0) { throw "Eliminazione fallita: $($asset.Tag)/$($asset.Name)." }
            Write-Host "Rimosso da GitHub: $($asset.Tag)/$($asset.Name)"
        }
    }
    Write-Host "Retention GitHub: conservati i pacchetti delle ultime due release stabili; tag e note mantenuti."
} finally {
    if ($null -eq $previousToken) { Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue }
    else { $env:GH_TOKEN = $previousToken }
}
