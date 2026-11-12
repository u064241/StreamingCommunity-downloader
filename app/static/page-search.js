/* StreamingCommunity Web Panel — page-search.js */
//
// The search page: the source selector, the query, the result grid and its
// filters, and the shelves the start page shows with the box empty.

// What a title is, in the source's own word. StreamingCommunity says movie/tv;
// AnimeUnity says Movie/TV/OVA/ONA/Special and keeps it in media_type, beside a
// type that is always "anime". Looked up lower case so one table serves both.
// One colour per kind, so a grid can be read at a glance instead of word by word.
const KIND_BADGES = {
  movie:   {label: 'catalog.movie',     cls: 'bg-blue-lt'},
  tv:      {label: 'catalog.tv', cls: 'bg-green-lt'},
  ova:     {label: 'OVA',      cls: 'bg-purple-lt'},
  ona:     {label: 'ONA',      cls: 'bg-teal-lt'},
  special: {label: 'catalog.special', cls: 'bg-yellow-lt'},
  anime:   {label: 'Anime',    cls: 'bg-purple-lt'},
};
function kindBadge(item) {
  const key = String(item.media_type || item.type || '').toLowerCase();
  // An unrecognised kind is shown as it came, in a neutral colour. Folding it
  // into "TV" is exactly the bug this replaced: every anime, films included,
  // was labelled a TV series. The source's own word first — AnimeUnity has a
  // long tail of them ("TV Short" turns up in a normal search) and its own name
  // for a thing says more than the "anime" every one of its records carries.
  return KIND_BADGES[key] ? {...KIND_BADGES[key], label: t(KIND_BADGES[key].label)} : {label: item.media_type || item.type || '?',
                              cls: 'bg-secondary-lt'};
}

// ── Source selector ────────────────────────────────────────────────────────────

function setSource(src) {
  _catalogSection = null;
  _cancelCatalogWork();
  currentSource = src;
  document.getElementById('src-all')?.classList.toggle('active', src === 'all');
  document.getElementById('src-sc').classList.toggle('active', src === 'streamingcommunity');
  document.getElementById('src-au').classList.toggle('active', src === 'animeunity');
  document.getElementById('src-rai')?.classList.toggle('active', src === 'raiplay');
  const input = document.getElementById('search-input');
  if (input) input.dataset.i18nPlaceholder = src === 'animeunity' ? 'search.animePlaceholder' : 'search.placeholder';
  if (input) input.placeholder = src === 'animeunity' ? t('search.animePlaceholder') : t('search.placeholder');
  document.getElementById('search-results').innerHTML = '';
  // The kinds are each source's own vocabulary, so a filter does not carry over.
  _searchResults = [];
  _requestStatus = {};
  _searchPage = 1;
  _searchExhausted = false;
  _kindFilter = '';
  _dubOnly = false;
  renderSearchFilters();
  _setMoreVisible(false);
  // Clearing the grid and leaving the query typed is what made the selector
  // read as broken: nothing happened until you typed again.
  _rerunSearch();
  syncHash();
}

// ── Search ─────────────────────────────────────────────────────────────────────

let _searchAbort = null;
let _searchDebounceTimer = null;

function setupSearchDebounce() {
  const input = document.getElementById('search-input');
  if (!input) return;
  input.addEventListener('input', () => {
    _catalogSection = null;
    _cancelCatalogWork();
    clearTimeout(_searchDebounceTimer);
    const q = input.value.trim();
    if (q.length >= 3) {
      _searchDebounceTimer = setTimeout(() => doSearch(), 400);
    } else if (q.length === 0) {
      // Only on empty, never on one or two characters: that is somebody on
      // their way to a three-letter query, and swapping the page out from
      // under them is worse than a blank pause.
      _searchDebounceTimer = setTimeout(() => showStartPage(), 200);
    }
  });
}

function _showSearchSkeletons() {
  const container = document.getElementById('search-results');
  container.innerHTML = '';
  for (let i = 0; i < 6; i++) {
    const col = document.createElement('div');
    col.className = 'col-6 col-sm-4 col-md-3 col-lg-2';
    col.innerHTML = '<div class="skeleton skeleton-card"></div>';
    container.appendChild(col);
  }
}

// ── Result filters and paging ─────────────────────────────────────────────────

let _searchPage = 1;
let _searchExhausted = false;
let _loadingMore = false;
let _kindFilter = '';   // '' | movie | tv | ova | ona | special
let _dubOnly = false;

// Each source classifies its catalogue in its own vocabulary, so the chips are
// rebuilt when the source changes rather than offering one of them a filter the
// other cannot answer.
const KIND_FILTERS = {
  all: [['', 'catalog.all'], ['movie', 'catalog.movie'], ['tv', 'catalog.tv']],
  streamingcommunity: [['', 'catalog.all'], ['movie', 'catalog.movie'], ['tv', 'catalog.tv']],
  animeunity: [['', 'catalog.all'], ['movie', 'catalog.movie'], ['tv', 'catalog.tv'],
               ['ova', 'OVA'], ['ona', 'ONA'], ['special', 'catalog.specials']],
  // Metadata only, and its search does not tell film from series: no filter.
  raiplay: [['', 'catalog.all']],
};

// Sources with a download engine. The others (RaiPlay) are browsed only: no
// request statuses, no domain, and their cards open read-only.
const DOWNLOAD_SOURCES = ['streamingcommunity', 'animeunity'];
// Whether the source needs the StreamingCommunity domain to be configured.
function _sourceNeedsDomain(src) { return src === 'streamingcommunity' || src === 'all'; }

function renderSearchFilters() {
  const bar = document.getElementById('search-filters');
  if (!bar) return;
  const chips = (KIND_FILTERS[currentSource] || []).map(([value, label]) =>
    `<button class="source-btn${value === _kindFilter ? ' active' : ''}" ` +
    `data-action="search:kind" data-kind="${value}">${t(label)}</button>`).join('');
  // AnimeUnity keeps an Italian dub as a record of its own, so this is a real
  // filter there and meaningless on the other source.
  const dub = currentSource === 'animeunity'
    ? `<button class="source-btn${_dubOnly ? ' active' : ''}" data-action="search:dub" data-on="${_dubOnly ? '0' : '1'}">` +
      `<i class="ti ti-microphone"></i>${searchLabel('catalog.dubbed')}</button>`
    : '';
  bar.innerHTML = chips + dub;
}

