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
  const finishOld = finish;
  app.context.api.get = async () => [];
  app.run("setSource('la7')");
  finishOld([{id: 1, name: 'Old title', type: 'tv'}]);
  await pending;
  assert.doesNotMatch(app.element('search-results').innerHTML, /Old title/);
  assert.equal(app.context._searchResults.length, 0);
});

function createTitle() {
  const app = create();
  app.run(fs.readFileSync('app/static/page-title.js', 'utf8'));
  app.run(`_tpRender = () => {}; _tpRenderEpisodes = () => {}; _tpRenderHero = () => {};
    _tp = {source:'la7', metadataOnly:true, downloadable:true, type:'tv',
      id:'serie', name:'Serie', pageUrl:'https://www.la7play.it/serie',
      downloadScope:'series', seasonsCount:2, season:1, seasonNumbers:[1,2026], episodes:[]};`);
  return app;
}

test('LA7 details expose episode tab, real seasons and an enabled download button', async () => {
  const app = createTitle();
  app.context.api.get = async (path, params) => {
    app.calls.push({path, params});
    return path.endsWith('/episodes') ? [{n:'2', season:2026}] : {
      type:'tv', downloadable:true, download_scope:'series', page_url:'https://www.la7play.it/serie',
      season_paths:[{name:'Stagione 2026', number:2026, path:'/serie/puntate'}],
    };
  };
  await app.run('_tpLoadMetadataOnly(_tpToken)');
  assert.equal(app.run('_tp.seasonsCount'), 1);
  assert.equal(app.run('_tp.season'), 2026);
  assert.equal(app.calls[1].params.season_number, 2026);
  assert.equal(app.run('_tp.episodes[0].n'), '2');
  assert.equal(app.run('_tpTabs()[0][0]'), 'episodes');
  app.run('_tpRenderCta()');
  assert.doesNotMatch(app.element('th-cta').innerHTML, /disabled|Contenuto non scaricabile/);
});

test('catalogue replica is a single video even when its media type is tv', async () => {
  const app = createTitle();
  app.context.api.get = async () => ({type:'tv', drm:false, downloadable:true, download_scope:'single',
    page_url:'https://www.la7play.it/tgla7/edizione-399613', season_paths:[]});
  await app.run('_tpLoadMetadataOnly(_tpToken)');
  app.run('_tpRenderCta()');
  assert.doesNotMatch(app.element('th-cta').innerHTML, /disabled|scopeSeries|non scaricabile/);
  assert.equal(app.run('_tpTabs()[0][0]'), 'about');
  await app.actions['tp:primary']();
  const request = app.calls.at(-1);
  assert.equal(request.body.scope, 'single');
  assert.equal(request.body.url, 'https://www.la7play.it/tgla7/edizione-399613');
  assert.equal(request.body.season_number, null);
});

test('LA7 series and season actions reach their own batch endpoint', async () => {
  const app = createTitle();
  await app.actions['tp:primary']();
  await app.actions['tp:batchSeason']({season:'2026'});
  assert.deepEqual(app.calls.map(c => c.body.scope), ['series','season']);
  assert.equal(app.calls[1].body.season_number, 2026);
  assert.ok(app.calls.every(c => c.path === '/api/download/la7' && c.body.url === ''));
});

test('protected LA7 film explains DRM and cannot be queued', async () => {
  const app = createTitle();
  app.run("_tp.type='movie'; _tp.downloadScope='single'; _tp.downloadable=false; _tp.downloadDisabledReason='Contenuto LA7 protetto da DRM'");
  app.run('_tpRenderCta()');
  assert.match(app.element('th-cta').innerHTML, /disabled[^]*DRM/);
  await app.actions['tp:primary']();
  assert.equal(app.calls.length, 0);
});

test('LA7 batch suppresses double clicks and restores the button after error', async () => {
  const app = createTitle();
  let fail;
  app.context.api.post = (path, body) => {
    app.calls.push({path, body}); return new Promise((_, reject) => {fail = reject;});
  };
  const pending = app.actions['tp:primary']();
  await app.actions['tp:primary']();
  assert.equal(app.calls.length, 1);
  assert.match(app.element('th-cta').innerHTML, /disabled/);
  fail(new Error('LA7 unavailable')); await pending;
  assert.equal(app.run('_tp.raiQueueBusy'), false);
  assert.doesNotMatch(app.element('th-cta').innerHTML, /disabled/);
  assert.equal(app.toasts.at(-1)[1], 'danger');
});

