# SoftEther Client: componente opzionale

## Inclusione del setup ufficiale

`build.ps1 -IncludeSoftEtherInstaller` include il setup in
`{app}\tools\softether` insieme a `bundle.json` e ai testi originali in `notices`.
Prima della compilazione verifica dimensione, SHA256, firma Authenticode valida e
certificato del pacchetto esaminato. Il setup non viene eseguito dalla build o
dall'installer principale. L'opzione vale anche per la modalita portable del
wizard Inno Setup; lo ZIP portable separato conserva il contenuto attuale.

Il binario locale va in `tools\softether\payload` (escluso da Git). Il pacchetto
approvato e identificato da `bundle.json`; aggiornare questo file richiede una
nuova verifica del pacchetto, della firma e delle licenze. Non modificare i byte
del setup originale. Esempio di build, da eseguire quando si desidera compilare:

```powershell
.\build.ps1 -Release -CleanReleaseArtifacts -IncludeSoftEtherInstaller
```

Non e stata verificata un'installazione silenziosa del client completo. Nel
sorgente `src/Cedar/SW.c` della release v4.44-9807-rtm, `SwParseCommandLine`
non espone `/S`, `/SILENT` o `/VERYSILENT`. `/EASY` e `/WEB` creano altri
installer; `/SUINSTMODE` installa un driver specifico, non il client completo;
`/HIDESTARTCOMMAND` riguarda il comando di avvio finale. Non usare questi flag
come sostituti di un'installazione non interattiva. Il setup non e stato lanciato
neppure per richiederne l'help.

La successiva installazione guidata richiede l'intervento dell'utente e puo
aggiungere servizi, driver e adattatori di rete. SoftEther conserva la propria
disinstallazione indipendente: disinstallare il downloader non rimuove la VPN.
Firma valida e hash fissato identificano il file verificato, non garantiscono
l'assenza di vulnerabilita. Il SHA256 e stato calcolato localmente, non confrontato
con un checksum ufficiale indipendente.

SoftEther usa Apache-2.0; le dipendenze hanno avvisi propri. Sono conservati
`LICENSE.TXT`, `THIRD_PARTY.TXT`, `WARNING.TXT` e `authors.txt` dal checkout
ufficiale locale al commit indicato nel manifest. Vedere anche
[il progetto del controllo aggiornamenti](UPDATE-DESIGN.md).

## Launcher esistente

Questo strumento apre il Client Manager di una installazione SoftEther VPN Client
gia presente su Windows. Il launcher non costituisce un client
VPN portable completo: servizio e adattatore virtuale dipendono dall'installazione
gestita dall'utente.

Il launcher non installa, avvia o arresta
servizi e driver, non connette o disconnette VPN e non legge o modifica credenziali
o configurazioni. Non richiede privilegi amministrativi. Le operazioni effettuate
successivamente dall'utente nel Manager sono gestite da SoftEther.

## Utilizzo

Dalla radice del repository, con Windows PowerShell 5.1 o PowerShell 7:

```powershell
# Rileva il Manager senza aprirlo
.\tools\softether\Open-SoftEtherClient.ps1 -CheckOnly

# Apre il Manager rilevato
.\tools\softether\Open-SoftEtherClient.ps1

# Installazione in una cartella personalizzata (anche con spazi)
.\tools\softether\Open-SoftEtherClient.ps1 -InstallationPath 'D:\SoftEther VPN Client' -CheckOnly
.\tools\softether\Open-SoftEtherClient.ps1 -InstallationPath 'D:\SoftEther VPN Client'

# Help completo
Get-Help .\tools\softether\Open-SoftEtherClient.ps1 -Full
```

Il rilevamento controlla la sottocartella `SoftEther VPN Client` di Program Files
a 64 e 32 bit, utilizzando le variabili Windows `ProgramW6432`, `ProgramFiles` e
`ProgramFiles(x86)`. In ogni cartella preferisce `vpncmgr_x64.exe`, poi
`vpncmgr.exe`. Non esegue ricerche nel PATH o nella cartella corrente.

Con `-InstallationPath` viene controllata **solo** la cartella specificata: deve
contenere uno dei due eseguibili. Il rilevamento per nome/percorso non certifica
l'autenticita dei file; utilizzare una propria installazione attendibile ottenuta
dal [progetto ufficiale SoftEther](https://github.com/SoftEtherVPN/SoftEtherVPN_Stable).
Il launcher non scarica nulla e non cambia le policy di esecuzione PowerShell.

## Risultati

| Codice di uscita | Significato |
| --- | --- |
| `0` | Manager trovato con `-CheckOnly`, oppure richiesta di avvio riuscita |
| `1` | Manager non trovato nelle cartelle controllate |
| `2` | Sistema operativo diverso da Windows |
| `3` | Errore nel rilevamento o nella richiesta di avvio |

Il percorso e l'esito sono stampati nella console. Il codice `0` dopo l'avvio
non conferma il funzionamento della VPN: la connessione resta sotto il controllo
dell'utente. Il launcher non verifica lo stato del servizio o l'IP pubblico.
