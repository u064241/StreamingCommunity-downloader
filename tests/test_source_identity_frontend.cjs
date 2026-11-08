const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function create() {
  const calls = [], routes = [];
  const context = vm.createContext({
    URLSearchParams, location: {hash: ''},
    window: {addEventListener() {}}, registerActions() {},
    registerPageHash() {}, currentSource: 'animeunity',
    api: {post: async (path, body) => {calls.push({path, body}); return {}; }},
    t: key => key, showToast() {}, can: () => true,
    loadTitlePage: route => routes.push(route),
    showPage() {}, document: {querySelector: () => null},
    scLanguage: {current: () => 'en'},
  });
  vm.runInContext(fs.readFileSync('app/static/router.js', 'utf8'), context);
  const run = code => vm.runInContext(code, context);
  vm.runInContext(fs.readFileSync('app/static/page-title.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('app/static/library.js', 'utf8'), context);
  return {context, run, calls, routes};
}

test('detail URLs carry source and old anime URLs retain AnimeUnity on reload', () => {
  const app = create();
  assert.match(app.run("titleHash({type:'anime', id:'42-show'})"), /src=animeunity/);
  app.context.loadTitlePage = route => app.routes.push(route);
  app.context.location.hash = '#/title/anime/42-show';
  app.run('routeFromHash()');
  assert.equal(app.routes[0].source, 'animeunity');
});

test('a different source with the same title id re-enters detail', () => {
  const app = create();
  app.run("_tp = {type:'movie', id:42, source:'streamingcommunity'}");
  app.context.loadTitlePage = route => app.routes.push(route);
  app.context.location.hash = '#/title/movie/42?src=other';
  app.run('routeFromHash()');
  assert.equal(app.routes[0].source, 'other');
  assert.equal(app.run("libKeyForRoute('movie',42,'other').source"), 'other');
});

test('film download uses title identity even when the search source has changed', async () => {
  const app = create();
  await app.run("startFilmDownload(42,'Film',null,null,null,null,null,'streamingcommunity')");
  assert.equal(app.calls[0].body.source, 'streamingcommunity');
});

test('anime and TV episode contexts pass their source to download', async () => {
  const app = create();
  app.run("_animeCtx={source:'animeunity',animeId:'42-show',animeName:'Show',episodes:[{id:1,number:1}]} ");
  await app.run('startAnimeDownload(0)');
  app.run("_epCtx={source:'streamingcommunity',tvId:42,tvName:'Show',currentSeason:1,episodes:[{id:2,n:1}]} ");
  await app.run('startEpisodeDownload(0)');
  assert.equal(app.calls[0].body.source, 'animeunity');
  assert.equal(app.calls[1].body.source, 'streamingcommunity');
});