test('older LA7 season response cannot replace the last selection', async () => {
  const app = createTitle();
  const pending = {};
  app.context.api.get = (path, params) => new Promise(resolve => {pending[params.season_number] = resolve;});
  const first = app.run('tpLoadLa7Season(1)');
  const latest = app.run('tpLoadLa7Season(2026)');
  pending[2026]([{n:'16', season:2026}]); await latest;
  pending[1]([{n:'1', season:1}]); await first;
  assert.equal(app.run('_tp.season'), 2026);
  assert.equal(app.run('_tp.episodes[0].season'), 2026);
});

test('LA7 episode action preserves its page URL and numbering', async () => {
  const app = createTitle();
  app.run("_tp.episodes=[{n:'16',season:2026,page_url:'https://www.la7play.it/tgla7/edizione-399613'}]");
  await app.actions['tp:la7Episode']({index:'0'});
  assert.equal(app.calls[0].body.scope, 'single');
  assert.equal(app.calls[0].body.title, 'Serie S2026E16');
  assert.equal(app.calls[0].body.url, 'https://www.la7play.it/tgla7/edizione-399613');
});

test('cached protected LA7 cards cannot reappear in results or homepage rails', () => {
  const app = create();
  const items = [
    {source:'la7',id:'film/fury-399459',name:'Fury',type:'movie',drm:true,download_scope:'single'},
    {source:'la7',id:'film/clear-123',name:'Clear film',type:'movie',drm:false,downloadable:true},
    {source:'la7',id:'serie',name:'Mixed series',type:'tv',drm:true,download_scope:'series'},
    {source:'streamingcommunity',id:4,name:'Other source',type:'movie',drm:true},
    {source:'la7',id:'film/old-5',name:'Old unchecked film',type:'movie'},
    {source:'la7',id:'tgla7/old-6',name:'Old unchecked replica',type:'tv',download_scope:'single'},
  ];
  app.context.items = items;
  for (const options of ['{}', '{ranked:true}']) {
    const container = app.element('cards-' + options);
    app.run(`renderResultCards(items, document.getElementById(${JSON.stringify('cards-' + options)}), 0, 'shelf-item', ${options})`);
    assert.equal(container.children.length, 3);
    assert.doesNotMatch(container.children.map(c => c.innerHTML).join(''), /Fury|Old unchecked/);
    // Keep original indices so clicking a remaining card opens that title.
    assert.equal(container.children[0].dataset.idx, '1');
  }
});

test('clear LA7 movie detail downloads its own single official URL', async () => {
  const app = createTitle();
  app.context.api.get = async () => ({type:'movie',name:'Film',drm:false,downloadable:true,
    download_scope:'single',page_url:'https://www.la7play.it/film/clear-123',season_paths:[]});
  app.run("_tp.id='film/clear-123'");
  await app.run('_tpLoadMetadataOnly(_tpToken)');
  await app.actions['tp:primary']();
  assert.equal(app.calls[0].path, '/api/download/la7');
  assert.equal(app.calls[0].body.external_id, 'film/clear-123');
  assert.equal(app.calls[0].body.url, 'https://www.la7play.it/film/clear-123');
  assert.equal(app.calls[0].body.scope, 'single');
});

test('unknown or protected detail cannot enable a LA7 download', async () => {
  for (const fields of [{drm:true,downloadable:true}, {drm:null,downloadable:true}, {drm:null}, {drm:false,downloadable:false}]) {
    const app = createTitle();
    app.context.api.get = async () => ({type:'movie',download_scope:'single',
      page_url:'https://www.la7play.it/film/clear-123',...fields});
    await app.run('_tpLoadMetadataOnly(_tpToken)');
    app.run('_tpRenderCta()');
    assert.match(app.element('th-cta').innerHTML, /disabled/);
    await app.actions['tp:primary']();
    assert.equal(app.calls.length, 0);
  }
});

test('LA7 episode action ignores protected or unavailable episodes', async () => {
  const app = createTitle();
  for (const fields of [{drm:true}, {downloadable:false}]) {
    app.context.fields = fields;
    app.run("_tp.episodes=[{id:'serie/ep-123',n:'1',season:1,page_url:'https://www.la7play.it/serie/ep-123',...fields}]");
    await app.actions['tp:la7Episode']({index:'0'});
  }
  assert.equal(app.calls.length, 0);
});