function setKindFilter(kind) {
  if (kind === _kindFilter) return;
  _kindFilter = kind;
  renderSearchFilters();
  _afterFilterChange();
}

function setDubOnly(on) {
  _dubOnly = !!on;
  renderSearchFilters();
  _afterFilterChange();
}

function _rerunSearch() {
  if (_catalogSection) { doSearch(); return; }
  const input = document.getElementById('search-input');
  if (input && input.value.trim()) doSearch();
  else showStartPage();
}

// With a query the filter belongs to the source, or paging and filtering stop
// composing: a page of sixty narrowed to four in the browser would page through
// a window that has already been narrowed. On the rails there is nothing to
// page, so it is applied where the cards already are.
function _afterFilterChange() {
  syncHash();
  const input = document.getElementById('search-input');
  if (_catalogSection || (input && input.value.trim())) doSearch();
  else applyClientFilter();
}

// A plain object for api.url, which drops undefined and keeps ''. That
// distinction matters here: an empty media_type must be *absent*, not sent
// blank, so the optional filters resolve to undefined rather than ''.
function _searchParams(q, page) {
  return {
    q, source: currentSource, page: String(page),
    // RaiPlay has no type filter and answers 422 for one.
    media_type: (currentSource !== 'raiplay' && _kindFilter) || undefined,
    // Sent only where it means something; the other source answers 422 for it.
    dubbed: (_dubOnly && currentSource === 'animeunity') ? 'true' : undefined,
  };
}

function _setMoreVisible(on) {
  const wrap = document.getElementById('search-more');
  if (wrap) wrap.style.display = on ? '' : 'none';
}

// Cards are rendered from a base index rather than from the array, so a page
// appended later indexes into the same flat _searchResults and card n keeps
// pointing at title n forever.
//
// options.action / options.noPreview: another page's own card list (the
// archive), whose indices are not into _searchResults.
// options.ranked: the source's "Top 10 di oggi" shelf. Each card carries its
// position as a large outlined number behind the artwork, as on the original.
function renderResultCards(items, container, baseIndex,
                           wrapperClass = 'col-6 col-sm-4 col-md-3 col-lg-2',
                           options = {}) {
  const ranked = !!(options && options.ranked);
  items.forEach((item, i) => {
    const idx = Number.isInteger(item._searchIndex) ? item._searchIndex : baseIndex + i;
    const isMovie = item.type === 'movie';
    const kind = kindBadge(item);
    const year = itemYear(item);
    const score = item.score ? parseFloat(item.score).toFixed(1) : null;
    // Wide dashboard cards should prefer the source backdrop. A vertical
    // poster inside a 16:9 frame is forced through object-fit:cover and loses
    // too much artwork; the poster remains the fallback for sources without a
    // backdrop (and for the compact result views).
    const artwork = item.backdrop || item.poster;
    const posterUrl = artwork
      ? (artwork.startsWith('http') || artwork.startsWith('/') ? artwork : imageUrl(artwork))
      : '';
    const card = document.createElement('div');
    card.className = ranked ? `${wrapperClass} shelf-item-ranked` : wrapperClass;
    card.dataset.idx = String(idx);
    const rankHtml = ranked
      ? `<span class="rank-number" aria-hidden="true">${escapeHtml(String(i + 1))}</span>`
      : '';
    const posterHtml = posterUrl
      ? `${!item.backdrop ? `<img class="poster-backdrop" src="${escapeHtml(posterUrl)}" alt="" aria-hidden="true" loading="lazy" onerror="this.remove()">` : ''}<img class="poster-main" src="${escapeHtml(posterUrl)}" alt="" loading="lazy" onerror="posterError(this)">`
      : '';
    // Movie cards carry no status ribbon: a movie can be requested again
    // freely (denied/failed/cancelled never block it), so a "richiesto" chip
    // would just read as blocked when it is not. TV and anime keep it — their
    // status is read from the grouped request rows anyway, not per-title.
    const ribbonHtml = isMovie
      ? ''
      : `<div class="status-ribbon" data-ribbon-for="${escapeHtml(String(item.id))}"></div>`;
    card.innerHTML = `${rankHtml}
      <div class="result-card" ${options?.action ? `data-action="${escapeHtml(options.action)}"` : 'data-action="search:open"'} data-idx="${idx}"${options?.noPreview ? ' data-preview-disabled="1"' : ''}
           role="button" tabindex="0" aria-label="Apri ${escapeHtml(item.name)}">
        <div class="poster-wrap ${item.backdrop ? 'has-backdrop' : 'poster-fallback'}">
          ${posterHtml}
          <div class="poster-noimg" style="${posterUrl?'display:none':''}">&#127916;</div>
          <div class="poster-overlay"></div>
          <div class="card-preview" aria-hidden="true"></div>
          ${ribbonHtml}
          <div class="poster-play"><i class="ti ti-player-play-filled" style="font-size:16px"></i></div>
        </div>
        <div class="card-meta">
          <div class="card-title-text" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
          <div class="card-badges">
            <span class="badge ${kind.cls}">${searchLabel(KIND_BADGES[String(item.media_type || item.type || '').toLowerCase()]?.label, kind.label)}</span>
            ${item.coming_soon ? `<span class="badge bg-orange-lt">${searchLabel('catalog.upcoming')}</span>` : ''}
            ${!item.coming_soon && item.upcoming_seasons?.length ? `<span class="badge bg-orange-lt">${searchLabel('catalog.upcomingSeason')}</span>` : ''}
            ${score?`<span class="badge bg-yellow-lt">★ ${score}</span>`:''}
            ${year?`<span style="font-size:10px;color:var(--text-muted)">${year}</span>`:''}
          </div>
        </div>
      </div>`;
    container.appendChild(card);
    // Do not hydrate every card during render: that creates a burst of
    // metadata requests and can trigger the source's anti-bot protection.
    // Artwork hydration remains lazy, on the single card the user visits.
  });
}

