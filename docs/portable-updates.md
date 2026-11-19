# Aggiornamenti della distribuzione Windows

**Stato: chiuso in questa versione.** Il pannello non offre più l'aggiornamento
in-place della versione portable. Nessuno ZIP portable è pubblicato come asset
di release, quindi non c'è un pacchetto da scaricare e verificare.

## Come aggiornare

Scarica il setup dell'ultima release dalla pagina Releases del repository e
installalo come la prima volta, seguendo il wizard. Il pannello segnala la
nuova versione nella finestra delle novità, ma non la installa da solo.

## Cosa resta nel codice

- La build scrive `sc-distribution.json` nella cartella portable e nel
  pacchetto. Il marker dichiara modalità (`portable` o `installed`), schema e
  repository.
- Gli endpoint `/api/updates/portable`, `/api/updates/portable/prepare` e
  `/api/updates/portable/apply` restano, ma `prepare` e `apply` rispondono 409
  finché `PORTABLE_ZIP_PUBLISHED` non è `True` (`app/routers/updates.py`).
  Lo stato riporta `available: false`.
- Il meccanismo completo (manifest validato, SHA256, firma Authenticode,
  helper, backup `StreamingCommunityDownloader.previous-<id>.exe`, rollback
  sugli errori di verifica) è ancora nel codice, ma non è raggiungibile.

## Condizioni per riaprirlo

Il canale può tornare attivo solo se tutte queste condizioni sono vere:

1. Una release pubblica include uno ZIP portable firmato, con manifest e SHA256.
2. `PORTABLE_ZIP_PUBLISHED` passa a `True`, con un commit dedicato.
3. Questo documento viene riscritto, con la procedura di prova.

## Limiti da ricordare se si riapre

- Il flusso sostituisce solo il programma principale. Helper e altre componenti
  richiedono distribuzione manuale se cambiano protocollo o formato.
- Non c'è riavvio automatico né rollback delle migrazioni dati. Pubblicare solo
  release compatibili con i dati esistenti.
- SHA256 garantisce la corrispondenza con il manifest pubblicato su GitHub, ma
  non sostituisce una firma del produttore.
