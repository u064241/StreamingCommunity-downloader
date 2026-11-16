# LA7: stato UI e contratto minimo per il catalogo

## Disponibile e collegato

Il provider dichiara `search=False, download=True`. L'unica operazione LA7
verificata dal frontend è `POST /api/download/la7`:

```json
{"source":"la7","url":"https://www.la7play.it/programma/puntate/puntata-123456","title":"Titolo inserito","scope":"single"}
```

Risposta attesa (202): `{"job_id":"...","status":"queued","scope":"single"}`.
Il messaggio di successo significa accodamento, non download completato.
Il server controlla URL, protezioni e formato all'avvio del job.

Il selettore LA7 mostra l'inserimento del link e del titolo. L'anteprima usa
la stessa card del catalogo, con segnaposto e tipo Puntata. Il dettaglio locale
mostra i valori inseriti, senza attribuirli a metadati ufficiali, e invia il
download singolo. Nessun fetch LA7 per ricerca, home, metadati o episodi.
Il dettaglio resta nella pagina di ricerca; non crea una rotta titolo persistente.
Tutte le fonti mantiene l'attuale aggregazione del server: il frontend non
aggiunge risultati LA7 inventati né modifica gli altri provider.

## Contratto richiesto al backend (proposta, NON implementata)

Riutilizzare i contratti provider esistenti; concordare l'esposizione HTTP prima
di abilitarla nel frontend. I nomi delle operazioni seguenti non sono nuovi endpoint.

| Operazione | Input minimo | Output minimo |
| --- | --- | --- |
| Capacità | fonte `la7` | ricerca, home, dettagli, episodi, download e scope supportati |
| Ricerca | query, pagina | lista di risultati con identità stabile, fonte, titolo, tipo, URL ufficiale; indicazione pagina successiva |
| Catalogo | pagina o sezione | sezioni ordinate con titolo e risultati; paginazione esplicita |
| Dettaglio | ID stabile della fonte | titolo, tipo, descrizione, URL ufficiale, immagini opzionali, possibilità di download e scope supportati |
| Stagioni/episodi | ID programma, ID stagione reale | stagioni deduplicate con etichetta e numero opzionale; episodi con ID, titolo, URL, numero e immagine opzionali |

Un risultato deve essere traducibile nel formato card già usato dal portale:
`source`, `id`, `slug`, `name`, `type`, `page_url`, `poster`, `backdrop`, `year`.
Immagini e anno possono mancare; usare il segnaposto esistente. Occorre distinguere
programmi, puntate ed extra e non trasformare indici di elenco in numeri di stagione.
Gli ID devono essere univoci nella fonte; tutte le rotte e azioni conservano `source=la7`.

Il dettaglio deve esporre URL della singola puntata scaricabile. Il contratto di
download corrente accetta solo `scope=single`: serie e stagioni intere richiedono
un'estensione backend prima di mostrare relativi pulsanti. Errori di catalogo,
fonte non disponibile e contenuto non scaricabile devono restare distinguibili.
Non nascondere contenuti con stato di protezione sconosciuto come se fosse certo.

Una volta implementato il contratto: abilitare ricerca e scaffali LA7 tramite
le API condivise, portare i risultati nelle card comuni e il dettaglio nella
pagina titolo. Caricare gli episodi alla scelta della stagione, conservando
selezione e posizione al ritorno. Nessun pre-caricamento globale di metadati.
