---
name: Bug report
about: Segnala un problema in una delle funzioni dell'app
title: "[BUG]"
labels: bug
assignees: EdoardoFiore

---

**Descrizione del bug**
Quando cerco di scaricare ....

**Per riprodurre il problema**
Come riprodurre il problema:
1. Cercare '...'
2. Cliccare '....'
3. Riprodurre film/anime/serie '....'
4. Vedere log

**Versione e deploy**
Senza queste informazioni è quasi impossibile capire quale codice stia girando.

- Immagine e tag (es. `ghcr.io/edoardofiore/streamingcommunity-downloader:latest`, `:1.0.0`), oppure
  commit se avviato da sorgente:
- Come è avviato: Docker Compose / Docker / da sorgente:
- Come apri il pannello: `localhost` / indirizzo di loopback / nome in `ALLOWED_HOSTS`:
- Dietro un reverse proxy? Se sì, quale, e `TRUST_PROXY_HEADERS`:

**Log**
Output rilevante del container (`docker compose logs web`) o della console del
pannello. Rimuovi token, URL di webhook e canali di notifica prima di incollarlo.
