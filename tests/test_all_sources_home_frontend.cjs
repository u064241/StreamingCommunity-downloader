const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create(source) {
  function node() {
    const children = [], selectors = new Map();
    return {
      children, dataset: {}, innerHTML: '', value: '', style: {},
      appendChild(child) { children.push(child); },
      insertAdjacentHTML(_, html) { this.innerHTML += html; },
      querySelector(selector) {
        if (!selectors.has(selector)) selectors.set(selector, node());
        return selectors.get(selector);
      },
    };
  }
  const host = node();
  const context = vm.createContext({
    AbortController,
    document: {addEventListener() {}, createElement: node, getElementById: () => host},
    window: {addEventListener() {}}, currentSource: source, currentDomain: 'source.test',
    _searchResults: [], _requestStatus: {}, t: key => key,
    escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'),
    itemYear: () => null, registerActions() {}, registerPageHash() {},
    can: () => false, loadRequestStatuses() {}, ensureDomain: async () => {},
    api: {get() { throw new Error('unexpected fetch'); }},
  });
  vm.runInContext(fs.readFileSync('app/static/page-search.js', 'utf8'), context);
  context._renderHeroArt = () => {};
  context._wireShelfArrows = () => () => {};
  context.applyClientFilter = () => {};
  return {context, host, run: code => vm.runInContext(code, context)};
}

const ALL_SHELVES = [
  {key: 'trending', source: 'streamingcommunity', base_title: 'Di tendenza',
   title: 'StreamingCommunity · Di tendenza',
   items: [{id: 1, name: 'Film', type: 'movie', source: 'streamingcommunity'}]},
  {key: 'latest', source: 'animeunity', base_title: 'Ultimi episodi',
   title: 'AnimeUnity · Ultimi episodi',
   items: [{id: '7-x', name: 'Anime', type: 'anime', source: 'animeunity'}]},
];

test('"Tutte le fonti" asks /api/home for source=all, not streamingcommunity', async () => {
  const app = create('all');
  app.context._cancelCatalogWork = () => {};
  app.context._setStartPageVisible = () => {};
  app.context.renderHomeExtras = () => {};
  const asked = [];
  app.context.api.get = async (path, params) => { asked.push([path, params.source]); return {shelves: ALL_SHELVES}; };
  await app.context.showStartPage();
  assert.deepEqual(asked, [['/api/home', 'all']]);
  assert.equal(app.host.children.length, 2);
});

test('shelf titles carry the source prefix; the rest stays translatable', () => {
  const app = create('all');
  app.context.shelves = ALL_SHELVES;
  app.run('_homeCache[`${currentSource}:${currentDomain}`] = {shelves}; renderShelves();');
  const titles = app.host.children.map(s => s.innerHTML.match(/<div class="shelf-title">(.*?)<\/div><\/div>/)[1]);
  assert.equal(titles[0], 'StreamingCommunity · <span data-catalog-label="Di tendenza">catalog.trending</span>');
  assert.equal(titles[1], 'AnimeUnity · <span data-catalog-label="Ultimi episodi">catalog.latestEpisodes</span>');
  // No source-specific browse buttons on the mixed page.
  app.host.children.forEach(s => assert.doesNotMatch(s.querySelector('.shelf-head').innerHTML, /catalog:open|anime:view/));
});

test('a single-source home keeps its own title untouched', () => {
  const app = create('animeunity');
  app.context.shelves = [{key: 'latest', title: 'Ultimi episodi', items: []}];
  app.run('_homeCache[`${currentSource}:${currentDomain}`] = {shelves}; renderShelves();');
  assert.match(app.host.children[0].innerHTML, /<div class="shelf-title"><span data-catalog-label="Ultimi episodi">/);
});

test('each card keeps its source, so the click opens the right catalogue', () => {
  const app = create('all');
  app.context.shelves = ALL_SHELVES;
  app.run('_homeCache[`${currentSource}:${currentDomain}`] = {shelves}; renderShelves();');
  const results = app.context._searchResults;
  assert.deepEqual([...results.map(i => i.source)], ['streamingcommunity', 'animeunity']);

  // openTitle(idx) -> titleHash(_searchResults[idx]): the address names the card's source.
  const titleSource = fs.readFileSync('app/static/page-title.js', 'utf8');
  const titleHashCode = titleSource.slice(titleSource.indexOf('function titleHash('), titleSource.indexOf('// From a result card'));
  const ctx = vm.createContext({location: {hash: ''}, history: {replaceState() {}}, window: {addEventListener() {}},
    document: {querySelector: () => null}, URLSearchParams});
  vm.runInContext(fs.readFileSync('app/static/router.js', 'utf8') + '\n' + titleHashCode, ctx);
  assert.equal(ctx.titleHash(results[0]), '#/title/movie/1?src=streamingcommunity');
  assert.equal(ctx.titleHash(results[1]), '#/title/anime/7-x?src=animeunity');
});
