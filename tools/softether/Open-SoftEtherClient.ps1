# Apre il Client Manager gia installato; non configura la VPN.
# Esempi: .\Open-SoftEtherClient.ps1 -CheckOnly | -InstallationPath 'D:\SoftEther VPN Client'
<#
.SYNOPSIS
Rileva e apre il SoftEther VPN Client Manager installato.
.DESCRIPTION
Cerca vpncmgr_x64.exe e vpncmgr.exe nelle cartelle comuni di Program Files.
Non installa componenti, non gestisce servizi o driver e non legge configurazioni
o credenziali VPN. Non richiede elevazione.
.PARAMETER InstallationPath
Cartella di installazione da usare al posto del rilevamento automatico.
.PARAMETER CheckOnly
Mostra il percorso rilevato senza avviare il programma.
.EXAMPLE
.\Open-SoftEtherClient.ps1 -CheckOnly
.EXAMPLE
.\Open-SoftEtherClient.ps1 -InstallationPath 'D:\SoftEther VPN Client'
.NOTES
Codici di uscita: 0 riuscito; 1 manager assente; 2 piattaforma non supportata;
3 errore durante il rilevamento o l'avvio.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$InstallationPath,

    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Write-Output 'Questo launcher supporta soltanto Windows.'
    exit 2
}

try {
    if ($PSBoundParameters.ContainsKey('InstallationPath')) {
        $directories = @($InstallationPath)
    }
    else {
        $roots = @(
            [Environment]::GetEnvironmentVariable('ProgramW6432'),
            [Environment]::GetEnvironmentVariable('ProgramFiles'),
            [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
        $directories = @($roots | ForEach-Object {
            Join-Path -Path $_ -ChildPath 'SoftEther VPN Client'
        })
    }

    $managerPath = $null
    foreach ($directory in $directories) {
        foreach ($fileName in @('vpncmgr_x64.exe', 'vpncmgr.exe')) {
            $candidate = Join-Path -Path $directory -ChildPath $fileName
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $managerPath = (Resolve-Path -LiteralPath $candidate).ProviderPath
                break
            }
        }
        if ($managerPath) { break }
    }

    if (-not $managerPath) {
        Write-Output 'SoftEther VPN Client Manager non trovato.'
        Write-Output 'Cartelle controllate:'
        $directories | ForEach-Object { Write-Output "  $_" }
        Write-Output 'Se gia installato altrove, specifica -InstallationPath con la cartella corretta.'
        Write-Output 'Non e stato installato o avviato alcun componente.'
        exit 1
    }

    Write-Output "Client Manager rilevato: $managerPath"
    if ($CheckOnly) {
        Write-Output 'Solo verifica: nessun programma avviato.'
        exit 0
    }

    Start-Process -FilePath $managerPath -WorkingDirectory (Split-Path -Parent $managerPath) -ErrorAction Stop
    Write-Output 'Avvio del Client Manager richiesto. Gestisci la connessione dalla sua interfaccia.'
    exit 0
}
catch {
    Write-Output ("Impossibile rilevare o aprire il Client Manager: {0}" -f $_.Exception.Message)
    exit 3
}
