const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const appSource = fs.readFileSync('app/static/app.js', 'utf8');
const bannerCode = appSource.slice(appSource.indexOf('const _TOOLS_DISMISS_KEY'),
  appSource.indexOf("// The banner is the shell's"));
const settingsSource = fs.readFileSync('app/static/page-settings.js', 'utf8');
const ffmpegAt = settingsSource.indexOf('async function updateFfmpeg()');
const installCode = settingsSource.slice(settingsSource.indexOf('// yt-dlp: a standalone yt-dlp.exe'),
  ffmpegAt + settingsSource.slice(ffmpegAt).search(/\r?\n\}\r?\n/) + 3);
const routerSource = fs.readFileSync('app/static/router.js', 'utf8');

function bannerHarness({get, stored = []} = {}) {
  const store = {'sc.toolsBanner.dismissed': JSON.stringify(stored)};
  const banner = {style: {display: 'none'}, dataset: {}};
  const message = {textContent: ''};
  const navigations = [];
  const context = vm.createContext({
    document: {getElementById: id => ({'tools-banner': banner, 'tools-banner-message': message}[id] || null)},
    localStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }},
    api: {get},
    navigate: (...args) => navigations.push(args),
  });
  vm.runInContext(bannerCode, context);
  return {context, banner, message, store, navigations};
}

test('toolsBannerState: missing tools, dismissals and their reset', () => {
  const {context} = bannerHarness();
  const s = (p, d) => JSON.parse(JSON.stringify(context.toolsBannerState(p, d)));
  assert.deepEqual(s({ytdlp: false, ffmpeg: false}, []), {kept: [], missing: ['ytdlp', 'ffmpeg']});
  assert.deepEqual(s({ytdlp: false, ffmpeg: true}, ['ytdlp']), {kept: ['ytdlp'], missing: []});
  // Installed: its dismissal is dropped, so a later loss shows the banner again.
  assert.deepEqual(s({ytdlp: true, ffmpeg: false}, ['ytdlp']), {kept: [], missing: ['ffmpeg']});
  assert.deepEqual(s({ytdlp: true, ffmpeg: true}, []), {kept: [], missing: []});
});

test('a re-check after an install takes the banner down without a reload', async () => {
  let presence = {ytdlp: false, ffmpeg: true};
  const h = bannerHarness({get: async () => presence});
  await h.context.loadToolsPresence();
  assert.equal(h.banner.style.display, '');
  assert.equal(h.banner.dataset.missing, 'ytdlp');
  presence = {ytdlp: true, ffmpeg: true};
  await h.context.loadToolsPresence();
  assert.equal(h.banner.style.display, 'none');
  assert.equal(h.banner.dataset.missing, '');
});

test('Ignora is remembered and reset once the tool is present', async () => {
  let presence = {ytdlp: false, ffmpeg: true};
  const h = bannerHarness({get: async () => presence});
  await h.context.loadToolsPresence();
  h.context.dismissToolsBanner();
  assert.equal(h.store['sc.toolsBanner.dismissed'], '["ytdlp"]');
  await h.context.loadToolsPresence();
  assert.equal(h.banner.style.display, 'none');
  presence = {ytdlp: true, ffmpeg: true};
  await h.context.loadToolsPresence();
  assert.equal(h.store['sc.toolsBanner.dismissed'], '[]');
});

test('a stale presence reply does not repaint over a newer one', async () => {
  const replies = [];
  const h = bannerHarness({get: () => new Promise(resolve => replies.push(resolve))});
  const first = h.context.loadToolsPresence();
  const second = h.context.loadToolsPresence();
  replies[1]({ytdlp: true, ffmpeg: true});
  await second;
  replies[0]({ytdlp: false, ffmpeg: false});
  await first;
  assert.equal(h.banner.style.display, 'none');
});

test('showPage re-checks presence once the boot has armed it', () => {
  const at = appSource.indexOf('function showPage(');
  const showPage = appSource.slice(at, at + appSource.slice(at).search(/syncHash\(page\);\r?\n\}/));
  assert.ok(showPage.length > 100);
  assert.match(showPage, /if \(_toolsPresenceArmed\) loadToolsPresence\(\);/);
  assert.match(appSource, /_toolsPresenceArmed = true; loadToolsPresence\(\);/);
});

test('Apri Impostazioni routes to Impostazioni > Download (Hardware section)', () => {
  const h = bannerHarness();
  h.context.openToolsSettings();
  assert.deepEqual(JSON.parse(JSON.stringify(h.navigations)), [['settings', ['download']]]);
  assert.match(appSource, /'tools:settings': \(\) => openToolsSettings\(\)/);

  // The router turns that address into openSettingsPage('download').
  const opened = [];
  const location = {hash: ''};
  const ctx = vm.createContext({
    location, history: {replaceState() {}}, document: {querySelector: () => null},
    window: {addEventListener() {}}, URLSearchParams,
    openSettingsPage: tab => opened.push(tab), showPage() {}, defaultPage: () => 'search',
  });
  vm.runInContext(routerSource, ctx);
  location.hash = ctx.hashFor('settings', ['download']);
  assert.equal(location.hash, '#/settings/download');
  ctx.routeFromHash();
  assert.deepEqual(opened, ['download']);

  // The Download pane opens on the Hardware section, which holds the buttons.
  const tpl = fs.readFileSync('app/templates/pages/_settings.html', 'utf8');
  const pane = tpl.slice(tpl.indexOf('data-settings-pane="download"'));
  assert.ok(pane.indexOf('settings.hardware.title') < pane.indexOf('cfg:checkYtdlp'));
  assert.ok(pane.indexOf('cfg:checkYtdlp') < pane.indexOf('data-settings-pane=', 10));
});

function installHarness(status) {
  const out = {className: '', textContent: ''};
  const rechecks = [];
  const context = vm.createContext({
    document: {getElementById: id => (id === 'ytdlp-status' ? out : null), querySelector: () => ({disabled: false})},
    api: {get: async () => status, post: async url => (url.includes('ffmpeg') ? {version: '7.1'} : {version: status.latest})},
    scConfirm: async () => true,
    _settingsError: (e, fallback) => (e && e.message) || fallback,
    _feedback() {}, _clearFfmpegFeedbackLater() {}, loadVideoEncoders: async () => {},
    loadToolsPresence: () => rechecks.push(1),
  });
  vm.runInContext(installCode, context);
  return {context, rechecks};
}

test('successful yt-dlp and FFmpeg installs re-check the banner; a no-op does not', async () => {
  const missing = installHarness({present: false, latest: '2026.11.10', update_available: false, can_update: true});
  await missing.context.checkYtdlp();
  assert.equal(missing.rechecks.length, 1);
  const upToDate = installHarness({present: true, installed: '1', latest: '1', update_available: false, can_update: true});
  await upToDate.context.checkYtdlp();
  assert.equal(upToDate.rechecks.length, 0);
  await upToDate.context.updateFfmpeg();
  assert.equal(upToDate.rechecks.length, 1);
});
