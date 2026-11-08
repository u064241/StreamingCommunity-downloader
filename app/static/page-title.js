// The title page.
//
// Replaces the detail / episode / anime modals: one address, one screen,
// everything a download decision needs. It rebuilds itself from the URL, so a
// reload or a pasted link works and there is nothing to "reopen".
//
// Downloads are not reimplemented here. The page fills _epCtx / _animeCtx and
// calls the existing startEpisodeDownload / downloadWholeSeason /
// startFilmDownload / startAnimeDownload, which already know how to branch
// between a download and a request.

let _tp = null;      // current title state
let _tpToken = 0;    // guards against a slow response painting over a newer title

function titleHash(item) {
  const kind = item.type === 'anime' ? 'anime' : (item.type === 'movie' ? 'movie' : 'tv');
  const source = item.source || (kind === 'anime' ? 'animeunity' : 'streamingcommunity');
  return hashFor('detail', [kind, item.id, item.slug || ''], {src: source});
}

// From a result card or a shelf.
function openTitle(idx) {
  const item = _searchResults[idx];
  if (!item) return;
  _tpSeed = item;                     // spares the first render a round trip
  location.hash = titleHash(item);
}
let _tpSeed = null;

async function loadTitlePage(route) {
  const token = ++_tpToken;
  // Every call below carries the site version, and a pasted link can arrive
  // before the boot knows it — it used to be sent empty and the endpoint
  // answered "field required". ensureDomain() is now shared with the search,
  // which needs the same guarantee for the same reason.
  const source = route.source || _tpSeed?.source || (route.type === 'anime' ? 'animeunity' : 'streamingcommunity');
  if (source === 'streamingcommunity') await ensureDomain();
  if (token !== _tpToken) return;
  const seed = _tpSeed; _tpSeed = null;

  _tp = {
    ...route,
    source,
    name: seed?.name || '',
    // Not for a series: its search record has no release_date, and the
    // last_air_date itemYear() falls back to is the latest season's - the
    // folder was named after it (issue #21). The title page's own year
    // arrives with the metadata.
    year: seed && route.type !== 'tv' ? itemYear(seed) : null,
    poster: seed?.poster || null,
    score: seed?.score ? parseFloat(seed.score).toFixed(1) : null,
    age: seed?.age || null,
    seasonsCount: seed?.seasons_count || 0,
    comingSoon: !!seed?.coming_soon,
    // The site's own season numbers, from the metadata. A count is not enough:
    // Bake Off Italia has 1-4 and 10-14, nine seasons (#40).
    seasonNumbers: [],
    episodesCount: seed?.episodes_count || 0,
    animeType: seed?.media_type || null,
    plot: seed?.plot || null,
    genres: seed?.genres || [],
    // AnimeUnity has no metadata endpoint - it never needed one, because its
    // search record already carries the plot, the genres, the banner and the
    // studio. So the page builds its own "meta" from what the card was
    // holding rather than asking for anything.
    meta: seed && seed.type === 'anime' ? {
      backdrop: seed.backdrop || null,
      plot: seed.plot || null,
      genres: seed.genres || [],
      rating: seed.score ? parseFloat(seed.score).toFixed(1) : null,
      status: seed.status || null,
      original_name: seed.original_name || null,
      studio: seed.studio || null,
      season: seed.season || null,
      cast: [], directors: [],
    } : null,
    // From the address when a link named one, so a reload of S03 is S03.
    season: route.season || 1,
    episodes: [],
    audio: [], subs: [], tracksLoaded: false, tracksError: null,
    // Raw value of the datetime input, kept so a re-render does not clear
    // what the user typed; scheduledAt is its ISO form, or null for "now".
    scheduledRaw: '', scheduledAt: null,
    // Batches skip episodes whose file is already in the library (#30). On by
    // default: fetching a season again over files already there is the
    // exception, and the per-episode button still forces one.
    onlyMissing: true,
    tab: null,
  };

  // Reached by pasted link: there is no card to borrow from, and AnimeUnity
  // has nothing to ask. The slug carried in the id is the only name available.
  if (!_tp.name && route.type === 'anime') {
    const slug = String(route.id).split('-').slice(1).join('-') || route.slug;
    _tp.name = slug ? slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Anime';
  }

  showPage('detail');
  _tpRender();
  // Which library folder holds this title - for whoever manages the library.
  libRenderTitleCard(_tp, _tp.name);

  let metaReady = Promise.resolve();
  if (route.type !== 'anime') {
    const q = { slug: route.slug, version: currentVersion || '', source: _tp.source };
    // Metadata and the track list are independent: one reads the title page,
    // the other reaches the stream host. Neither waits on the other.
    metaReady = api.get(`/api/metadata/${route.type}/${route.id}`, q)
      .then(meta => { if (token === _tpToken) { _tpApplyMeta(meta); _tpRender(); } })
      .catch(() => {});

    metaReady.then(() => {
      if (token !== _tpToken || _tp.comingSoon) return {audio: [], subtitles: []};
      // /search/languages still belongs to StreamingCommunity. A provider
      // without a track adapter must not accidentally query that source.
      if (_tp.source !== 'streamingcommunity') throw new Error(t('title.sourceUnavailable'));
      return api.get(`/api/search/languages/${route.id}`,
            { type: route.type, slug: route.slug, version: currentVersion || '' });
    })
      .then(info => {
        if (token !== _tpToken) return;
        const audio = dedupeLangs(info.audio);
        const subs = dedupeLangs(info.subtitles);
        const preferred = preferredSubSelection(subs);
        const defaultAudio = audio.find(c => ['ita', 'it'].includes(c)) || audio[0];
        _tp.audio = audio.map(c => ({ code: c, on: c === defaultAudio }));
        _tp.subs  = subs.map(c => ({ code: c, on: preferred.has(c) }));
        _tp.tracksLoaded = true;
        _tpRender();
      })
      .catch(e => {
        if (token !== _tpToken) return;
        // Swallowing this used to leave no chips, no reason, and a download
        // button that looked perfectly fine.
        _tp.tracksError = e.message || t('title.sourceUnavailable');
        _tp.tracksLoaded = true;
        _tpRender();
      });
  }

  if (route.type === 'tv') {
    // The episode list waits for the year: the in-library marks and every
    // download built from _epCtx are named after it.
    await metaReady;
    if (token !== _tpToken) return;
    // The address may name a season (#s=3); one the title does not have, or
    // none, opens the first it does - which is not always 1.
    const seasons = _tpSeasons();
    await tpLoadSeason(seasons.includes(route.season) ? route.season : (seasons[0] || 1), token);
  }
  else if (route.type === 'anime') await _tpLoadAnime(token);
}

