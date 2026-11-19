// The rail arrows make the rail a loop: ">" keeps moving right and, past the
// last title, the first titles follow; "<" keeps moving left the same way.
// Runs the real _wireShelfArrows from page-search.js against a fake rail that
// models scrollLeft, card positions, DOM reordering and animation frames.
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
  const classes = new Set();
  const rail = {
    children, clientWidth: VIEW, _left: 0,
    classList: {
      add: c => classes.add(c),
      remove: c => classes.delete(c),
      contains: c => classes.has(c),
    },
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

function setup(n, wirings = 1) {
  const rail = makeRail(n);
  // Every live handler runs on a click, like the DOM; a handler whose signal
  // was aborted by a later wiring is gone.
  const makeArrow = () => ({
    hidden: false, handlers: {}, listeners: {},
    addEventListener(type, fn, opts = {}) {
      const list = (this.handlers[type] ||= []);
      list.push({fn, signal: opts.signal});
      this.listeners[type] = () => list.filter(h => !h.signal?.aborted).forEach(h => h.fn());
    },
  });
  const prev = makeArrow(), next = makeArrow();
  const section = {querySelector: sel => ({'.shelf-rail': rail, '.shelf-arrow-prev': prev, '.shelf-arrow-next': next})[sel]};

  // Manual clock and frame queue: flush() runs every pending animation frame
  // at a time well past the glide, so each glide finishes in one call.
  let clock = 0;
  let frames = [];
  const context = vm.createContext({
    getComputedStyle: () => ({paddingLeft: '0px'}),
    AbortController,
    performance: {now: () => clock},
    requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
    cancelAnimationFrame: id => { if (frames[id - 1]) frames[id - 1] = null; },
  });
  vm.runInContext(wire + '\nthis.wire = _wireShelfArrows;', context);
  for (let i = 0; i < wirings; i++) context.wire(section);
  const flush = () => {
    let guard = 0;
    while (frames.some(Boolean) && guard++ < 100) {
      clock += 1000;
      const pending = frames;
      frames = [];
      pending.forEach(fn => fn && fn(clock));
    }
  };
  return {rail, prev, next, flush};
}

// Id of the first card that is at least half on screen.
function firstVisible(rail) {
  const card = rail.children.find(c => c.getBoundingClientRect().left >= -CARD / 2);
  return card.id;
}

function clicks(arrow, rail, flush, times) {
  const seen = [];
  for (let i = 0; i < times; i++) {
    arrow.listeners.click();
    flush();
    seen.push(firstVisible(rail));
  }
  return seen;
}

test('">" keeps moving right: past the last title the first ones follow', () => {
  const {rail, next, flush} = setup(10);
  const seen = clicks(next, rail, flush, 40);
  let prev = 0;
  for (const id of seen) {
    const forward = (id - prev + 10) % 10;
    assert.ok(forward >= 1 && forward <= 3, `step from ${prev} to ${id} is not a short forward move`);
    prev = id;
  }
  assert.equal(new Set(seen).size, 10, 'the loop must reach every title');
});

test('"<" keeps moving left: before the first title the last ones come', () => {
  const {rail, prev: back, flush} = setup(10);
  const seen = clicks(back, rail, flush, 40);
  let current = 0;
  for (const id of seen) {
    const backward = (current - id + 10) % 10;
    assert.ok(backward >= 1 && backward <= 3, `step from ${current} to ${id} is not a short backward move`);
    current = id;
  }
  assert.equal(new Set(seen).size, 10, 'the loop must reach every title');
});

test('a click at the end never shows a jump back to the start', () => {
  const {rail, next, flush} = setup(10);
  rail.scrollLeft = rail.scrollWidth - VIEW;       // already at the end
  const before = firstVisible(rail);
  next.listeners.click();
  flush();
  const after = firstVisible(rail);
  const forward = (after - before + 10) % 10;
  assert.ok(forward >= 1 && forward <= 3, `from ${before} to ${after}: not a short forward step`);
});

test('a click during a glide continues from where the rail is heading', () => {
  const {rail, next, flush} = setup(10);
  next.listeners.click();
  next.listeners.click();                          // before the first glide finished
  flush();
  assert.equal(rail.scrollLeft, 2 * 3 * CARD, 'two clicks should add up to two steps');
});

test('the rail is in glide mode only while it moves', () => {
  const {rail, next, flush} = setup(10);
  next.listeners.click();
  assert.equal(rail.classList.contains('is-gliding'), true);
  flush();
  assert.equal(rail.classList.contains('is-gliding'), false);
});

test('both arrows stay visible when the rail overflows', () => {
  const {prev, next} = setup(10);
  assert.equal(prev.hidden, false);
  assert.equal(next.hidden, false);
});

test('a rail that fits shows no arrows', () => {
  const {prev, next} = setup(2);
  assert.equal(prev.hidden, true);
  assert.equal(next.hidden, true);
});

// The title page's "Correlati" rail holds at most 8 titles, so it can scroll
// less than one three-card step. Rotating three cards there needed a scroll
// correction larger than the scroll position: the browser clamped it at 0 and
// the cards on screen jumped back on every click (154 px at 1400 px wide).
test('a short rail never makes the cards on screen jump when it wraps', () => {
  const {rail, next, prev: back, flush} = setup(6);   // 6 * 144 - 500 = 364 px of scroll
  assert.ok(rail.scrollWidth - VIEW < 3 * CARD, 'the rail must be shorter than a full step');
  const onScreen = x => x > -CARD && x < VIEW;
  for (const arrow of [next, back]) {
    const seen = new Set();
    for (let i = 0; i < 12; i++) {
      const before = new Map(rail.children.map(c => [c.id, c.getBoundingClientRect().left]));
      arrow.listeners.click();
      // The click itself (rotation included) must not move anything on screen:
      // only the glide that follows does.
      for (const card of rail.children) {
        const was = before.get(card.id), now = card.getBoundingClientRect().left;
        if (onScreen(was) && onScreen(now)) assert.equal(now, was, `card ${card.id} jumped on click ${i}`);
      }
      flush();
      rail.children.forEach(c => { if (onScreen(c.getBoundingClientRect().left)) seen.add(c.id); });
    }
    assert.equal(seen.size, 6, 'the loop must bring every title on screen');
  }
});

test('wiring the same rail again does not double the step of a click', () => {
  const once = setup(10, 1);
  const twice = setup(10, 2);
  once.next.listeners.click(); once.flush();
  twice.next.listeners.click(); twice.flush();
  assert.equal(twice.rail._left, once.rail._left);
  assert.equal(firstVisible(twice.rail), firstVisible(once.rail));
});
