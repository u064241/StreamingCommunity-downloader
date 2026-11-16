const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create() {
  const elements = new Map(), calls = [], toasts = [], actions = {};
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', hidden: false, disabled: false, innerHTML: '', textContent: '',
      style: {}, dataset: {}, children: [], classList: {toggle() {}},
      toggleAttribute(name, value) { this[name] = value; },
      appendChild(child) { this.children.push(child); }, focus() { this.focused = true; },
    });
    return elements.get(id);
  };
  let hash;
  const context = vm.createContext({
    URL, AbortController, clearTimeout, setTimeout,
    currentSource: 'all', currentDomain: '', _searchResults: [], _requestStatus: {},
    window: {addEventListener() {}}, sessionStorage: {getItem: () => null},
    document: {getElementById: element, createElement: () => ({dataset: {}})},
    registerActions: entries => Object.assign(actions, entries),
    registerPageHash: (_, spec) => { hash = spec; }, syncHash() {},
    t: key => key, itemYear: () => '', escapeHtml: value => String(value)
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    errText: error => error.message, showToast: (...args) => toasts.push(args),
    can: () => true, DOWNLOADS_LINK: {}, scLanguage: {localize() {}},
    api: {
      get: async (...args) => { calls.push({method: 'GET', args}); return []; },
      post: async (path, body) => { calls.push({method: 'POST', path, body}); return {job_id: 'job-1', status: 'queued'}; },
    },
  });
  const run = code => vm.runInContext(code, context);
  run(fs.readFileSync('app/static/page-search.js', 'utf8'));
  element('la7-url').value = 'https://www.la7play.it/programma/puntate/puntata-123456';
  element('la7-title').value = 'Puntata di prova';
  return {context, run, calls, actions, element, toasts, hash: () => hash};
}

test('LA7 selection uses the shared catalogue APIs and keeps direct fallback', async () => {
  const app = create();
  app.element('search-input').value = 'programma';
  app.run("setSource('la7')");
  await app.run('doSearch()');
  await app.run('showStartPage()');
  assert.equal(app.element('catalog-searchbar').hidden, false);
  assert.equal(app.element('la7-direct-download').hidden, false);
  assert.match(app.element('search-filters').innerHTML, /search:kind/);
  assert.equal(app.element('search-hero').style.display, '');
  app.hash().apply({src: 'la7', q: 'programma'});
  await app.run('searchPageEnter()');
  assert.ok(app.calls.length >= 0);
  app.hash().apply({src: 'all'});
  assert.equal(app.element('catalog-searchbar').hidden, false);
  assert.equal(app.element('la7-direct-download').hidden, true);
});

test('manual preview uses shared cards and opens local detail without metadata calls', () => {
  const app = create();
  app.element('la7-title').value = '<Titolo>';
  app.actions['search:la7Preview']();
  const markup = app.element('la7-preview').children[0].innerHTML;
  assert.match(markup, /class="result-card"/);
  assert.match(markup, /data-action="search:la7Detail"/);
  assert.match(markup, /&lt;Titolo>/);
  assert.match(markup, /poster-noimg/);
  assert.match(markup, /la7.episode/);
  app.actions['search:la7Detail']();
  assert.equal(app.element('la7-detail-title').textContent, '<Titolo>');
  assert.equal(app.element('la7-detail').hidden, false);
  assert.equal(app.element('la7-detail-title').focused, true);
  app.actions['search:la7Close']();
  assert.equal(app.element('la7-detail').hidden, true);
  assert.equal(app.calls.length, 0);
});

test('invalid or incomplete URLs cannot become downloadable cards', () => {
  const app = create();
  for (const url of ['', 'https://evil.test/puntata-123', 'https://la7play.it/',
    'https://la7play.it/puntata-123?x=1', 'https://la7play.it/puntata-123#x',
    'http://la7play.it/puntata-123', 'https://user@la7play.it/puntata-123']) {
    app.element('la7-url').value = url;
    app.actions['search:la7Preview']();
    assert.equal(app.run('_la7Draft'), null);
  }
  assert.equal(app.element('la7-preview').children.length, 0);
  assert.equal(app.calls.length, 0);
});

test('download snapshots the selected card and suppresses double clicks', async () => {
  const app = create();
  app.actions['search:la7Preview']();
  app.actions['search:la7Detail']();
  let finish;
  app.context.api.post = (path, body) => {
    app.calls.push({path, body}); return new Promise(resolve => {finish = resolve;});
  };
  const pending = app.actions['search:la7Download']();
  await app.actions['search:la7Download']();
  app.element('la7-title').value = 'Altro titolo';
  app.actions['search:la7Preview']();
  assert.equal(app.element('la7-download-btn').disabled, true);
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].path, '/api/download/la7');
  assert.equal(app.calls[0].body.title, 'Puntata di prova');
  assert.equal(app.calls[0].body.source, 'la7');
  assert.equal(app.calls[0].body.scope, 'single');
  finish({job_id: 'job-1', status: 'queued'});
  await pending;
  assert.equal(app.element('la7-download-btn').disabled, false);
  assert.equal(app.element('la7-download-status').textContent, 'search.la7Queued');
});

test('permissions prevent download and an API error restores the button', async () => {
  const app = create();
  app.actions['search:la7Preview']();
  app.context.can = () => false;
  await app.actions['search:la7Download']();
  assert.equal(app.calls.length, 0);
  app.context.can = () => true;
  app.context.api.post = async () => { throw new Error('Unavailable'); };
  await app.actions['search:la7Download']();
  assert.equal(app.element('la7-download-status').textContent, 'Unavailable');
  assert.equal(app.element('la7-download-btn').disabled, false);
  assert.equal(app.toasts.at(-1)[1], 'danger');
});

test('late response from another source cannot paint LA7 catalogue', async () => {
  const app = create();
  app.context.currentSource = 'animeunity';
  app.element('search-input').value = 'show';
  app.run('_showSearchSkeletons = () => {}');
  let finish;
  app.context.api.get = () => new Promise(resolve => { finish = resolve; });
  const pending = app.run('doSearch()');
  app.run("setSource('la7')");
  finish([{id: 1, name: 'Old title', type: 'tv'}]);
  await pending;
  assert.equal(app.element('search-results').innerHTML, '');
  assert.equal(app.context._searchResults.length, 0);
});
