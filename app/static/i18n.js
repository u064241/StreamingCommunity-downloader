/* Shared IT/EN messages for the shell, catalog and title detail.
   Render translated text in dedicated nodes to preserve icons and controls. */
(function () {
  const translations = {
    it: {
      'common.cancel': 'Annulla', 'common.confirm': 'Conferma', 'common.close': 'Chiudi',
      'common.save': 'Salva', 'common.loading': 'Caricamento...', 'common.error': 'Errore',
      'common.retry': 'Riprova', 'common.refresh': 'Aggiorna',
      'nav.search': 'Cerca', 'nav.downloads': 'Download', 'nav.requests': 'Richieste',
      'nav.myRequests': 'Le mie richieste', 'nav.watches': 'Serie seguite',
      'nav.files': 'File', 'nav.users': 'Utenti', 'nav.settings': 'Impostazioni',
      'updates.check': 'Controlla aggiornamenti', 'home.next': 'La tua prossima',
      'home.media': 'serie, film, anime.', 'home.subtitle': 'Cerca nel catalogo, scegli audio e sottotitoli, e mandalo in libreria.',
      'search.placeholder': 'Film, serie TV...', 'search.button': 'Cerca',
      'title.related': 'Correlati', 'title.switch': 'Scegli un altro titolo', 'title.item': 'Titolo'
    },
    en: {
      'common.cancel': 'Cancel', 'common.confirm': 'Confirm', 'common.close': 'Close',
      'common.save': 'Save', 'common.loading': 'Loading...', 'common.error': 'Error',
      'common.retry': 'Retry', 'common.refresh': 'Refresh',
      'nav.search': 'Search', 'nav.downloads': 'Downloads', 'nav.requests': 'Requests',
      'nav.myRequests': 'My requests', 'nav.watches': 'Followed series',
      'nav.files': 'Files', 'nav.users': 'Users', 'nav.settings': 'Settings',
      'updates.check': 'Check for updates', 'home.next': 'Your next',
      'home.media': 'series, films, anime.', 'home.subtitle': 'Search the catalog, choose audio and subtitles, and send it to your library.',
      'search.placeholder': 'Films, TV series...', 'search.button': 'Search',
      'title.related': 'Related', 'title.switch': 'Choose another title', 'title.item': 'Title'
    }
  };

  // Shared catalog and title messages; data stays separate from UI labels.
  const pageMessages = {
"files.moved": {it:"Spostato: {name}",en:"Moved: {name}"},
"files.movedCount": {it:"{count} file spostati",en:"{count} files moved"},
"files.moveFailedCount": {it:"{count} file non spostati",en:"{count} files could not be moved"},
"files.deletedCount": {it:"{count} file eliminati",en:"{count} files deleted"},
"files.deleteFailedCount": {it:"{count} file non eliminati",en:"{count} files could not be deleted"},
"files.renamed": {it:"Rinominato in: {name}",en:"Renamed to: {name}"},
"files.deleted": {it:"Eliminato: {name}",en:"Deleted: {name}"},
"files.deleteFolder": {it:"Eliminare la cartella \"{name}\" e tutto il suo contenuto?",en:"Delete folder \"{name}\" and all its contents?"},
"files.deleteFile": {it:"Eliminare il file \"{name}\"?",en:"Delete file \"{name}\"?"},
"downloads.pausedCount": {it:"{count} download in pausa — quello in corso termina",en:"{count} downloads paused — the active download will finish"},
"downloads.resumedCount": {it:"{count} download rimessi in coda",en:"{count} downloads queued again"},
"downloads.cancelledCount": {it:"{count} download annullati",en:"{count} downloads cancelled"},
"downloads.removedCount": {it:", {count} eliminati dalla libreria",en:", {count} removed from library"},
"downloads.notQueued": {it:"{count} download non sono stati rimessi in coda",en:"{count} downloads could not be queued again"},
"watches.confirmQueue": {it:"I nuovi episodi di «{name}» torneranno in coda. Procedere?",en:"New episodes of “{name}” will return to the queue. Continue?"},
"watches.autoEnabled": {it:"«{name}»: i nuovi episodi verranno scaricati automaticamente",en:"“{name}”: new episodes will download automatically"},
"watches.autoDisabled": {it:"«{name}»: i nuovi episodi torneranno in coda",en:"“{name}”: new episodes will return to the queue"},
"watches.confirmUnfollow": {it:"Smettere di seguire «{name}»?",en:"Unfollow “{name}”?"},
"requests.confirmCancel": {it:"Annullare {count} richieste?",en:"Cancel {count} requests?"},
"updates.badgeHint": {it:"Disponibile la v{version}: apri le note di rilascio",en:"Version {version} available: open release notes"},
"updates.confirmRestart": {it:"Aggiornare il pannello alla v{version}? Verrà riavviato: qualche istante senza interfaccia.",en:"Update the panel to v{version}? It will restart and be unavailable briefly."},
"dynamic.hint0": {it:"Pausa",en:"Pause"},
"dynamic.hint1": {it:"In pausa · riparte quando riprendi il gruppo",en:"Paused · resumes when you resume the group"},
"dynamic.hint2": {it:"Elimina questa notifica",en:"Delete this notification"},
"dynamic.hint3": {it:"Elimina",en:"Delete"},
"dynamic.hint4": {it:"Per aggiornare:",en:"To update:"},
"dynamic.hint5": {it:"Tracce audio e sottotitoli dei prossimi episodi",en:"Audio and subtitle tracks for upcoming episodes"},
"dynamic.hint6": {it:"I nuovi episodi torneranno a passare dalla coda di approvazione",en:"New episodes will go through the approval queue again"},
"dynamic.hint7": {it:"Approva la serie una volta: i nuovi episodi verranno scaricati senza passare dalla coda",en:"Approve the series once: new episodes will download without approval"},
"dynamic.hint8": {it:"Scarica tutte le lingue richieste dai follower, dai prossimi episodi",en:"Download all languages requested by followers starting with upcoming episodes"},
"dynamic.hint9": {it:"Cerca subito nuovi episodi, senza aspettare il controllo automatico",en:"Check for new episodes now without waiting for the automatic check"},
"dynamic.hint10": {it:"Lettura delle tracce dalla fonte...",en:"Reading tracks from the source..."},
"dynamic.hint11": {it:"Approva anche i prossimi episodi di questa serie, senza ripassare dalla coda",en:"Also approve upcoming episodes of this series without returning to the queue"},
"dynamic.hint12": {it:"In attesa di una verifica da parte di un amministratore.",en:"Waiting for an administrator to review."},
"dynamic.hint13": {it:"Rimuovi dall'elenco",en:"Remove from list"},
"dynamic.hint14": {it:"Metti in pausa: il download in corso finisce, gli altri aspettano",en:"Pause: the active download finishes and the others wait"},
"dynamic.hint15": {it:"Rimetti in coda gli episodi in pausa",en:"Queue paused episodes again"},
"dynamic.hint16": {it:"Annulla tutti i download rimasti del gruppo",en:"Cancel all remaining downloads in the group"},
    'dynamic.selected': {it: '{count} selezionati', en: '{count} selected'},
    'dynamic.selected.one': {it: '{count} selezionato', en: '{count} selected'},
    'dynamic.deleteSelected': {it: 'Eliminare {count} elementi selezionati?', en: 'Delete {count} selected items?'},
    'dynamic.checkedMinutes': {it: 'controllata {count} min fa', en: 'checked {count} min ago'},
    'dynamic.checkedHours': {it: 'controllata {count} h fa', en: 'checked {count} h ago'},
    'dynamic.checkedDays': {it: 'controllata {count} g fa', en: 'checked {count} days ago'},
    'dynamic.newEpisodes': {it: '{count} nuovi episodi trovati', en: '{count} new episodes found'},
    'dynamic.updateVersion': {it: 'Versione {version} disponibile (attuale {current})', en: 'Version {version} available (current {current})'},
    'dynamic.updateConfirm': {it: "Versione {version} verificata. Applicarla? L'applicazione verrà riavviata automaticamente dopo la sostituzione.", en: 'Version {version} verified. Apply it? The application will restart automatically after replacement.'},
    'dynamic.installedVersion': {it: 'Versione installata: v{version}', en: 'Installed version: v{version}'},
    'dynamic.domainUpdated': {it: 'Dominio aggiornato: {domain}', en: 'Domain updated: {domain}'},
"dynamic.extra0": {it:"Attivo",en:"Active"},
"dynamic.extra1": {it:"Disabilitato",en:"Disabled"},
"dynamic.extra2": {it:"Non importato",en:"Not imported"},
"dynamic.extra3": {it:"Importa",en:"Import"},
"dynamic.extra4": {it:"Segui",en:"Follow"},
"dynamic.extra5": {it:"Seguita",en:"Following"},
"dynamic.extra6": {it:"Lingue",en:"Languages"},
"dynamic.extra7": {it:"Controlla ora",en:"Check now"},
"dynamic.extra8": {it:"Non seguire più",en:"Unfollow"},
"dynamic.extra9": {it:"Togli automatico",en:"Disable automatic downloads"},
"dynamic.extra10": {it:"Approva automatico",en:"Approve automatic downloads"},
"dynamic.extra11": {it:"Unisci lingue",en:"Merge languages"},
"dynamic.extra12": {it:"download automatico",en:"automatic downloads"},
"dynamic.extra13": {it:"passa dalla coda",en:"requires approval"},
"dynamic.extra14": {it:"Nessuna serie seguita.",en:"No followed series."},
"dynamic.extra15": {it:"Nessuna traccia disponibile.",en:"No tracks available."},
"dynamic.extra16": {it:"Nessun download in questo filtro",en:"No downloads match this filter"},
"dynamic.extra17": {it:"Nessun download in corso",en:"No active downloads"},
"dynamic.extra18": {it:"Nessun file trovato",en:"No files found"},
"dynamic.extra19": {it:"Ricerca...",en:"Searching..."},
"dynamic.extra20": {it:"Controllo...",en:"Checking..."},
"dynamic.extra21": {it:"Nessuna notifica.",en:"No notifications."},
"dynamic.extra22": {it:"Nessuna nota di rilascio.",en:"No release notes."},
"dynamic.extra23": {it:"Nuova richiesta",en:"New request"},
"dynamic.extra24": {it:"Richiesta già presente",en:"Request already exists"},
"dynamic.extra25": {it:"Richiesta approvata",en:"Request approved"},
"dynamic.extra26": {it:"Richiesta rifiutata",en:"Request rejected"},
"dynamic.extra27": {it:"Richiesta in download",en:"Request downloading"},
"dynamic.extra28": {it:"Richiesta completata",en:"Request completed"},
"dynamic.extra29": {it:"Richiesta fallita",en:"Request failed"},
"dynamic.extra30": {it:"Richiesta da verificare",en:"Request needs attention"},
"dynamic.extra31": {it:"Download completato",en:"Download completed"},
"dynamic.extra32": {it:"Download fallito",en:"Download failed"},
"dynamic.extra33": {it:"Stagione o serie completata",en:"Season or series completed"},
"dynamic.extra34": {it:"Stagione o serie fallita",en:"Season or series failed"},
"dynamic.extra35": {it:"Serie seguita da approvare",en:"Followed series needs approval"},
"dynamic.extra36": {it:"Serie approvata",en:"Series approved"},
"dynamic.extra37": {it:"Lingue diverse su una serie seguita",en:"Different languages on a followed series"},
"dynamic.extra38": {it:"Nuovo dominio trovato",en:"New domain found"},
"dynamic.extra39": {it:"Dominio aggiornato",en:"Domain updated"},
"dynamic.extra40": {it:"Hook fallito",en:"Hook failed"},
"dynamic.extra41": {it:"Richieste",en:"Requests"},
"dynamic.extra42": {it:"Download diretti",en:"Direct downloads"},
"dynamic.extra43": {it:"Serie seguite",en:"Followed series"},
"dynamic.extra44": {it:"Sorgente",en:"Source"},
"dynamic.extra45": {it:"Approva",en:"Approve"},
"dynamic.extra46": {it:"Rifiuta",en:"Reject"},
"dynamic.extra47": {it:"Correggi",en:"Correct"},
"dynamic.extra48": {it:"Auto i prossimi",en:"Auto-download upcoming"},
"dynamic.extra49": {it:"In coda",en:"Queued"},
"dynamic.extra50": {it:"Lancia subito",en:"Start now"},
"dynamic.extra51": {it:"Interrompi",en:"Stop"},
"dynamic.extra52": {it:"Rimuovi dall’elenco",en:"Remove from list"},
"dynamic.extra53": {it:"Riprendi",en:"Resume"},
"dynamic.ui0": {it:"Riconnessione...",en:"Reconnecting..."},
"dynamic.ui1": {it:"Programmato",en:"Scheduled"},
"dynamic.ui2": {it:"In attesa",en:"Waiting"},
"dynamic.ui3": {it:"In pausa",en:"Paused"},
"dynamic.ui4": {it:"In corso",en:"Running"},
"dynamic.ui5": {it:"Finalizzazione",en:"Finalizing"},
"dynamic.ui6": {it:"Conversione",en:"Conversion"},
"dynamic.ui7": {it:"Unione",en:"Merging"},
"dynamic.ui8": {it:"Completato",en:"Completed"},
"dynamic.ui9": {it:"Errore",en:"Error"},
"dynamic.ui10": {it:"Annullato",en:"Cancelled"},
"dynamic.ui11": {it:"Fine",en:"Done"},
"dynamic.ui12": {it:"Approvata",en:"Approved"},
"dynamic.ui13": {it:"In download",en:"Downloading"},
"dynamic.ui14": {it:"Completata",en:"Completed"},
"dynamic.ui15": {it:"Già in libreria",en:"Already in library"},
"dynamic.ui16": {it:"Rifiutata",en:"Rejected"},
"dynamic.ui17": {it:"Fallita",en:"Failed"},
"dynamic.ui18": {it:"Annullata",en:"Cancelled"},
"dynamic.ui19": {it:"Da verificare",en:"Needs attention"},
"dynamic.ui20": {it:"Scarica direttamente",en:"Download directly"},
"dynamic.ui21": {it:"Può richiedere",en:"Can request"},
"dynamic.ui22": {it:"Approva richieste",en:"Approve requests"},
"dynamic.ui23": {it:"Gestisce utenti",en:"Manage users"},
"dynamic.ui24": {it:"Gestisce impostazioni",en:"Manage settings"},
"dynamic.ui25": {it:"Gestisce file",en:"Manage files"},
"dynamic.ui26": {it:"Sfoglia libreria",en:"Browse library"},
"dynamic.ui27": {it:"Importati",en:"Imported"},
"dynamic.ui28": {it:"Da importare",en:"Not imported"},
"dynamic.ui29": {it:"Disabilitati",en:"Disabled"},
"dynamic.ui30": {it:"Nessun utente corrisponde al filtro.",en:"No users match the filter."},
"dynamic.ui31": {it:"Nessun account trovato su Jellyfin.",en:"No accounts found on Jellyfin."},
"dynamic.ui32": {it:"Disabilita",en:"Disable"},
"dynamic.ui33": {it:"Abilita",en:"Enable"},
"dynamic.ui34": {it:"Utente importato",en:"User imported"},
"dynamic.ui35": {it:"Permessi aggiornati",en:"Permissions updated"},
"dynamic.ui36": {it:"Disabilitare questo utente? Le sue sessioni verranno chiuse subito.",en:"Disable this user? Their sessions will be closed immediately."},
"dynamic.ui37": {it:"Utente abilitato",en:"User enabled"},
"dynamic.ui38": {it:"Utente disabilitato",en:"User disabled"},
"dynamic.ui39": {it:"Chiunque abbia un account Jellyfin può ora accedere",en:"Anyone with a Jellyfin account can now sign in"},
"dynamic.ui40": {it:"Accesso limitato agli utenti importati",en:"Access restricted to imported users"},
"dynamic.ui41": {it:"mai controllata",en:"never checked"},
"dynamic.ui42": {it:"controllata ora",en:"checked just now"},
"dynamic.ui43": {it:"Impossibile caricare le serie seguite.",en:"Unable to load followed series."},
"dynamic.ui44": {it:"Seguite",en:"Following"},
"dynamic.ui45": {it:"Automatiche",en:"Automatic"},
"dynamic.ui46": {it:"Dalla coda",en:"Through queue"},
"dynamic.ui47": {it:"Serie TV",en:"TV series"},
"dynamic.ui48": {it:"originale",en:"original"},
"dynamic.ui49": {it:"nessuno",en:"none"},
"dynamic.ui50": {it:"Altri follower",en:"Other followers"},
"dynamic.ui51": {it:"chiede",en:"requests"},
"dynamic.ui52": {it:"chiedono",en:"request"},
"dynamic.ui53": {it:" e ",en:" and "},
"dynamic.ui54": {it:"Scegli almeno una traccia audio",en:"Select at least one audio track"},
"dynamic.ui55": {it:"Lingue aggiornate: varranno dai prossimi episodi",en:"Languages updated: applies to future episodes"},
"dynamic.ui56": {it:"Lingue unite: varranno dai prossimi episodi",en:"Languages merged: applies to future episodes"},
"dynamic.ui57": {it:"Serie non più seguita",en:"Series unfollowed"},
"dynamic.ui58": {it:"Impossibile seguire la serie",en:"Unable to follow series"},
"dynamic.ui59": {it:"Serie seguita: i nuovi episodi arriveranno da soli",en:"Following series: new episodes will download automatically"},
"dynamic.ui60": {it:"Serie seguita: i nuovi episodi verranno richiesti a un amministratore",en:"Following series: new episodes will be requested from an administrator"},
"dynamic.ui61": {it:"Errore di rete",en:"Network error"},
"dynamic.ui62": {it:"Controllo fallito",en:"Check failed"},
"dynamic.ui63": {it:"1 nuovo episodio trovato",en:"1 new episode found"},
"dynamic.ui64": {it:"Nessun nuovo episodio",en:"No new episodes"},
"dynamic.ui65": {it:"Operazione fallita",en:"Operation failed"},
"dynamic.ui66": {it:"Percorso cartella di destinazione (vuoto = radice):",en:"Destination folder path (empty = root):"},
"dynamic.ui67": {it:"Errore ricerca",en:"Search failed"},
"dynamic.ui68": {it:"radice",en:"root"},
"dynamic.ui69": {it:"Nuovo nome:",en:"New name:"},
"dynamic.ui70": {it:"File spostato",en:"File moved"},
"dynamic.ui71": {it:"File eliminato",en:"File deleted"},
"dynamic.ui72": {it:"Rinominato",en:"Renamed"},
"dynamic.ui73": {it:"Richiesta corretta — approvala per riprovare",en:"Request corrected — approve it to retry"},
"dynamic.ui74": {it:"Nessuna traccia rilevata all’ultimo tentativo",en:"No tracks detected on the last attempt"},
"dynamic.ui75": {it:"Nessuna richiesta da mostrare.",en:"No requests to show."},
"dynamic.ui76": {it:"Non hai ancora richiesto niente.",en:"You have not requested anything yet."},
"dynamic.ui77": {it:"Nessuna richiesta in questa categoria.",en:"No requests in this category."},
"dynamic.ui78": {it:"Nessuna nota pubblicata.",en:"No release notes published."},
"dynamic.ui79": {it:"Aggiorna il programma portable conservando il vecchio EXE come backup.",en:"Update the portable app, keeping the old EXE as a backup."},
"dynamic.ui80": {it:"Per questa distribuzione scarica la release ed esegui l’aggiornamento guidato.",en:"Download the release and run the update wizard for this distribution."},
"dynamic.ui81": {it:"Aggiornamento diretto disponibile solo dal pannello locale portable.",en:"Direct updates are available only from the local portable panel."},
"dynamic.ui82": {it:"Scaricare e verificare la nuova versione portable?",en:"Download and verify the new portable version?"},
"dynamic.ui83": {it:"Aggiornamento fallito",en:"Update failed"},
"dynamic.ui84": {it:"Download e verifica in corso…",en:"Downloading and verifying…"},
"dynamic.ui85": {it:"Aggiornamento annullato. Nessun file dell’app sostituito.",en:"Update cancelled. No application files were replaced."},
"dynamic.ui86": {it:"Aggiornamento non riuscito",en:"Update unsuccessful"},
"dynamic.ui87": {it:"Nessun aggiornamento disponibile.",en:"No updates available."},
"dynamic.ui88": {it:"Controllo aggiornamenti non disponibile.",en:"Update check unavailable."},
"dynamic.ui89": {it:"Eliminazione fallita",en:"Deletion failed"},
"dynamic.ui90": {it:"Notifiche eliminate",en:"Notifications deleted"},
"dynamic.ui91": {it:"Domain non configurato",en:"Domain not configured"},
"dynamic.ui92": {it:"Impossibile applicare il dominio",en:"Unable to apply domain"},
"dynamic.ui93": {it:"Annulla",en:"Cancel"},
"dynamic.ui94": {it:"Conferma",en:"Confirm"},
"dynamic.ui95": {it:"Chiudi",en:"Close"},
"dynamic.ui96": {it:"Salva",en:"Save"},
"dynamic.ui97": {it:"Riprova",en:"Retry"},
"dynamic.ui98": {it:"Aggiorna",en:"Refresh"},
"dynamic.ui99": {it:"Caricamento...",en:"Loading..."},
"title.follow": {it:"Segui",en:"Follow"},
"title.following": {it:"Seguita",en:"Following"},
"title.followHint": {it:"Cerca automaticamente i nuovi episodi di questa serie",en:"Automatically check for new episodes of this series"},
"title.followingHint": {it:"I nuovi episodi vengono cercati automaticamente. Premi per smettere.",en:"New episodes are checked automatically. Click to unfollow."},
"catalog.genre0": {it:"Azione",en:"Action"},
"catalog.genre1": {it:"Avventura",en:"Adventure"},
"catalog.genre2": {it:"Animazione",en:"Animation"},
"catalog.genre3": {it:"Commedia",en:"Comedy"},
"catalog.genre4": {it:"Crime",en:"Crime"},
"catalog.genre5": {it:"Documentario",en:"Documentary"},
"catalog.genre6": {it:"Dramma",en:"Drama"},
"catalog.genre7": {it:"Drammatico",en:"Drama"},
"catalog.genre8": {it:"Famiglia",en:"Family"},
"catalog.genre9": {it:"Fantasy",en:"Fantasy"},
"catalog.genre10": {it:"Fantascienza",en:"Science fiction"},
"catalog.genre11": {it:"Horror",en:"Horror"},
"catalog.genre12": {it:"Mistero",en:"Mystery"},
"catalog.genre13": {it:"Musica",en:"Music"},
"catalog.genre14": {it:"Romance",en:"Romance"},
"catalog.genre15": {it:"Romantico",en:"Romance"},
"catalog.genre16": {it:"Storia",en:"History"},
"catalog.genre17": {it:"Thriller",en:"Thriller"},
"catalog.genre18": {it:"Guerra",en:"War"},
"catalog.genre19": {it:"Western",en:"Western"},
"catalog.genre20": {it:"Sci-Fi & Fantasy",en:"Sci-Fi & Fantasy"},
"catalog.genre21": {it:"Action & Adventure",en:"Action & Adventure"},
"catalog.genre22": {it:"Bambini",en:"Kids"},
"catalog.genre23": {it:"Film TV",en:"TV movie"},

    "catalog.all": {it: "Tutti", en: "All"},
    "catalog.movie": {it: "Film", en: "Film"},
    "catalog.tv": {it: "Serie TV", en: "TV series"},
    "catalog.special": {it: "Speciale", en: "Special"},
    "catalog.specials": {it: "Speciali", en: "Specials"},
    "catalog.dubbed": {it: "Solo doppiati IT", en: "Italian dubbed only"},
    "search.animePlaceholder": {it: "Cerca anime...", en: "Search anime..."},
    "search.loading": {it: "Carico...", en: "Loading..."},
    "search.empty": {it: "Nessun risultato.", en: "No results."},
    "search.noMore": {it: "Nessun altro risultato", en: "No more results"},
    "search.networkError": {it: "Errore di rete", en: "Network error"},
    "catalog.title": {it: "Catalogo", en: "Catalog"},
    "catalog.home": {it: "Torna alla home", en: "Back to home"},
    "catalog.more": {it: "Carica altri", en: "Load more"},
    "catalog.browse": {it: "Sfoglia tutti", en: "Browse all"},
    "catalog.moreGenres": {it: "Carica altri generi", en: "Load more genres"},
    "catalog.retrySections": {it: "Riprova sezioni non disponibili", en: "Retry unavailable sections"},
    "catalog.empty": {it: "Nessun titolo disponibile.", en: "No titles available."},
    "catalog.unavailable": {it: "Catalogo non disponibile", en: "Catalog unavailable"},
    "catalog.upcoming": {it: "In arrivo", en: "Coming soon"},
    "catalog.upcomingSeason": {it: "Nuova stagione in arrivo", en: "New season coming soon"},
    "catalog.preview": {it: "Anteprima", en: "Preview"},
    "catalog.previous": {it: "Titoli precedenti", en: "Previous titles"},
    "catalog.next": {it: "Titoli successivi", en: "Next titles"},
    "catalog.trending": {it: "Di tendenza", en: "Trending"},
    "catalog.latest": {it: "Aggiunti di recente", en: "Recently added"},
    "catalog.top10": {it: "Top 10 di oggi", en: "Today's top 10"},
    "catalog.latestEpisodes": {it: "Ultimi episodi", en: "Latest episodes"},
    "catalog.featured": {it: "In evidenza", en: "Featured"},
    "catalog.pending": {it: "Richiesto", en: "Requested"},
    "catalog.approved": {it: "Approvato", en: "Approved"},
    "catalog.downloading": {it: "In download", en: "Downloading"},
    "catalog.inLibrary": {it: "In libreria", en: "In library"},
    "catalog.denied": {it: "Rifiutato", en: "Rejected"},
    "catalog.failed": {it: "Fallito", en: "Failed"},
    "catalog.attention": {it: "Attenzione", en: "Attention"},
    "catalog.cancelled": {it: "Annullato", en: "Cancelled"},
    "title.episodes": {it: "Episodi", en: "Episodes"},
    "title.about": {it: "Descrizione", en: "Description"},
    "title.tracks": {it: "Tracce", en: "Tracks"},
    "title.back": {it: "Torna alla ricerca", en: "Back to search"},
    "title.unavailable": {it: "In arrivo: contenuto non ancora disponibile", en: "Coming soon: content not yet available"},
    "title.request": {it: "Richiedi", en: "Request"},
    "title.schedule": {it: "Programma", en: "Schedule"},
    "title.download": {it: "Scarica", en: "Download"},
    "title.scopeEpisodes": {it: " tutti gli episodi", en: " all episodes"},
    "title.scopeSeries": {it: " la serie", en: " series"},
    "title.scopeSeason": {it: " la stagione", en: " season"},
    "title.scheduleHint": {it: "Lascia vuoto per scaricare subito", en: "Leave empty to download now"},
    "title.tracksHint": {it: "Scegli audio e sottotitoli", en: "Choose audio and subtitles"},
    "title.noPlot": {it: "Nessuna descrizione disponibile per questo titolo.", en: "No description available for this title."},
    "title.person": {it: "Cerca informazioni su {name}", en: "Search for information about {name}"},
    "title.directors": {it: "Regia", en: "Directors"},
    "title.cast": {it: "Interpreti", en: "Cast"},
    "title.readingTracks": {it: "Lettura delle tracce…", en: "Reading tracks…"},
    "title.selectAll": {it: "Seleziona tutte", en: "Select all"},
    "title.none": {it: "Nessuno", en: "None"},
    "title.originalOnly": {it: "solo la traccia originale", en: "original track only"},
    "title.subtitles": {it: "Sottotitoli", en: "Subtitles"},
    "title.noSubtitles": {it: "nessun sottotitolo disponibile", en: "no subtitles available"},
    "title.trackNote": {it: "Vale per ogni download avviato da questa pagina. Una lingua audio mai presente nel file non viene sostituita con un'altra: la richiesta si ferma e la guarda una persona.", en: "Applies to every download started from this page. If a requested audio language is missing from the file, it is not replaced: the request is paused for review."},
    "title.originalName": {it: "Titolo originale", en: "Original title"},
    "title.year": {it: "Anno", en: "Year"},
    "title.seasons": {it: "Stagioni", en: "Seasons"},
    "title.runtime": {it: "Durata", en: "Runtime"},
    "title.status": {it: "Stato", en: "Status"},
    "title.releaseSeason": {it: "Stagione di uscita", en: "Release season"},
    "title.quality": {it: "Qualità", en: "Quality"},
    "title.genre": {it: "Genere", en: "Genre"},
    "title.playTrailer": {it: "Riproduci il trailer", en: "Play trailer"},
    "title.episodesUnavailable": {it: "In arrivo: episodi non ancora disponibili.", en: "Coming soon: episodes not yet available."},
    "title.onlyMissing": {it: "Solo mancanti", en: "Missing only"},
    "title.skipHint": {it: "Salta gli episodi il cui file è già in libreria", en: "Skip episodes whose files are already in the library"},
    "title.loadingEpisodes": {it: "Caricamento episodi…", en: "Loading episodes…"},
    "title.noEpisodes": {it: "Nessun episodio in questa stagione.", en: "No episodes in this season."},
    "title.alreadyInLibrary": {it: "Già in libreria", en: "Already in library"},
    "title.noRequests": {it: "Nessun episodio da richiedere", en: "No episodes to request"},
    "title.requestFailed": {it: "Richiesta non riuscita", en: "Request failed"},
    "title.sourceUnavailable": {it: "Sorgente non raggiungibile", en: "Source unreachable"},
    "title.downloadsLink": {it: "Vai ai download", en: "Go to downloads"},
    "title.startError": {it: "Errore avviando i download", en: "Error starting downloads"},
    "title.allOwned": {it: "Tutti gli episodi della stagione sono già in libreria", en: "All episodes in this season are already in the library"},
    "title.planAction": {it: "Programmare", en: "Schedule"},
    "title.queueAction": {it: "Aggiungere alla coda", en: "Queue"},
    "title.skipNote": {it: ", saltando quelli già in libreria", en: ", skipping episodes already in the library"},
    "title.seasonCount.one": {it: "{count} stagione", en: "{count} season"},
    "title.seasonCount.other": {it: "{count} stagioni", en: "{count} seasons"},
    "title.episodeCount.one": {it: "{count} episodio", en: "{count} episode"},
    "title.episodeCount.other": {it: "{count} episodi", en: "{count} episodes"},
    "title.seasonEpisodes": {it: "{count} episodi in S{season}", en: "{count} episodes in S{season}"},
    "title.episodeFacts": {it: "Episodi (S{season})", en: "Episodes (S{season})"},
    "title.owned": {it: "({count} già presenti)", en: "({count} already in library)"},
    "title.season": {it: "Stagione {season}", en: "Season {season}"},
    "title.episode": {it: "Episodio {number}", en: "Episode {number}"},
    "title.requestSeasonConfirm": {it: "Richiedere tutti i {count} episodi della stagione {season}?", en: "Request all {count} episodes of season {season}?"},
    "title.requests.one": {it: "{count} richiesta", en: "{count} request"},
    "title.requests.other": {it: "{count} richieste", en: "{count} requests"},
    "title.joined": {it: "{count} già in coda", en: "{count} already queued"},
    "title.availableMessage": {it: "{title} è già in libreria.", en: "{title} is already in the library."},
    "title.requestedMessage": {it: "{title} era già stato richiesto: sarai avvisato.", en: "{title} has already been requested: you will be notified."},
    "title.requestSent": {it: "Richiesta inviata: {title}", en: "Request sent: {title}"},
    "title.scheduled": {it: "Programmato: {title}", en: "Scheduled: {title}"},
    "title.scheduledDate": {it: "Programmato: {title} — {date}", en: "Scheduled: {title} — {date}"},
    "title.started": {it: "Download avviato: {title}", en: "Download started: {title}"},
    "title.queued": {it: "In coda: {title}", en: "Queued: {title}"},
    "title.skipped": {it: ", {count} già in libreria", en: ", {count} already in library"},
    "title.batchScheduled": {it: "{count} episodi programmati", en: "{count} episodes scheduled"},
    "title.batchQueued": {it: "{count} episodi in coda", en: "{count} episodes queued"},
    "title.confirmMissing": {it: "{action} i {count} episodi mancanti della stagione {season}?", en: "{action} the {count} missing episodes of season {season}?"},
    "title.confirmSeason": {it: "{action} tutti i {count} episodi della stagione {season}?", en: "{action} all {count} episodes of season {season}?"},
    "title.confirmSeries": {it: "{action} tutte le {count} stagioni{note}?", en: "{action} all {count} seasons{note}?"},
    "title.confirmAnime": {it: "{action} tutti i {count} episodi{note}?", en: "{action} all {count} episodes{note}?"},
    "title.actorQuery": {it: "informazioni attore", en: "actor information"},
    "title.forced": {it: "(forzati)", en: "(forced)"},
    "title.unfollow": {it: "Non seguire più", en: "Unfollow"}
  };
  Object.entries(pageMessages).forEach(([key, values]) => {
    translations.it[key] = values.it;
    translations.en[key] = values.en;
  });
  let activeLanguage;

  // Central catalog for the login and settings pages.
  const pageCatalog = [
  {
    "key": "login.sign.in.sc.panel",
    "it": "Accedi — SC Panel",
    "en": "Sign in — SC Panel"
  },
  {
    "key": "login.sign.in.with.jellyfin",
    "it": "Accesso tramite Jellyfin",
    "en": "Sign in with Jellyfin"
  },
  {
    "key": "login.initial.setup",
    "it": "Configurazione iniziale",
    "en": "Initial setup"
  },
  {
    "key": "login.connect.the.panel.to.your.jellyfin.server",
    "it": "Collega il pannello al tuo server Jellyfin.",
    "en": "Connect the panel to your Jellyfin server."
  },
  {
    "key": "login.you.need.a",
    "it": "Serve un account",
    "en": "You need a"
  },
  {
    "key": "login.jellyfin.administrator.account",
    "it": "amministratore Jellyfin",
    "en": "Jellyfin administrator account"
  },
  {
    "key": "login.it.will.become.the.panel.administrator.the.password",
    "it": ": diventerà l'amministratore del pannello. La password non viene salvata.",
    "en": ": it will become the panel administrator. The password is not stored."
  },
  {
    "key": "login.jellyfin.server.url",
    "it": "URL del server Jellyfin",
    "en": "Jellyfin server URL"
  },
  {
    "key": "login.administrator.username",
    "it": "Utente amministratore",
    "en": "Administrator username"
  },
  {
    "key": "login.set.up.and.sign.in",
    "it": "Configura e accedi",
    "en": "Set up and sign in"
  },
  {
    "key": "login.continue.without.jellyfin",
    "it": "Continua senza Jellyfin",
    "en": "Continue without Jellyfin"
  },
  {
    "key": "login.you.will.use.the.panel.without.signing.in",
    "it": "Userai il pannello senza login. Potrai collegarlo a Jellyfin in seguito dalle Impostazioni.",
    "en": "You will use the panel without signing in. You can connect it to Jellyfin later in Settings."
  },
  {
    "key": "login.sign.in",
    "it": "Accedi",
    "en": "Sign in"
  },
  {
    "key": "login.use.your.jellyfin.account.credentials",
    "it": "Usa le credenziali del tuo account Jellyfin.",
    "en": "Use your Jellyfin account credentials."
  },
  {
    "key": "login.username",
    "it": "Utente",
    "en": "Username"
  },
  {
    "key": "login.unable.to.contact.the.panel",
    "it": "Impossibile contattare il pannello.",
    "en": "Unable to contact the panel."
  },
  {
    "key": "login.connecting",
    "it": "Connessione...",
    "en": "Connecting..."
  },
  {
    "key": "login.setup.failed",
    "it": "Configurazione fallita.",
    "en": "Setup failed."
  },
  {
    "key": "login.please.wait",
    "it": "Attendere...",
    "en": "Please wait..."
  },
  {
    "key": "login.operation.failed",
    "it": "Operazione fallita.",
    "en": "Operation failed."
  },
  {
    "key": "login.signing.in",
    "it": "Accesso...",
    "en": "Signing in..."
  },
  {
    "key": "login.sign.in.failed",
    "it": "Accesso fallito.",
    "en": "Sign-in failed."
  },
  {
    "key": "settings.settings",
    "it": "Impostazioni",
    "en": "Settings"
  },
  {
    "key": "settings.source.libraries.and.naming.templates.determine.where.files",
    "it": "Sorgente, librerie e schema dei nomi decidono dove finiscono i file e come si chiamano. Ogni sezione si salva da sé.",
    "en": "Source, libraries and naming templates determine where files are stored and how they are named. Each section saves independently."
  },
  {
    "key": "settings.source",
    "it": "Sorgente",
    "en": "Source"
  },
  {
    "key": "settings.libraries",
    "it": "Librerie",
    "en": "Libraries"
  },
  {
    "key": "settings.associations",
    "it": "Associazioni",
    "en": "Associations"
  },
  {
    "key": "settings.naming",
    "it": "Nomi",
    "en": "Naming"
  },
  {
    "key": "settings.access",
    "it": "Accesso",
    "en": "Access"
  },
  {
    "key": "settings.notifications",
    "it": "Notifiche",
    "en": "Notifications"
  },
  {
    "key": "settings.the.domain.the.panel.uses.to.search.and",
    "it": "Il dominio da cui il pannello cerca e scarica. Salvando viene verificato.",
    "en": "The domain the panel uses to search and download. It is verified when saved."
  },
  {
    "key": "settings.domain",
    "it": "Domain",
    "en": "Domain"
  },
  {
    "key": "settings.verify.and.save",
    "it": "Verifica e salva",
    "en": "Verify and save"
  },
  {
    "key": "settings.other.hosts",
    "it": "Altri host",
    "en": "Other hosts"
  },
  {
    "key": "settings.animeunity.and.the.video.server.also.change.domains",
    "it": "Anche AnimeUnity e il server video cambiano dominio. Il server video viene letto dalla sorgente a ogni download; impostalo qui solo se la sorgente continua a indicarne uno che non risponde. Un campo vuoto torna al comportamento automatico. Salvando ogni host viene verificato.",
    "en": "AnimeUnity and the video server also change domains. The video server is read from the source for each download; set it here only if the source keeps returning an unresponsive server. Leave a field empty to restore automatic behavior. Each host is verified when saved."
  },
  {
    "key": "settings.video.server",
    "it": "Server video",
    "en": "Video server"
  },
  {
    "key": "settings.automatic.from.source",
    "it": "Automatico (dalla sorgente)",
    "en": "Automatic (from source)"
  },
  {
    "key": "settings.automatic.recovery",
    "it": "Recupero automatico",
    "en": "Automatic recovery"
  },
  {
    "key": "settings.the.source.domain.changes.every.few.weeks.the",
    "it": "Il dominio della sorgente cambia ogni poche settimane. Il pannello può accorgersene e proporre quello nuovo, dopo averlo verificato.",
    "en": "The source domain changes every few weeks. The panel can detect this and suggest a new domain after verifying it."
  },
  {
    "key": "settings.the.domain.is.read.from.the.streaming.community",
    "it": "Il dominio viene letto dal canale Telegram Streaming_community_sito e da una pagina esterna. Vengono presi in considerazione solo domini di secondo livello con un nome riconosciuto, e solo se rispondono davvero come la sorgente attesa. Con l’applicazione automatica attiva nessuno rilegge quella scelta prima che abbia effetto.",
    "en": "The domain is read from the Streaming_community_sito Telegram channel and an external page. Only second-level domains with a recognized name are considered, and only if they respond as the expected source. With automatic application enabled, the choice takes effect without manual review."
  },
  {
    "key": "settings.check.when.the.source.is.unresponsive",
    "it": "Controlla se la sorgente non risponde",
    "en": "Check when the source is unresponsive"
  },
  {
    "key": "settings.apply.the.discovered.domain.without.asking",
    "it": "Applica il dominio trovato senza chiedere",
    "en": "Apply the discovered domain without asking"
  },
  {
    "key": "settings.check.interval.minutes",
    "it": "Intervallo del controllo (minuti)",
    "en": "Check interval (minutes)"
  },
  {
    "key": "settings.save",
    "it": "Salva",
    "en": "Save"
  },
  {
    "key": "settings.check.now",
    "it": "Controlla ora",
    "en": "Check now"
  },
  {
    "key": "settings.application.log",
    "it": "Log applicazione",
    "en": "Application log"
  },
  {
    "key": "settings.latest.events.in.the.current.session.automatically.refreshed",
    "it": "Ultimi eventi della sessione corrente. Aggiornamento automatico ogni 3 secondi.",
    "en": "Latest events in the current session. Automatically refreshed every 3 seconds."
  },
  {
    "key": "settings.refresh",
    "it": "Aggiorna",
    "en": "Refresh"
  },
  {
    "key": "settings.default.download.folder",
    "it": "Cartella download predefinita",
    "en": "Default download folder"
  },
  {
    "key": "settings.used.for.new.downloads.without.a.specific.library",
    "it": "Usata per i nuovi download che non hanno una libreria specifica. I file esistenti restano nella cartella precedente.",
    "en": "Used for new downloads without a specific library. Existing files remain in the previous folder."
  },
  {
    "key": "settings.full.folder.path",
    "it": "Percorso completo della cartella",
    "en": "Full folder path"
  },
  {
    "key": "settings.browse",
    "it": "Sfoglia…",
    "en": "Browse…"
  },
  {
    "key": "settings.save.folder",
    "it": "Salva cartella",
    "en": "Save folder"
  },
  {
    "key": "settings.media.libraries",
    "it": "Librerie multimediali",
    "en": "Media libraries"
  },
  {
    "key": "settings.where.downloaded.files.are.stored.with.one.folder",
    "it": "Dove finiscono i file scaricati, una cartella per tipo di contenuto.",
    "en": "Where downloaded files are stored, with one folder for each content type."
  },
  {
    "key": "settings.docker.requires.the.path.inside.the.container",
    "it": "Su Docker serve il percorso interno al container",
    "en": "Docker requires the path inside the container"
  },
  {
    "key": "settings.that.is",
    "it": ", cioè",
    "en": ", that is"
  },
  {
    "key": "settings.for.example",
    "it": "— per esempio",
    "en": "— for example"
  },
  {
    "key": "settings.the.host.path",
    "it": ". Il percorso dell’host (",
    "en": ". The host path ("
  },
  {
    "key": "settings.belongs.in",
    "it": ") va nel",
    "en": ") belongs in"
  },
  {
    "key": "settings.not.here.it.is.the.part.before.the",
    "it": ", non qui: è la parte a sinistra dei due punti nel volume montato su",
    "en": ", not here: it is the part before the colon in the volume mounted at"
  },
  {
    "key": "settings.add.library",
    "it": "Aggiungi libreria",
    "en": "Add library"
  },
  {
    "key": "settings.excluded.folders",
    "it": "Cartelle escluse",
    "en": "Excluded folders"
  },
  {
    "key": "settings.comma.separated.names.ignored.when.browsing.libraries",
    "it": "Nomi separati da virgola, ignorati sfogliando le librerie.",
    "en": "Comma-separated names, ignored when browsing libraries."
  },
  {
    "key": "settings.save.libraries.and.folders",
    "it": "Salva librerie e cartelle",
    "en": "Save libraries and folders"
  },
  {
    "key": "settings.titles.and.folders",
    "it": "Titoli e cartelle",
    "en": "Titles and folders"
  },
  {
    "key": "settings.which.library.folder.contains.each.title.the.panel",
    "it": "Quale cartella della libreria contiene quale titolo. Il pannello le registra da solo a fine download, e riconosce le cartelle più vecchie della stessa serie salvate con un anno diverso. Qui si correggono: cambiare ID o cartella sostituisce l’associazione, e quella vecchia non viene più proposta. Nessun file viene spostato o rinominato.",
    "en": "Which library folder contains each title. The panel records associations after downloading and recognizes older folders for the same series saved with a different year. Correct them here: changing the ID or folder replaces the association, and the old one is no longer suggested. No files are moved or renamed."
  },
  {
    "key": "settings.all",
    "it": "Tutte",
    "en": "All"
  },
  {
    "key": "settings.matched.by.year",
    "it": "Riconosciute per anno",
    "en": "Matched by year"
  },
  {
    "key": "settings.set.manually",
    "it": "Impostate a mano",
    "en": "Set manually"
  },
  {
    "key": "settings.missing.folder",
    "it": "Cartella mancante",
    "en": "Missing folder"
  },
  {
    "key": "settings.check.folders",
    "it": "Verifica cartelle",
    "en": "Check folders"
  },
  {
    "key": "settings.excluded.associations",
    "it": "Associazioni escluse (",
    "en": "Excluded associations ("
  },
  {
    "key": "settings.corrected.or.removed.pairs.the.panel.no.longer",
    "it": "Coppie corrette o rimosse. Il pannello non le ripropone; ripristinarne una la rende di nuovo riconoscibile in automatico.",
    "en": "Corrected or removed pairs. The panel no longer suggests them; restoring a pair enables automatic recognition again."
  },
  {
    "key": "settings.filter",
    "it": "Filtro",
    "en": "Filter"
  },
  {
    "key": "settings.search.title.or.folder",
    "it": "Cerca titolo o cartella",
    "en": "Search title or folder"
  },
  {
    "key": "settings.look.for.renamed.or.removed.folders",
    "it": "Cerca le cartelle rinominate o rimosse",
    "en": "Look for renamed or removed folders"
  },
  {
    "key": "settings.naming.templates",
    "it": "Schema dei nomi",
    "en": "Naming templates"
  },
  {
    "key": "settings.how.folders.and.files.are.named.available.placeholders",
    "it": "Come vengono chiamati cartelle e file. Segnaposto disponibili:",
    "en": "How folders and files are named. Available placeholders:"
  },
  {
    "key": "settings.zero.padded",
    "it": "(con lo zero),",
    "en": "(zero-padded),"
  },
  {
    "key": "settings.zero.padded.text.in.square.brackets.appears.only",
    "it": "(con lo zero). Quello che sta fra parentesi quadre compare solo se i segnaposto al suo interno hanno un valore:",
    "en": "(zero-padded). Text in square brackets appears only when its placeholders have a value:"
  },
  {
    "key": "settings.disappears.entirely.when.the.year.is.unknown",
    "it": "sparisce del tutto quando l’anno non è noto.",
    "en": "disappears entirely when the year is unknown."
  },
  {
    "key": "settings.the.field.suggestions.are.the.defaults.and.follow",
    "it": "I valori suggeriti nei campi sono quelli predefiniti e seguono la struttura raccomandata dalla",
    "en": "The field suggestions are the defaults and follow the structure recommended by the"
  },
  {
    "key": "settings.official.jellyfin.documentation",
    "it": "documentazione ufficiale di Jellyfin",
    "en": "official Jellyfin documentation"
  },
  {
    "key": "settings.and",
    "it": "e",
    "en": "and"
  },
  {
    "key": "settings.with.the.extension.determined.by.the.output.format",
    "it": ", con l’estensione decisa dal formato di uscita. Cambiali solo se la tua libreria segue una convenzione diversa: Jellyfin riconosce quella sopra senza configurazione.",
    "en": ", with the extension determined by the output format. Change them only if your library uses a different convention: Jellyfin recognizes the structure above without configuration."
  },
  {
    "key": "settings.previously.downloaded.files.are.not.renamed.they.are",
    "it": "I file già scaricati non vengono rinominati. Continuano a essere riconosciuti come presenti, quindi non verranno riscaricati, ma restano con il nome vecchio finché non li rinomini dal gestore file.",
    "en": "Previously downloaded files are not renamed. They are still recognized as present and will not be downloaded again, but keep their old names until you rename them in the file manager."
  },
  {
    "key": "settings.films",
    "it": "Film",
    "en": "Films"
  },
  {
    "key": "settings.folder",
    "it": "Cartella",
    "en": "Folder"
  },
  {
    "key": "settings.tv.series",
    "it": "Serie TV",
    "en": "TV series"
  },
  {
    "key": "settings.series.folder",
    "it": "Cartella serie",
    "en": "Series folder"
  },
  {
    "key": "settings.season.folder",
    "it": "Cartella stagione",
    "en": "Season folder"
  },
  {
    "key": "settings.episode.file",
    "it": "File episodio",
    "en": "Episode file"
  },
  {
    "key": "settings.film.file",
    "it": "File film",
    "en": "Film file"
  },
  {
    "key": "settings.restore.defaults",
    "it": "Ripristina predefiniti",
    "en": "Restore defaults"
  },
  {
    "key": "settings.video.conversion.after.download",
    "it": "Conversione video dopo il download",
    "en": "Video conversion after download"
  },
  {
    "key": "settings.original.mode.preserves.source.quality.conversion.is.optional",
    "it": "La modalità Originale conserva la qualità della sorgente. La conversione è facoltativa, richiede tempo e può ridurre qualità e dimensioni. Si applica ai nuovi download; audio e sottotitoli vengono conservati. Una conversione alla volta.",
    "en": "Original mode preserves source quality. Conversion is optional, takes time and can reduce quality and file size. It applies to new downloads; audio and subtitles are preserved. One conversion runs at a time."
  },
  {
    "key": "settings.original.no.re.encoding",
    "it": "Originale — senza ricodifica",
    "en": "Original — no re-encoding"
  },
  {
    "key": "settings.convert.to.h.264",
    "it": "Converti in H.264",
    "en": "Convert to H.264"
  },
  {
    "key": "settings.convert.to.h.265.hevc",
    "it": "Converti in H.265 / HEVC",
    "en": "Convert to H.265 / HEVC"
  },
  {
    "key": "settings.automatic.gpu",
    "it": "GPU automatica",
    "en": "Automatic GPU"
  },
  {
    "key": "settings.quality",
    "it": "Qualità",
    "en": "Quality"
  },
  {
    "key": "settings.high.larger.files",
    "it": "Alta — file più grandi",
    "en": "High — larger files"
  },
  {
    "key": "settings.balanced",
    "it": "Bilanciata",
    "en": "Balanced"
  },
  {
    "key": "settings.compact.higher.compression",
    "it": "Compatta — maggiore compressione",
    "en": "Compact — higher compression"
  },
  {
    "key": "settings.use.the.cpu.if.the.gpu.is.unavailable",
    "it": "Usa la CPU se la GPU non è disponibile o la conversione GPU fallisce",
    "en": "Use the CPU if the GPU is unavailable or GPU conversion fails"
  },
  {
    "key": "settings.detecting.encoders",
    "it": "Rilevamento encoder…",
    "en": "Detecting encoders…"
  },
  {
    "key": "settings.save.conversion",
    "it": "Salva conversione",
    "en": "Save conversion"
  },
  {
    "key": "settings.detect.gpu",
    "it": "Rileva GPU",
    "en": "Detect GPU"
  },
  {
    "key": "settings.concurrent.downloads",
    "it": "Download contemporanei",
    "en": "Concurrent downloads"
  },
  {
    "key": "settings.recommended.2.4.higher.values.increase.network.and",
    "it": "Suggerito: 2–4. Valori alti aumentano l'uso di rete e RAM.",
    "en": "Recommended: 2–4. Higher values increase network and RAM usage."
  },
  {
    "key": "settings.parallel.segments",
    "it": "Segmenti in parallelo",
    "en": "Parallel segments"
  },
  {
    "key": "settings.overall.panel.limit.not.per.download.if.the",
    "it": "Tetto massimo complessivo del pannello, non per singolo download. Se la fonte risponde 503 il pannello scende sotto questo valore da solo e risale quando torna a servire. Suggerito: 8–16.",
    "en": "Overall panel limit, not per download. If the source returns 503, the panel automatically lowers this value and raises it again when service recovers. Recommended: 8–16."
  },
  {
    "key": "settings.followed.series.checks",
    "it": "Controllo serie seguite",
    "en": "Followed series checks"
  },
  {
    "key": "settings.how.often.to.check.for.new.episodes.recommended",
    "it": "Ogni quanto cercare nuovi episodi. Suggerito: 240 (4 ore).",
    "en": "How often to check for new episodes. Recommended: 240 (4 hours)."
  },
  {
    "key": "settings.save.performance.settings",
    "it": "Salva performance",
    "en": "Save performance settings"
  },
  {
    "key": "settings.output.format",
    "it": "Formato di uscita",
    "en": "Output format"
  },
  {
    "key": "settings.choose.the.container.for.downloaded.files.and.where",
    "it": "In quale contenitore finiscono i file scaricati, e dove finiscono i sottotitoli. Il video non viene mai ricodificato: la sorgente è già H.264 con audio AAC, che entrambi i contenitori reggono così come sono, quindi cambiare questa impostazione non allunga di un secondo i download.",
    "en": "Choose the container for downloaded files and where subtitles are stored. This setting does not re-encode video: the source is already H.264 with AAC audio, supported directly by both containers, so changing it does not extend download time."
  },
  {
    "key": "settings.container",
    "it": "Contenitore",
    "en": "Container"
  },
  {
    "key": "settings.mkv.supports.all.tracks.and.is.the.default",
    "it": "MKV regge qualsiasi traccia ed è il predefinito. Scegli MP4 se guardi su dispositivi che non aprono gli MKV: parecchie smart TV non recenti, Chromecast e console.",
    "en": "MKV supports all tracks and is the default. Choose MP4 for devices that cannot open MKV files, such as many older smart TVs, Chromecast devices and consoles."
  },
  {
    "key": "settings.subtitles",
    "it": "Sottotitoli",
    "en": "Subtitles"
  },
  {
    "key": "settings.embedded.in.the.file",
    "it": "Integrati nel file",
    "en": "Embedded in the file"
  },
  {
    "key": "settings.separate.vtt.files",
    "it": "File .vtt separati",
    "en": "Separate .vtt files"
  },
  {
    "key": "settings.embedding.subtitles.in.mp4.converts.them.to",
    "it": "Integrati in un MP4 significa convertiti in",
    "en": "Embedding subtitles in MP4 converts them to"
  },
  {
    "key": "settings.losing.colors.bold.text.and.positioning.separate.files",
    "it": ", che perde colori, grassetti e posizionamento. I file separati evitano la conversione: Jellyfin li riconosce lo stesso, perché prendono il nome del video.",
    "en": ", losing colors, bold text and positioning. Separate files avoid conversion: Jellyfin still recognizes them because they share the video's name."
  },
  {
    "key": "settings.previously.downloaded.files.are.not.converted.they.keep",
    "it": "I file già scaricati non vengono convertiti: restano nel contenitore con cui sono stati creati, e continuano a contare come presenti, quindi non verranno riscaricati da soli. Se invece riscarichi un titolo di proposito, la copia nel vecchio contenitore viene sostituita, non affiancata.",
    "en": "Previously downloaded files are not converted: they keep their original container and are still counted as present, so they are not automatically downloaded again. If you deliberately download a title again, the old-container copy is replaced rather than kept alongside it."
  },
  {
    "key": "settings.save.format",
    "it": "Salva formato",
    "en": "Save format"
  },
  {
    "key": "settings.access.and.users",
    "it": "Accesso e utenti",
    "en": "Access and users"
  },
  {
    "key": "settings.the.panel.is.running.without.jellyfin.sign.in",
    "it": "Il pannello sta funzionando senza Jellyfin: nessun login, nessuna coda richieste, nessuna gestione utenti. Collegalo per abilitarli.",
    "en": "The panel is running without Jellyfin: sign-in, request queue and user management are disabled. Connect Jellyfin to enable them."
  },
  {
    "key": "settings.jellyfin.server.url",
    "it": "URL server Jellyfin",
    "en": "Jellyfin server URL"
  },
  {
    "key": "settings.a.jellyfin.administrator.account.is.required.the.password",
    "it": "Serve un account amministratore Jellyfin. La password non viene salvata.",
    "en": "A Jellyfin administrator account is required. The password is not stored."
  },
  {
    "key": "settings.connect.to.jellyfin",
    "it": "Collega a Jellyfin",
    "en": "Connect to Jellyfin"
  },
  {
    "key": "settings.connected.to",
    "it": "Collegato a",
    "en": "Connected to"
  },
  {
    "key": "settings.change.jellyfin.server",
    "it": "Cambia server Jellyfin",
    "en": "Change Jellyfin server"
  },
  {
    "key": "settings.anyone.using.the.panel.without.signing.in.will",
    "it": "Chi sta usando il pannello senza login dovrà accedere di nuovo dopo l'aggiornamento. Puntare a un server Jellyfin diverso renderà orfani gli utenti già importati.",
    "en": "Anyone using the panel without signing in will need to sign in after the change. Switching to a different Jellyfin server will leave previously imported users without matching accounts."
  },
  {
    "key": "settings.update.configuration",
    "it": "Aggiorna configurazione",
    "en": "Update configuration"
  },
  {
    "key": "settings.forward.panel.notifications.to.discord.telegram.ntfy.email",
    "it": "Inoltra le notifiche del pannello a Discord, Telegram, ntfy, email e altri servizi. Un canale è una URL Apprise, ad esempio",
    "en": "Forward panel notifications to Discord, Telegram, ntfy, email and other services. A channel is an Apprise URL, for example"
  },
  {
    "key": "settings.or",
    "it": "oppure",
    "en": "or"
  },
  {
    "key": "settings.full.list.of.services.and.url.formats",
    "it": "Elenco completo dei servizi e formato delle URL",
    "en": "Full list of services and URL formats"
  },
  {
    "key": "settings.these.channels.belong.to",
    "it": "Questi canali sono",
    "en": "These channels belong to"
  },
  {
    "key": "settings.the.panel.not.individual.users",
    "it": "del pannello, non del singolo utente",
    "en": "the panel, not individual users"
  },
  {
    "key": "settings.they.receive.events.from",
    "it": ": ricevono gli eventi di",
    "en": ": they receive events from"
  },
  {
    "key": "settings.everyone",
    "it": "tutti",
    "en": "everyone"
  },
  {
    "key": "settings.including.requests.and.downloads.started.by.others.treat",
    "it": ", comprese le richieste e i download avviati da altri. Pensali come un canale di amministrazione — un webhook su un canale Discord privato, non un gruppo condiviso con chi usa il pannello. Con «Tutti gli eventi» attivo su una libreria usata da più persone il volume può essere notevole: conviene scegliere solo gli eventi che ti interessano davvero.",
    "en": ", including requests and downloads started by others. Treat them as administration channels, such as a webhook in a private Discord channel rather than a shared group. With “All events” enabled in a shared library, notification volume can be high: select only the events you need."
  },
  {
    "key": "settings.name.e.g.discord.admin",
    "it": "Nome (es. Discord admin)",
    "en": "Name (e.g. Discord admin)"
  },
  {
    "key": "settings.add",
    "it": "Aggiungi",
    "en": "Add"
  },
  {
    "key": "settings.add.channel",
    "it": "Aggiungi canale",
    "en": "Add channel"
  },
  {
    "key": "settings.jellyfin.library",
    "it": "Libreria Jellyfin",
    "en": "Jellyfin library"
  },
  {
    "key": "settings.after.a.download.the.panel.can.ask.jellyfin",
    "it": "A fine download il pannello può chiedere a Jellyfin di aggiornare la libreria, invece di aspettare la sua scansione periodica.",
    "en": "After a download, the panel can ask Jellyfin to refresh the library instead of waiting for its scheduled scan."
  },
  {
    "key": "settings.refresh.the.library.after.downloading",
    "it": "Aggiorna la libreria a fine download",
    "en": "Refresh the library after downloading"
  },
  {
    "key": "settings.an.http.call.when.a.download.finishes.the",
    "it": "Una chiamata HTTP quando un download finisce. Nel corpo si possono usare",
    "en": "An HTTP call when a download finishes. The body supports"
  },
  {
    "key": "settings.leaving.the.body.empty.sends.json.containing.all",
    "it": ". Con il corpo vuoto viene inviato un JSON con tutti i campi.",
    "en": ". Leaving the body empty sends JSON containing all fields."
  },
  {
    "key": "settings.a.webhook.can.also.point.to.local.network",
    "it": "Un webhook può puntare anche a servizi della rete locale. Il pannello non restituisce mai la risposta ricevuta, solo l’esito: chi configura un hook sa se ha funzionato, non cosa ha risposto l’altro capo.",
    "en": "A webhook can also point to local network services. The panel returns only the outcome, never the response body: whoever configures a hook can tell whether it worked, but cannot read the remote response."
  },
  {
    "key": "settings.name.e.g.jellyfin",
    "it": "Nome (es. Jellyfin)",
    "en": "Name (e.g. Jellyfin)"
  },
  {
    "key": "settings.body.optional",
    "it": "Corpo, opzionale.",
    "en": "Body, optional."
  },
  {
    "key": "settings.add.webhook",
    "it": "Aggiungi webhook",
    "en": "Add webhook"
  },
  {
    "key": "settings.mcp.server",
    "it": "Server MCP",
    "en": "MCP server"
  },
  {
    "key": "settings.allows.an.ai.agent.claude.code.cursor.to",
    "it": "Permette a un agente AI (Claude Code, Cursor, …) di cercare titoli, avviare download, seguire serie e gestire le richieste. Il server risponde all’indirizzo del pannello, sotto",
    "en": "Allows an AI agent (Claude Code, Cursor, …) to search titles, start downloads, follow series and manage requests. The server is available at the panel address under"
  },
  {
    "key": "settings.no.extra.port.needs.to.be.opened",
    "it": ": nessuna porta in più da aprire.",
    "en": ": no extra port needs to be opened."
  },
  {
    "key": "settings.the.agent.acts.as.the.user.who.generated",
    "it": "L’agente agisce come l’utente che ha generato il token, con i suoi permessi: chi ha il token può fare tutto ciò che quell’utente può fare.",
    "en": "The agent acts as the user who generated the token, with that user's permissions: anyone with the token can do everything that user can do."
  },
  {
    "key": "settings.enable.the.mcp.server",
    "it": "Abilita il server MCP",
    "en": "Enable the MCP server"
  },
  {
    "key": "settings.copy.it.now.the.panel.stores.only.its",
    "it": "Copialo adesso: il pannello ne conserva solo l’impronta e non potrà mostrarlo di nuovo.",
    "en": "Copy it now: the panel stores only its fingerprint and cannot show it again."
  },
  {
    "key": "settings.generate.new.token",
    "it": "Genera nuovo token",
    "en": "Generate new token"
  },
  {
    "key": "settings.revoke",
    "it": "Revoca",
    "en": "Revoke"
  },
  {
    "key": "settings.client.configuration",
    "it": "Configurazione del client",
    "en": "Client configuration"
  },
  {
    "key": "settings.streamable.http.transport.with.the.token.in.the",
    "it": "Trasporto Streamable HTTP, con il token nell’header",
    "en": "Streamable HTTP transport, with the token in the header"
  },
  {
    "key": "settings.behind.a.reverse.proxy.use.the.panel.s",
    "it": ". Dietro un reverse proxy usa l’indirizzo pubblico del pannello, in HTTPS.",
    "en": ". Behind a reverse proxy, use the panel's public HTTPS address."
  },
  {
    "key": "settings.copy",
    "it": "Copia",
    "en": "Copy"
  },
  {
    "key": "settings.copy.token",
    "it": "Copia token",
    "en": "Copy token"
  },
  {
    "key": "settings.all.events",
    "it": "Tutti gli eventi",
    "en": "All events"
  },
  {
    "key": "settings.1.event",
    "it": "1 evento",
    "en": "1 event"
  },
  {
    "key": "settings.events",
    "it": "{count} eventi",
    "en": "{count} events"
  },
  {
    "key": "settings.choose.which.notifications.to.receive",
    "it": "Scegli quali notifiche ricevere",
    "en": "Choose which notifications to receive"
  },
  {
    "key": "settings.no.channels.configured",
    "it": "Nessun canale configurato.",
    "en": "No channels configured."
  },
  {
    "key": "settings.select.at.least.one.event.or.enable.all",
    "it": "Seleziona almeno un evento, oppure attiva «Tutti gli eventi».",
    "en": "Select at least one event, or enable “All events”."
  },
  {
    "key": "settings.events.updated",
    "it": "Eventi aggiornati.",
    "en": "Events updated."
  },
  {
    "key": "settings.failed.to.update.events",
    "it": "Errore aggiornamento eventi.",
    "en": "Failed to update events."
  },
  {
    "key": "settings.enter.a.name.and.url",
    "it": "Compila nome e URL.",
    "en": "Enter a name and URL."
  },
  {
    "key": "settings.saving",
    "it": "Salvataggio...",
    "en": "Saving..."
  },
  {
    "key": "settings.channel.added",
    "it": "Canale aggiunto.",
    "en": "Channel added."
  },
  {
    "key": "settings.failed.to.save",
    "it": "Errore salvataggio.",
    "en": "Failed to save."
  },
  {
    "key": "settings.channel.enabled",
    "it": "Canale attivo.",
    "en": "Channel enabled."
  },
  {
    "key": "settings.channel.disabled",
    "it": "Canale disattivato.",
    "en": "Channel disabled."
  },
  {
    "key": "settings.update.failed",
    "it": "Errore aggiornamento.",
    "en": "Update failed."
  },
  {
    "key": "settings.delete.channel",
    "it": "Eliminare il canale «{name}»?",
    "en": "Delete channel “{name}”?"
  },
  {
    "key": "settings.channel.deleted",
    "it": "Canale eliminato.",
    "en": "Channel deleted."
  },
  {
    "key": "settings.deletion.failed",
    "it": "Errore eliminazione.",
    "en": "Deletion failed."
  },
  {
    "key": "settings.sending.test.notification",
    "it": "Invio notifica di test...",
    "en": "Sending test notification..."
  },
  {
    "key": "settings.test.notification.sent",
    "it": "Notifica di test inviata.",
    "en": "Test notification sent."
  },
  {
    "key": "settings.test.notification.sent.189",
    "it": "Notifica di test inviata",
    "en": "Test notification sent"
  },
  {
    "key": "settings.sending.failed.check.the.url",
    "it": "Invio fallito: controlla la URL.",
    "en": "Sending failed: check the URL."
  },
  {
    "key": "settings.enter.the.url.and.administrator.username",
    "it": "Compila URL e utente amministratore.",
    "en": "Enter the URL and administrator username."
  },
  {
    "key": "settings.connection.failed",
    "it": "Collegamento fallito.",
    "en": "Connection failed."
  },
  {
    "key": "settings.connected.reloading",
    "it": "Collegato. Ricaricamento...",
    "en": "Connected. Reloading..."
  },
  {
    "key": "settings.network.error",
    "it": "Errore di rete.",
    "en": "Network error."
  },
  {
    "key": "settings.saved",
    "it": "Salvato.",
    "en": "Saved."
  },
  {
    "key": "settings.output.format.saved",
    "it": "Formato di uscita salvato",
    "en": "Output format saved"
  },
  {
    "key": "settings.verifying",
    "it": "Verifica in corso...",
    "en": "Verifying..."
  },
  {
    "key": "settings.hosts.saved",
    "it": "Host salvati",
    "en": "Hosts saved"
  },
  {
    "key": "settings.example",
    "it": "Esempio: {preview}",
    "en": "Example: {preview}"
  },
  {
    "key": "settings.naming.templates.saved",
    "it": "Schema dei nomi salvato",
    "en": "Naming templates saved"
  },
  {
    "key": "settings.restore.the.default.naming.templates",
    "it": "Ripristinare lo schema dei nomi predefinito?",
    "en": "Restore the default naming templates?"
  },
  {
    "key": "settings.defaults.restored.press.save.to.apply.them",
    "it": "Predefiniti ripristinati: premi Salva per applicarli.",
    "en": "Defaults restored: press Save to apply them."
  },
  {
    "key": "settings.completed",
    "it": "Completato",
    "en": "Completed"
  },
  {
    "key": "settings.failed",
    "it": "Fallito",
    "en": "Failed"
  },
  {
    "key": "settings.cancelled",
    "it": "Annullato",
    "en": "Cancelled"
  },
  {
    "key": "settings.jellyfin.is.not.connected.connect.it.in.access",
    "it": "Jellyfin non è collegato: collegalo da Accesso e utenti perché questa opzione abbia effetto.",
    "en": "Jellyfin is not connected: connect it in Access and users for this option to take effect."
  },
  {
    "key": "settings.setting.saved",
    "it": "Impostazione salvata",
    "en": "Setting saved"
  },
  {
    "key": "settings.all.outcomes",
    "it": "Tutti gli esiti",
    "en": "All outcomes"
  },
  {
    "key": "settings.no.webhooks.configured",
    "it": "Nessun webhook configurato.",
    "en": "No webhooks configured."
  },
  {
    "key": "settings.name.and.url.are.required",
    "it": "Nome e URL sono obbligatori.",
    "en": "Name and URL are required."
  },
  {
    "key": "settings.added",
    "it": "Aggiunto.",
    "en": "Added."
  },
  {
    "key": "settings.delete.this.webhook",
    "it": "Eliminare questo webhook?",
    "en": "Delete this webhook?"
  },
  {
    "key": "settings.sending.test.request",
    "it": "Invio chiamata di prova...",
    "en": "Sending test request..."
  },
  {
    "key": "settings.succeeded.http",
    "it": "Riuscito (HTTP {status}).",
    "en": "Succeeded (HTTP {status})."
  },
  {
    "key": "settings.webhook.reached",
    "it": "Webhook raggiunto",
    "en": "Webhook reached"
  },
  {
    "key": "settings.failed.http",
    "it": "Fallito (HTTP {status}).",
    "en": "Failed (HTTP {status})."
  },
  {
    "key": "settings.no.response",
    "it": "Nessuna risposta.",
    "en": "No response."
  },
  {
    "key": "settings.the.interval.must.be.between.1.and.1440",
    "it": "Intervallo tra 1 e 1440 minuti.",
    "en": "The interval must be between 1 and 1440 minutes."
  },
  {
    "key": "settings.automatic.application.adopts.the.discovered.domain.without.asking",
    "it": "Con l'applicazione automatica il pannello adotta il dominio trovato senza chiedere. Verranno accettati solo domini verificati e con un nome riconosciuto. Continuare?",
    "en": "Automatic application adopts the discovered domain without asking. Only verified domains with a recognized name will be accepted. Continue?"
  },
  {
    "key": "settings.settings.saved",
    "it": "Impostazioni salvate",
    "en": "Settings saved"
  },
  {
    "key": "settings.checking",
    "it": "Controllo in corso...",
    "en": "Checking..."
  },
  {
    "key": "settings.check.failed",
    "it": "Controllo fallito.",
    "en": "Check failed."
  },
  {
    "key": "settings.applied",
    "it": "Applicato {domain}.",
    "en": "Applied {domain}."
  },
  {
    "key": "settings.found.awaiting.application",
    "it": "Trovato {domain}: da applicare.",
    "en": "Found {domain}: awaiting application."
  },
  {
    "key": "settings.the.current.domain.is.responding",
    "it": "Il dominio attuale risponde.",
    "en": "The current domain is responding."
  },
  {
    "key": "settings.no.suitable.domain.rejected",
    "it": "Nessun dominio adottabile. Scartati: {rejected}",
    "en": "No suitable domain. Rejected: {rejected}"
  },
  {
    "key": "settings.no.domain.found",
    "it": "Nessun dominio trovato.",
    "en": "No domain found."
  },
  {
    "key": "settings.invalid.values",
    "it": "Valori non validi.",
    "en": "Invalid values."
  },
  {
    "key": "settings.performance.settings.saved",
    "it": "Performance salvate",
    "en": "Performance settings saved"
  },
  {
    "key": "settings.enter.a.domain",
    "it": "Inserisci un domain.",
    "en": "Enter a domain."
  },
  {
    "key": "settings.ok.version",
    "it": "OK — versione {version}",
    "en": "OK — version {version}"
  },
  {
    "key": "settings.domain.saved",
    "it": "Domain salvato",
    "en": "Domain saved"
  },
  {
    "key": "settings.network.error.233",
    "it": "Errore di rete",
    "en": "Network error"
  },
  {
    "key": "settings.no.libraries",
    "it": "Nessuna libreria.",
    "en": "No libraries."
  },
  {
    "key": "settings.type",
    "it": "Tipo...",
    "en": "Type..."
  },
  {
    "key": "settings.libraries.saved",
    "it": "Librerie salvate",
    "en": "Libraries saved"
  },
  {
    "key": "settings.no.token.generate.one.to.allow.an.agent",
    "it": "Nessun token: genera un token perché un agente possa collegarsi.",
    "en": "No token: generate one to allow an agent to connect."
  },
  {
    "key": "settings.the.token.is.no.longer.valid.its.owner",
    "it": "Il token non è più valido: chi l’ha generato non è più un utente attivo. Generane uno nuovo.",
    "en": "The token is no longer valid: its owner is no longer an active user. Generate a new token."
  },
  {
    "key": "settings.active.token.generated.by.the.agent.uses.their",
    "it": "Token attivo, generato da {owner}: l’agente agisce con i suoi permessi.",
    "en": "Active token generated by {owner}: the agent uses their permissions."
  },
  {
    "key": "settings.failed.to.load",
    "it": "Errore nel caricamento.",
    "en": "Failed to load."
  },
  {
    "key": "settings.generate.a.new.token.the.current.one.will",
    "it": "Generare un nuovo token? Quello attuale smette subito di funzionare: gli agenti che lo usano vanno riconfigurati.",
    "en": "Generate a new token? The current one will stop working immediately: agents using it must be reconfigured."
  },
  {
    "key": "settings.failed.to.generate.the.token",
    "it": "Errore nella generazione del token.",
    "en": "Failed to generate the token."
  },
  {
    "key": "settings.revoke.the.token.no.agent.will.be.able",
    "it": "Revocare il token? Nessun agente potrà collegarsi finché non ne generi uno nuovo.",
    "en": "Revoke the token? No agent will be able to connect until you generate a new one."
  },
  {
    "key": "settings.token.revoked",
    "it": "Token revocato",
    "en": "Token revoked"
  },
  {
    "key": "settings.failed.to.revoke.the.token",
    "it": "Errore nella revoca.",
    "en": "Failed to revoke the token."
  },
  {
    "key": "settings.copied",
    "it": "{item} copiato",
    "en": "{item} copied"
  },
  {
    "key": "settings.unable.to.copy.select.it.manually",
    "it": "Impossibile copiare: selezionalo a mano",
    "en": "Unable to copy: select it manually"
  },
  {
    "key": "settings.configuration",
    "it": "Configurazione",
    "en": "Configuration"
  },
  {
    "key": "settings.folder.saved.for.new.downloads",
    "it": "Cartella salvata per i nuovi download.",
    "en": "Folder saved for new downloads."
  },
  {
    "key": "settings.no.events.recorded",
    "it": "Nessun evento registrato.",
    "en": "No events recorded."
  },
  {
    "key": "settings.select.a.folder.in.the.windows.dialog",
    "it": "Seleziona una cartella nella finestra di Windows.",
    "en": "Select a folder in the Windows dialog."
  },
  {
    "key": "settings.folder.selected.press.save.folder.to.apply.it",
    "it": "Cartella selezionata. Premi Salva cartella per applicarla.",
    "en": "Folder selected. Press Save folder to apply it."
  },
  {
    "key": "settings.selection.cancelled",
    "it": "Selezione annullata.",
    "en": "Selection cancelled."
  },
  {
    "key": "settings.testing.encoders",
    "it": "Prova degli encoder in corso…",
    "en": "Testing encoders…"
  },
  {
    "key": "settings.available.verified",
    "it": "disponibile (verificato)",
    "en": "available (verified)"
  },
  {
    "key": "settings.unavailable",
    "it": "non disponibile",
    "en": "unavailable"
  },
  {
    "key": "settings.detection.failed",
    "it": "Rilevamento non riuscito",
    "en": "Detection failed"
  },
  {
    "key": "settings.verifying.and.saving",
    "it": "Verifica e salvataggio…",
    "en": "Verifying and saving…"
  },
  {
    "key": "settings.saved.applies.to.new.downloads",
    "it": "Salvato. Si applica ai nuovi download.",
    "en": "Saved. Applies to new downloads."
  }
];
  const extraCatalog = {
    'softether.title': ['SoftEther VPN (opzionale)', 'SoftEther VPN (optional)'],
    'softether.manual': ['Apri il Client Manager installato su questo PC per gestire manualmente la VPN. Il pannello non avvia servizi, driver, profili o connessioni. Dopo aver gestito la connessione in SoftEther, puoi riprovare manualmente l’operazione bloccata.', 'Open the Client Manager installed on this PC to manage the VPN manually. The panel does not start services, drivers, profiles or connections. After managing the connection in SoftEther, you can manually retry the blocked operation.'],
    'softether.open': ['Apri SoftEther', 'Open SoftEther'],
    'softether.install': ['Installa SoftEther', 'Install SoftEther'],
    'softether.startingInstall': ['Avvio dell’installer SoftEther… Conferma l’installazione nella finestra di Windows.', 'Starting the SoftEther installer… Confirm installation in the Windows dialog.'],
    'softether.installStarted': ['Installer SoftEther avviato. Completa l’installazione e premi Ricontrolla.', 'SoftEther installer started. Complete the installation and select Check again.'],
    'softether.installerMissing': ['Installer SoftEther incluso non trovato.', 'Bundled SoftEther installer not found.'],
    'softether.installFailed': ['Impossibile avviare l’installer SoftEther.', 'Unable to start the SoftEther installer.'],
    'softether.bundledHint': ['Setup locale incluso nell’installer del pannello.', 'Local setup included in the panel installer.'],
    'softether.blockDetectedSettings': ['La sorgente sembra bloccata. Puoi installare il setup SoftEther incluso qui sotto.', 'The source appears to be blocked. You can install the bundled SoftEther setup below.'],
    'softether.remoteHint': ['Questo pulsante scarica l’ultima release ufficiale da GitHub.', 'This button downloads the latest official release from GitHub.'],
    'softether.vpngate': ['Mostra server VPN Gate', 'Show VPN Gate servers'],
    'softether.vpngateChecking': ['Caricamento server VPN Gate…', 'Loading VPN Gate servers…'],
    'softether.vpngateEmpty': ['Nessun server VPN Gate disponibile.', 'No VPN Gate servers available.'],
    'softether.vpngateNotice': ['Server pubblici di terze parti: verifica il gestore prima dell’uso.', 'Public third-party servers: verify the operator before use.'],
    'softether.refresh': ['Ricontrolla', 'Check again'],
    'softether.checkUpdates': ['Controlla aggiornamenti VPN', 'Check VPN updates'],
    'softether.checkingUpdates': ['Controllo aggiornamenti SoftEther…', 'Checking SoftEther updates…'],
    'softether.updateAvailable': ['È disponibile SoftEther VPN Client v{version}. Apri la pagina ufficiale per scaricarlo.', 'SoftEther VPN Client v{version} is available. Open the official page to download it.'],
    'softether.updateUnavailable': ['Nessun aggiornamento SoftEther verificabile al momento.', 'No verifiable SoftEther update is available at the moment.'],
    'softether.downloadUpdate': ['Scarica e installa aggiornamento', 'Download and install update'],
    'softether.startingDownload': ['Download dell’installer SoftEther in corso…', 'Downloading the SoftEther installer…'],
    'softether.downloadStarted': ['Installer scaricato e avviato. Completa l’installazione nella finestra di Windows.', 'Installer downloaded and started. Complete the installation in the Windows dialog.'],
    'softether.downloadFailed': ['Download dell’installer SoftEther non riuscito.', 'SoftEther installer download failed.'],
    'softether.invalidAsset': ['Asset SoftEther non valido.', 'Invalid SoftEther asset.'],
    'softether.blockDetected': ['La sorgente sembra bloccata. Puoi verificare e installare SoftEther dalle Impostazioni.', 'The source appears to be blocked. You can check and install SoftEther from Settings.'],
    'softether.openSettings': ['Apri Impostazioni', 'Open Settings'],
    'softether.checking': ['Verifica del Client Manager…', 'Checking for Client Manager…'],
    'softether.ready': ['Client Manager disponibile su questo PC.', 'Client Manager is available on this PC.'],
    'softether.missing': ['Client Manager non trovato nelle cartelle Program Files. Installa il client ufficiale, poi premi Ricontrolla.', 'Client Manager was not found in Program Files. Install the official client, then select Check again.'],
    'softether.unsupported': ['Il Client Manager è disponibile solo su Windows.', 'Client Manager is only available on Windows.'],
    'softether.opened': ['Avvio del Client Manager richiesto. Scegli e gestisci la connessione nella sua finestra.', 'Client Manager launch requested. Choose and manage the connection in its window.'],
    'softether.localOnly': ['Apri le Impostazioni dal pannello locale (localhost o 127.0.0.1) e premi Ricontrolla.', 'Open Settings from the local panel (localhost or 127.0.0.1) and select Check again.'],
    'softether.detectionFailed': ['Impossibile verificare l’installazione di SoftEther.', 'Unable to check the SoftEther installation.'],
    'softether.openFailed': ['Impossibile aprire il Client Manager. Verifica l’installazione e i permessi di Windows.', 'Unable to open Client Manager. Check the installation and Windows permissions.'],
    'softether.noParameters': ['La richiesta non può contenere percorsi o parametri.', 'The request cannot contain paths or parameters.'],
    'softether.denied': ['Non hai il permesso di aprire SoftEther dalle Impostazioni.', 'You do not have permission to open SoftEther from Settings.'],
    'softether.authentication': ['Accedi al pannello per usare questa funzione.', 'Sign in to the panel to use this feature.'],
    'softether.requestFailed': ['Impossibile contattare il pannello. Premi Ricontrolla per riprovare.', 'Unable to reach the panel. Select Check again to retry.'],
    'settings.encoder.status': ['{label} · {codec}: {status}', '{label} · {codec}: {status}'],
    'settings.notification.created': ['Nuova richiesta', 'New request'],
    'settings.notification.joined': ['Richiesta già presente', 'Request already exists'],
    'settings.notification.approved': ['Richiesta approvata', 'Request approved'],
    'settings.notification.denied': ['Richiesta rifiutata', 'Request denied'],
    'settings.notification.downloading': ['Richiesta in download', 'Request downloading'],
    'settings.notification.completed': ['Richiesta completata', 'Request completed'],
    'settings.notification.failed': ['Richiesta fallita', 'Request failed'],
    'settings.notification.attention': ['Richiesta da verificare', 'Request needs attention'],
    'settings.notification.available': ['Già in libreria', 'Already in library'],
    'settings.notification.downloadCompleted': ['Download completato', 'Download completed'],
    'settings.notification.downloadFailed': ['Download fallito', 'Download failed'],
    'settings.notification.batchCompleted': ['Stagione o serie completata', 'Season or series completed'],
    'settings.notification.batchFailed': ['Stagione o serie fallita', 'Season or series failed'],
    'settings.notification.watchApproval': ['Serie seguita da approvare', 'Followed series awaiting approval'],
    'settings.notification.watchApproved': ['Serie approvata', 'Series approved'],
    'settings.notification.watchLanguages': ['Lingue diverse su una serie seguita', 'Different languages on a followed series'],
    'settings.notification.domainFound': ['Nuovo dominio trovato', 'New domain found'],
    'settings.notification.domainApplied': ['Dominio aggiornato', 'Domain updated'],
    'settings.notification.hookFailed': ['Hook fallito', 'Hook failed'],
    'settings.notification.requests': ['Richieste', 'Requests'],
    'settings.notification.directDownloads': ['Download diretti', 'Direct downloads'],
    'settings.notification.watches': ['Serie seguite', 'Followed series'],
    'login.invalidCredentials': ['Credenziali Jellyfin non valide', 'Invalid Jellyfin credentials'],
    'login.adminRequired': ['Solo un amministratore Jellyfin può configurare il pannello', 'Only a Jellyfin administrator can configure the panel'],
    'login.configured': ['Il pannello è già configurato', 'The panel is already configured'],
    'login.denied': ['Permesso negato', 'Permission denied'],
    'login.connected': ['Il pannello è già stato collegato a Jellyfin', 'The panel is already connected to Jellyfin'],
    'login.notConfigured': ['Pannello non ancora configurato', 'The panel has not been configured yet'],
    'login.disabled': ['Account disabilitato sul pannello', 'Account disabled on the panel'],
    'login.expired': ['Sessione Jellyfin non valida o scaduta', 'Invalid or expired Jellyfin session'],
    'login.rateLimited': ['Troppi tentativi falliti. Riprova tra {seconds} secondi.', 'Too many failed attempts. Try again in {seconds} seconds.'],
    'login.apiKeyFailed': ['Creazione API key fallita: {reason}', 'API key creation failed: {reason}'],
    'settings.folderUnavailable': ['Cartella download non utilizzabile: {reason}', 'Download folder cannot be used: {reason}'],
    'settings.windowsOnly': ['La scelta cartella nativa è disponibile solo su Windows.', 'The native folder picker is available only on Windows.'],
    'settings.domainEmpty': ['Il dominio non può essere vuoto', 'Domain cannot be empty'],
    'settings.genericError': ['Errore', 'Error'],
    'settings.backend.validation': ['Valore non valido: {reason}', 'Invalid value: {reason}'],
    'login.notImported': ['Utente Jellyfin non abilitato sul pannello. Chiedi a un amministratore di importarlo.', 'Jellyfin user not enabled on the panel. Ask an administrator to import it.'],
    'login.urlMissing': ['URL Jellyfin mancante', 'Jellyfin URL is missing'],
    'login.urlScheme': ["L'URL Jellyfin deve iniziare con http:// o https://", 'The Jellyfin URL must start with http:// or https://'],
    'login.urlInvalid': ['URL Jellyfin non valido', 'Invalid Jellyfin URL'],
    'login.unreachable': ['Server Jellyfin non raggiungibile', 'Jellyfin server is unreachable'],
    'login.unreachableReason': ['Server Jellyfin non raggiungibile: {reason}', 'Jellyfin server is unreachable: {reason}'],
    'login.httpError': ['Jellyfin ha risposto HTTP {status}', 'Jellyfin returned HTTP {status}'],
    'login.invalidResponse': ["Risposta non valida dal server Jellyfin (l'URL punta davvero a Jellyfin?)", 'Invalid response from the Jellyfin server (does the URL point to Jellyfin?)'],
    'login.incompleteResponse': ['Risposta di autenticazione Jellyfin incompleta', 'Incomplete Jellyfin authentication response'],
    'login.missingApiKey': ["API key creata ma non ritrovata nell'elenco di Jellyfin", 'API key created but not found in the Jellyfin list'],
    'settings.backend.absolutePath': ['Indica un percorso assoluto per la cartella download.', 'Enter an absolute path for the download folder.'],
    'settings.backend.unknownSlot': ['Sezione sconosciuta: {slot}', 'Unknown section: {slot}'],
    'settings.backend.emptyTemplate': ['Il modello non può essere vuoto', 'The template cannot be empty'],
    'settings.backend.pathTemplate': ['Il modello non può contenere / o \\: è un solo nome, non un percorso', 'The template cannot contain / or \\: it is a single name, not a path'],
    'settings.backend.dotsTemplate': ['Il modello non può contenere ..', 'The template cannot contain ..'],
    'settings.backend.dotEdges': ['Il modello non può iniziare o finire con un punto', 'The template cannot start or end with a dot'],
    'settings.backend.brackets': ['Parentesi quadre non bilanciate', 'Unbalanced square brackets'],
    'settings.backend.nestedGroups': ['I gruppi opzionali non possono essere annidati', 'Optional groups cannot be nested'],
    'settings.backend.invalidTokens': ['Segnaposto non validi qui: {tokens}. Disponibili: {available}', 'Invalid placeholders here: {tokens}. Available: {available}'],
    'settings.backend.emptyPreview': ['Con valori di esempio il modello produce un nome vuoto', 'The template produces an empty name with example values'],
    'settings.backend.invalidFilename': ['Il modello contiene caratteri non ammessi in un nome di file', 'The template contains characters that are not allowed in a file name'],
    'settings.backend.channelRequired': ['Nome e URL sono obbligatori', 'Name and URL are required'],
    'settings.backend.appriseUrl': ['URL Apprise non riconosciuta', 'Unrecognized Apprise URL'],
    'settings.backend.channelMissing': ['Canale non trovato', 'Channel not found'],
    'settings.backend.hookMissing': ['Hook non trovato', 'Hook not found'],
    'settings.backend.hookScheme': ["L'URL deve iniziare con http:// o https://", 'The URL must start with http:// or https://'],
    'settings.backend.unknownEvent': ['Evento sconosciuto: {events}', 'Unknown event: {events}'],
    'settings.backend.method': ['Metodo non ammesso: {methods}', 'Method not allowed: {methods}'],
    'settings.backend.events': ['Eventi non validi: {events}. Ammessi: {allowed}', 'Invalid events: {events}. Allowed: {allowed}'],
    'settings.backend.domainLevel': ['non è un dominio di secondo livello', 'not a second-level domain'],
    'settings.backend.domainName': ['nome «{name}» non riconosciuto', 'unrecognized name “{name}”'],
    'settings.backend.domainAddress': ['risolve a un indirizzo non pubblico', 'resolves to a non-public address'],
    'settings.backend.domainUrl': ['Usa un URL HTTP o HTTPS', 'Use an HTTP or HTTPS URL'],
    'settings.backend.validDomain': ['Inserisci un dominio valido o un URL HTTP(S)', 'Enter a valid domain or HTTP(S) URL'],
  };
  Object.entries(extraCatalog).forEach(([key, [it, en]]) => pageCatalog.push({key, it, en}));
  const pageKeys = new Map();
  const renderedMessages = new Map();
  pageCatalog.forEach(({key, it, en}) => {
    translations.it[key] = it;
    translations.en[key] = en;
    pageKeys.set(it, key);
    pageKeys.set(en, key);
  });

  // Only exact, known interface messages are translated. User input, paths,
  // technical logs and unknown server details are preserved.
  function message(text) {
    if (typeof text === 'string' && text.startsWith('Value error, ')) {
      return translate('settings.backend.validation', {reason: message(text.slice(13))});
    }
    return resolve(describe(text));
  }

  function describe(text) {
    if (renderedMessages.has(text)) return renderedMessages.get(text);
    const key = pageKeys.get(text);
    if (key) return {key};
    // Parameterized server details retain their actual values, including
    // rejected paths and diagnostic reasons. No API contract is changed.
    for (const entry of pageCatalog) {
      if (!entry.key.startsWith('login.') && !entry.key.startsWith('settings.backend.') && entry.key !== 'settings.folderUnavailable') continue;
      const names = [];
      const pattern = entry.it.split(/(\{\w+\})/).map(part => {
        if (/^\{\w+\}$/.test(part)) { names.push(part.slice(1, -1)); return '([\\s\\S]*?)'; }
        return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      }).join('');
      if (!names.length) continue;
      const match = String(text).match(new RegExp('^' + pattern + '$'));
      if (match) return {key: entry.key, params: Object.fromEntries(names.map((name, index) => [name, match[index + 1]]))};
    }
    return {text};
  }

  function resolve(descriptor) {
    if (!descriptor.key) return descriptor.text;
    let params = descriptor.params;
    if (descriptor.key === 'settings.encoder.status') {
      params = {...params, status: message(params.status), label: message(params.label)};
    }
    if (descriptor.key === 'settings.folderUnavailable' || descriptor.key === 'settings.backend.validation') {
      params = {...params, reason: message(params.reason)};
    }
    return translate(descriptor.key, params);
  }

  function preferredLanguage() {
    let saved;
    try { saved = localStorage.getItem('sc-panel-language'); } catch (_) { /* private browsing */ }
    if (saved === 'it' || saved === 'en') return saved;
    return (navigator.language || '').toLowerCase().startsWith('it') ? 'it' : 'en';
  }

  function applyLanguage(language, persist) {
    const lang = translations[language] ? language : 'it';
    if (persist) {
      try { localStorage.setItem('sc-panel-language', lang); } catch (_) { /* session-only choice */ }
    }
    activeLanguage = lang;
    document.documentElement.lang = lang;
    localize();
    ['title', 'aria-label'].forEach(attribute => {
      document.querySelectorAll('[data-i18n-' + attribute + ']').forEach(node => {
        node.setAttribute(attribute, translate(node.getAttribute('data-i18n-' + attribute)));
      });
    });
    document.querySelectorAll('[data-language]').forEach((button) => {
      button.classList.toggle('active', button.dataset.language === lang);
      button.setAttribute('aria-pressed', button.dataset.language === lang ? 'true' : 'false');
    });
    window.dispatchEvent(new CustomEvent('sc:language-changed', {detail: {language: lang}}));
  }

  function translate(key, params) {
    const lang = activeLanguage || preferredLanguage();
    const pluralKey = params && typeof params.count === 'number'
      ? key + '.' + new Intl.PluralRules(lang).select(params.count) : key;
    let value = translations[lang]?.[pluralKey] || translations[lang]?.[key] || translations.it[pluralKey] || translations.it[key] || key;
    Object.entries(params || {}).forEach(([name, replacement]) => {
      value = value.replaceAll(`{${name}}`, () => String(replacement));
    });
    // Bound the cache: it supports in-place translation of current feedback,
    // not a history of every message produced during a long session.
    if (renderedMessages.size >= 512) renderedMessages.delete(renderedMessages.keys().next().value);
    renderedMessages.set(value, {key, params: params ? {...params} : undefined});
    return value;
  }

  function localize(root = document) {
    root.querySelectorAll('[data-i18n]').forEach(node => {
      node.textContent = translate(node.dataset.i18n);
    });
    ['title', 'aria-label', 'placeholder'].forEach(attribute => {
      root.querySelectorAll('[data-i18n-' + attribute + ']').forEach(node => {
        node.setAttribute(attribute, translate(node.getAttribute('data-i18n-' + attribute)));
      });
    });
  }

  window.t = translate;
  window.scLanguage = {apply: (language) => applyLanguage(language, true), current: () => activeLanguage || preferredLanguage(), t: translate, localize, message, describe, resolve};
  document.addEventListener('DOMContentLoaded', () => {
    applyLanguage(preferredLanguage(), false);
    document.querySelectorAll('[data-language]').forEach((button) => {
      button.addEventListener('click', () => applyLanguage(button.dataset.language, true));
    });
  });
})();