function _tpApplyMeta(meta) {
  if (!meta) return;
  _tp.meta = meta;
  if (typeof meta.coming_soon === 'boolean') _tp.comingSoon = meta.coming_soon;
  _tp.name = _tp.name || meta.name || '';
  _tp.plot = meta.plot || _tp.plot;
  _tp.genres = meta.genres?.length ? meta.genres : _tp.genres;
  _tp.score = _tp.score || (meta.rating ? String(meta.rating) : null);
  _tp.poster = _tp.poster || meta.poster;
  _tp.age = _tp.age ?? meta.age;
  // Authoritative over the search card's guess. A series with no year keeps
  // none rather than the card's, which is the wrong one (issue #21).
  if (meta.year || _tp.type === 'tv') _tp.year = meta.year || null;
  if (!_tp.seasonsCount && meta.seasons_count) _tp.seasonsCount = meta.seasons_count;
  if (meta.season_numbers?.length) {
    _tp.seasonNumbers = meta.season_numbers;
    _tp.seasonsCount = meta.season_numbers.length;
  }
}

function _tpSeasons() {
  return _tp.seasonNumbers.length
    ? _tp.seasonNumbers
    : Array.from({ length: _tp.seasonsCount }, (_, i) => i + 1);
}

// ── Episodes ──────────────────────────────────────────────────────────────────

async function tpLoadSeason(n, token = _tpToken) {
  if (_tp.comingSoon) { _tp.episodes = []; _tpRenderEpisodes(); return; }
  _tp.season = n;
  syncHash('detail');
  _tp.episodes = null;                       // renders the loading state
  _tpRenderEpisodes();
  try {
    const tk = await api.get(`/api/tv/${_tp.id}/token`, {source: _tp.source});
    const eps = await api.get(`/api/tv/${_tp.id}/seasons/${n}/episodes`, {
      slug: _tp.slug, version: currentVersion || '', token: tk.token,
      title: _tp.name, year: _tp.year || '',
      source: _tp.source,
    });
    if (token !== _tpToken) return;
    _tp.episodes = eps;
    _tpSyncEpCtx(tk.token);
  } catch (e) {
    if (token !== _tpToken) return;
    _tp.episodes = [];
    _tp.episodesError = e.message || 'Errore';
  }
  _tpRenderEpisodes();
  _tpRenderFacts();      // the episode count is per season
  _tpRenderHero();       // and so is the chip
}

async function _tpLoadAnime(token) {
  _tp.episodes = null;
  _tpRenderEpisodes();
  try {
    const eps = await api.get(`/api/anime/${_tp.id}/episodes`, {source: _tp.source});
    if (token !== _tpToken) return;
    // Spread, not a projection: startAnimeDownload reads episode.number and
    // posts the whole record back. Narrowing it to {id, n} here sent the API
    // a stripped object and labelled every job "E undefined". `n` is added
    // only so the shared row renderer has the field it expects.
    _tp.episodes = eps.map(e => ({ ...e, n: e.number ?? e.n }));
    _animeCtx = {
      source: _tp.source,
      animeId: _tp.id, animeName: _tp.name, animeType: _tp.animeType || 'tv',
      animeYear: _tp.year, episodes: _tp.episodes,
      audioLangs: _tpPicked('audio'), subLangs: _tpPicked('subs'), scheduledAt: _tp.scheduledAt,
    };
  } catch (e) {
    if (token !== _tpToken) return;
    _tp.episodes = [];
    _tp.episodesError = e.message || 'Errore';
  }
  _tpRenderEpisodes();
}

// The download functions read these globals. Keeping them in sync here means
// the page does not duplicate the download-vs-request branching.
function _tpSyncEpCtx(token) {
  _epCtx = {
    source: _tp.source,
    tvId: _tp.id, tvName: _tp.name, slug: _tp.slug, year: _tp.year,
    scheduledAt: _tp.scheduledAt, token, episodes: _tp.episodes || [],
    currentSeason: _tp.season, poster: _tp.poster,
    // downloadWholeSeries reads this off the context, and said "tutte le
    // undefined stagioni" without it.
    seasonsCount: _tp.seasonsCount,
    audioLangs: _tpPicked('audio'), subLangs: _tpPicked('subs'),
  };
}

function _tpPicked(which) {
  const picked = (_tp[which] || []).filter(t => t.on).map(t => t.code);
  return which === 'audio' ? (picked.length ? picked : (_tp.audio?.length ? [_tp.audio[0].code] : ['ita'])) : picked;
}

// ── Tracks ────────────────────────────────────────────────────────────────────

// Empty means "now", which is what the download endpoints already expect.
function tpSetSchedule(value) {
  _tp.scheduledRaw = value || '';
  _tp.scheduledAt = value ? new Date(value).toISOString() : null;
  if (_tp.type === 'anime') { if (_animeCtx) _animeCtx.scheduledAt = _tp.scheduledAt; }
  else if (_epCtx) _epCtx.scheduledAt = _tp.scheduledAt;
  _tpRenderEpisodes();     // the batch buttons say "Programma" once a time is set
}

function tpToggleTrack(which, code) {
  const t = (_tp[which] || []).find(x => x.code === code);
  if (!t) return;
  if (which === 'audio' && t.on && (_tp.audio || []).filter(x => x.on).length === 1) return;
  t.on = !t.on;
  if (_tp.type === 'anime') { if (_animeCtx) { _animeCtx.audioLangs = _tpPicked('audio'); _animeCtx.subLangs = _tpPicked('subs'); } }
  else if (_epCtx) { _epCtx.audioLangs = _tpPicked('audio'); _epCtx.subLangs = _tpPicked('subs'); }
  _tpRenderTracks();
}

