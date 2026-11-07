# Controllo aggiornamenti SoftEther: progetto

Stato: packaging opzionale implementato; controllo remoto e pulsanti di
installazione/aggiornamento del portale non ancora attivati. Nessuna installazione
e nessun download sono necessari per applicare queste modifiche al sorgente.

## Evidenze

- File analizzato: client ufficiale 4.44.0.9807, 56.249.368 byte.
- SHA256 e certificato verificati su Windows sono registrati in `bundle.json`.
- `7z l` mostra un contenitore PE x86 con risorse `VPNCLIENT_X64.EXE`,
  `VPNCMD_X64.EXE`, `VPNCMGR_X64.EXE` e `VPNSETUP_X64.EXE`. Non va scartato
  perche l'intestazione PE del bootstrapper e a 32 bit.
- Il nome corrisponde all'asset della release ufficiale:
  https://github.com/SoftEtherVPN/SoftEtherVPN_Stable/releases/expanded_assets/v4.44-9807-rtm
- Verifica statica argomenti: checkout locale del tag, commit
  `ed17437af9719ac66acab30faa29e375d613c35f`, `src/Cedar/SW.c`,
  funzioni `SwParseCommandLine`, `SwUiMain`, `SWExecMain`. Nessun flag silent del
  client completo trovato. Questo non equivale a un test runtime del setup.

## Contratto proposto

In `app/routers/softether.py` aggiungere un POST `/check-updates`, mantenendo
permesso MANAGE_SETTINGS, vincoli loopback, Origin e token gia esistenti.
Il controllo deve essere esplicito, con cache condivisa di 24 ore e un'unica
richiesta in corso. Nessuna richiesta a ogni rendering del pannello, nessun
download o lancio di processi nel controllo aggiornamenti.

Endpoint fisso:
`https://api.github.com/repos/SoftEtherVPN/SoftEtherVPN_Stable/releases?per_page=30`.
Usare timeout di connessione/lettura, massimo 2 MiB di JSON, TLS verificato,
redirect disabilitati, ETag/If-None-Match; non inoltrare cookie o token del
portale. Per 403/429 rispettare Retry-After/X-RateLimit-Reset e non riprovare
automaticamente. Se le prime 30 release non contengono un candidato valido,
mostrare esito non determinabile, senza dichiarare che il client e aggiornato.

Scartare draft, prerelease, beta e RC anche se etichettati erroneamente come
stabili. Selezionare per versione numerica (major, minor, build), non per ordine
di pubblicazione ne per confronto alfabetico. Non basarsi soltanto su /latest.
Il selettore offline `app/core/softether_releases.py` implementa gia questa
validazione sui metadati ricevuti e rifiuta candidati ambigui.

Per Windows x64 il pacchetto attuale e universale x86_x64, con nome completo
`softether-vpnclient-v<major>.<minor>-<build>-rtm-<YYYY.MM.DD>-windows-x86_x64-intel.exe`.
Verificare corrispondenza con il tag e URL HTTPS nel repository ufficiale;
escludere vpnserver, vpnbridge, archivi sorgente, Linux e ARM.
Nuovi schemi di nome richiedono aggiornamento e verifica espliciti.

Distinguere versione *installata* e versione del setup *incluso*. Leggere la
FileVersion del manager installato tramite API Windows, senza eseguirlo;
se la lettura fallisce, mostrare versione sconosciuta. Non usare il manifest
del setup per affermare che la VPN e gia installata o aggiornata.

Risposta prevista: installed_version, bundled_version, latest_version,
update_available (null se non determinabile), checked_at, cached,
release_url, stato/errore traducibile. Il testo release e input non attendibile:
renderizzare testo semplice, con limite di lunghezza e senza HTML remoto.

## Interfaccia e installazione successive

In `app/static/api.js`, `app/static/page-settings.js`,
`app/templates/pages/_settings.html`, `app/static/i18n.js`: pulsante
"Controlla aggiornamenti VPN", esito inline visibile e tradotto IT/EN, versione
installata e inclusa separate, link alla release ufficiale con protezione noopener.
Non associare blocchi Cloudflare a installazioni o connessioni VPN automatiche.

Una futura azione "Installa SoftEther" deve accettare solo il pacchetto incluso,
rivalidare hash/firma al click e aprire il wizard con conferma e UAC Windows.
Nessun percorso, URL o argomento di processo deve provenire dal browser.
Il pannello rimane senza privilegi elevati. Non annunciare l'installazione
completata al solo avvio del processo: ricontrollare la versione installata.
Download di nuove release e aggiornamento automatico del manifest sono esclusi
da questa fase; richiedono verifica firma, integrita e prove separate.

## Rischi e prove rimanenti

- Il pacchetto aggiunge circa 54 MiB prima della compressione Inno Setup.
- Installazione/aggiornamento VPN puo interrompere connessioni e download.
- Le firme possono richiedere accesso a revoche/catene Windows. Il packaging
  fallisce se Windows non considera valida la firma: nessun bypass.
- Conservare licenza Apache-2.0 e avvisi delle dipendenze del tag distribuito.
  Riferimenti: LICENSE, THIRD_PARTY.TXT, WARNING.TXT nel repository ufficiale.
- Test futuri su VM Windows x64: UAC, annullamento, client gia installato,
  versione piu nuova, riavvio richiesto, disinstallazioni indipendenti.
- In questa attivita non eseguire `tests/test_windows_installer.ps1`, perche
  installa e disinstalla realmente. Verificare solo sintassi e compilazione.
