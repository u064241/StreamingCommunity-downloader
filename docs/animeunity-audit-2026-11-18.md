# AnimeUnity: analisi e correzione del catalogo

Data delle verifiche: 18 novembre 2026. Versione applicazione: **2.8.1**.

Base: `b64c197` del progetto `C:\SOURCECODE\PYTHON\StreamingCommunity-downloader`.
Branch isolato: `fix/animeunity-catalogue`.
Workspace: `C:\Users\Administrator\Documents\Codex\2026-11-11\anali\work\animeunity-fix`.
Nessun push, pubblicazione, compilazione o modifica al checkout principale.

## Causa verificata

Non bastava sostituire un dominio. AnimeUnity ha migrato il frontend e le API:

| Componente | Adattatore precedente | Sorgente osservata |
| --- | --- | --- |
| Dominio | `www.animeunity.co` | Redirect a `animeunity.cx` |
| Homepage | Attributi `items-json` e `animes` di componenti Vue | Payload Inertia `data-page`, `props.latestEpisodes.data`, `props.carouselAnimes` |
| Ricerca | POST `/archivio/get-animes`, `title`, offset | GET `/archive`, `query`, `page`, `type`, `language` |
| Locandine | `imageurl`, `imageurl_cover` | `images.poster`, `images.cover`, `images.banner` |
| Puntate recenti | `row.anime` | `row.episode.anime` |
| Audio | Record distinti per il doppiaggio | `variants` dentro lo stesso titolo |
| Episodi | `/info_api/...` | Pagina `/anime/{id-slug}` e GET `/api/anime/{id}/episodes?start=...` |
| Player | GET `/embed-url/{id}` | `embedUrl` nella pagina `/anime/{id-slug}/{video-id}` |

Il vecchio parser non trovava più le liste e restituiva `[]`. La risposta veniva
memorizzata nella cache server per circa 30 minuti e in quella frontend per
15 minuti. Il frontend cancellava il contenuto anche quando una richiesta
falliva: nessun messaggio spiegava l'assenza delle card.

## Rapporto con StreamingCommunity