function tpSelectTracks(which, enabled) {
  if (!['audio', 'subs'].includes(which) || (which === 'audio' && !enabled)) return;
  (_tp[which] || []).forEach(t => { t.on = enabled; });
  if (_epCtx) { _epCtx.audioLangs = _tpPicked('audio'); _epCtx.subLangs = _tpPicked('subs'); }
  _tpRenderTracks();
}

// ── Tabs ──────────────────────────────────────────────────────────────────────

function _tpTabs() {
  const tabs = [];
  if (_tp.type !== 'movie') tabs.push(['episodes', t('title.episodes')]);
  tabs.push(['about', t('title.about')]);
  if (_tp.meta?.cast?.length || _tp.meta?.directors?.length) tabs.push(['cast', 'Cast']);
  // Anime tracks are chosen by the source, not here.
  if (_tp.type !== 'anime') tabs.push(['tracks', t('title.tracks')]);
  return tabs;
}

function tpSetTab(name) {
  _tp.tab = name;
  for (const [key] of [['episodes'], ['about'], ['cast'], ['tracks']]) {
    const pane = document.getElementById(`th-pane-${key}`);
    if (pane) pane.hidden = key !== name;
  }
  document.querySelectorAll('.th-tab').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === name));
}

// ── Render ────────────────────────────────────────────────────────────────────

function _tpRender() {
  if (!_tp) return;
  _tpRenderHero();
  _tpRenderTabsBar();
  _tpRenderAbout();
  _tpRenderCast();
  _tpRenderTracks();
  _tpRenderFacts();
  _tpRenderTrailer();
  _tpRenderEpisodes();
  _tpRenderRelated();
  if (!_tp.tab || !_tpTabs().some(([k]) => k === _tp.tab)) {
    tpSetTab(_tpTabs()[0][0]);
  }
}

function _tpRenderRelated() {
  const section = document.getElementById('th-related');
  const rail = document.getElementById('th-related-rail');
  if (!section || !rail || !_tp) return;
  const related = (_searchResults || []).filter(item =>
    String(item.id) !== String(_tp.id) && item.type === _tp.type)
    .slice(0, 8).map(item => ({...item, _searchIndex: _searchResults.indexOf(item)}));
  if (!related.length) { section.hidden = true; rail.replaceChildren(); return; }
  section.hidden = false;
  rail.replaceChildren();
  renderResultCards(related, rail, 0, 'shelf-item');
  _wireShelfArrows(section);
}

function _tpRenderHero() {
  // Set straight away rather than behind a preload that flips a class on
  // load: the same pattern on the episode stills left them invisible whenever
  // the load event was missed. A backdrop that 404s simply paints nothing
  // behind the gradient, which is what "no backdrop" looks like anyway.
  const art = document.getElementById('th-art');
  const src = _tp.meta?.backdrop;
  if (src) {
    art.style.backgroundImage = `url("${encodeURI(src)}")`;
    art.classList.add('is-loaded');
  } else {
    art.style.backgroundImage = '';
    art.classList.remove('is-loaded');
  }

  const kindLabel = _tp.type === 'movie' ? t('catalog.movie') : (_tp.type === 'anime' ? 'Anime' : t('catalog.tv'));
  // A back control, not just a trail. The trail alone was a row of grey text
  // against artwork and read as a caption rather than as the way out.
  document.getElementById('th-crumbs').innerHTML =
    `<button class="th-back" data-action="tp:crumb-search" title="${escapeHtml(t('title.back'))}">
       <i class="ti ti-arrow-left"></i>${t('search.button')}
     </button>` +
    `<span class="sep">›</span><button class="th-crumb-link" data-action="tp:crumb-kind" data-kind="${_tp.type}" type="button">${escapeHtml(kindLabel)}</button>` +
    `<span class="sep">›</span><span class="th-crumb-here">${escapeHtml(_tp.name || '—')}</span>` +
    `<select class="th-title-switcher" id="th-title-switcher" aria-label="${escapeHtml(t('title.switch'))}">
       <option value="">${escapeHtml(t('title.switch'))}</option>
     </select>`;
  const switcher = document.getElementById('th-title-switcher');
  (_searchResults || []).filter(item => String(item.id) !== String(_tp.id) && item.type === _tp.type)
    .slice(0, 20).forEach(item => {
      const option = document.createElement('option');
      option.value = String(_searchResults.indexOf(item));
      option.textContent = item.name || t('title.item');
      switcher.appendChild(option);
    });
  switcher.addEventListener('change', () => {
    const item = _searchResults[Number(switcher.value)];
    if (item) openTitle(Number(switcher.value));
  });

  document.getElementById('th-name').textContent = _tp.name || '—';

  const chips = [['ti-movie', kindLabel]];
  if (_tp.year) chips.push(['ti-calendar', _tp.year]);
  if (_tp.type === 'tv' && _tp.seasonsCount)
    chips.push(['ti-layout-grid', t('title.seasonCount', {count: _tp.seasonsCount})]);
  if (_tp.episodes?.length) {
    chips.push(['ti-list', _tp.type === 'tv'
      ? t('title.seasonEpisodes', {count: _tp.episodes.length, season: String(_tp.season).padStart(2, '0')})
      : t('title.episodeCount', {count: _tp.episodes.length})]);
  }
  if (_tp.meta?.runtime) chips.push(['ti-clock', `${_tp.meta.runtime} min`]);
  if (_tp.age) chips.push(['ti-shield', `${_tp.age}+`]);
  if (_tp.meta?.quality) chips.push(['ti-badge-hd', _tp.meta.quality]);
  document.getElementById('th-chips').innerHTML = chips
    .map(([i, t]) => `<span class="th-chip"><i class="ti ${i}"></i>${escapeHtml(String(t))}</span>`).join('');

  const plotEl = document.getElementById('th-plot');
  plotEl.textContent = _tp.plot || '';
  plotEl.style.display = _tp.plot ? '' : 'none';

  document.getElementById('th-score').innerHTML = _tp.score
    ? `<span class="th-score"><i class="ti ti-star-filled"></i>${escapeHtml(_tp.score)}/10</span>` : '';
  document.getElementById('th-genres').innerHTML = (_tp.genres || []).slice(0, 4)
    .map(g => `<span class="th-genre">${escapeHtml(catalogLabel(g))}</span>`).join('');

  _tpRenderCta();
}

