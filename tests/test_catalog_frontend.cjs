const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

// The real translations, in Italian, the way the other frontend tests load them.
function italian() {
  const i18n = vm.createContext({
    window: {}, document: {addEventListener() {}},
    localStorage: {getItem: () => 'it'}, navigator: {language: 'it'},
  });
  vm.runInContext(fs.readFileSync('app/static/i18n.js', 'utf8'), i18n);
  return i18n.window.t;
}

function create() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', innerHTML: '', textContent: '', style: {}, hidden: false, dataset: {},
      classList: {toggle() {}}, querySelectorAll: () => [],
      appendChild() {}, querySelector: () => element('mock-rail'),
    });
    return elements.get(id);
  }
  const calls = [], renders = [];
  let hash;
  const context = vm.createContext({
    document: {getElementById: element, createElement: () => ({
      innerHTML: '', querySelector: () => element('mock-rail'), insertAdjacentHTML() {},
    })}, window: {addEventListener() {}},
    currentSource: 'streamingcommunity', currentDomain: 'source.test',
    _searchResults: [], _requestStatus: {}, AbortController,
    api: {get: async (path, params) => { calls.push({path, params}); return {
      title: 'Azione', items: [{id: params.page, type: 'movie'}], page: params.page, has_more: true,
    }; }},
    ensureDomain: async () => {}, loadRequestStatuses() {}, showToast() {},
    escapeHtml: String, errText: e => e.message, registerActions() {},
    registerPageHash: (name, spec) => {hash = spec;}, syncHash() {},
    t: italian(),
  });
  vm.runInContext(fs.readFileSync('app/static/page-search.js', 'utf8'), context);
  context.renderResultCards = items => renders.push(items);
  context._showSearchSkeletons = () => {};
  return {context, calls, renders, hash: () => hash, element,
    run: code => vm.runInContext(code, context)};
}

test('catalog route survives a reload and forces the StreamingCommunity source', async () => {
  const app = create();
  app.hash().apply({section: 'genre', genre: '4', src: 'animeunity', type: 'movie'});
  await app.run('loadCatalog()');
  assert.equal(app.calls[0].path, '/api/browse');
  assert.equal(app.calls[0].params.genre_id, 4);
  assert.equal(app.calls[0].params.media_type, 'movie');
  assert.equal(app.context.currentSource, 'streamingcommunity');
  assert.equal(app.hash().read().params.section, 'genre');
  assert.equal(app.element('catalog-title').textContent, 'Azione');
});

test('load more advances exactly one page and keeps card indices', async () => {
  const app = create();
  app.hash().apply({section: 'latest'});
  await app.run('loadCatalog()');
  await app.run('loadMoreResults()');
  assert.deepEqual(app.calls.map(c => c.params.page), [1, 2]);
  assert.equal(app.context._searchResults.length, 2);
});

test('failed load more retries the same page', async () => {
  const app = create();
  app.hash().apply({section: 'latest'});
  await app.run('loadCatalog()');
  const get = app.context.api.get;
  app.context.api.get = async () => {throw new Error('offline');};
  await app.run('loadMoreResults()');
  app.context.api.get = get;
  await app.run('loadMoreResults()');
  assert.deepEqual(app.calls.map(c => c.params.page), [1, 2]);
});

test('a late response cannot overwrite a different catalog', async () => {
  const app = create();
  let resolveOld;
  app.context.api.get = () => new Promise(resolve => {resolveOld = resolve;});
  app.hash().apply({section: 'upcoming'});
  const pending = app.run('loadCatalog()');
  await new Promise(resolve => setImmediate(resolve));
  app.hash().apply({section: 'genre', genre: '4'});
  resolveOld({title: 'Old', items: [{id: 99}], page: 1, has_more: false});
  await pending;
  assert.equal(app.renders.length, 0);
  assert.equal(app.context._searchResults.length, 0);
});

test('upcoming detail disables the primary action but available seasons remain usable', () => {
  const app = create();
  app.context.can = () => true;
  vm.runInContext(fs.readFileSync('app/static/page-title.js', 'utf8'), app.context);
  app.run('_tp = {comingSoon: true}; _tpRenderCta(); tpPrimary();');
  assert.match(app.element('th-cta').innerHTML, /non ancora disponibile/);
  app.run('_tpApplyMeta({coming_soon: false, upcoming_seasons: [{number: 2}]});');
  assert.equal(app.run('_tp.comingSoon'), false);
});

test('home cache expires and never reuses another domain', async () => {
  const app = create();
  app.context.renderShelves = () => {};
  app.context.renderHomeExtras = () => {};
  app.context.api.get = async (path) => {app.calls.push(path); return {shelves: [], genres: []};};
  await app.run('showStartPage()');
  await app.run('showStartPage()');
  assert.equal(app.calls.length, 1);
  app.context.currentDomain = 'new.test';
  await app.run('showStartPage()');
  assert.equal(app.calls.length, 2);
  app.run('_homeCache["streamingcommunity:new.test"].expires = 0');
  await app.run('showStartPage()');
  assert.equal(app.calls.length, 3);
});

test('partial section failure preserves successes and remains retryable', async () => {
  const app = create();
  app.context._wireShelfArrows = () => () => {};
  app.context.applyClientFilter = () => {};
  app.context.api.get = async (path, spec) => {
    if (spec.genre_id === 4) throw new Error('offline');
    return {title: 'In arrivo', items: [{id: 1, type: 'movie'}]};
  };
  app.run('_homeSectionState = {generation: _catalogGeneration, loading: false, offset: 0, sections: [{section: "upcoming"}, {section: "genre", genre_id: 4}]}');
  await app.run('loadHomeSections()');
  assert.equal(app.context._searchResults.length, 1);
  assert.equal(app.run('_homeSectionState.sections.length - _homeSectionState.offset'), 1);
  app.context.api.get = async () => ({title: 'Azione', items: [{id: 2, type: 'movie'}]});
  await app.run('loadHomeSections()');
  assert.equal(app.context._searchResults.length, 2);
  assert.equal(app.element('home-sections-more').hidden, true);
});