async function hydrateShelfArtwork(card, item) {
  const key = `${item.type}:${item.id}`;
  const cached = _cardArtworkCache.get(key);
  if (cached) {
    const image = card?.querySelector('.poster-main');
    if (image) {
      image.src = cached;
      const wrap = image.closest('.poster-wrap');
      wrap?.querySelector('.poster-backdrop')?.remove();
      wrap?.classList.remove('poster-fallback');
    }
    return;
  }
  if (!_isStreamingItem(item)) return;
  _cardArtworkCache.set(key, null);
  try {
    const meta = await api.get(`/api/metadata/${item.type}/${item.id}`,
      {slug: item.slug || '', version: currentVersion || ''});
    const backdrop = meta?.backdrop;
    if (!backdrop || !card?.isConnected) return;
    const src = (backdrop.startsWith('http') || backdrop.startsWith('/'))
      ? backdrop : imageUrl(backdrop);
    _cardArtworkCache.set(key, src);
    const image = card.querySelector('.poster-main');
    if (image) {
      image.src = src;
      const wrap = image.closest('.poster-wrap');
      wrap.querySelector('.poster-backdrop')?.remove();
      wrap.classList.remove('poster-fallback');
    }
  } catch (_) { /* the poster remains a valid fallback */ }
}

// Card previews are deliberately lazy: the catalogue can contain hundreds of
// titles and only the card under the pointer is allowed to request metadata.
const _cardPreviewCache = new Map();
const _cardArtworkCache = new Map();
const _cardPreviewTimers = new WeakMap();

function youtubeId(url) {
  try {
    const parsed = new URL(url);
    if (parsed.hostname === 'youtu.be') return parsed.pathname.slice(1).split('/')[0];
    if (parsed.hostname.endsWith('youtube.com')) {
      if (parsed.pathname === '/watch') return parsed.searchParams.get('v');
      const match = parsed.pathname.match(/^\/(?:embed|shorts)\/([^/?]+)/);
      return match ? match[1] : null;
    }
  } catch (_) { /* invalid trailer URL */ }
  return null;
}

function stopCardPreview(card) {
  const timer = _cardPreviewTimers.get(card);
  if (timer) { clearTimeout(timer); _cardPreviewTimers.delete(card); }
  const frame = card.querySelector('.card-preview iframe');
  if (frame) frame.remove();
  card.classList.remove('preview-visible');
}

