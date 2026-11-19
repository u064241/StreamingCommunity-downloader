const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/page-settings.js', 'utf8');
const code = source.slice(source.indexOf('// yt-dlp: a standalone yt-dlp.exe'), source.indexOf('async function updateFfmpeg()'));

function setup(status, {confirm = true, post = async () => ({version: status.latest})} = {}) {
  const out = {className: '', textContent: ''};
  const button = {disabled: false};
  const posts = [];
  const asked = [];
  const context = vm.createContext({
    document: {getElementById: id => (id === 'ytdlp-status' ? out : null), querySelector: () => button},
    api: {get: async () => status, post: async (url, body) => { posts.push(url); return post(url, body); }},
    scConfirm: async question => { asked.push(question); return confirm; },
    _settingsError: (e, fallback) => (e && e.message) || fallback,
  });
  vm.runInContext(code, context);
  return {context, out, button, posts, asked};
}

test('up to date: no confirmation, no update request', async () => {
  const s = setup({present: true, installed: '2026.11.10', latest: '2026.11.10', update_available: false, can_update: true});
  await s.context.checkYtdlp();
  assert.equal(s.out.textContent, 'yt-dlp 2026.11.10 aggiornato.');
  assert.deepEqual(s.posts, []);
  assert.equal(s.asked.length, 0);
  assert.equal(s.button.disabled, false);
});

test('missing: asks, then installs from GitHub', async () => {
  const s = setup({present: false, installed: null, latest: '2026.11.10', update_available: false, can_update: true});
  await s.context.checkYtdlp();
  assert.match(s.asked[0], /GitHub/);
  assert.deepEqual(s.posts, ['/api/domain/ytdlp/update']);
  assert.equal(s.out.textContent, 'yt-dlp 2026.11.10 installato.');
});

test('update declined or downloads running: nothing is posted', async () => {
  const declined = setup({present: true, installed: '2025.1.1', latest: '2026.11.10', update_available: true, can_update: true},
    {confirm: false});
  await declined.context.checkYtdlp();
  assert.deepEqual(declined.posts, []);
  const busy = setup({present: true, installed: '2025.1.1', latest: '2026.11.10', update_available: true, can_update: true, busy: true});
  await busy.context.checkYtdlp();
  assert.deepEqual(busy.posts, []);
  assert.equal(busy.asked.length, 0);
  assert.match(busy.out.textContent, /terminati/);
});

test('a refused update shows the server message and restores the button', async () => {
  const s = setup({present: true, installed: '2025.1.1', latest: '2026.11.10', update_available: true, can_update: true},
    {post: async () => { throw new Error('SHA-256 non corrispondente'); }});
  await s.context.checkYtdlp();
  assert.equal(s.out.className, 'form-text text-danger');
  assert.equal(s.out.textContent, 'SHA-256 non corrispondente');
  assert.equal(s.button.disabled, false);
});
