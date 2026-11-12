// Archivio: the source's /archive, sixty titles a page.
//
// Every request to the source here answers a user action: opening the page,
// changing a filter, pressing "Carica altri". Nothing is fetched on a timer, on
// scroll or on hover (the cards are rendered with noPreview, so hovering them
// asks the source nothing either). Coming back to the page shows what was
// already loaded instead of asking again.
//
// Genre and country are listed as the source sends them, but stay disabled
// while the backend does not know the query names /archive accepts for them
// (data.filters, app/core/catalog.py ARCHIVE_PARAMS).

const _arc = {
  kind: '', genre: '', country: '',
  page: 0, items: [], seen: new Set(),
  exhausted: false, loading: false, loaded: false, total: null,
  generation: 0, abort: null,
};

function _arcEl(id) { return document.getElementById(id); }

function _arcFilterKey() { return `${_arc.kind}|${_arc.genre}|${_arc.country}`; }
let _arcLoadedKey = null;

function archivePageEnter() {
  _arcRenderKinds();
  if (_arc.loaded && _arcLoadedKey === _arcFilterKey()) return undefined;
  return loadArchive(false);
}

function _arcRenderKinds() {
  (_arcEl('archive-kinds')?.querySelectorAll('[data-kind]') || []).forEach(btn =>
    btn.classList.toggle('active', btn.dataset.kind === _arc.kind));
}

function _arcFillSelect(select, rows, value, emptyKey, supported, labelOf) {
  if (!select) return;
  const options = rows.map(row =>
    `<option value="${escapeHtml(String(row.id))}"${String(row.id) === value ? ' selected' : ''}>` +
    `${escapeHtml(labelOf(row))}</option>`).join('');
  select.innerHTML = `<option value="" data-i18n="${emptyKey}">${escapeHtml(t(emptyKey))}</option>${options}`;
  select.value = value;
  select.disabled = !supported;
  select.title = supported ? '' : t('archive.filterUnsupported');
}

function _arcRenderCount() {
  const count = _arcEl('archive-count');
  if (count) count.textContent = _arc.total === null ? '' : t('archive.count', {count: _arc.total});
}

function _arcSetMore(on) {
  const wrap = _arcEl('archive-more');
  if (wrap) wrap.hidden = !on;
}

async function loadArchive(append = false) {
  if (append && (_arc.loading || _arc.exhausted)) return;
  if (!append) {
    _arc.generation++;
    _arc.abort?.abort();
    _arc.abort = new AbortController();
    _arc.page = 0;
    _arc.items = [];
    _arc.seen = new Set();
    _arc.exhausted = false;
    _arc.loaded = false;
    const box = _arcEl('archive-results');
    if (box) box.innerHTML = `<p class="text-muted">${escapeHtml(t('common.loading'))}</p>`;
    _arcSetMore(false);
  }
  const generation = _arc.generation;
  const key = _arcFilterKey();
  _arc.loading = true;
  const more = _arcEl('archive-more-btn');
  if (more) more.disabled = true;
  let stamp = null;
  try {
    await ensureDomain();
    if (generation !== _arc.generation) return;
    const data = await api.get('/api/archive', {
      page: append ? _arc.page + 1 : 1,
      media_type: _arc.kind || undefined,
      genre_id: _arc.genre || undefined,
      country_id: _arc.country || undefined,
    }, {signal: _arc.abort.signal, onHeaders: h => { stamp = _cacheStamp(h); }});
    if (generation !== _arc.generation) return;
    const box = _arcEl('archive-results');
    if (!append && box) box.innerHTML = '';
    // A page that brings nothing new (the source ignoring the page number,
    // say) ends the list: better one wasted request than an endless button.
    const fresh = (data.items || []).filter(item => {
      const id = `${item.type}:${item.id}`;
      if (_arc.seen.has(id)) return false;
      _arc.seen.add(id);
      return true;
    });
    if (box) renderResultCards(fresh, box, _arc.items.length, undefined,
      {action: 'archive:open', noPreview: true});
    _arc.items.push(...fresh);
    _arc.page = data.page;
    _arc.exhausted = !data.has_more || (append && !fresh.length);
    _arc.loaded = true;
    _arcLoadedKey = key;
    if (!_arc.items.length && box) box.innerHTML = `<p class="text-muted">${escapeHtml(t('catalog.empty'))}</p>`;
    _arcSetMore(!_arc.exhausted);
    const filters = data.filters || {};
    const genreLabel = typeof catalogLabel === 'function' ? row => catalogLabel(row.name) : row => row.name;
    _arcFillSelect(_arcEl('archive-genre'), data.genres || [], _arc.genre, 'archive.anyGenre', !!filters.genre, genreLabel);
    _arcFillSelect(_arcEl('archive-country'), data.countries || [], _arc.country, 'archive.anyCountry', !!filters.country, row => row.name);
    _arc.total = Number.isInteger(data.total_count) ? data.total_count : null;
    _arcRenderCount();
    const stampBox = _arcEl('archive-stamp');
    if (stampBox && !append) stampBox.innerHTML = typeof _cacheStampMarkup === 'function' ? _cacheStampMarkup(stamp, 'mb-2') : '';
    loadRequestStatuses(fresh.filter(i => i.type !== 'movie').map(i => String(i.id)));
  } catch (error) {
    if (error.name === 'AbortError' || generation !== _arc.generation) return;
    if (!append) {
      const box = _arcEl('archive-results');
      if (box) box.textContent = errText(error, t('archive.unavailable'));
    } else showToast(errText(error), 'danger');
  } finally {
    if (generation === _arc.generation) {
      _arc.loading = false;
      if (more) more.disabled = false;
    }
  }
}

function _arcFiltersChanged() {
  _arcRenderKinds();
  loadArchive(false);
  syncHash();
}

registerActions({
  'archive:kind': d => {
    if ((d.kind || '') === _arc.kind) return;
    _arc.kind = d.kind || '';
    _arcFiltersChanged();
  },
  'archive:filter': () => {
    const genre = _arcEl('archive-genre')?.value || '';
    const country = _arcEl('archive-country')?.value || '';
    if (genre === _arc.genre && country === _arc.country) return;
    _arc.genre = genre;
    _arc.country = country;
    _arcFiltersChanged();
  },
  'archive:more': () => loadArchive(true),
  'archive:open': d => {
    const item = _arc.items[Number(d.idx)];
    if (!item) return;
    _tpSeed = item;                     // spares the title page a round trip
    location.hash = titleHash(item);
  },
});

// The filters are the address; the page number is not (restoring page 4 would
// be four requests to the source before anything appeared).
registerPageHash('archive', {
  read: () => ({params: {type: _arc.kind || null, genre: _arc.genre || null, country: _arc.country || null}}),
  apply: params => {
    const pick = v => (/^\d+$/.test(String(v || '')) ? String(v) : '');
    _arc.kind = ['movie', 'tv'].includes(params.type) ? params.type : '';
    _arc.genre = pick(params.genre);
    _arc.country = pick(params.country);
  },
});

window.addEventListener('sc:language-changed', () => {
  const root = document.getElementById('page-archive');
  if (!root || typeof scLanguage === 'undefined') return;
  scLanguage.localize(root);
  _arcRenderCount();
  root.querySelectorAll('select[disabled]').forEach(select => { select.title = t('archive.filterUnsupported'); });
});
