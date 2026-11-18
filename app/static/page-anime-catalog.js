/* AnimeUnity: complete catalogue, recent episode pages and weekly schedule. */
const ANIME_VIEWS = ['home', 'latest', 'archive', 'calendar', 'latest_animes', 'upcoming', 'top_day', 'top_week', 'top_month'];
const ANIME_SORTS = ['title_az', 'title_za', 'latest', 'score', 'views'];
let _animeState = {view: 'latest', page: 1, size: 60, sort: 'title_az', day: ''};
let _animeAbort = null;
let _animeGeneration = 0;
let _animeData = null;
let _animeReturnY = null;

function cancelAnimeBrowse() {
  _animeGeneration++;
  _animeAbort?.abort();
}
function resetAnimeBrowse() {
  cancelAnimeBrowse();
  _animeState = {view: 'latest', page: 1, size: 60, sort: 'title_az', day: ''};
  _animeData = null;
  _animeReturnY = null;
  document.getElementById('search-results')?.classList.remove('anime-grid');
  renderAnimeNavigation();
}
function animeCalendarActive() { return _animeState.view === 'calendar'; }
function animeHashState() {
  return currentSource !== 'animeunity' ? {} : {
    auview: _animeState.view, aupage: _animeState.page, ausize: _animeState.size,
    ausort: _animeState.sort, auday: _animeState.day || null,
  };
}
function restoreAnimeBrowse(params) {
  cancelAnimeBrowse();
  _animeState = {
    view: ANIME_VIEWS.includes(params.auview) ? params.auview : params.q ? 'archive' : 'latest',
    page: Math.min(10000, Math.max(1, parseInt(params.aupage, 10) || 1)),
    size: [20, 30, 60].includes(Number(params.ausize)) ? Number(params.ausize) : 60,
    sort: ANIME_SORTS.includes(params.ausort) ? params.ausort : 'title_az', day: params.auday || '',
  };
  _animeData = null;
  document.getElementById('search-results')?.classList.toggle('anime-grid', currentSource === 'animeunity' && _animeState.view !== 'home');
}
function setAnimeReturnPosition(y) {
  _animeReturnY = _animeState.view === 'home' ? null : Math.max(0, Number(y) || 0);
}
function animeShelfSection(key) {
  return ({latest_episodes: 'latest', latest_animes: 'latest_animes', upcoming_animes: 'upcoming',
    top_day: 'top_day', top_week: 'top_week', top_month: 'top_month'})[key];
}
function renderAnimeNavigation() {
  const active = currentSource === 'animeunity';
  const nav = document.getElementById('anime-navigation');
  if (!nav) return;
  nav.hidden = !active;
  nav.innerHTML = active ? ['latest', 'home', 'archive', 'calendar'].map(view =>
    `<button class="source-btn${_animeState.view === view ? ' active' : ''}" aria-pressed="${_animeState.view === view}" ` +
    `data-action="anime:view" data-view="${view}">${escapeHtml(t('anime.' + view))}</button>`).join('') : '';
  for (const id of ['anime-toolbar', 'anime-pagination-top', 'anime-pagination-bottom', 'anime-calendar-days']) {
    const node = document.getElementById(id);
    if (node && (!active || _animeState.view === 'home')) node.hidden = true;
  }
  if (!active || _animeState.view === 'home') document.getElementById('search-results')?.classList.remove('anime-grid');
}
function enterAnimeBrowse() {
  renderSearchFilters();
  if (_animeState.view === 'home') return false;
  loadAnimeBrowse();
  return true;
}
function openAnimeView(view) {
  if (!ANIME_VIEWS.includes(view)) return;
  clearTimeout(_searchDebounceTimer);
  _cancelCatalogWork();
  _searchAbort?.abort();
  _catalogSection = null;
  _animeState.view = view;
  _animeState.page = 1;
  _animeState.day = '';
  _animeData = null;
  document.getElementById('search-input').value = '';
  renderSearchFilters();
  syncHash();
  if (view === 'home') return showStartPage();
  return loadAnimeBrowse();
}
function searchAnimeArchive() {
  _animeState.view = 'archive';
  _animeState.page = 1;
  renderSearchFilters();
  return loadAnimeBrowse();
}
function animeFilterChanged() {
  if (_animeState.view === 'home') _animeState.view = 'archive';
  _animeState.page = 1;
  renderSearchFilters();
  return loadAnimeBrowse();
}
function animeLocale() { return t('anime.locale'); }
function animeDayLabel(day) {
  if (day.undetermined) return t('anime.undetermined');
  let text = day.weekday ? t('anime.day.' + day.weekday) : day.key.split(' - ')[0];
  if (day.date) text = new Intl.DateTimeFormat(animeLocale(), {weekday: 'long', day: 'numeric', month: 'short',
    timeZone: 'Europe/Rome'}).format(new Date(day.date + 'T12:00:00+01:00'));
  else if (day.display_date) text += ' ' + day.display_date;
  return text + (day.today ? ' · ' + t('anime.today') : '');
}
function animeEntryInfo(item) {
  const bits = [];
  if (item.episode_number != null) bits.push(t('anime.episode') + ' ' + item.episode_number);
  if (item.language) bits.push(String(item.language).toUpperCase());
  if (item.next_episode != null) bits.push(t('anime.nextEpisode') + ' ' + item.next_episode);
  if (item.release_time && !Number.isNaN(Date.parse(item.release_time))) {
    bits.push(new Intl.DateTimeFormat(animeLocale(), {hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Rome'})
      .format(new Date(item.release_time)));
  }
  if (item.paused_until) bits.push(t('anime.paused') + ' ' + item.paused_until);
  else if (item.aired) bits.push(t('anime.aired'));
  else if (item.late) bits.push(t('anime.late'));
  return bits.join(' · ');
}
function renderAnimeCards(items) {
  const container = document.getElementById('search-results');
  container.innerHTML = '';
  container.classList.add('anime-grid');
  _searchResults = items;
  _requestStatus = {};
  // Archive/calendar are poster grids; the original backdrop stays in the
  // model used by openTitle, while cards get a portrait cover.
  renderResultCards(items.map(i => ({...i, backdrop: ''})), container, 0, 'anime-card');
  [...container.children].forEach((card, index) => {
    const info = animeEntryInfo(items[index]);
    if (info) card.querySelector('.card-meta')?.insertAdjacentHTML('beforeend',
      `<div class="anime-entry-info">${escapeHtml(info)}</div>`);
  });
  if (!items.length) container.innerHTML = `<div class="col-12 text-muted">${escapeHtml(t('search.empty'))}</div>`;
  loadRequestStatuses([...new Set(items.map(i => String(i.id)))]);
}
function renderAnimeToolbar() {
  const toolbar = document.getElementById('anime-toolbar');
  toolbar.hidden = false;
  const calendar = animeCalendarActive();
  toolbar.innerHTML = `<h2 class="shelf-title">${escapeHtml(t('anime.' + _animeState.view))}</h2>` +
    (calendar ? `<span>${escapeHtml(t('anime.scheduleNote'))}</span>` :
    `<label>${escapeHtml(t('anime.perPage'))} <select class="form-select form-select-sm" data-change="anime:size">` +
      [20, 30, 60].map(n => `<option value="${n}"${n === _animeState.size ? ' selected' : ''}>${n}</option>`).join('') + '</select></label>') +
    (_animeState.view === 'archive' ? `<label>${escapeHtml(t('anime.order'))} <select class="form-select form-select-sm" data-change="anime:sort">` +
      ANIME_SORTS.map(s => `<option value="${s}"${s === _animeState.sort ? ' selected' : ''}>${escapeHtml(t('anime.sort.' + s))}</option>`).join('') + '</select></label>' : '') +
    `<button class="source-btn" data-action="anime:reload">${escapeHtml(t('common.refresh'))}</button>`;
}
function renderAnimePagination(data) {
  for (const id of ['anime-pagination-top', 'anime-pagination-bottom']) {
    const node = document.getElementById(id);
    node.hidden = animeCalendarActive();
    if (node.hidden) continue;
    node.innerHTML = `<button class="source-btn" data-action="anime:page" data-page="${data.page - 1}"${data.has_previous ? '' : ' disabled'}>${escapeHtml(t('catalog.previous'))}</button>` +
      `<label>${escapeHtml(t('anime.page'))} <input type="number" class="form-control form-control-sm d-inline-block" min="1" ` +
      `${data.last_page ? `max="${data.last_page}" ` : ''}value="${data.page}" data-change="anime:jump"></label>` +
      `<span>${data.last_page ? '/ ' + data.last_page : ''}${data.total != null ? ' · ' + data.total + ' ' + escapeHtml(t('anime.entries')) : ''}</span>` +
      `<button class="source-btn" data-action="anime:page" data-page="${data.page + 1}"${data.has_next ? '' : ' disabled'}>${escapeHtml(t('catalog.next'))}</button>`;
  }
}
function renderAnimeCalendar(data) {
  const days = document.getElementById('anime-calendar-days');
  days.hidden = false;
  if (!data.supported) {
    days.innerHTML = escapeHtml(t('anime.legacyCalendar'));
    renderAnimeCards([]);
    return;
  }
  let selected = data.days.find(d => d.key === _animeState.day) || data.days.find(d => d.today) || data.days[0];
  _animeState.day = selected?.key || '';
  days.innerHTML = data.days.map((day, index) =>
    `<button class="source-btn${day === selected ? ' active' : ''}" aria-pressed="${day === selected}" data-action="anime:day" data-day="${index}">` +
    `${escapeHtml(animeDayLabel(day))} (${day.items.length})</button>`).join('');
  renderAnimeCards(selected?.items || []);
}
function renderAnimeData() {
  if (!_animeData || currentSource !== 'animeunity' || _animeState.view === 'home') return;
  renderAnimeToolbar();
  document.getElementById('anime-calendar-days').hidden = !animeCalendarActive();
  renderAnimePagination(_animeData);
  if (animeCalendarActive()) renderAnimeCalendar(_animeData);
  else {
    renderAnimeCards(_animeData.items);
    if (_animeData.supported === false) document.getElementById('search-results').innerHTML =
      `<div class="col-12 text-muted">${escapeHtml(t('anime.legacySection'))}</div>`;
  }
}
async function loadAnimeBrowse() {
  _cancelCatalogWork();
  _searchAbort?.abort();
  _catalogSection = null;
  const generation = _animeGeneration;
  _animeAbort = new AbortController();
  _animeData = null;
  _setStartPageVisible(false);
  _setMoreVisible(false);
  renderSearchFilters();
  renderAnimeToolbar();
  for (const id of ['anime-calendar-days', 'anime-pagination-top', 'anime-pagination-bottom']) document.getElementById(id).hidden = true;
  const container = document.getElementById('search-results');
  container.classList.add('anime-grid');
  container.innerHTML = `<div class="col-12" role="status">${escapeHtml(t('search.loading'))}</div>`;
  syncHash();
  try {
    const calendar = animeCalendarActive();
    const params = calendar ? {dubbed: _dubOnly} : {
      section: _animeState.view, page: _animeState.page, per_page: _animeState.size,
      q: document.getElementById('search-input').value.trim(), media_type: _kindFilter || undefined,
      dubbed: _dubOnly, sort: _animeState.sort,
    };
    const data = await api.get(calendar ? '/api/anime/calendar' : '/api/anime/catalog', params, {signal: _animeAbort.signal});
    if (generation !== _animeGeneration || currentSource !== 'animeunity') return;
    if (!calendar && data.last_page && _animeState.page > data.last_page) {
      _animeState.page = data.last_page;
      return loadAnimeBrowse();
    }
    _animeData = data;
    renderAnimeData();
    syncHash();
    if (_animeReturnY != null) {
      const y = _animeReturnY;
      _animeReturnY = null;
      requestAnimationFrame(() => { if (generation === _animeGeneration) window.scrollTo(0, y); });
    }
  } catch (error) {
    if (generation !== _animeGeneration || currentSource !== 'animeunity' || error.name === 'AbortError') return;
    container.innerHTML = `<div class="col-12 alert alert-warning" role="alert">${escapeHtml(t('title.sourceUnavailable'))} ` +
      `<button class="btn btn-primary" data-action="anime:reload">${escapeHtml(t('common.retry'))}</button></div>`;
  }
}
function changeAnimePage(value) {
  const page = Number(value);
  if (!Number.isInteger(page) || page < 1 || page > (_animeData?.last_page || 10000)) return;
  _animeState.page = page;
  return loadAnimeBrowse();
}
registerActions({
  'anime:view': d => openAnimeView(d.view),
  'anime:reload': () => loadAnimeBrowse(),
  'anime:page': d => changeAnimePage(d.page),
  'anime:jump': (d, el) => changeAnimePage(el.value),
  'anime:size': (d, el) => {
    if (![20, 30, 60].includes(Number(el.value))) return;
    _animeState.size = Number(el.value); _animeState.page = 1; return loadAnimeBrowse();
  },
  'anime:sort': (d, el) => {
    if (!ANIME_SORTS.includes(el.value)) return;
    _animeState.sort = el.value; _animeState.page = 1; return loadAnimeBrowse();
  },
  'anime:day': d => {
    const day = _animeData?.days?.[Number(d.day)];
    if (!day) return;
    _animeState.day = day.key; renderAnimeCalendar(_animeData); syncHash();
  },
});
window.addEventListener('sc:language-changed', () => {
  if (currentSource === 'animeunity') { renderSearchFilters(); renderAnimeData(); }
});
