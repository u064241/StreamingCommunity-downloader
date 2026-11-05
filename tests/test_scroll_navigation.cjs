const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function create(height, reduced = false) {
  const listeners = {}, clicks = {}, calls = [], elements = {};
  for (const id of ['page-scroll', 'page-scroll-up', 'page-scroll-down']) {
    elements[id] = {addEventListener: (event, fn) => {clicks[id] = fn;}};
  }
  const window = {innerHeight: 600, scrollY: 0,
    addEventListener: (name, fn) => {listeners[name] = fn;},
    matchMedia: () => ({matches: reduced}), scrollTo: data => calls.push(data)};
  vm.runInNewContext(fs.readFileSync('app/static/scroll-navigation.js', 'utf8'), {
    document: {getElementById: id => elements[id], documentElement: {scrollHeight: height}},
    window, requestAnimationFrame: fn => fn(),
  });
  return {window, listeners, clicks, calls, elements};
}
test('short pages do not show floating arrows', () => {
  assert.equal(create(700).elements['page-scroll'].hidden, true);
});
test('arrows jump to both ends and update their disabled state', () => {
  const app = create(3000);
  assert.equal(app.elements['page-scroll'].hidden, false);
  assert.equal(app.elements['page-scroll-up'].disabled, true);
  app.clicks['page-scroll-down']();
  assert.equal(app.calls[0].top, 3000);
  assert.equal(app.calls[0].behavior, 'smooth');
  app.window.scrollY = 2400;
  app.listeners.scroll();
  assert.equal(app.elements['page-scroll-down'].disabled, true);
  app.clicks['page-scroll-up']();
  assert.equal(app.calls[1].top, 0);
});
test('reduced motion disables smooth scrolling', () => {
  const app = create(3000, true);
  app.clicks['page-scroll-down']();
  assert.equal(app.calls[0].behavior, 'instant');
});
