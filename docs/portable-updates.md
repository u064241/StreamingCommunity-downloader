# Aggiornamenti della distribuzione Windows u064241

La build inserisce `sc-distribution.json` nello ZIP portable e nella cartella
scelta dal wizard. Il wizard scrive `mode: portable` oppure `mode: installed`,
con schema 1 e repository `u064241/StreamingCommunity-downloader`.
La posizione sotto Program Files non determina l'idoneità all'update:
senza marker valido la sostituzione è disabilitata. Le vecchie distribuzioni
devono essere aggiornate manualmente una prima volta con un nuovo pacchetto.

Il pulsante **Aggiorna portable**, nella finestra delle novità, richiede
`MANAGE_SETTINGS`. Gli endpoint `/api/updates/portable`,
`/api/updates/portable/prepare` e `/api/updates/portable/apply` sono accessibili
solo tramite loopback con Host locale. I POST richiedono Origin corrispondente
e token locale, anche in modalità aperta; resta valido il controllo CSRF della
sessione quando l'autenticazione è attiva. Le richieste inoltrate da proxy
sono rifiutate.

La preparazione seleziona Latest dal fork, esclude prerelease/draft e scarica
manifest e pacchetto dalla stessa release. Il manifest deve dichiarare schema,
variante, canale, repository, versione, dimensione e SHA256 compatibili con gli
asset GitHub. Tutti i redirect vengono verificati prima della richiesta.
Lo ZIP deve contenere solo EXE principale, helper e marker portable alla radice.
Si estrae soltanto l'EXE principale in staging temporaneo. Nessun percorso
fornito dal browser viene accettato.

Dopo la seconda conferma, il server ricontrolla modalità e lavori pendenti,
avvia l'helper e attende la conferma che questo abbia verificato il PID e
preparato la sostituzione. La preparazione dura 30 minuti, è legata all'utente
e il token è utilizzabile una volta sola. Chiudere poi tutte le schede entro
5 minuti, attendere l'uscita del processo e riaprire manualmente il programma.
L'helper non termina processi. Se l'app rimane aperta oltre il timeout,
l'EXE resta invariato; riavviare l'app prima di riprovare.

Il backup `StreamingCommunityDownloader.previous-<id>.exe` resta nella cartella
portable. Il rollback automatico copre errori di verifica dopo la sostituzione;
non verifica l'avvio della nuova applicazione. Per un recupero manuale,
chiudere l'app, conservare l'EXE problematico e ripristinare il backup al nome
originale. Un lock residuo dopo un crash richiede controllo manuale che nessun
helper sia ancora in esecuzione.

Limiti: il flusso sostituisce solo il programma principale. L'helper e altre
componenti richiedono distribuzione manuale se cambiano protocollo o formato.
Installazioni sorgente e installabili usano il collegamento alla release;
Inno Setup rimane guidato. Non vengono modificati database, configurazioni
o download. Non c'è riavvio automatico né rollback delle migrazioni dati:
pubblicare su questo canale solo release compatibili con i dati esistenti.
SHA256 garantisce corrispondenza al manifest pubblicato su GitHub, ma non
sostituisce una firma del produttore.
