const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function create() {
  const actions = {}, events = {}, elements = new Map(), pages = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', dataset: {}, innerHTML: '', style: {},
      classList: {toggle() {}}, querySelectorAll: () => [],
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    URLSearchParams, AbortController,
    location: {hash: '#/title/anime/42?src=animeunity'},
    history: {length: 1},
    window: {addEventListener() {}},
    document: {referrer: '', getElementById: element, querySelector: () => null,
      addEventListener: (name, fn) => {events[name] = fn;},
      createElement: () => ({dataset: {}, innerHTML: ''})},
    currentSource: 'streamingcommunity', _searchResults: [], _requestStatus: {},
    registerActions: entries => Object.assign(actions, entries),
    t: key => key, escapeHtml: String, itemYear: () => null,
    setTimeout: () => 1, clearTimeout() {},
    showPage: (name, params) => {pages.push(name); context.PAGE_HASH_SEARCH(params);},
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['router.js', 'page-search.js', 'page-title.js']) {
    run(fs.readFileSync(`app/static/${file}`, 'utf8'));
  }
  context.PAGE_HASH_SEARCH = params => {
    context.routeParams = params;
    run('PAGE_HASH.search.apply(routeParams)');
  };
  return {run, context, actions, events, element, pages};
}

for (const action of ['tp:crumb-search', 'tp:crumb-kind']) {
  test(`${action} preserves AnimeUnity through actual hash routing`, () => {
    const app = create();
    app.run("_tp = {type:'anime', source:'animeunity'}");
    app.actions[action]({kind: 'anime'});
    assert.equal(app.context.location.hash, '#/search?src=animeunity');
    app.run('routeFromHash()');
    assert.equal(app.context.currentSource, 'animeunity');
    assert.deepEqual(app.pages, ['search']);
  });
}

test('kind breadcrumb preserves the StreamingCommunity film filter', () => {
  const app = create();
  app.run("_tp = {type:'movie', source:'streamingcommunity'}");
  app.actions['tp:crumb-kind']({kind: 'movie'});
  app.run('routeFromHash()');
  assert.equal(app.context.currentSource, 'streamingcommunity');
  assert.equal(app.run('_kindFilter'), 'movie');
});

test('back from a directly opened anime retains its source', () => {
  const app = create();
  app.run("_tp = {type:'anime', source:'animeunity'}; tpBack()");
  app.run('routeFromHash()');
  assert.equal(app.context.currentSource, 'animeunity');
});

test('AnimeUnity uses its wide artwork and falls back to a complete poster', () => {
  const app = create(), cards = [];
  app.context.host = {appendChild: card => cards.push(card)};
  app.run(`renderResultCards([
    {id:1,type:'anime',name:'Wide',poster:'https://img.test/poster',backdrop:'https://img.test/banner'},
    {id:2,type:'anime',name:'Poster only',poster:'https://img.test/poster'}
  ],host,0)`);
  assert.match(cards[0].innerHTML, /poster-wrap has-backdrop/);
  assert.match(cards[0].innerHTML, /poster-main[^>]*src="https:\/\/img.test\/banner"/);
  assert.doesNotMatch(cards[0].innerHTML, /class="poster-backdrop"/);
  assert.match(cards[1].innerHTML, /poster-wrap poster-fallback/);
  assert.match(cards[1].innerHTML, /poster-main[^>]*src="https:\/\/img.test\/poster"/);
});

test('hover enlarges edge cards inward, including after horizontal scrolling', () => {
  const app = create();
  const rail = {getBoundingClientRect: () => ({left: 100, right: 900})};
  const card = {
    dataset: {}, style: {}, offsetWidth: 190, contains: () => false,
    closest: selector => selector === '.shelf-rail' ? rail : null,
  };
  for (const [left, right, expected] of [
    [105,295,'left 18%'], [705,895,'right 18%'], [400,590,'center 18%'],
  ]) {
    card.getBoundingClientRect = () => ({left, right});
    app.events.pointerover({target: {closest: () => card}, relatedTarget: null});
    assert.equal(card.style.transformOrigin, expected);
  }
});

test('wide AnimeUnity backdrops fill the 16:9 artwork frame', () => {
  const css = fs.readFileSync('app/static/css/pages.css', 'utf8');
  assert.match(css, /\.poster-wrap\.has-backdrop \.poster-main\s*\{[\s\S]*?object-fit:\s*cover/);
  assert.match(css, /\.poster-wrap\.poster-fallback img\s*\{[\s\S]*?object-fit:\s*contain/);
});

test('layout keeps artwork contained and reserves rail snap/hover space', () => {
  const css = fs.readFileSync('app/static/css/pages.css', 'utf8');
  const rule = selector => css.slice(css.indexOf(selector)).split('}')[0];
  assert.match(rule('.poster-wrap img {'), /position: absolute/);
  assert.match(rule('.poster-wrap img {'), /object-fit: contain/);
  assert.match(rule('.shelf-rail {'), /scroll-padding-inline: 28px/);
  assert.match(rule('.shelf-rail {'), /padding: 32px 28px 20px/);
  assert.match(rule('.shelf-rail-wrap {'), /margin-inline: 0/);
  assert.doesNotMatch(css, /\.th-related \.shelf-rail\s*\{[^}]*padding-left: 0/);
});