function _tpRenderCta() {
  if (_tp.comingSoon) {
    document.getElementById('th-cta').innerHTML = `<span class="badge bg-orange-lt">${t('title.unavailable')}</span>`;
    return;
  }
  const wants = !can('DOWNLOAD');
  // The hero takes the broadest action available - the whole series where
  // there is one - and the season bar keeps the season. Two buttons that both
  // said "season" left nowhere to ask for the lot.
  const verb = wants ? t('title.request') : (_tp.scheduledAt ? t('title.schedule') : t('title.download'));
  // A requester can ask for a season but not for a series or a whole anime:
  // those two go through the batch download endpoints, which require
  // DOWNLOAD. Offering them would be a button that 403s.
  const scope = _tp.type === 'movie' ? ''
    : (_tp.type === 'anime' ? (wants ? '' : t('title.scopeEpisodes'))
    : (!wants && _tp.seasonsCount > 1 ? t('title.scopeSeries') : t('title.scopeSeason')));
  const label = `${verb}${scope}`;
  const icon = wants ? 'ti-send' : (_tp.seasonsCount > 1 && _tp.type === 'tv' ? 'ti-stack-2' : 'ti-download');
  // An anime film is _tp.type 'anime'; only the source's own kind says it is a film.
  const followable = _tp.type !== 'movie' && !(_tp.type === 'anime' && isAnimeFilm(_tp.animeType));

  // Scheduling is part of the download privilege: a requester picks tracks and
  // an approver decides when it runs.
  const sched = can('DOWNLOAD') ? `
    <label class="th-sched" title="${escapeHtml(t('title.scheduleHint'))}">
      <i class="ti ti-clock"></i>
      <input type="datetime-local" id="th-sched-at" value="${escapeHtml(_tp.scheduledRaw)}"
             data-change="tp:schedule">
    </label>` : '';

  // An anime requester has nothing batchable to press: every episode is its
  // own request, from the rows below.
  const primary = (wants && _tp.type === 'anime') ? '' : `
    <button class="btn btn-primary" data-action="tp:primary">
      <i class="ti ${icon} me-1"></i>${label}
    </button>`;

  document.getElementById('th-cta').innerHTML = `
    ${sched}
    ${primary}
    <!-- Left bare: _renderFollowButton owns this button's class and label,
         and rewriting them here would fight it. -->
    ${followable ? `<button id="th-follow-btn" data-action="tp:follow"></button>` : ''}
    <button class="th-icon-btn" data-action="tp:tab" data-tab="tracks" title="${escapeHtml(t('title.tracksHint'))}">
      <i class="ti ti-adjustments"></i>
    </button>`;
  if (followable && !_tpLanguageRender) tpRefreshFollow();
}

function _tpRenderTabsBar() {
  document.getElementById('th-tabs').innerHTML = _tpTabs()
    .map(([k, label]) =>
      `<button class="th-tab${_tp.tab === k ? ' active' : ''}" data-tab="${k}" role="tab"
        data-action="tp:tab" data-tab="${k}">${escapeHtml(label)}</button>`).join('');
}

function _tpRenderAbout() {
  const el = document.getElementById('th-pane-about');
  el.innerHTML = _tp.plot
    ? `<p style="color:var(--text-sub);font-size:13.5px;line-height:1.7;max-width:70ch">${escapeHtml(_tp.plot)}</p>`
    : `<div class="th-empty">${t('title.noPlot')}</div>`;
}

function _tpRenderCast() {
  const el = document.getElementById('th-pane-cast');
  const m = _tp.meta;
  if (!m?.cast?.length && !m?.directors?.length) { el.innerHTML = ''; return; }
  const personLink = name => {
    return `<button type="button" class="th-track th-person-link" data-action="tp:person" ` +
      `data-person="${escapeHtml(name)}" title="${escapeHtml(t('title.person', {name}))}">` +
      `${escapeHtml(name)}</button>`;
  };
  const row = (k, v) => `<div class="th-track-group">
      <div class="th-track-h">${k}</div>
      <div class="th-track-chips">${v.map(personLink).join('')}</div>
    </div>`;
  el.innerHTML = (m.directors?.length ? row(t('title.directors'), m.directors) : '')
               + (m.cast?.length ? row(t('title.cast'), m.cast) : '');
}

function _tpRenderTracks() {
  const el = document.getElementById('th-pane-tracks');
  if (_tp.type === 'anime') { el.innerHTML = ''; return; }
  if (!_tp.tracksLoaded) {
    el.innerHTML = `<div class="th-empty"><span class="spinner-border spinner-border-sm me-2"></span>${t('title.readingTracks')}</div>`;
    return;
  }
  if (_tp.tracksError) {
    el.innerHTML = `<div class="alert alert-danger">${escapeHtml(_tp.tracksError)}</div>`;
    return;
  }
  const group = (which, title, icon, empty) => {
    const list = _tp[which] || [];
    const chips = list.length
      ? list.map(t => `<button class="th-track${t.on ? ' on' : ''}"
          data-action="tp:track" data-which="${which}" data-code="${escapeHtml(t.code)}">${escapeHtml(_tpLangName(t.code))}</button>`).join('')
      : `<span class="th-empty" style="padding:0">${empty}</span>`;
    return `<div class="th-track-group">
        <div class="th-track-h"><i class="ti ${icon}"></i>${title}</div>
        ${list.length ? `<div class="th-track-chips mb-2"><button class="th-track" data-action="tp:tracks-all" data-which="${which}">${t('title.selectAll')}</button>${which === 'subs' ? `<button class="th-track" data-action="tp:tracks-none" data-which="subs">${t('title.none')}</button>` : ''}</div>` : ''}
        <div class="th-track-chips">${chips}</div>
      </div>`;
  };
  el.innerHTML =
    group('audio', 'Audio', 'ti-volume', t('title.originalOnly'))
  + group('subs', t('title.subtitles'), 'ti-badge-cc', t('title.noSubtitles'))
  + `<p style="color:var(--text-muted);font-size:12px;margin:0">
       ${t('title.trackNote')}
     </p>`;
}

