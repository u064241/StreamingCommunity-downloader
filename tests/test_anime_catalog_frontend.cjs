const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create() {
  const elements = new Map(), actions = {}, events = {}, routes = {};
  function node() {
    const classes = new Set();
    return {innerHTML: '', children: [], dataset: {}, value: '', style: {}, hidden: false,
      classList: {add: x => classes.add(x), remove: x => classes.delete(x),
        toggle: (x, on) => on ? classes.add(x) : classes.delete(x)},
      appendChild(child) {this.children.push(child);},
      querySelector: () => ({insertAdjacentHTML() {}}), querySelectorAll: () => [],
      insertAdjacentHTML(_, html) {this.innerHTML += html;},
    };
  }
  const element = id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); };
  const context = vm.createContext({
    AbortController, URLSearchParams, Intl, setTimeout, clearTimeout,
    document: {getElementById: element, createElement: node, addEventListener() {}},
    window: {addEventListener: (name, fn) => {events[name] = fn;}, scrollTo() {}},
    currentSource: 'animeunity', currentDomain: '', _searchResults: [], _requestStatus: {},
    location: {hash: '#/search?src=animeunity'}, sessionStorage: {getItem: () => null},
    registerActions: map => Object.assign(actions, map), registerPageHash: (name, spec) => {routes[name] = spec;},
    t: key => key === 'anime.locale' ? 'en-GB' : key, escapeHtml: s => String(s).replaceAll('<', '&lt;'),
    syncHash() {}, itemYear: () => '', loadRequestStatuses() {},
    requestAnimationFrame: fn => fn(),
    api: {get: async () => ({items: [], page: 1, per_page: 60, total: 0, last_page: 1})},
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['page-search.js', 'page-anime-catalog.js', 'page-title.js']) run(fs.readFileSync('app/static/' + file, 'utf8'));
  return {context, run, actions, element, events, routes};
}

test('all seven requested filter buttons are visible on AnimeUnity arrival', () => {
  const app = create();
  app.context.renderSearchFilters();
  const html = app.element('search-filters').innerHTML;
  for (const kind of ['', 'movie', 'tv', 'ova', 'ona', 'special']) assert.ok(html.includes(`data-kind="${kind}"`));
  assert.match(html, /search:dub/);
  assert.equal(app.element('anime-navigation').hidden, false);
  assert.match(app.element('anime-navigation').innerHTML, /data-view="archive"/);
  assert.match(app.element('anime-navigation').innerHTML, /data-view="calendar"/);
});

test('default entry renders a complete 60-card request and numbered navigation', async () => {
  const app = create(); let call;
  app.context.api.get = async (path, params) => {
    call = {path, params};
    return {items: [], page: 1, total: 180, last_page: 3, has_next: true};
  };
  await app.context.loadAnimeBrowse();
  assert.equal(call.path, '/api/anime/catalog');
  assert.equal(call.params.section, 'latest');
  assert.equal(call.params.per_page, 60);
  assert.match(app.element('anime-pagination-top').innerHTML, /data-page="2"/);
  assert.match(app.element('anime-pagination-bottom').innerHTML, /180/);
  assert.equal(app.element('search-more').style.display, 'none');
});

test('archive jump beyond page 50 and filters reset to page one', async () => {
  const app = create(), calls = [];
  app.context.api.get = async (path, params) => {
    calls.push({...params});
    return {items: [], page: params.page, total: 6290, last_page: 210};
  };
  await app.actions['anime:view']({view: 'archive'});
  await app.actions['anime:jump']({}, {value: '210'});
  assert.equal(calls.at(-1).page, 210);
  app.run("_kindFilter = 'ova'; _dubOnly = true");
  await app.context.animeFilterChanged();
  assert.equal(calls.at(-1).page, 1);
  assert.equal(calls.at(-1).media_type, 'ova');
  assert.equal(calls.at(-1).dubbed, true);
});

test('stale response cannot replace newer source or page', async () => {
  const app = create(); let finish;
  app.context.api.get = () => new Promise(resolve => {finish = resolve;});
  const pending = app.context.loadAnimeBrowse();
  app.context.currentSource = 'raiplay';
  app.context.cancelAnimeBrowse();
  app.element('search-results').innerHTML = 'RaiPlay';
  finish({items: [], page: 1, total: 0, last_page: 1});
  await pending;
  assert.equal(app.element('search-results').innerHTML, 'RaiPlay');
});

test('failed page has retry and retry uses the same page', async () => {
  const app = create(); const calls = [];
  app.run('_animeState.page = 3');
  app.context.api.get = async (_, params) => {calls.push(params.page); throw new Error('offline');};
  await app.context.loadAnimeBrowse();
  assert.match(app.element('search-results').innerHTML, /anime:reload/);
  await app.actions['anime:reload']();
  assert.deepEqual(calls, [3, 3]);
});

test('calendar changes days without requesting the source again and localizes dates', async () => {
  const app = create(); let calls = 0;
  app.context.api.get = async (path, params) => {
    calls++; assert.equal(path, '/api/anime/calendar');
    return {supported: true, days: [
      {key: 'Oggi', weekday: 'wednesday', date: '2026-11-18', today: true, items: []},
      {key: 'Domani', weekday: 'thursday', date: '2026-11-19', items: []},
    ]};
  };
  await app.actions['anime:view']({view: 'calendar'});
  assert.match(app.element('anime-calendar-days').innerHTML, /Wednesday/);
  app.actions['anime:day']({day: '1'});
  assert.equal(app.run('_animeState.day'), 'Domani');
  assert.equal(calls, 1);
  assert.equal(app.element('anime-pagination-top').hidden, true);
  assert.match(app.element('search-filters').innerHTML, /search:dub/);
  assert.doesNotMatch(app.element('search-filters').innerHTML, /search:kind/);
});

test('hash restores archive page, size, order and filters without defaulting to home', () => {
  const app = create();
  app.routes.search.apply({src: 'animeunity', q: 'One', auview: 'archive', aupage: '89', ausize: '30', ausort: 'score', type: 'movie', dub: '1'});
  const params = app.routes.search.read().params;
  assert.equal(params.auview, 'archive'); assert.equal(params.aupage, 89);
  assert.equal(params.ausize, 30); assert.equal(params.ausort, 'score');
  assert.equal(params.q, 'One'); assert.equal(params.type, 'movie'); assert.equal(params.dub, '1');
});

test('title breadcrumb returns to the saved exact catalogue URL', () => {
  const app = create();
  const hash = '#/search?src=animeunity&auview=archive&aupage=89&ausize=30&type=movie&dub=1';
  app.context.sessionStorage.getItem = () => JSON.stringify({hash, y: 850});
  app.run("_tp={source:'animeunity'}");
  app.actions['tp:crumb-search']();
  assert.equal(app.context.location.hash, hash);
});

test('new labels exist in both languages including all weekdays', () => {
  for (const language of ['it', 'en']) {
    const context = vm.createContext({window: {}, document: {addEventListener() {}},
      localStorage: {getItem: () => language}, navigator: {language}});
    vm.runInContext(fs.readFileSync('app/static/i18n.js', 'utf8'), context);
    for (const key of ['archive', 'calendar', 'perPage', 'entries', 'latest', 'day.monday', 'day.sunday', 'legacyCalendar'])
      assert.notEqual(context.window.t('anime.' + key), 'anime.' + key);
  }
});
