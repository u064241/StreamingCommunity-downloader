# Legacy compatibility shim. VPN Gate is downloaded from the official source at runtime.
function Get-SoftEtherFileEntries {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$ProjectRoot)

    return ''
    <#
    $bundleRoot = Join-Path $ProjectRoot 'tools\softether'
    $manifest = Get-Content -LiteralPath (Join-Path $bundleRoot 'bundle.json') -Raw | ConvertFrom-Json
    if ($manifest.fileName -notmatch '^softether-vpnclient-v[0-9]+\.[0-9]+-[0-9]+-rtm-[0-9]{4}\.[0-9]{2}\.[0-9]{2}-windows-x86_x64-intel\.exe$') {
        throw 'Pacchetto SoftEther non approvato: nome asset Windows x64 non valido.'
    }
    $setup = Join-Path $bundleRoot (Join-Path 'payload' $manifest.fileName)
    if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) {
        throw "Setup SoftEther mancante: $setup. Vedere tools/softether/README.md."
    }
    if ((Get-Item -LiteralPath $setup).Length -ne $manifest.size -or
        (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash -ine $manifest.sha256) {
        throw 'Dimensione o SHA256 del setup SoftEther non corrispondono al pacchetto verificato.'
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $setup
    if ($signature.Status -ne 'Valid' -or
        $signature.SignerCertificate.Thumbprint -ine $manifest.signerThumbprint) {
        throw 'Firma Authenticode SoftEther non valida o firmatario diverso da quello verificato.'
    }

    $sources = @($setup, (Join-Path $bundleRoot 'bundle.json'))
    foreach ($notice in @('LICENSE.TXT', 'THIRD_PARTY.TXT', 'WARNING.TXT', 'authors.txt')) {
        $sources += Join-Path $bundleRoot "notices\$notice"
    }
    $entries = foreach ($source in $sources) {
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "File SoftEther mancante: $source" }
        # These paths are inserted into an Inno Setup quoted Source field.
        if ($source.Contains('"') -or $source.Contains("`r") -or $source.Contains("`n")) {
            throw 'Percorso SoftEther non valido per Inno Setup.'
        }
        $destination = if ($source -like '*\notices\*') { '{app}\tools\softether\notices' } else { '{app}\tools\softether' }
        'Source: "{0}"; DestDir: "{1}"; Flags: ignoreversion' -f $source, $destination
    }
    return $entries -join "`r`n" #>
}
