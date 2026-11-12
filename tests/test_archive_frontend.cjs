const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

// A minimal DOM: only what page-archive.js touches.
function create(pages) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      id, value: '', innerHTML: '', textContent: '', title: '', hidden: false, disabled: false,
      dataset: {}, classList: {toggle() {}}, querySelectorAll: () => [],
    });
    return elements.get(id);
  }
  const calls = [], renders = [], actions = {};
  let hash;
  const context = vm.createContext({
    document: {getElementById: element}, window: {addEventListener() {}},
    location: {hash: ''}, AbortController,
    api: {get: async (path, params) => {
      calls.push({path, params});
      return pages(params, calls.length);
    }},
    ensureDomain: async () => {}, loadRequestStatuses() {}, showToast() {},
    escapeHtml: String, errText: (e, fallback) => e.message || fallback,
    registerActions: map => Object.assign(actions, map),
    registerPageHash: (name, spec) => { hash = spec; }, syncHash() {},
    renderResultCards: (items, box, base, cls, options) => renders.push({items, base, options}),
    _cacheStamp: () => null, _cacheStampMarkup: () => '',
    titleHash: item => `#/title/${item.type}/${item.id}`,
    t: (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key),
  });
  vm.runInContext(fs.readFileSync('app/static/page-archive.js', 'utf8'), context);
  return {context, calls, renders, actions, element, hash: () => hash,
    run: code => vm.runInContext(code, context)};
}

const titles = (from, n) => Array.from({length: n}, (_, i) => ({id: from + i, type: 'tv', name: `T${from + i}`}));
const page = (params, items, extra = {}) => ({
  items, page: params.page, total_count: 600, has_more: true,
  genres: [{id: 4, name: 'Azione'}], countries: [{id: 1, code: 'IT', name: 'Italia'}],
  filters: {genre: false, country: false, sort: false}, ...extra,
});

test('loading the script sends nothing: only an explicit action does', async () => {
  const app = create(params => page(params, titles(1, 60)));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.calls.length, 0);
});

test('opening the page asks one page; coming back asks nothing', async () => {
  const app = create(params => page(params, titles(1, 60)));
  await app.run('archivePageEnter()');
  await app.run('archivePageEnter()');
  assert.deepEqual(app.calls.map(c => [c.path, c.params.page]), [['/api/archive', 1]]);
  assert.equal(app.renders[0].options.action, 'archive:open');
  assert.equal(app.renders[0].options.noPreview, true);
  assert.equal(app.element('archive-more').hidden, false);
  assert.equal(app.element('archive-count').textContent, 'archive.count:{"count":600}');
});

test('load more advances one page; a page with nothing new ends the list', async () => {
  const app = create((params, n) => page(params, n === 1 ? titles(1, 60) : titles(1, 60)));
  await app.run('archivePageEnter()');
  await app.actions['archive:more']();
  await app.actions['archive:more']();
  assert.deepEqual(app.calls.map(c => c.params.page), [1, 2]);
  assert.equal(app.element('archive-more').hidden, true);
  assert.equal(app.renders[1].items.length, 0);
});

test('a filter change reloads page one with the filter and keeps it in the address', async () => {
  const app = create(params => page(params, titles(1, 3), {has_more: false}));
  await app.run('archivePageEnter()');
  app.actions['archive:kind']({kind: 'movie'});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(app.calls.map(c => [c.params.page, c.params.media_type]), [[1, undefined], [1, 'movie']]);
  assert.equal(app.hash().read().params.type, 'movie');
  assert.equal(app.element('archive-more').hidden, true);
});

test('unsupported genre/country selects stay disabled; hash ignores junk', async () => {
  const app = create(params => page(params, titles(1, 3)));
  app.hash().apply({type: 'anime', genre: '4<x', country: '1'});
  await app.run('archivePageEnter()');
  assert.equal(app.calls[0].params.media_type, undefined);
  assert.equal(app.calls[0].params.genre_id, undefined);
  assert.equal(app.calls[0].params.country_id, '1');
  assert.equal(app.element('archive-genre').disabled, true);
  assert.equal(app.element('archive-genre').title, 'archive.filterUnsupported');
  assert.match(app.element('archive-genre').innerHTML, /Azione/);
});

test('a card opens its own archive title, not a search result', async () => {
  const app = create(params => page(params, titles(7, 2)));
  await app.run('archivePageEnter()');
  app.actions['archive:open']({idx: '1'});
  assert.equal(app.context.location.hash, '#/title/tv/8');
  assert.equal(app.run('_tpSeed').id, 8);
});

test('an error shows a message and does not retry by itself', async () => {
  const app = create(() => { throw new Error('Nessuna richiesta inviata: sorgente in pausa'); });
  await app.run('archivePageEnter()');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.calls.length, 1);
  assert.match(app.element('archive-results').textContent, /in pausa/);
});
