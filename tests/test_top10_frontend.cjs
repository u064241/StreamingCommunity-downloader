const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

// A minimal DOM: only what renderShelves/renderResultCards touch.
function fakeElement() {
  const el = {
    dataset: {}, innerHTML: '', style: {}, children: [], className: '',
    scrollWidth: 0, clientWidth: 0, scrollLeft: 0, hidden: false,
    classList: {
      _set: new Set(),
      add(name) { this._set.add(name); },
      toggle() {}, remove() {},
      contains(name) { return this._set.has(name); },
    },
    parts: {},
    appendChild(child) { el.children.push(child); return child; },
    addEventListener() {}, insertAdjacentHTML() {},
    querySelectorAll: () => [],
    querySelector(selector) {
      if (!el.parts[selector]) el.parts[selector] = fakeElement();
      return el.parts[selector];
    },
  };
  return el;
}

function create() {
  const host = fakeElement();
  const escapeHtml = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const context = vm.createContext({
    URLSearchParams, AbortController,
    location: {hash: ''}, history: {length: 1},
    window: {addEventListener() {}},
    document: {
      getElementById: id => (id === 'home-shelves' ? host : null),
      querySelector: () => null,
      createElement: () => fakeElement(),
    },
    currentSource: 'streamingcommunity', currentDomain: 'example.test',
    registerActions() {}, t: key => key, escapeHtml, itemYear: () => null,
    setTimeout: () => 1, clearTimeout() {},
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['router.js', 'page-search.js']) {
    run(fs.readFileSync(`app/static/${file}`, 'utf8'));
  }
  // Network and filter side effects are not what is under test here.
  run('loadRequestStatuses = () => {}; applyClientFilter = () => {}');
  return {run, context, host};
}

const items = n => Array.from({length: n}, (_, i) =>
  ({id: i + 1, type: 'tv', name: `Titolo <${i + 1}>`, backdrop: `/img/${i + 1}.jpg`}));

test('ranked cards carry an outlined rank number before the card', () => {
  const app = create(), cards = [];
  app.context.rail = {appendChild: card => cards.push(card)};
  app.context.list = items(10);
  app.run("renderResultCards(list, rail, 0, 'shelf-item', {ranked: true})");
  assert.equal(cards.length, 10);
  cards.forEach((card, i) => {
    assert.match(card.className, /\bshelf-item\b/);
    assert.match(card.className, /\bshelf-item-ranked\b/);
    const rank = card.innerHTML.match(/<span class="rank-number" aria-hidden="true">(\d+)<\/span>/);
    assert.ok(rank, 'rank number missing');
    assert.equal(rank[1], String(i + 1));
    // The number comes first, so the card is painted on top of it.
    assert.ok(card.innerHTML.indexOf('rank-number') < card.innerHTML.indexOf('result-card'));
    // Titles still go through escapeHtml.
    assert.doesNotMatch(card.innerHTML, /<\d+>/);
  });
});

test('ordinary cards have no rank number', () => {
  const app = create(), cards = [];
  app.context.rail = {appendChild: card => cards.push(card)};
  app.context.list = items(3);
  app.run("renderResultCards(list, rail, 0, 'shelf-item')");
  cards.forEach(card => {
    assert.equal(card.className, 'shelf-item');
    assert.doesNotMatch(card.innerHTML, /rank-number/);
  });
});

test('only the top10 shelf on the start page is ranked', () => {
  const app = create();
  app.context.shelves = [
    {key: 'trending', title: 'Titoli del momento', items: items(3)},
    {key: 'top10', title: 'Top 10 di oggi', items: items(10)},
    {key: 'latest', title: 'Aggiunti di recente', items: items(2)},
  ];
  app.run("_homeCache[`${currentSource}:${currentDomain}`] = {shelves, stamp: null}; renderShelves()");
  const sections = app.host.children;
  assert.equal(sections.length, 3);
  const cardsOf = section => section.parts['.shelf-rail'].children;
  const ranked = section => cardsOf(section).filter(c => /rank-number/.test(c.innerHTML)).length;
  assert.equal(ranked(sections[0]), 0);
  assert.equal(ranked(sections[1]), 10);
  assert.equal(ranked(sections[2]), 0);
  assert.equal(sections[1].className, 'shelf shelf-ranked');
  assert.equal(sections[0].className, 'shelf');
  // Rank restarts at 1 for the shelf even though its cards index further on.
  assert.match(cardsOf(sections[1])[0].innerHTML, /rank-number" aria-hidden="true">1</);
  assert.equal(cardsOf(sections[1])[0].dataset.idx, '3');
});

test('a top10 shelf is recognised by its shown name too', () => {
  const app = create();
  assert.equal(app.run("_isTop10Shelf({key: 'x', title: 'Top 10 di oggi'})"), true);
  assert.equal(app.run("_isTop10Shelf({key: 'top10'})"), true);
  assert.equal(app.run("_isTop10Shelf({key: 'trending', title: 'Di tendenza'})"), false);
});