function _tpRenderFacts() {
  const m = _tp.meta || {};
  const rows = [];
  if (m.original_name && m.original_name !== _tp.name) rows.push([t('title.originalName'), m.original_name]);
  if (_tp.year) rows.push([t('title.year'), _tp.year]);
  if (_tp.type === 'tv' && _tp.seasonsCount) rows.push([t('title.seasons'), _tp.seasonsCount]);
  // Per season, not for the series: the source gives no series-wide total, and
  // labelling one season's count "Episodi" read as if it were that total.
  if (_tp.episodes?.length) {
    rows.push([_tp.type === 'tv' ? t('title.episodeFacts', {season: String(_tp.season).padStart(2, '0')}) : t('title.episodes'),
               _tp.episodes.length]);
  }
  if (m.runtime) rows.push([t('title.runtime'), `${m.runtime} min`]);
  if (m.status) rows.push([t('title.status'), m.status]);
  if (m.studio) rows.push(['Studio', m.studio]);
  if (m.season) rows.push([t('title.releaseSeason'), m.season]);
  if (m.quality) rows.push([t('title.quality'), m.quality]);
  if (_tp.genres?.length) rows.push([t('title.genre'), _tp.genres.map(catalogLabel).join(', ')]);
  // Last and quiet: the id is what an association in Settings is keyed on,
  // and the only place to read it from.
  if (_tp.id) {
    const key = libKeyForRoute(_tp.type, _tp.id, _tp.source);
    rows.push(['ID', libIdLabel(key.source, key.external_id), 'th-fact-id']);
  }
  document.getElementById('th-facts').innerHTML = rows
    .map(([k, v, cls]) => `<dt>${escapeHtml(k)}</dt><dd${cls ? ` class="${cls}"` : ''}>${escapeHtml(String(v))}</dd>`).join('')
    || `<dd style="grid-column:1/-1;text-align:left" class="th-empty">—</dd>`;
}

function _tpRenderTrailer() {
  if (_tpLanguageRender && document.querySelector('#th-trailer iframe')) return;
  const card = document.getElementById('th-trailer-card');
  const url = _tp.meta?.trailer_url;
  if (!url) { card.hidden = true; return; }
  card.hidden = false;
  const still = _tp.meta?.backdrop ? `style="background-image:url('${encodeURI(_tp.meta.backdrop)}')"` : '';
  document.getElementById('th-trailer').innerHTML =
    `<button class="th-trailer-facade" ${still} data-action="tp:trailer" aria-label="${escapeHtml(t('title.playTrailer'))}"></button>`;
}

function tpPlayTrailer() {
  const url = _tp.meta?.trailer_url || '';
  const id = (url.match(/[?&]v=([\w-]+)/) || [])[1];
  if (!id) { window.open(url, '_blank', 'noopener'); return; }
  // Only now does anything reach YouTube, and through the no-cookie host.
  document.getElementById('th-trailer').innerHTML =
    `<iframe class="th-trailer-frame" data-title-id="${escapeHtml(String(_tp.id))}" title="Trailer" src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1"
       allow="accelerometer; autoplay; encrypted-media; picture-in-picture" allowfullscreen
       referrerpolicy="no-referrer"></iframe>`;
}

function _tpRenderEpisodes() {
  const el = document.getElementById('th-pane-episodes');
  if (!el || _tp.type === 'movie') { if (el) el.innerHTML = ''; return; }
  if (_tp.comingSoon) { el.textContent = t('title.episodesUnavailable'); return; }

  const pills = _tp.type === 'tv' && _tp.seasonsCount > 1
    ? `<div class="th-season-pills">${_tpSeasons()
        .map(n => `<button class="th-season-pill${n === _tp.season ? ' active' : ''}"
          data-action="tp:season" data-n="${n}">S${String(n).padStart(2, '0')}</button>`).join('')}</div>` : '';

  // Batching is a download privilege; a requester asks episode by episode.
  // The verb follows the schedule field, so the button says what will happen.
  const verb = _tp.scheduledAt ? t('title.schedule') : t('title.download');
  let batch = '';
  if (_tp.episodes?.length) {
    if (can('DOWNLOAD')) {
      // Only a StreamingCommunity season knows what it holds before asking:
      // its episodes arrive flagged in_library. For anime the server decides.
      const owned = _tp.type === 'tv' ? _tp.episodes.filter(e => e.in_library).length : 0;
      const ownedHint = owned ? ` <span class="th-only-missing-n">${t('title.owned', {count: owned})}</span>` : '';
      batch = `<label class="form-check form-switch th-only-missing"
                      title="${escapeHtml(t('title.skipHint'))}">
            <input class="form-check-input" type="checkbox" data-change="tp:onlyMissing"
                   ${_tp.onlyMissing ? 'checked' : ''}>
            <span class="form-check-label">${t('title.onlyMissing')}${ownedHint}</span>
          </label>` + (_tp.type === 'anime'
        ? `<button class="btn btn-sm btn-outline-primary" data-action="tp:batchAnime">
             <i class="ti ti-download me-1"></i>${verb}${t('title.scopeEpisodes')}</button>`
        : `<button class="btn btn-sm btn-outline-primary" data-action="tp:batchSeason" data-season="${_tp.season}">
             <i class="ti ti-download me-1"></i>${verb}${t('title.scopeSeason')}</button>`);
    } else if (_tp.type === 'tv' && can('REQUEST')) {
      batch = `<button class="btn btn-sm btn-outline-primary" data-action="tp:requestSeason">
                 <i class="ti ti-send me-1"></i>${t('title.request')}${t('title.scopeSeason')}</button>`;
    }
  }

  const head = `<div class="th-season-bar">
      <span class="th-season-label">${_tp.type === 'anime' ? t('title.episodes') : t('title.season', {season: _tp.season})}</span>
      ${pills}
      <span class="th-season-batch">${batch}</span>
    </div>`;

  if (_tp.episodes === null) {
    el.innerHTML = head + `<div class="th-empty"><span class="spinner-border spinner-border-sm me-2"></span>${t('title.loadingEpisodes')}</div>`;
    return;
  }
  if (_tp.episodesError) {
    el.innerHTML = head + `<div class="alert alert-danger">${escapeHtml(_tp.episodesError)}</div>`;
    return;
  }
  if (!_tp.episodes.length) {
    el.innerHTML = head + `<div class="th-empty">${t('title.noEpisodes')}</div>`;
    return;
  }

  const wants = !can('DOWNLOAD');
  const rows = _tp.episodes.map((ep, i) => {
    // Only when the source has one. The placeholder means "an image was
    // expected and did not arrive", which is true of a StreamingCommunity
    // still that failed and false of AnimeUnity, whose episode records carry
    // no image field at all - there, reserving the slot just draws the same
    // empty box down the whole list.
    const episodeImage = ep.still || ep.image || ep.imageurl || ep.thumbnail || ep.image_url || null;
    const still = episodeImage ? thumbHtml(episodeImage, 'th-ep-still') : '';
    const plot = ep.plot ? `<div class="th-ep-plot">${escapeHtml(ep.plot)}</div>` : '';
    const dur = ep.duration ? `<span class="th-ep-dur">${escapeHtml(String(ep.duration))}m</span>` : '';
    const owned = ep.in_library
      ? `<i class="ti ti-circle-check-filled th-ep-owned" title="${escapeHtml(t('title.alreadyInLibrary'))}"></i>` : '';
    return `<div class="th-ep">
        <span class="th-ep-n">${escapeHtml(String(ep.n))}</span>
        ${still}
        <div class="th-ep-body">
          <div class="th-ep-name">${escapeHtml(ep.name || t('title.episode', {number: ep.n}))}</div>
          ${plot}
        </div>
        ${dur}${owned}
        <button class="btn btn-sm ${wants ? 'btn-outline-primary' : 'btn-primary'}"
                data-action="tp:episode" data-index="${i}">
          <i class="ti ${wants ? 'ti-send' : 'ti-download'} me-1"></i>${wants ? t('title.request') : t('title.download')}
        </button>
      </div>`;
  }).join('');

  el.innerHTML = head + `<div class="th-eps">${rows}</div>`;
}