Il sito corrente verificato è [streamingcommunityz.dad](https://streamingcommunityz.dad/).
Il [canale pubblico Telegram](https://t.me/s/Streaming_community_sito) indica il
passaggio da `.nexus` a `.dad` nell'annuncio del 16 novembre 2026.
La homepage `.dad` pubblica un link a [AnimeUnity](https://animeunity.cx/).

Sono provider distinti: il catalogo AnimeUnity non usa le API o le immagini di
StreamingCommunity e continua a funzionare senza un dominio StreamingCommunity
configurato. Il link della homepage StreamingCommunity è ora un recupero
limitato dell'indirizzo AnimeUnity se quello configurato non risponde o è
ritirato (404/410). Il candidato deve essere un link HTTPS esplicito a
`animeunity.<tld>`, risolvere a indirizzi pubblici e servire dati riconoscibili.
I redirect sono verificati prima di seguirli. Le impostazioni dell'utente non
vengono riscritte automaticamente. Un 403 non provoca tentativi su altri domini.

## Modifiche

- Nuovo default `animeunity.cx`, mantenendo override salvati e variabile ambiente.
- Lettura Inertia per homepage, archivio ed episodi; compatibilità con il vecchio formato.
- Immagini, anno, studio, lingua e conteggio della variante corretti.
- Ricerca e filtri inviati al nuovo archivio, con pagine da 30 risultati.
- Enumerazione di tutti i blocchi episodi; uso dell'ID video riproducibile,
  distinto dall'ID dell'entità episodio.
- Lingua conservata nell'URL della card, nella lista episodi e nel batch.
  I video nascosti dalla sorgente vengono esclusi senza mescolare lingue.
- Player risolto alla partenza usando la pagina del video, senza conservare
  token nelle fixture o nella cache delle card.
- Stato CSRF, formato e redirect azzerati al cambio host; rilevamento iniziale
  condiviso tra chiamate contemporanee.
- Homepage non riconosciute segnalate come errore, senza memorizzare un falso
  catalogo vuoto. Il frontend mostra un messaggio tradotto con pulsante Riprova.
- Una risposta tardiva non cancella un'altra sorgente già aperta. Il salvataggio
  degli host elimina anche la cache frontend di AnimeUnity.

### File applicazione modificati

- `app/config.py`
- `app/core/animeunity.py`
- `app/core/home.py`
- `app/requests/resolver.py`
- `app/routers/anime.py`
- `app/routers/downloads.py`
- `app/static/page-search.js`
- `app/static/page-settings.js`
- `app/static/page-title.js`
- `app/static/router.js`

### Test e fixture

- Nuovi: `tests/test_animeunity_inertia.py` (24 casi) e
  `tests/fixtures/animeunity_2026_11_18.json` (campi pubblici selezionati da dati reali).
- Aggiornati: `tests/test_home.py`, `tests/test_hosts.py`, `tests/test_search_sources.py`,
  `tests/test_anime_home_frontend.cjs`, `tests/test_card_navigation_frontend.cjs`.

## Verifiche reali

Provider e API del portale interrogati attraverso un TestClient locale,
con database e configurazione temporanei e senza avviare scheduler o download.

| Verifica | Risultato |
| --- | --- |
| `/api/home?source=animeunity` | 200; 17 card negli ultimi episodi, 5 in evidenza |
| Seconda richiesta homepage | `X-Cache: HIT` |
| Download HTTP di una locandina reale | 200, `image/jpeg`, 90.065 byte |
| Ricerca One Piece, film italiani | 200; 15 risultati |
| Seconda pagina ricerca One Piece | 200; 2 risultati |
| Film One Piece Movie 01, elenco video italiano | 200; 1 episodio |
| Black Clover | 200; 170 episodi originali, da 1 a 170, su due blocchi |
| Super Cube, lingua originale | 200; 11 video disponibili su 12 entità episodio |
| Super Cube, italiano | 200; 4 video disponibili |
| Player Black Clover episodio 1 | Script recuperato, 847 caratteri; credenziali non riportate |

Per Super Cube è stato controllato anche il dato grezzo: la sorgente marca
`is_hidden: true` il video cinese dell'episodio 2. La sua assenza è quindi una
restrizione della sorgente, non una perdita del parser corretto.

## Test automatici

**409 test backend superati** su provider, homepage, ricerca, host, identità
sorgente, richieste, batch, rete e recupero dominio:

```powershell
& C:\SOURCECODE\PYTHON\StreamingCommunity-downloader\tmp\raiplay-qa\Scripts\python.exe -m pytest -q tests/test_animeunity_inertia.py tests/test_home.py tests/test_search_sources.py tests/test_hosts.py tests/test_provider_dispatch.py tests/test_source_identity.py tests/test_fix_media_network.py tests/test_download_batches.py tests/test_domain_recovery.py tests/test_fix_domain_aliases.py tests/test_unified_search.py tests/test_permissions.py tests/test_requests.py tests/test_batch_requests.py tests/test_shared_destination.py
```

**34 test frontend mirati superati**:

```powershell
node --test tests/test_anime_home_frontend.cjs tests/test_card_navigation_frontend.cjs tests/test_source_identity_frontend.cjs tests/test_catalog_frontend.cjs tests/test_domain_sync_frontend.cjs
```

Superati anche i controlli di sintassi dei quattro file JavaScript modificati
e `git diff --check`.

La suite frontend completa presenta un test RaiPlay già fallito sulla base:
`tests/test_source_safety_frontend.cjs`, caso che si aspetta disabilitato il
pulsante di una serie RaiPlay. Il fallimento è stato riprodotto nel checkout
principale non modificato; non riguarda AnimeUnity e non è stato corretto in
questa attività.

## Limiti e stato della consegna

- Interfaccia verificata tramite test JavaScript, senza prova visuale manuale nel browser.
- Risoluzione reale del player verificata; nessun download completo di un episodio effettuato.
- I video nascosti o assenti nella sorgente non vengono resi disponibili dalla patch.
- Copie dei file originali in `tmp/animeunity-backups/`, escluso da Git.
- La pulizia della prima cartella diagnostica temporanea
  `R:\TEMP\sc-animeunity-audit-rgk7oo6r` è stata rifiutata dai controlli automatici
  con messaggio `blocked by policy`; è stata lasciata intatta. La successiva
  verifica con chiusura delle connessioni SQLite ha completato correttamente
  anche la pulizia del proprio database temporaneo.