async function startCardPreview(card) {
  if (card.dataset.previewDisabled === '1') return;
  const item = _searchResults[Number(card.dataset.idx)];
  if (!item || String(item.type).toLowerCase() === 'anime' || !_isStreamingItem(item)) return;
  const key = `${item.type}:${item.id}`;
  let url = _cardPreviewCache.get(key);
  if (url === undefined) {
    _cardPreviewCache.set(key, null);
    try {
      const meta = await api.get(`/api/metadata/${item.type}/${item.id}`,
        {slug: item.slug || '', version: currentVersion || ''});
      const backdrop = meta?.backdrop || null;
      if (backdrop && !_cardArtworkCache.has(key)) {
        const src = (backdrop.startsWith('http') || backdrop.startsWith('/'))
          ? backdrop : imageUrl(backdrop);
        _cardArtworkCache.set(key, src);
        const image = card.querySelector('.poster-main');
        if (image) {
          image.src = src;
          const wrap = image.closest('.poster-wrap');
          wrap.querySelector('.poster-backdrop')?.remove();
          wrap.classList.remove('poster-fallback');
        }
      }
      url = meta?.trailer_url || null;
      _cardPreviewCache.set(key, url);
    } catch (_) { url = null; }
  }
  if (!url || !card.matches(':hover')) {
    if (!url) card.dataset.previewDisabled = '1';
    return;
  }
  const id = youtubeId(url);
  if (!id || card.querySelector('.card-preview iframe')) return;
  const frame = document.createElement('iframe');
  frame.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&mute=1&controls=0&loop=1&playlist=${encodeURIComponent(id)}&playsinline=1&rel=0`;
  frame.title = t('catalog.preview');
  frame.setAttribute('aria-hidden', 'true');
  frame.tabIndex = -1;
  frame.allow = 'autoplay; encrypted-media';
  card.querySelector('.card-preview').appendChild(frame);
  card.classList.add('preview-visible');
}

// Hover previews. Guarded so the file also loads under a minimal DOM (the
// Node tests in tests/*.cjs give `document` no addEventListener); a browser
// always has it, so nothing changes there.
function _onCardPointerOver(event) {
  const card = event.target.closest?.('.result-card');
  if (!card || card.contains(event.relatedTarget)) return;
  // Keep the enlarged card inside its scrolling rail (or the result grid).
  const bounds = card.closest('.shelf-rail') || card.closest('#search-results');
  if (bounds) {
    const edge = bounds.getBoundingClientRect();
    const rect = card.getBoundingClientRect();
    const growth = card.offsetWidth * .12;
    card.style.transformOrigin = rect.left - growth < edge.left ? 'left 18%'
      : rect.right + growth > edge.right ? 'right 18%' : 'center 18%';
  }
  const timer = setTimeout(() => startCardPreview(card), 380);
  _cardPreviewTimers.set(card, timer);
}

function _onCardPointerOut(event) {
  const card = event.target.closest?.('.result-card');
  if (!card || card.contains(event.relatedTarget)) return;
  stopCardPreview(card);
}

if (typeof document.addEventListener === 'function') {
  document.addEventListener('pointerover', _onCardPointerOver);
  document.addEventListener('pointerout', _onCardPointerOut);
}

async function doSearch(options) {
  if (_catalogSection) return loadCatalog(!!options?.append);
  _cancelCatalogWork();
  const append = !!(options && options.append);
  const q = document.getElementById('search-input').value.trim();
  if (!q) return;
  // The boot no longer blocks routing on the domain, so a search restored
  // from the address can start before it is known. Asking costs nothing once
  // it is: ensureDomain() shares the one request.
  if (_sourceNeedsDomain(currentSource)) {
    await ensureDomain();
    if (!currentDomain && currentSource !== 'all') { openSettings(); return; }
  }
  if (append && (_loadingMore || _searchExhausted)) return;

  if (!append) {
    // Only a fresh search cancels what is in flight. A load-more must not abort
    // itself, and must not be aborted by the debounce still pending from the
    // last keystroke.
    if (_searchAbort) _searchAbort.abort();
    _searchAbort = new AbortController();
    _searchPage = 1;
    _searchExhausted = false;
    _searchResults = [];
    _requestStatus = {};
    _setStartPageVisible(false);
  }

  const container = document.getElementById('search-results');
  const btn = document.getElementById('search-btn');
  const moreBtn = document.getElementById('search-more-btn');
  const moreLabel = moreBtn ? moreBtn.innerHTML : '';
  if (append) {
    _loadingMore = true;
    if (moreBtn) {
      moreBtn.disabled = true;
      moreBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${searchLabel('search.loading')}`;
    }
  } else {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${searchLabel('search.button')}`;
    _showSearchSkeletons();
    _setMoreVisible(false);
  }

  try {
    let results;
    let stamp = null;
    try {
      const onHeaders = h => { stamp = _cacheStamp(h); };
      results = await api.get('/api/search', _searchParams(q, _searchPage),
                              append ? {onHeaders} : {signal: _searchAbort.signal, onHeaders});
    } catch (e) {
      if (e.name === 'AbortError') throw e;   // handled by the catch below
      const detail = escapeHtml(errText(e));
      if (append) showToast(detail, 'danger');
      else container.innerHTML = `<div class="col-12"><div class="alert alert-danger">${detail}</div></div>`;
      return;
    }
    if (!append) {
      container.innerHTML = '';
      if (stamp && results.length) container.insertAdjacentHTML('beforeend', _cacheStampMarkup(stamp, 'col-12'));
    }

    renderResultCards(results, container, _searchResults.length);
    _searchResults = _searchResults.concat(results);
    syncHash();

    // A filtered page coming back empty means "none of that kind here", not
    // "no more results". StreamingCommunity has no filter of its own, so the
    // panel applies one per page, and the source groups its results by kind:
    // measured, a query whose first page is sixty series has its films on a
    // later one. AnimeUnity filters at the source, where the offset walks the
    // filtered catalogue, so there an empty page really is the end.
    const filterIsPerPage = currentSource !== 'animeunity' && !!_kindFilter;
    _searchExhausted = !results.length && !filterIsPerPage;

    if (!_searchResults.length) {
      container.innerHTML = `<div class="col-12"><p class="text-muted">${searchLabel('search.empty')}</p></div>`;
    } else if (append && !results.length) {
      showToast(t('search.noMore'), 'info');
    }
    _setMoreVisible(!_searchExhausted);

    loadRequestStatuses(results.filter(r => r.type !== 'movie').map(r => String(r.id)));
  } catch(e) {
    if (e.name === 'AbortError') return; // cancelled by new search
    if (append) showToast(t('search.networkError'), 'danger');
    else container.innerHTML=`<div class="col-12"><div class="alert alert-danger">${t('common.error')}: ${escapeHtml(e.message)}</div></div>`;
  } finally {
    if (append) {
      _loadingMore = false;
      if (moreBtn) { moreBtn.disabled = false; moreBtn.innerHTML = moreLabel; scLanguage.localize(moreBtn); }
    } else {
      btn.disabled=false; btn.innerHTML=`<i class="ti ti-search me-1"></i>${searchLabel('search.button')}`;
    }
  }
}

async function loadMoreResults() {
  if (_loadingMore || _searchExhausted) return;
  if (!_catalogSection) _searchPage += 1;
  await doSearch({append: true});
}

// ── Start page ────────────────────────────────────────────────────────────────
//
// With nothing typed, the source's own front page: what is trending, what was
// added lately, today's top ten, the latest anime episodes. It is decoration, so
// it degrades to a bare search box rather than to an error banner — a red box
// where a carousel was is worse than no carousel.

let _homeAbort = null;
const _homeCache = {};   // source -> shelves, for a switch back and forth

// When an answer came from the panel's own cache (X-Cache: HIT), how old it is:
// the server keeps the source's pages for a while to stay polite to it, and
// says so rather than passing them off as live.
function _cacheStamp(headers) {
  if (!headers || headers.get('X-Cache') !== 'HIT') return null;
  const at = Number(headers.get('X-Cache-Updated'));
  return at ? {at: at * 1000, stale: headers.get('X-Stale') || ''} : null;
}

function _cacheStampMarkup(stamp, extraClass = '') {
  if (!stamp) return '';
  const hhmm = new Date(stamp.at).toLocaleTimeString('it-IT', {hour: '2-digit', minute: '2-digit'});
  const why = stamp.stale === 'backoff' || stamp.stale === 'budget'
    ? ' title="La sorgente è in pausa: copia salvata"' : '';
  return `<div class="${extraClass} small text-muted source-updated"${why}>` +
    `<i class="ti ti-clock me-1" aria-hidden="true"></i>Aggiornato alle ${escapeHtml(hhmm)}</div>`;
}

function _setStartPageVisible(on) {
  const shelves = document.getElementById('home-shelves');
  const results = document.getElementById('search-results');
  const hero = document.getElementById('search-hero');
  if (hero) hero.style.display = on ? '' : 'none';
  if (shelves) shelves.style.display = on ? '' : 'none';
  if (results) results.style.display = on ? 'none' : '';
  if (on) _setMoreVisible(false);
}

// What happens on arriving at the search page, from the nav or from a link.
//
// With an empty box it is the start page. With a query it is that query's
// results — but only when there are none on screen, so coming back to the tab
// after a look at Download does not silently re-run the search you already
// have.
function searchPageEnter() {
  if (_catalogSection) { doSearch(); return; }
  const input = document.getElementById('search-input');
  if (!input || !input.value.trim()) { showStartPage(); return; }
  if (!_searchResults.length) doSearch();
  else _setStartPageVisible(false);
}


function _shelfSkeletons() {
  const card = '<div class="shelf-item"><div class="skeleton skeleton-card"></div></div>';
  return ('<div class="shelf"><div class="shelf-rail">' + card.repeat(8) + '</div></div>').repeat(2);
}

async function showStartPage() {
  _catalogSection = null;
  _cancelCatalogWork();
  const homeGeneration = _catalogGeneration;
  if (_searchAbort) _searchAbort.abort();
  const host = document.getElementById('home-shelves');
  if (!host) return;
  if (_sourceNeedsDomain(currentSource)) {
    await ensureDomain();
    if (homeGeneration !== _catalogGeneration) return;
    if (!currentDomain) { _setStartPageVisible(false); return; }
  }

  _setStartPageVisible(true);
  const cacheKey = `${currentSource}:${currentDomain || ''}`;
  const cached = _homeCache[cacheKey];
  if (cached && cached.expires > Date.now()) {
    renderShelves(); renderHomeExtras(cached); return;
  }

  if (_homeAbort) _homeAbort.abort();
  _homeAbort = new AbortController();
  const source = currentSource;
  host.innerHTML = _shelfSkeletons();
  try {
    // A 409 with no domain configured throws like any other refusal, and
    // lands in the catch below, which leaves the same bare search box.
    let stamp = null;
    const body = await api.get('/api/home', {source}, {signal: _homeAbort.signal,
                                                       onHeaders: h => { stamp = _cacheStamp(h); }});
    _homeCache[cacheKey] = {shelves: body.shelves || [], genres: body.genres || [], stamp,
                            expires: Date.now() + 900000};
    if (homeGeneration !== _catalogGeneration || source !== currentSource || _catalogSection || document.getElementById('search-input')?.value.trim()) return;
    renderShelves();
    renderHomeExtras(_homeCache[cacheKey]);
  } catch (e) {
    if (e.name === 'AbortError') return;
    host.innerHTML = '';
  }
}

// The start page's own backdrop: a handful of titles the source is showing
// today, behind the headline. Decoration, so it is drawn from what has
// already been fetched and simply stays empty when there is nothing.
function _renderHeroArt(items) {
  const art = document.getElementById('search-hero-art');
  if (!art) return;
  const withArt = items.filter(i => i.poster);
  if (!withArt.length) { art.innerHTML = ''; return; }
  // Sampled without replacement, so the strip never repeats a title.
  const pool = [...withArt];
  const picked = [];
  while (picked.length < 7 && pool.length) {
    picked.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
  }
  art.innerHTML = picked
    .map(i => `<span style="background-image:url('${encodeURI(posterUrl(i))}')"></span>`)
    .join('');
}

// Today's top ten on the start page: keyed on the stable machine name, with the
// shown title as a fallback for a shelf whose key the source renamed.
function _isTop10Shelf(shelf) {
  return !!shelf && (shelf.key === 'top10' || shelf.title === 'Top 10 di oggi');
}

function renderShelves() {
  const host = document.getElementById('home-shelves');
  const entry = _homeCache[`${currentSource}:${currentDomain || ''}`];
  const shelves = entry?.shelves || [];
  host.innerHTML = shelves.length ? _cacheStampMarkup(entry?.stamp) : '';
  // One flat array across every rail, reset once here and appended to in order,
  // so openTitle(idx) needs no special case for the start page.
  _searchResults = [];
  _requestStatus = {};
  const ribbonIds = [];

  _shelfSyncs = [];
  shelves.forEach(shelf => {
    const section = document.createElement('div');
    section.className = 'shelf';
    section.innerHTML =
      `<div class="shelf-head"><div class="shelf-title">${catalogLabelMarkup(shelf.title)}</div></div>` +
      '<div class="shelf-rail-wrap">' +
        `<button class="shelf-arrow shelf-arrow-prev" hidden data-i18n-aria-label="catalog.previous" aria-label="${t('catalog.previous')}">` +
          '<i class="ti ti-chevron-left"></i></button>' +
        '<div class="shelf-rail"></div>' +
        `<button class="shelf-arrow shelf-arrow-next" hidden data-i18n-aria-label="catalog.next" aria-label="${t('catalog.next')}">` +
          '<i class="ti ti-chevron-right"></i></button>' +
      '</div>';
    // /api/browse currently supports only these StreamingCommunity sections.
    // AnimeUnity's latest_episodes/featured shelves have no paginated endpoint.
    if (currentSource === 'streamingcommunity' && ['trending', 'latest', 'top10'].includes(shelf.key)) {
      section.querySelector('.shelf-head').insertAdjacentHTML('beforeend',
        `<button class="source-btn shelf-browse" data-action="catalog:open" data-section="${shelf.key}">${searchLabel('catalog.browse')} <i class="ti ti-arrow-right" aria-hidden="true"></i></button>`);
    }
    const ranked = _isTop10Shelf(shelf);
    if (ranked) section.className = 'shelf shelf-ranked';
    renderResultCards(shelf.items, section.querySelector('.shelf-rail'),
                      _searchResults.length, 'shelf-item', {ranked});
    _searchResults = _searchResults.concat(shelf.items);
    shelf.items.forEach(i => { if (i.type !== 'movie') ribbonIds.push(String(i.id)); });
    host.appendChild(section);
    _shelfSyncs.push(_wireShelfArrows(section));
  });
  _renderHeroArt(_searchResults);

  applyClientFilter();
  loadRequestStatuses(ribbonIds);
}

// The arrows scroll a few titles at a time rather than a whole screenful: the
// rail is a browse, and losing your place in it is worse than taking two clicks.
// Each one hides itself at the end it points at, so a rail that already fits
// shows neither.
let _shelfSyncs = [];

function _wireShelfArrows(section) {
  const rail = section.querySelector('.shelf-rail');
  const prev = section.querySelector('.shelf-arrow-prev');
  const next = section.querySelector('.shelf-arrow-next');

  function step() {
    const card = rail.querySelector('.shelf-item:not(.d-none)');
    const one = (card ? card.offsetWidth : 132) + 12;   // the rail's gap
    // Three cards, unless the rail is too narrow to show three — then as many
    // as fit, and never less than one.
    return Math.max(one, Math.min(one * 3, Math.floor(rail.clientWidth / one) * one));
  }

  function sync() {
    const max = rail.scrollWidth - rail.clientWidth;
    const scrollable = max > 4;   // a couple of pixels of rounding is not overflow
    prev.hidden = !scrollable || rail.scrollLeft <= 2;
    next.hidden = !scrollable || rail.scrollLeft >= max - 2;
  }

  prev.addEventListener('click', () => rail.scrollBy({left: -step(), behavior: 'smooth'}));
  next.addEventListener('click', () => rail.scrollBy({left: step(), behavior: 'smooth'}));
  rail.addEventListener('scroll', sync, {passive: true});
  sync();
  return sync;
}

function _syncShelfArrows() {
  _shelfSyncs.forEach(sync => sync());
}

window.addEventListener('resize', _syncShelfArrows);

function itemMatchesFilter(item) {
  if (_kindFilter &&
      String(item.media_type || item.type || '').toLowerCase() !== _kindFilter) return false;
  if (_dubOnly && currentSource === 'animeunity' && !item.dubbed) return false;
  return true;
}

// The rails are whatever the source's front page holds: there is no server-side
// variant of them to ask for, and no paging to keep honest. So the chips hide
// cards instead of refetching — instant, and it cannot desynchronise an index,
// because nothing is removed and nothing is reordered.
function applyClientFilter() {
  const host = document.getElementById('home-shelves');
  if (!host) return;
  host.querySelectorAll('.shelf').forEach(section => {
    let visible = 0;
    section.querySelectorAll('.shelf-item').forEach(card => {
      const item = _searchResults[Number(card.dataset.idx)];
      const show = !item || itemMatchesFilter(item);
      card.classList.toggle('d-none', !show);
      if (show) visible++;
    });
    section.classList.toggle('d-none', visible === 0);
  });
  _syncShelfArrows();
}

// ── Request status on the result cards ─────────────────────────────────────────
//
// The one thing worth taking from Seerr: the state of a title is readable on the
// card itself, without opening anything.

const STATUS_RIBBONS = {
  pending:         { label: 'catalog.pending',    cls: 'ribbon-pending',   icon: 'ti-clock' },
  approved:        { label: 'catalog.approved',    cls: 'ribbon-approved',  icon: 'ti-check' },
  downloading:     { label: 'catalog.downloading',  cls: 'ribbon-download',  icon: 'ti-download' },
  // Not "Disponibile": the panel never learns how many episodes a series has
  // without asking the source, and this ribbon is painted from one call that
  // decorates a whole page of cards. "In libreria" is what it can actually
  // vouch for — the count says how much, instead of implying all of it.
  completed:       { label: 'catalog.inLibrary',  cls: 'ribbon-available', icon: 'ti-circle-check' },
  available:       { label: 'catalog.inLibrary',  cls: 'ribbon-available', icon: 'ti-circle-check' },
  denied:          { label: 'catalog.denied',    cls: 'ribbon-denied',    icon: 'ti-x' },
  failed:          { label: 'catalog.failed',      cls: 'ribbon-denied',    icon: 'ti-alert-triangle' },
  needs_attention: { label: 'catalog.attention',   cls: 'ribbon-attention', icon: 'ti-alert-circle' },
  cancelled:       { label: 'catalog.cancelled',    cls: 'ribbon-denied',    icon: 'ti-ban' },
};

async function loadRequestStatuses(externalIds) {
  if (!externalIds.length || !(can('REQUEST') || can('DOWNLOAD'))) return;
  if (!DOWNLOAD_SOURCES.includes(currentSource)) return;
  try {
    const statuses = await api.post('/api/requests/status',
      { source: currentSource, external_ids: externalIds });
    // Merged, not replaced. Called with only the new page's ids, an
    // assignment would wipe every ribbon already painted on the pages
    // before it, because renderRequestRibbons re-reads the whole map.
    // Staleness is bounded: _requestStatus is cleared on every fresh
    // search and on a source switch.
    Object.assign(_requestStatus, statuses);
    renderRequestRibbons();
  } catch (e) { /* the cards simply stay plain */ }
}

const IN_LIBRARY_STATUSES = new Set(['completed', 'available']);

// What the ribbon can add without overstating: one episode names itself, more
// than one is counted. The server sends season and episode_number precisely so
// this does not have to guess, and a film is never counted — it is one file,
// and films carry no ribbon anyway.
function ribbonDetail(info) {
  if (!IN_LIBRARY_STATUSES.has(info.status) || info.media_type === 'film') return '';
  const n = info.library_count || 0;
  if (n === 1 && info.season != null && info.episode_number != null) {
    const pad = v => String(v).padStart(2, '0');
    return `S${pad(info.season)}E${pad(info.episode_number)}`;
  }
  return n ? `${n} ep` : '';
}

function renderRequestRibbons() {
  document.querySelectorAll('[data-ribbon-for]').forEach(el => {
    const info = _requestStatus[el.dataset.ribbonFor];
    const style = info && STATUS_RIBBONS[info.status];
    if (!style) { el.innerHTML = ''; el.className = 'status-ribbon'; return; }
    const detail = ribbonDetail(info);
    el.className = `status-ribbon ${style.cls}`;
    el.innerHTML = `<i class="ti ${style.icon}"></i>${t(style.label)}` +
      (detail ? `<span class="ribbon-count">${escapeHtml(detail)}</span>` : '');
  });
}


// ── Delegated handlers ───────────────────────────────────────────────────────

registerActions({
  'catalog:open': d => openCatalog(d.section, d.genre),
  'catalog:home': () => { _catalogSection = null; navigate('search'); },
  'catalog:sections': () => loadHomeSections(),
  'search:source': d => setSource(d.source),
  'search:run':    () => doSearch(),
  'search:more':   () => loadMoreResults(),
  'search:kind':   d => setKindFilter(d.kind),
  'search:dub':    d => setDubOnly(d.on === '1'),
  'search:open':   d => openTitle(Number(d.idx)),
});


// ── The address ──────────────────────────────────────────────────────────────
//
// The query is the page here, so it is what the link has to carry. The page
// number is not: restoring "page 4" would mean four requests to the source
// before anything appeared, and the results of pages 1-3 are what the user
// actually wants back first.

let _catalogSection = null;
let _catalogAbort = null;
let _catalogGeneration = 0;
let _homeSectionState = null;
let _homeObserver = null;

function _cancelCatalogWork() {
  _catalogGeneration++;
  _catalogAbort?.abort();
  _homeAbort?.abort();
  _homeObserver?.disconnect();
  _homeSectionState = null;
  _loadingMore = false;
  const heading = document.getElementById('catalog-heading');
  if (heading) heading.hidden = true;
}

function openCatalog(section, genre) {
  navigate('search', [], {section, genre: genre || null});
}

function renderHomeExtras(entry) {
  if (currentSource !== 'streamingcommunity') return;
  const host = document.getElementById('home-shelves');
  const extra = document.createElement('div');
  extra.id = 'home-extra';
  extra.innerHTML = '<div class="search-filters" id="home-genres"></div>' +
    '<div id="home-extra-rows"></div><div class="text-center my-3">' +
    `<button class="source-btn" data-action="catalog:sections" id="home-sections-more">${searchLabel('catalog.moreGenres')}</button></div>`;
  host.appendChild(extra);
  const genres = extra.querySelector('#home-genres');
  entry.genres.forEach(g => {
    const button = document.createElement('button');
    button.className = 'source-btn';
    button.dataset.catalogLabel = g.name;
    button.textContent = catalogLabel(g.name);
    button.dataset.action = 'catalog:open';
    button.dataset.section = 'genre';
    button.dataset.genre = String(g.id);
    genres.appendChild(button);
  });
  _homeSectionState = {generation: _catalogGeneration, loading: false, offset: 0,
    sections: [{section: 'upcoming', title: 'In arrivo'},
      ...entry.genres.map(g => ({section: 'genre', genre_id: g.id, title: g.name}))]};
  if (typeof IntersectionObserver !== 'undefined') {
    _homeObserver = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) loadHomeSections();
    }, {rootMargin: '200px'});
    _homeObserver.observe(extra.querySelector('#home-sections-more'));
  }
  loadHomeSections();
}

async function loadHomeSections() {
  const state = _homeSectionState;
  if (!state || state.loading || state.generation !== _catalogGeneration) return;
  const batch = state.sections.slice(state.offset, state.offset + 3);
  if (!batch.length) return;
  state.loading = true;
  const button = document.getElementById('home-sections-more');
  button.disabled = true;
  const rows = await Promise.all(batch.map(async spec => {
    try { return {spec, data: await api.get('/api/home/section', {
      section: spec.section, genre_id: spec.genre_id,
    })}; } catch (error) { return {spec, error}; }
  }));
  if (state !== _homeSectionState || state.generation !== _catalogGeneration) return;
  let failures = 0;
  rows.forEach(({spec, data, error}) => {
    if (error) { failures++; return; }
    const section = document.createElement('section');
    section.className = 'shelf';
    section.innerHTML = `<div class="shelf-head"><div class="shelf-title">${catalogLabelMarkup(data.title)}</div>` +
      `<button class="source-btn shelf-browse" data-action="catalog:open" data-section="${spec.section}"` +
      ` data-genre="${spec.genre_id || ''}">${searchLabel('catalog.browse')} <i class="ti ti-arrow-right" aria-hidden="true"></i></button></div>` +
      `<div class="shelf-rail-wrap"><button class="shelf-arrow shelf-arrow-prev" hidden data-i18n-aria-label="catalog.previous" aria-label="${t('catalog.previous')}">‹</button>` +
      `<div class="shelf-rail"></div><button class="shelf-arrow shelf-arrow-next" hidden data-i18n-aria-label="catalog.next" aria-label="${t('catalog.next')}">›</button></div>`;
    renderResultCards(data.items.slice(0, 30), section.querySelector('.shelf-rail'), _searchResults.length, 'shelf-item');
    _searchResults.push(...data.items.slice(0, 30));
    if (!data.items.length) section.insertAdjacentHTML('beforeend', `<p class="text-muted">${searchLabel('catalog.empty')}</p>`);
    document.getElementById('home-extra-rows').appendChild(section);
    _shelfSyncs.push(_wireShelfArrows(section));
    loadRequestStatuses(data.items.filter(i => i.type !== 'movie').map(i => String(i.id)));
  });
  state.offset += batch.length;
  // Failed sections remain retryable without repeating the successful rows.
  state.sections.push(...rows.filter(row => row.error).map(row => row.spec));
  state.loading = false;
  button.disabled = false;
  button.dataset.i18n = failures ? 'catalog.retrySections' : 'catalog.moreGenres';
  button.textContent = t(button.dataset.i18n);
  button.hidden = state.offset >= state.sections.length;
  if (failures) _homeObserver?.disconnect();
  applyClientFilter();
}

async function loadCatalog(append = false) {
  if (append && (_loadingMore || _searchExhausted)) return;
  if (!append) {
    _cancelCatalogWork();
    _homeAbort?.abort();
    _searchAbort?.abort();
    _catalogAbort = new AbortController();
    _searchPage = 1;
    _searchResults = [];
    _searchExhausted = false;
    _setStartPageVisible(false);
    _showSearchSkeletons();
    _setMoreVisible(false);
  }
  const generation = _catalogGeneration;
  const spec = {..._catalogSection};
  _loadingMore = true;
  const more = document.getElementById('search-more-btn');
  more.disabled = true;
  const heading = document.getElementById('catalog-heading');
  heading.hidden = false;
  document.getElementById('catalog-title').dataset.catalogLabel = 'Catalogo';
  document.getElementById('catalog-title').textContent = t('catalog.title');
  try {
    await ensureDomain();
    if (generation !== _catalogGeneration) return;
    const data = await api.get('/api/browse', {...spec, page: append ? _searchPage + 1 : 1,
      media_type: _kindFilter || undefined}, {signal: _catalogAbort.signal});
    if (generation !== _catalogGeneration) return;
    const container = document.getElementById('search-results');
    if (!append) container.innerHTML = '';
    renderResultCards(data.items, container, _searchResults.length);
    _searchResults.push(...data.items);
    _searchPage = data.page;
    _searchExhausted = !data.has_more;
    document.getElementById('catalog-title').dataset.catalogLabel = data.title;
    document.getElementById('catalog-title').textContent = catalogLabel(data.title);
    if (!_searchResults.length) container.innerHTML = `<p class="text-muted">${searchLabel('catalog.empty')}</p>`;
    _setMoreVisible(data.has_more);
    loadRequestStatuses(data.items.filter(i => i.type !== 'movie').map(i => String(i.id)));
  } catch (error) {
    if (error.name === 'AbortError' || generation !== _catalogGeneration) return;
    if (!append) document.getElementById('search-results').textContent = errText(error, t('catalog.unavailable'));
    else showToast(errText(error), 'danger');
  } finally {
    if (generation === _catalogGeneration) { _loadingMore = false; more.disabled = false; }
  }
}

registerPageHash('search', {
  read: () => ({
    params: {
      q: document.getElementById('search-input')?.value.trim() || null,
      src: ['animeunity', 'raiplay'].includes(currentSource) ? currentSource : null,
      type: _kindFilter || null,
      dub: _dubOnly ? '1' : null,
      section: _catalogSection?.section || null,
      genre: _catalogSection?.genre_id || null,
    },
  }),
  apply: params => {
    _cancelCatalogWork();
    _catalogSection = ['trending', 'latest', 'top10', 'upcoming', 'genre'].includes(params.section)
      ? {section: params.section, genre_id: params.section === 'genre' ? Number(params.genre) : undefined} : null;
    const input = document.getElementById('search-input');
    if (input) input.value = params.q || '';
    // Not setSource(): that clears the grid and re-runs the search, which is
    // the opposite of restoring one. Only the parts that are state.
    currentSource = !_catalogSection && ['all', 'animeunity', 'raiplay'].includes(params.src) ? params.src : 'streamingcommunity';
    document.getElementById('src-all')?.classList.toggle('active', currentSource === 'all');
    document.getElementById('src-sc')?.classList.toggle('active', currentSource === 'streamingcommunity');
    document.getElementById('src-au')?.classList.toggle('active', currentSource === 'animeunity');
    document.getElementById('src-rai')?.classList.toggle('active', currentSource === 'raiplay');
    if (input) input.dataset.i18nPlaceholder = currentSource === 'animeunity' ? 'search.animePlaceholder' : 'search.placeholder';
    if (input) input.placeholder = currentSource === 'animeunity' ? t('search.animePlaceholder') : t('search.placeholder');
    _kindFilter = params.type || '';
    _dubOnly = params.dub === '1';
    _searchResults = [];
    _searchPage = 1;
    _searchExhausted = false;
    renderSearchFilters();
  },
});

// Update labels in place: card indices, paging, scroll positions and requests stay intact.
function searchLabel(key, fallback = '') {
  return key ? `<span data-i18n="${key}">${escapeHtml(t(key))}</span>` : escapeHtml(fallback);
}
const CATALOG_LABEL_KEYS = {
  "Catalogo": "catalog.title",
"Azione": "catalog.genre0",
"Avventura": "catalog.genre1",
"Animazione": "catalog.genre2",
"Commedia": "catalog.genre3",
"Crime": "catalog.genre4",
"Documentario": "catalog.genre5",
"Dramma": "catalog.genre6",
"Drammatico": "catalog.genre7",
"Famiglia": "catalog.genre8",
"Fantasy": "catalog.genre9",
"Fantascienza": "catalog.genre10",
"Horror": "catalog.genre11",
"Mistero": "catalog.genre12",
"Musica": "catalog.genre13",
"Romance": "catalog.genre14",
"Romantico": "catalog.genre15",
"Storia": "catalog.genre16",
"Thriller": "catalog.genre17",
"Guerra": "catalog.genre18",
"Western": "catalog.genre19",
"Sci-Fi & Fantasy": "catalog.genre20",
"Action & Adventure": "catalog.genre21",
"Bambini": "catalog.genre22",
"Film TV": "catalog.genre23",

  "In arrivo": "catalog.upcoming",
  "Di tendenza": "catalog.trending",
  "Aggiunti di recente": "catalog.latest",
  "Top 10 di oggi": "catalog.top10",
  "Ultimi episodi": "catalog.latestEpisodes",
  "In evidenza": "catalog.featured"
};
function catalogLabel(value) {
  return CATALOG_LABEL_KEYS[value] ? t(CATALOG_LABEL_KEYS[value]) : value;
}
function catalogLabelMarkup(value) {
  return `<span data-catalog-label="${escapeHtml(value)}">${escapeHtml(catalogLabel(value))}</span>`;
}
window.addEventListener('sc:language-changed', () => {
  const root = document.getElementById('page-search');
  if (!root) return;
  scLanguage.localize(root);
  const input = document.getElementById('search-input');
  const key = currentSource === 'animeunity' ? 'search.animePlaceholder' : 'search.placeholder';
  if (input) { input.dataset.i18nPlaceholder = key; input.placeholder = t(key); }
  renderSearchFilters();
  renderRequestRibbons();
  root.querySelectorAll('[data-catalog-label]').forEach(node => {
    node.textContent = catalogLabel(node.dataset.catalogLabel);
  });
  root.querySelectorAll('.card-preview iframe').forEach(frame => { frame.title = t('catalog.preview'); });
});

// Only StreamingCommunity titles have numeric ids on /api/metadata. RaiPlay
// (metadata-only) ids are "programmi:<slug>": asking the metadata route for
// them is a 400 on every card, so those cards simply keep their own artwork.
function _isStreamingItem(item) {
  const source = item && item.source;
  return (!source || source === 'streamingcommunity') && /^\d+$/.test(String(item && item.id));
}
