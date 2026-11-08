const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create(source = 'animeunity') {
  function node() {
    const children = [], selectors = new Map();
    return {
      children, dataset: {}, innerHTML: '',
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
    document: {addEventListener() {}, createElement: node, getElementById: () => host},
    window: {addEventListener() {}}, currentSource: source, currentDomain: '',
    _searchResults: [], _requestStatus: {}, t: key => key,
    escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'),
    itemYear: () => null, registerActions() {}, registerPageHash() {},
    loadRequestStatuses() {},
    api: {get() { throw new Error('Rendering must not fetch metadata'); }},
  });
  vm.runInContext(fs.readFileSync('app/static/page-search.js', 'utf8'), context);
  return {context, host, node, run: code => vm.runInContext(code, context)};
}

test('AnimeUnity cards use available wide artwork and retain full escaped titles', () => {
  const app = create();
  app.context.renderResultCards([{
    id: 1, name: 'A long anime title <special>', type: 'anime', media_type: 'Movie',
    poster: 'https://images.test/poster.jpg', backdrop: '/images/banner.jpg',
  }], app.host, 7, 'shelf-item');
  const card = app.host.children[0];
  assert.equal(card.dataset.idx, '7');
  assert.equal(card.className, 'shelf-item');
  assert.match(card.innerHTML, /poster-wrap has-backdrop/);
  assert.match(card.innerHTML, /src="\/images\/banner.jpg"/);
  assert.doesNotMatch(card.innerHTML, /poster-backdrop|\/api\/image\/\/images/);
  assert.match(card.innerHTML, /A long anime title &lt;special>/);
  assert.match(card.innerHTML, /catalog.movie/);
});

test('poster-only AnimeUnity cards retain the contained fallback and its background', () => {
  const app = create();
  app.context.renderResultCards([{
    id: 2, name: 'Anime', type: 'anime', poster: 'https://images.test/poster.jpg',
  }], app.host, 0);
  const html = app.host.children[0].innerHTML;
  assert.match(html, /poster-wrap poster-fallback/);
  assert.match(html, /class="poster-backdrop"/);
  assert.match(html, /class="poster-main"/);
});

test('shelf browse controls only expose sections supported by the backend', () => {
  for (const source of ['animeunity', 'streamingcommunity']) {
    const app = create(source);
    app.context._renderHeroArt = () => {};
    app.context._wireShelfArrows = () => () => {};
    app.context.applyClientFilter = () => {};
    app.context.loadRequestStatuses = () => {};
    app.context.shelves = (source === 'animeunity'
      ? ['latest_episodes', 'featured'] : ['latest', 'trending', 'top10', 'other'])
      .map((key, i) => ({key, title: key, items: [{id: i, name: key, type: 'anime'}]}));
    app.run('_homeCache[`${currentSource}:`] = {shelves}; renderShelves();');
    app.host.children.forEach((section, i) => {
      const header = section.querySelector('.shelf-head').innerHTML;
      assert.equal(header.includes('data-action="catalog:open"'), source === 'streamingcommunity' && i < 3);
      assert.equal(section.querySelector('.shelf-rail').children[0].dataset.idx, String(i));
    });
    assert.equal(app.context._searchResults.length, app.context.shelves.length);
  }
});

test('home headline translates consistently with the initial template', () => {
  for (const [language, expected] of [['it', 'Scopri cosa guardare'], ['en', 'Discover what to watch']]) {
    const context = vm.createContext({
      window: {}, document: {addEventListener() {}},
      localStorage: {getItem: () => language}, navigator: {language},
    });
    vm.runInContext(fs.readFileSync('app/static/i18n.js', 'utf8'), context);
    assert.equal(context.window.t('home.next'), expected);
  }
  assert.match(fs.readFileSync('app/templates/index.html', 'utf8'), /data-i18n="home.next">Scopri cosa guardare<\/span>/);
});
