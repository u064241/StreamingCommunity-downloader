// The rail arrows are circular: ">" past the last title returns to the first,
// "<" before the first goes to the last. Runs the real _wireShelfArrows from
// page-search.js against a small fake DOM.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('app/static/page-search.js', 'utf8').replace(/\r\n/g, '\n');
const start = source.indexOf('function _wireShelfArrows(section)');
assert.ok(start >= 0, '_wireShelfArrows not found');
const end = source.indexOf('\n}\n', start) + 3;
const wire = source.slice(start, end);

function setup({scrollLeft = 0, count = 10, clientWidth = 500, scrollWidth = 1440}) {
  const cards = Array.from({length: count}, (_, i) => ({offsetLeft: i * 144, offsetWidth: 132}));
  const rail = {
    scrollWidth, clientWidth, scrollLeft, calls: [],
    querySelectorAll: () => cards,
    addEventListener() {},
    scrollTo(opts) { this.calls.push(opts.left); this.scrollLeft = opts.left; },
    scrollBy() {},
  };
  const makeArrow = () => ({hidden: false, listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; }});
  const prev = makeArrow(), next = makeArrow();
  const section = {querySelector: sel => ({'.shelf-rail': rail, '.shelf-arrow-prev': prev, '.shelf-arrow-next': next})[sel]};
  const context = vm.createContext({getComputedStyle: () => ({paddingLeft: '0px'}), _shelfSyncs: []});
  vm.runInContext(wire + '\nthis.wire = _wireShelfArrows;', context);
  context.wire(section);
  return {rail, prev, next};
}

const MAX = 1440 - 500; // scrollWidth - clientWidth

test('">" at the end wraps to the start', () => {
  const {rail, next} = setup({scrollLeft: MAX});
  next.listeners.click();
  assert.deepEqual(rail.calls, [0]);
});

test('"<" at the start wraps to the end', () => {
  const {rail, prev} = setup({scrollLeft: 0});
  prev.listeners.click();
  assert.deepEqual(rail.calls, [MAX]);
});

test('">" in the middle still moves forward, never backward', () => {
  const {rail, next} = setup({scrollLeft: 300});
  next.listeners.click();
  assert.equal(rail.calls.length, 1);
  assert.ok(rail.calls[0] > 300, `expected forward, got ${rail.calls[0]}`);
});

test('the last title reached by ">" scrolls to the true end of the rail', () => {
  // Three titles per step from index 6 reaches the last card (index 9).
  const {rail, next} = setup({scrollLeft: 6 * 144});
  next.listeners.click();
  assert.deepEqual(rail.calls, [MAX]);
});

test('both arrows stay visible at either end when the rail overflows', () => {
  const atStart = setup({scrollLeft: 0});
  assert.equal(atStart.prev.hidden, false);
  assert.equal(atStart.next.hidden, false);
  const atEnd = setup({scrollLeft: MAX});
  assert.equal(atEnd.prev.hidden, false);
  assert.equal(atEnd.next.hidden, false);
});

test('a rail that fits shows no arrows', () => {
  const {prev, next} = setup({scrollWidth: 400, clientWidth: 500, count: 2});
  assert.equal(prev.hidden, true);
  assert.equal(next.hidden, true);
});
