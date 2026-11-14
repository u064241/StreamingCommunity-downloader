const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function create() {
  const calls = [], toasts = [], actions = {}, elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {innerHTML: '', hidden: false});
    return elements.get(id);
  };
  const context = vm.createContext({
    window: {addEventListener() {}}, URLSearchParams,
    location: {hash: '#/title/tv/programmi:serie/serie?src=raiplay'},
    history: {replaceState() {}}, document: {getElementById: element, querySelector: () => null},
    registerActions: entries => Object.assign(actions, entries), registerPageHash() {},
    can: () => true, scConfirm: async () => true,
    t: (key, args = {}) => key + JSON.stringify(args),
    escapeHtml: String, errText: e => e.message, showPage() {},
    showToast: (...args) => toasts.push(args), DOWNLOADS_LINK: {},
    _searchResults: [], _requestStatus: {}, currentSource: 'raiplay',
    api: {
      get: async (path, params) => { calls.push({method: 'GET', path, params}); return []; },
      post: async (path, body) => { calls.push({method: 'POST', path, body}); return {count: 4}; },
    },
  });
  const run = code => vm.runInContext(code, context);
  run(fs.readFileSync('app/static/router.js', 'utf8'));
  run(fs.readFileSync('app/static/page-title.js', 'utf8'));
  run(`_tpRender = () => {}; _tpRenderEpisodes = () => {}; _tpRenderHero = () => {};
    _tp = {source:'raiplay', metadataOnly:true, raiDownloadable:true, type:'tv',
      id:'programmi:serie', slug:'serie', name:'Serie', pageUrl:'https://www.raiplay.it/programmi/serie',
      season:1, seasonNumbers:[1,6,8], seasonPaths:[{number:1},{number:6},{number:8}], episodes:[]};`);
  return {context, calls, toasts, actions, element, run};
}

test('RaiPlay hero and season button send explicit batch scope', async () => {
  const app = create();
  await app.actions['tp:primary']();
  await app.run('downloadWholeSeason(6)');
  assert.deepEqual(app.calls.map(c => c.body.scope), ['series', 'season']);
  assert.equal(app.calls[1].body.season_number, 6);
  assert.ok(app.calls.every(c => c.path === '/api/download/raiplay'));
  assert.match(app.element('th-cta').innerHTML, /title.scopeSeries/);
});

test('film identified by metadata corrects the search card type and uses film scope', async () => {
  const app = create();
  app.context.api.get = async (path, params) => {
    app.calls.push({path, params});
    return {type:'movie', name:'Film', downloadable:true, page_url:'https://www.raiplay.it/programmi/film', seasons:[]};
  };
  await app.run("_tpLoadMetadataOnly(_tpToken)");
  assert.equal(app.run('_tp.type'), 'movie');
  assert.equal(app.calls.length, 1);
  await app.actions['tp:primary']();
  assert.equal(app.calls[1].body.scope, 'film');
});

test('numeric seasons, not array indices, reach the episode API', async () => {
  const app = create();
  await app.actions['tp:raiSeason']({}, {value:'6'});
  assert.equal(app.calls[0].params.season_number, 6);
  assert.equal(app.run('_tp.season'), 6);
  assert.equal(app.run('_tp.raiSeasonIndex'), 1);
});

test('slow earlier season cannot overwrite the last selection', async () => {
  const app = create();
  const pending = {};
  app.context.api.get = (path, params) => new Promise(resolve => {pending[params.season_number] = resolve;});
  const old = app.run('tpLoadRaiSeason(6)');
  const latest = app.run('tpLoadRaiSeason(8)');
  pending[8]([{n:'1', season:'8'}]); await latest;
  pending[6]([{n:'1', season:'6'}]); await old;
  assert.equal(app.run('_tp.episodes[0].season'), '8');
  assert.equal(app.run('_tp.season'), 8);
});

test('episode failure is cleared on next season and title navigation invalidates old request', async () => {
  const app = create();
  app.context.api.get = async () => {throw new Error('feed unavailable');};
  await app.run('tpLoadRaiSeason(6)');
  assert.match(app.run('_tp.episodesError'), /feed unavailable/);
  app.context.api.get = async () => [{n:'1', season:'8'}];
  await app.run('tpLoadRaiSeason(8)');
  assert.equal(app.run('_tp.episodesError'), null);
  let resolve;
  app.context.api.get = () => new Promise(r => {resolve = r;});
  const pending = app.run('tpLoadRaiSeason(6)');
  app.run("++_tpToken; _tp = {id:'another', episodes:[]}");
  resolve([{season:'6'}]); await pending;
  assert.equal(app.run('_tp.id'), 'another');
  assert.equal(app.run('_tp.episodes.length'), 0);
});

test('Back between seasons stays on RaiPlay and absent s returns to first real season', async () => {
  const app = create();
  app.run("_tp.season=8; location.hash='#/title/tv/programmi%3Aserie/serie?src=raiplay&s=6'; routeFromHash()");
  assert.equal(app.calls[0].params.season_number, 6);
  app.run("location.hash='#/title/tv/programmi%3Aserie/serie?src=raiplay'; routeFromHash()");
  assert.equal(app.calls[1].params.season_number, 1);
  assert.ok(app.calls.every(c => c.path.startsWith('/api/titles/raiplay/')));
});

test('double click queues just one batch and partial results show warning', async () => {
  const app = create();
  app.context.api.post = async (path, body) => {
    app.calls.push({path, body}); return {count:2, unavailable_seasons:[8]};
  };
  await Promise.all([app.actions['tp:primary'](), app.actions['tp:primary']()]);
  assert.equal(app.calls.length, 1);
  assert.equal(app.toasts.at(-1)[1], 'warning');
  assert.match(app.toasts.at(-1)[0], /title.raiUnavailable/);
});

test('DRM or missing download permission disables the action', async () => {
  const app = create();
  app.run('_tp.raiDownloadable=false');
  await app.actions['tp:primary']();
  assert.equal(app.calls.length, 0);
  app.run('_tp.raiDownloadable=true');
  app.context.can = () => false;
  await app.actions['tp:primary']();
  assert.equal(app.calls.length, 0);
});