// ── Actions ───────────────────────────────────────────────────────────────────

function tpPrimary() {
  if (_tp.comingSoon) return;
  // Without DOWNLOAD a season becomes a season's worth of requests, made
  // server-side so dedup and the library check apply to each of them.
  if (!can('DOWNLOAD') && _tp.type === 'tv') { tpRequestSeason(); return; }
  if (_tp.type === 'movie') {
    startFilmDownload(_tp.id, _tp.name, _tp.year, _tp.scheduledAt, _tpPicked('audio'), _tpPicked('subs'), _tp.poster, _tp.source);
  } else if (_tp.type === 'anime') {
    downloadAllAnime();
  } else if (_tp.seasonsCount > 1) {
    downloadWholeSeries();
  } else {
    downloadWholeSeason(_tp.season);
  }
}

async function tpToggleFollow() { await toggleFollowSeries('page'); _tpLocalizeFollow(); }
async function tpRefreshFollow() { await checkWatchStatus('page'); _tpLocalizeFollow(); }

// Back to wherever the visitor came from, and to the search page when they
// arrived by pasted link and there is no history to go back to.
function tpBack() {
  // Back where possible, because it returns to the exact search that was
  // there — query, filters and all — rather than to an empty one.
  if (history.length > 1 && document.referrer !== '') { history.back(); return; }
  navigate('search');
}

// Every episode of the season, as requests. The server enumerates and creates
// them, so each one is an ordinary request: already-asked episodes are joined
// rather than duplicated, and the reply says how many of each.
async function tpRequestSeason() {
  const n = _tp.episodes?.length || 0;
  if (!await scConfirm(t('title.requestSeasonConfirm', {count: n, season: _tp.season}))) return;
  try {
    const r = await api.post('/api/requests/season', {
      source: _tp.source,
      external_id: String(_tp.id), title: _tp.name, slug: _tp.slug,
      season: _tp.season, year: _tp.year || null, poster: _tp.poster || null,
      audio_languages: _tpPicked('audio'),
      subtitle_languages: _tpPicked('subs'),
    });
    const parts = [];
    if (r.created) parts.push(t('title.requests', {count: r.created}));
    if (r.joined) parts.push(t('title.joined', {count: r.joined}));
    showToast(parts.join(', ') || t('title.noRequests'), 'success');
    refreshQueueBadge();
  } catch (e) {
    showToast(e.message || t('title.requestFailed'), 'danger');
  }
}


// ── Avvio di un download ─────────────────────────────────────────────────────
//
// Moved here from app.js: after the detail page replaced the three modals,
// this page is the only caller of any of it.

// ── Tracce audio e sottotitoli ───────────────────────────────────────────────

// Which subtitle tracks start selected.
//
// A forced Italian track subtitles only what the Italian audio does not cover
// - signs, and lines spoken in another language - while a full Italian track
// repeats dialogue you can already hear. Alongside Italian audio the forced
// one is almost always what is wanted, so it wins whenever the source has it,
// and the full track is the fallback rather than the default.
function preferredSubSelection(codes) {
  const forcedIta = codes.find(c => /^forced[-_ ]?ita/i.test(c));
  const picked = new Set();
  if (forcedIta) picked.add(forcedIta);
  else if (codes.includes('ita')) picked.add('ita');
  if (codes.includes('eng')) picked.add('eng');
  return picked;
}

// The source lists some languages more than once (two "ita", two "ger" on a
// single title). Two identical chips are two controls for one thing.
function dedupeLangs(codes) {
  return [...new Set(codes || [])];
}


// Guards against a stale response painting over a newer one: open a title, close
// it, open another before the first reply lands, and the first one used to win.
// The two fetches of one open land in either order, and each needs something
// the other has: the failure message depends on whether a fallback exists.





// ── Requesting ─────────────────────────────────────────────────────────────────

// Same form, different action: without the download permission the choice of
// audio and subtitles becomes a request instead of a job.
async function submitRequest(payload, label) {
  let data;
  try {
    data = await api.post('/api/requests', payload);
  } catch (e) { showToast(errText(e), 'danger'); return false; }

  try {
    const status = data.request.status;
    if (status === 'available') showToast(t('title.availableMessage', {title: label}), 'info');
    else if (!data.created) showToast(t('title.requestedMessage', {title: label}), 'info');
    else showToast(t('title.requestSent', {title: label}), 'success');

    _requestStatus[String(payload.external_id)] = { id: data.request.id, status };
    renderRequestRibbons();
    refreshNotifications();
    return true;
  } catch (e) {
    // The request went through; only the bookkeeping after it did not.
    console.error('submitRequest:', e);
    return true;
  }
}

