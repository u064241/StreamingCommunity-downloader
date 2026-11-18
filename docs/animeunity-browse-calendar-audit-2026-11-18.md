# AnimeUnity: catalogo paginato, archivio e palinsesto

Verifica del **18 novembre 2026**. Applicazione **2.8.1**.

Base verificata: `8b76f47` di `C:\SOURCECODE\PYTHON\StreamingCommunity-downloader`.
Branch isolato: `feat/animeunity-browse-calendar`.
Workspace: `C:\Users\Administrator\Documents\Codex\2026-11-11\anali\work\animeunity-browse-calendar`.
Il checkout principale non è stato modificato. Nessun push, setup o release.
La delega non era disponibile in questa sessione; analisi e implementazione
sono state svolte direttamente dal modello principale.

## Cause verificate

1. La home del portale leggeva `props.latestEpisodes.data` dalla prima pagina
   Inertia, ignorando `current_page`, `last_page`, `total` e l'API delle pagine
   successive. Accorpava poi i video per anime: più episodi della stessa serie
   diventavano una sola card.
2. Le sette strisce aggiunte in precedenza erano anteprime della homepage.
   Mancava una vista paginata collegata all'archivio completo. Il menu laterale
   Archivio era e resta l'archivio StreamingCommunity.
3. Il calendario pubblico e il relativo endpoint JSON non venivano letti.
4. I pulsanti tipo/doppiaggio esistevano nel codice, ma la vista anime non aveva
   un ingresso esplicito alle viste complete; sulla home filtravano soltanto le
   card già caricate. Ora vengono ricostruiti all'ingresso e restano visibili
   sopra il catalogo, con stato selezionato accessibile.
   Il selettore predefinito Tutte le fonti espone solo Tutti/Film/Serie TV:
   OVA/ONA/Speciali/doppiaggio compaiono selezionando AnimeUnity. Non è stato
   riprodotto un difetto CSS che nascondesse i pulsanti con AnimeUnity già selezionato.
5. Il pulsante Cerca nella scheda titolo ricreava un indirizzo con la sola
   sorgente, perdendo la pagina di provenienza. Per AnimeUnity ora riutilizza
   l'indirizzo completo salvato.

## Struttura reale della sorgente

Fonti primarie lette via HTTP 200, senza autenticazione:

- [Home](https://animeunity.cx/)
- [Archivio](https://animeunity.cx/archive)
- [Classifiche](https://animeunity.cx/top-anime)
- [Calendario](https://animeunity.cx/calendar)
- [Bundle pubblico esaminato](https://animeunity.cx/build/assets/common-DUqe5BIs.js)

Il nome del bundle è quello osservato e può cambiare con un rilascio del sito.
Il codice del portale non dipende da quel nome e non copia codice Vue del sito.

### Inertia e JSON

| Vista | Pagina Inertia / componente | API JSON | Contenuto |
|---|---|---|---|
| Home | `/`, `Home` | `/api/home/latest-episodes` | Paginatore `latestEpisodes`; strisce `carouselAnimes`, `latestAnimes`, `upcomingAnimes`, `topAnimes.day/week/month`; `calendarEntries`, `calendarSeason` |
| Archivio | `/archive`, `Archive` | `/api/archive` | `props.animes` e vocabolari dei filtri |
| Classifiche | `/top-anime`, `Archive` | `/api/top-anime` | Stesso paginatore, ordinamento per visualizzazioni e periodo |
| Calendario | `/calendar`, `Calendar` | `/api/calendar` | `props.entries` nella pagina; dizionario giorni → voci nell'API |
| Scheda | `/anime/{id-slug}` | `/api/anime/{id}/episodes?start=...` | Gestione già esistente, conservata |

Le pagine includono JSON in `div#app[data-page]`. Il parser esistente decodifica
l'attributo tramite BeautifulSoup. Le API delle liste restituiscono direttamente
il paginatore, non un wrapper `props`.

Campi paginatore: `data`, `current_page`, `per_page`, `total`, `last_page`,
`from`, `to`, `first_page_url`, `last_page_url`, `next_page_url`,
`prev_page_url`, `path`, `links`. Il portale calcola le pagine usando i numeri:
non segue URL arbitrari contenuti nella risposta.

### Query osservate

| Endpoint | Parametri e comportamento |
|---|---|
| `/api/home/latest-episodes` | `page` da 1; `per_page=20` desktop, `18` nel frontend mobile della sorgente; `language=dub` oppure `sub`, assente per tutti |
| `/api/archive` | `page`, `query`, `type`, `language`, `sort_by`, `status` |
| `/api/top-anime` | Filtri archivio, `sort_by=views`, `period=day/week/month` |
| `/api/calendar` | Nessun parametro data nel frontend esaminato; restituisce la settimana corrente e il gruppo Indeterminato. Il sito aggiorna ogni 30 secondi |

Differenza essenziale: **`language=dub` negli ultimi episodi, `language=ita`
nell'archivio**. `language=ita` sugli ultimi episodi restituisce 422.
`per_page=40/60` sugli ultimi episodi restituisce 422. Il parametro `type=movie`
viene ignorato da questa API: il confronto reale restituiva anche TV e ONA.
Per questo il portale aggrega pagine native e, quando si filtra il tipo degli
aggiornamenti, filtra l'intero feed recente, non una singola pagina.

La UI sorgente dell'archivio espone inoltre `genres[]`, `genres_exclude[]`,
`studio[]`, `season`, `country`, `source`, `adult`, `year_from`, `year_to`.
Queste chiavi sono state individuate nel costruttore della query del bundle;
non sono tutte state provate con richieste singole né aggiunte al pannello.
L'implementazione espone i filtri richiesti e cinque ordinamenti.

Vocabolari reali di `props`:

- `animeTypes`: `tv`, `movie`, `ova`, `ona`, `tv_short`, `special`.
- `animeLanguages`: `ita`, `jpn`, `chn`, `kor`, `eng`.
- `animeStatuses`: `ongoing`, `completed`, `upcoming`, `dropped`.
- `animeSeasons`: `winter`, `spring`, `summer`, `fall`.
- `animeCountries`: `JP`, `CN`, `KR`, `TW`, `US`.
- `animeGenres`, `animeStudios`: oggetti con `id` e `name`.
- `animeYears`, `animeSources`: valori disponibili dalla sorgente.

### Campi delle card e del calendario

Titoli: `id`, `slug`, `title`, `title_ja`, `title_it`, `type`, `season`, `year`,
`total_episodes`, `episode_runtime`, `default_status`, `score`, `views`,
`images.poster/cover/banner/logo`, `variants`, ed eventualmente `plot`,
`genres`, `studios`. Ogni variante comprende `language`, `status`,
`airing_day`, `episodes_count`.

Ultimi episodi: ogni riga è un **video**, con `id`, `language`, `is_hidden` e
`episode.number`, `episode.anime`. L'ID video identifica la card, mentre
l'ID anime apre la scheda nella lingua corretta. Due video della stessa serie
rimangono due card e mostrano numero episodio e lingua.

Calendario: `anime`, `isDub`, `airedEpisodes`, `isDailyRelease`, `airingDay`,
`releaseTime` ISO con offset, `nextEpisodeNumber`, `isLate`, `isAired`,
`isNextAiring`, `pausedUntil`. I gruppi hanno etichette come
`Mercoledì - Oggi`, `Giovedì - 19/11`, `Indeterminato`.
Il portale normalizza i giorni per tradurli, mantiene l'offset dei dati e
visualizza gli orari nel fuso `Europe/Rome`. Non inventa orari mancanti.

## Implementazione

- Nuove viste **Ultimi episodi**, **In evidenza**, **Archivio AnimeUnity**,
  **Palinsesto / Calendario** nel selettore della sorgente.
- Griglia di locandine da **20, 30 o 60 card**, default 60; navigazione precedente,
  successiva e salto pagina, sopra e sotto la griglia. Il conteggio viene dalla
  sorgente e si ricalcola sui risultati dei filtri.
- Archivio con ricerca, tutti i filtri richiesti e ordinamenti titolo A–Z/Z–A,
  ultime aggiunte, valutazione, visualizzazioni.
- Tutti, Film, Serie TV, OVA, ONA, Speciali e Solo doppiati IT sono visibili nel
  catalogo. In calendario è esposto il solo filtro doppiaggio: le righe del
  calendario non contengono `type`, e non viene dedotto erroneamente come TV.
- Le strisce della home mantengono le anteprime; sei pulsanti Sfoglia tutti
  aprono gli elenchi completi corrispondenti. In evidenza/carousel non ha un
  endpoint completo distinto.
- Stato vista/pagina/dimensione/ordinamento/giorno nell'hash, insieme a ricerca,
  tipo e doppiaggio; ritorno dalla scheda alla stessa posizione della lista.
- Backend `GET /api/anime/catalog` e `GET /api/anime/calendar`, con gli stessi
  permessi di ricerca/download già usati dal router anime.
- Cache limitata a 128 risposte, TTL 120 secondi, separata per host e parametri,
  invalidata al cambio host. Richieste alla sorgente serializzate; gli errori
  non vengono salvati come pagine vuote. Nessun polling aggiuntivo nel portale.
- Controllo del numero pagina restituito: se il sito ripete una pagina,
  si segnala un errore anziché duplicare silenziosamente le card.
- Risposte tardive scartate al cambio fonte/vista/filtro; retry sulla stessa
  pagina; traduzioni italiano/inglese e controlli leggibili.

### Compatibilità legacy

Rimane il rilevamento del formato originale. L'archivio legacy continua a usare
POST `/archivio/get-animes` con `title`, `offset`, `type` maiuscolo, `dubbed=1`
e gestione CSRF esistente. Vengono aggregate le finestre native da 30 record;
in assenza di un totale affidabile si mostrano precedente/successiva senza
inventare il numero totale di pagine.

La vecchia home conserva il parser `items-json`/`the-carousel`. Calendario e
classifiche avanzate non risultano disponibili nel vecchio contratto:
il portale lo comunica e lascia accessibile l'archivio. La pipeline episodi e
download esistente non è stata riscritta.

## File modificati

Applicazione:

- `app/core/animeunity_catalog.py` — nuovo adattatore liste/calendario e cache.
- `app/core/animeunity.py` — invalidazione della nuova cache al cambio host.
- `app/routers/anime.py` — API, parametri validati e gestione errori.
- `app/static/page-anime-catalog.js` — viste, pagine, calendario e stato.
- `app/static/page-search.js` — integrazione filtri, ingresso viste, strisce e hash.
- `app/static/page-title.js` — ritorno all'indirizzo originale della ricerca anime.
- `app/static/i18n.js` — etichette IT/EN.
- `app/static/css/pages.css` — griglia responsive, toolbar e pulsanti.
- `app/templates/index.html` — contenitori e caricamento del nuovo script.

Verifiche e documentazione:

- `tests/test_animeunity_catalog.py` — 29 regressioni backend.
- `tests/test_anime_catalog_frontend.cjs` — 9 regressioni frontend.
- `tests/fixtures/animeunity_browse_2026_11_18.json` — campioni pubblici ridotti.
- `tests/manual_animeunity_browser.py` — smoke test opt-in su template e API reali,
  con risposte sorgente registrate e dati sintetici per le pagine.
- Questo rapporto.

Backup dei file originali in `tmp/animeunity-browse-backups/`, escluso da Git.

## Verifiche

Confronti reali con la sorgente del 18/11/2026:

| Richiesta al nuovo adattatore | Risultato |
|---|---|
| Ultimi episodi, pagina 1, 60 card | 60 card; totale 180, 3 pagine nel portale |
| Ultimi episodi, pagina 3, 60 card | 60 card, compresa l'ultima finestra |
| Archivio, pagina 1, 60 card | 60 card; 6.290 titoli, 105 pagine nel portale |
| Archivio, pagina 105, 60 card | Ultimi 50 titoli; nessuna pagina successiva |
| Archivio One Piece, Film, Solo doppiati IT | 15 titoli, lingua italiana conservata |
| Ultimi episodi, Film, Solo doppiati IT | 3 video, trovati oltre la prima pagina nativa |
| Classifica settimanale, pagina 2, 30 card | 30 titoli, totale 6.290 |
| Calendario completo | 8 gruppi, 107 voci: 10+8+13+18+21+4+10+23 |

Il sito usa 30 titoli per pagina di archivio (210 pagine) e 20 video per pagina
recente (9 pagine). Le 105/3 pagine del portale corrispondono alla scelta 60 card:
non sono contenuti tagliati. Totali variabili quando la sorgente viene aggiornata.

**223 test backend superati**:

```powershell
& C:/SOURCECODE/PYTHON/StreamingCommunity-downloader/tmp/raiplay-qa/Scripts/python.exe -m pytest -q tests/test_animeunity_catalog.py tests/test_animeunity_inertia.py tests/test_home.py tests/test_search_sources.py tests/test_provider_dispatch.py tests/test_hosts.py tests/test_permissions.py tests/test_unified_search.py
```

**43 test frontend mirati superati**:

```powershell
node --test tests/test_anime_catalog_frontend.cjs tests/test_anime_home_frontend.cjs tests/test_catalog_frontend.cjs tests/test_card_navigation_frontend.cjs tests/test_domain_sync_frontend.cjs tests/test_source_identity_frontend.cjs
```

Suite frontend completa: **99/100**. Il fallimento
`test_source_safety_frontend.cjs: a RaiPlay card loads from /api/titles only and keeps the button disabled`
è stato riprodotto anche nel checkout principale `8b76f47` senza modifiche.
Il test si aspetta disabilitato il pulsante di una serie RaiPlay, che la base
renderizza attivo. Non è stato modificato in questo lavoro.

**Browser Edge/Chromium headless**, template completo e router FastAPI reali:
60 card, cambio pagina, 7 strisce e 6 ingressi agli elenchi completi, dimensione
30, filtri, apertura scheda/ritorno alla pagina 2, calendario e cambio giorno,
inglese, viewport mobile 390×844. Nessun errore JavaScript e nessun overflow
orizzontale del documento. Il database è temporaneo e lo scheduler non parte.
Screenshot verificati visivamente in `tmp/animeunity-browser/`:
`archive-it.png`, `calendar-en.png`, `archive-mobile.png`.
Le card sintetiche ripetute negli screenshot servono a testare la paginazione:
non costituiscono una prova del catalogo live. I conteggi reali sono riportati sopra.

## Limiti

- Nessun download completo o nuovo setup provato: l'intervento riguarda
  catalogo, calendario e navigazione, non l'encoding o il resolver video.
- Il sito pubblica la settimana corrente e le uscite indeterminate, non un
  archivio storico di palinsesti consultabile per data tramite questa API.
- Il filtro tipo dei recenti richiede la lettura del feed completo (9 pagine
  osservate); cache condivisa e richieste seriali ne limitano il costo. Se la
  sorgente supera 100 pagine recenti si produce un errore esplicito, senza taglio.
- `tv_short`, tipo aggiuntivo della sorgente, rimane visibile in Tutti con il
  suo tipo originale; i pulsanti implementati sono quelli richiesti.
- Calendario e classifiche non sono disponibili nel vecchio formato; ricerca,
  archivio, home, episodi e download legacy restano compatibili.
- Le verifiche browser usano payload registrati; le richieste live sono state
  verificate separatamente sul nuovo adattatore. Nessuna pubblicazione effettuata.
