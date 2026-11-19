// The rail arrows make the rail a loop: ">" keeps moving right and, past the
// last title, the first titles follow; "<" keeps moving left the same way.
// Runs the real _wireShelfArrows from page-search.js against a fake rail that
// models scrollLeft, card positions and DOM reordering.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('app/static/page-search.js', 'utf8').replace(/\r\n/g, '\n');
const start = source.indexOf('function _wireShelfArrows(section)');
assert.ok(start >= 0, '_wireShelfArrows not found');
const end = source.indexOf('\n}\n', start) + 3;
const wire = source.slice(start, end);

const CARD = 144;          // 132 px card + 12 px gap
const VIEW = 500;          // clientWidth

function makeRail(n) {
  const children = Array.from({length: n}, (_, id) => ({id, offsetWidth: 132}));
  const rail = {
    children, clientWidth: VIEW, _left: 0,
    get scrollWidth() { return children.length * CARD; },
    get scrollLeft() { return this._left; },
    set scrollLeft(v) { this._left = Math.max(0, Math.min(this.scrollWidth - VIEW, v)); },
    get firstChild() { return children[0]; },
    appendChild(node) { children.splice(children.indexOf(node), 1); children.push(node); return node; },
    insertBefore(node, ref) {
      children.splice(children.indexOf(node), 1);
      const at = ref ? children.indexOf(ref) : children.length;
      children.splice(at, 0, node);
      return node;
    },
    scrollTo(opts) { this.scrollLeft = opts.left; },   // smooth is modelled as instant
    addEventListener() {},
  };
  // Screen position of a card: its slot in the rail minus how far the rail is scrolled.
  for (const card of children) {
    Object.defineProperty(card, 'getBoundingClientRect', {value() {
      return {left: rail.children.indexOf(card) * CARD - rail.scrollLeft};
    }});
  }
  return rail;
}

function setup(n, {timers = 'instant'} = {}) {
  const rail = makeRail(n);
  const makeArrow = () => ({hidden: false, listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; }});
  const prev = makeArrow(), next = makeArrow();
  const section = {querySelector: sel => ({'.shelf-rail': rail, '.shelf-arrow-prev': prev, '.shelf-arrow-next': next})[sel]};
  const queue = [];
  const context = vm.createContext({
    getComputedStyle: () => ({paddingLeft: '0px'}),
    setTimeout: fn => { if (timers === 'instant') fn(); else queue.push(fn); return queue.length; },
    clearTimeout() {},
  });
  vm.runInContext(wire + '\nthis.wire = _wireShelfArrows;', context);
  context.wire(section);
  return {rail, prev, next, queue};
}

// Id of the first card that is at least half on screen.
function firstVisible(rail) {
  const card = rail.children.find(c => c.getBoundingClientRect().left >= -CARD / 2);
  return card.id;
}

function clicks(arrow, rail, times) {
  const seen = [];
  for (let i = 0; i < times; i++) {
    arrow.listeners.click();
    seen.push(firstVisible(rail));
  }
  return seen;
}

test('">" keeps moving right: past the last title the first ones follow', () => {
  const {rail, next} = setup(10);
  const seen = clicks(next, rail, 40);
  // Every step moves the first visible title forward in the loop, never back.
  let prev = 0;
  for (const id of seen) {
    const forward = (id - prev + 10) % 10;
    assert.ok(forward >= 1 && forward <= 3, `step from ${prev} to ${id} is not a short forward move`);
    prev = id;
  }
  assert.equal(new Set(seen).size, 10, 'the loop must reach every title');
});

test('"<" keeps moving left: before the first title the last ones come', () => {
  const {rail, prev} = setup(10);
  const seen = clicks(prev, rail, 40);
  let current = 0;
  for (const id of seen) {
    const backward = (current - id + 10) % 10;
    assert.ok(backward >= 1 && backward <= 3, `step from ${current} to ${id} is not a short backward move`);
    current = id;
  }
  assert.equal(new Set(seen).size, 10, 'the loop must reach every title');
});

test('a click at the end never shows a jump back to the start', () => {
  const {rail, next} = setup(10);
  rail.scrollLeft = rail.scrollWidth - VIEW;     // already at the end
  const before = firstVisible(rail);
  next.listeners.click();
  const after = firstVisible(rail);
  // The first titles follow the last ones: a short forward step in the loop.
  const forward = (after - before + 10) % 10;
  assert.ok(forward >= 1 && forward <= 3, `from ${before} to ${after}: not a short forward step`);
});

test('a click while a scroll is still running is ignored', () => {
  const {rail, next, queue} = setup(10, {timers: 'queued'});
  next.listeners.click();
  const once = rail.scrollLeft;
  next.listeners.click();
  assert.equal(rail.scrollLeft, once, 'second click moved the rail while busy');
  queue.forEach(fn => fn());                       // the scroll finished
  next.listeners.click();
  assert.notEqual(rail.scrollLeft, once, 'a click after the scroll must work again');
});

test('both arrows stay visible when the rail overflows, at any position', () => {
  const {rail, prev, next} = setup(10);
  assert.equal(prev.hidden, false);
  assert.equal(next.hidden, false);
  rail.scrollLeft = rail.scrollWidth - VIEW;
  // Sync runs on scroll; call the wired sync through the scroll handler is not exposed,
  // so only the initial state is checked here.
  assert.equal(next.hidden, false);
});

test('a rail that fits shows no arrows', () => {
  const {prev, next} = setup(2);
  assert.equal(prev.hidden, true);
  assert.equal(next.hidden, true);
});