// ── Film download ──────────────────────────────────────────────────────────────

async function startFilmDownload(id, title, year=null, scheduledAt=null, audioLangs=null, subLangs=null, poster=null, source='streamingcommunity') {
  if (!can('DOWNLOAD')) {
    const ok = await submitRequest({
      source, media_type: 'film', external_id: String(id),
      title, year, poster,
      audio_languages: audioLangs || ['ita'],
      subtitle_languages: subLangs || [],
    }, title);
    if (ok) showPage('my-requests');
    return;
  }
  try {
    const endpoint = scheduledAt ? '/api/download/schedule/film' : '/api/download/film';
    const body = {
      id, title, year, source,
      audio_languages: audioLangs || ['ita'],
      subtitle_languages: subLangs || ['ita', 'eng'],
    };
    if (scheduledAt) body.scheduled_at = scheduledAt;
    await api.post(endpoint, body);
    showToast(scheduledAt
      ? t('title.scheduledDate', {title, date: new Date(scheduledAt).toLocaleString(scLanguage.current() === 'en' ? 'en-GB' : 'it-IT')})
      : t('title.started', {title}), 'success', DOWNLOADS_LINK);
  } catch(e) { showToast(errText(e), 'danger'); }
}

// ── Episode Browser ────────────────────────────────────────────────────────────

let _epCtx = {};




async function startEpisodeDownload(epIndex) {
  const { tvId, tvName, slug, year, scheduledAt, token, episodes, currentSeason, audioLangs, subLangs, poster } = _epCtx;
  const source = _epCtx.source || 'streamingcommunity';
  const ep = episodes[epIndex];
  const label = `${tvName} S${String(currentSeason).padStart(2,'0')}E${String(ep.n).padStart(2,'0')}`;

  if (!can('DOWNLOAD')) {
    await submitRequest({
      source, media_type: 'episode', external_id: String(tvId),
      slug, title: tvName, year, poster,
      season: currentSeason, episode_number: String(ep.n),
      audio_languages: audioLangs || ['ita'],
      subtitle_languages: subLangs || [],
    }, label);
    return;
  }

  const endpoint = scheduledAt ? '/api/download/schedule/episode' : '/api/download/episode';
  const body = {
    source, tv_id: tvId, eps: episodes, ep_index: epIndex, token,
    tv_name: tvName, season: currentSeason, year,
    audio_languages: audioLangs || ['ita'],
    subtitle_languages: subLangs || ['ita', 'eng'],
  };
  if (scheduledAt) body.scheduled_at = scheduledAt;
  try {
    await api.post(endpoint, body);
    showToast(scheduledAt ? t('title.scheduled', {title: label}) : t('title.queued', {title: label}), 'success');
  } catch(e) { showToast(errText(e), 'danger'); }
}

const DOWNLOADS_LINK = { get label() { return t('title.downloadsLink'); }, page: 'downloads' };

// Whole seasons and whole series are one call: the server lists the episodes
// itself and queues them as a batch. That is also what lets it report the season
// once at the end instead of pinging for every episode.
// modalId is optional: the title page is a page, so there is nothing to
// close behind it. It remains for the callers that are still modals.
async function _startBatch(path, body, modalId) {
  let data;
  try {
    data = await api.post(path, body);
  } catch (e) {
    showToast(errText(e, t('title.startError')), 'danger');
    return false;
  }
  const skipped = data.skipped ? t('title.skipped', {count: data.skipped}) : '';
  showToast(
    (body.scheduled_at ? t('title.batchScheduled', {count: data.count}) : t('title.batchQueued', {count: data.count}))
      + skipped,
    'success',
    DOWNLOADS_LINK,
  );
  if (modalId) hideModal(modalId);
  // No jump to the downloads page: queueing seasons 4, 5 and 6 meant finding
  // the series again after each one (#20).
  return true;
}

// The "Solo mancanti" switch, and the words a confirmation adds for it.
function _onlyMissing() { return !!_tp?.onlyMissing; }
function _skipNote() { return _onlyMissing() ? t('title.skipNote') : ''; }

async function downloadWholeSeason(season) {
  const { tvId, slug, tvName, year, episodes, scheduledAt, audioLangs, subLangs } = _epCtx;
  const label = scheduledAt ? t('title.planAction') : t('title.queueAction');
  // The episode list says which are already there, so the question can name
  // the real number instead of promising every episode.
  const missing = episodes.filter(e => !e.in_library).length;
  if (_onlyMissing() && missing === 0) {
    showToast(t('title.allOwned'), 'info');
    return;
  }
  const question = _onlyMissing() && missing < episodes.length
    ? t('title.confirmMissing', {action: label, count: missing, season})
    : t('title.confirmSeason', {action: label, count: episodes.length, season});
  if (!await scConfirm(question)) return;
  await _startBatch('/api/download/season', {
    source: _epCtx.source || 'streamingcommunity',
    tv_id: tvId, slug, tv_name: tvName, season, year,
    audio_languages: audioLangs, subtitle_languages: subLangs,
    scheduled_at: scheduledAt || null,
    only_missing: _onlyMissing(),
  });
}

async function downloadWholeSeries() {
  const { tvId, slug, tvName, year, scheduledAt, seasonsCount, audioLangs, subLangs } = _epCtx;
  const label = scheduledAt ? t('title.planAction') : t('title.queueAction');
  if (!await scConfirm(t('title.confirmSeries', {action: label, count: seasonsCount, note: _skipNote()}))) return;
  await _startBatch('/api/download/series', {
    source: _epCtx.source || 'streamingcommunity',
    tv_id: tvId, slug, tv_name: tvName, year,
    audio_languages: audioLangs, subtitle_languages: subLangs,
    scheduled_at: scheduledAt || null,
    only_missing: _onlyMissing(),
  });
}

// ── Anime Browser (AnimeUnity) ─────────────────────────────────────────────────


async function startAnimeDownload(epIndex) {
  const { animeId, animeName, animeType, animeYear, scheduledAt, episodes, audioLangs, subLangs } = _animeCtx;
  const source = _animeCtx.source || 'animeunity';
  const episode = episodes[epIndex];
  const label = `${animeName} E${episode.number}`;

  if (!can('DOWNLOAD')) {
    await submitRequest({
      source, media_type: 'anime', external_id: String(animeId),
      title: animeName, year: animeYear, anime_type: animeType,
      episode_number: String(episode.number),
      audio_languages: audioLangs || ['ita'],
      subtitle_languages: subLangs || [],
    }, label);
    return;
  }

  const endpoint = scheduledAt ? '/api/download/schedule/anime' : '/api/download/anime';
  const body = {
    source, anime_id: animeId, episode, anime_name: animeName, anime_type: animeType, year: animeYear,
    audio_languages: audioLangs || ['ita'],
    subtitle_languages: subLangs || ['ita', 'eng'],
  };
  if (scheduledAt) body.scheduled_at = scheduledAt;
  try {
    await api.post(endpoint, body);
    showToast(scheduledAt ? t('title.scheduled', {title: label}) : t('title.queued', {title: label}), 'success');
  } catch(e) { showToast(errText(e), 'danger'); }
}


async function downloadAllAnime() {
  const { animeId, animeName, animeType, animeYear, episodes, scheduledAt,
          audioLangs, subLangs } = _animeCtx;
  const label = scheduledAt ? t('title.planAction') : t('title.queueAction');
  if (!await scConfirm(t('title.confirmAnime', {action: label, count: episodes.length, note: _skipNote()}))) return;
  await _startBatch('/api/download/anime-all', {
    source: _animeCtx.source || 'animeunity',
    anime_id: String(animeId), anime_name: animeName, anime_type: animeType,
    year: animeYear,
    audio_languages: audioLangs, subtitle_languages: subLangs,
    scheduled_at: scheduledAt || null,
    only_missing: _onlyMissing(),
  });
}


// ── Delegated handlers ───────────────────────────────────────────────────────
//
// The episode button used to name its handler as a string and interpolate the
// index into an attribute. Which of the two it is depends on the title's kind,
// which is state this page already holds, so the branch belongs here and not
// in the markup.

registerActions({
  'tp:back':          () => tpBack(),
  'tp:crumb-search':  () => { setSource(_tp?.source || currentSource); navigate('search'); },
  'tp:crumb-kind':    d => {
    navigate('search');
    window.setTimeout(() => {
      if (d.kind === 'tv' || d.kind === 'movie') setKindFilter(d.kind);
    }, 0);
  },
  'tp:primary':       () => tpPrimary(),
  'tp:follow':        () => tpToggleFollow(),
  'tp:tab':           d => tpSetTab(d.tab),
  'tp:tracks-all': d => tpSelectTracks(d.which, true),
  'tp:tracks-none': d => tpSelectTracks(d.which, false),
  'tp:track':         d => tpToggleTrack(d.which, d.code),
  'tp:trailer':       () => tpPlayTrailer(),
  'tp:season':        d => tpLoadSeason(Number(d.n)),
  'tp:schedule':      (d, el) => tpSetSchedule(el.value),
  'tp:onlyMissing':   (d, el) => { _tp.onlyMissing = el.checked; },
  'tp:requestSeason': () => tpRequestSeason(),
  'tp:batchSeason':   d => downloadWholeSeason(Number(d.season)),
  'tp:batchAnime':    () => downloadAllAnime(),
  'tp:episode':       d => (_tp.type === 'anime' ? startAnimeDownload : startEpisodeDownload)(Number(d.index)),
  'tp:person':        d => window.open(`https://www.google.com/search?q=${encodeURIComponent(`${d.person} ${t('title.actorQuery')}`)}`, '_blank', 'noopener,noreferrer'),
});


// The title is a path — kind, id, slug — and the season is the one piece of
// state on the page worth carrying, because changing it is the main thing you
// do here and losing it on reload is what sent you back to S01.
//
// The chosen audio and subtitle tracks are deliberately left out: they are a
// decision about a download you have not started, not a place you are, and a
// link that silently pre-selects someone else's tracks is a link that starts
// the wrong download.
registerPageHash('detail', {
  read: () => (_tp ? {
    extra: [_tp.type, _tp.id, _tp.slug || ''],
    params: { src: _tp.source, s: _tp.season !== _tpSeasons()[0] ? _tp.season : null },
  } : {}),
});

// Translate interface labels without changing the title's selected tracks or tab.
let _tpLanguageRender = false;
function _tpLangName(code) {
  if (scLanguage.current() === 'it') return langName(code);
  const forced = /^forced[-_ ]?/i.test(code);
  const clean = code.replace(/^forced[-_ ]?/i, '');
  const aliases = {ita: 'it', eng: 'en', ger: 'de', deu: 'de', fre: 'fr', fra: 'fr', spa: 'es', jpn: 'ja', por: 'pt', rus: 'ru', chi: 'zh', zho: 'zh', kor: 'ko'};
  let name;
  try { name = new Intl.DisplayNames(['en'], {type: 'language'}).of(aliases[clean] || clean); }
  catch (_) { name = clean; }
  return name + (forced ? ' ' + t('title.forced') : '');
}
function _tpLocalizeFollow() {
  const button = document.getElementById('th-follow-btn');
  if (!button) return;
  const following = button.dataset.following === '1';
  button.innerHTML = `<i class="ti ${following ? 'ti-bell-check' : 'ti-bell-plus'} me-1"></i>${t(following ? 'title.following' : 'title.follow')}`;
  button.title = t(following ? 'title.followingHint' : 'title.followHint');
}
window.addEventListener('sc:language-changed', () => {
  if (!_tp) return;
  const button = document.getElementById('th-follow-btn');
  const focus = document.activeElement;
  const schedule = document.getElementById('th-sched-at');
  if (schedule && schedule.value !== _tp.scheduledRaw) tpSetSchedule(schedule.value);
  _tpLanguageRender = true;
  try {
    _tpRender();
    if (button) document.getElementById('th-follow-btn')?.replaceWith(button);
    _tpLocalizeFollow();
    if (focus?.id === 'th-sched-at') document.getElementById(focus.id)?.focus();
  } finally { _tpLanguageRender = false; }
});
