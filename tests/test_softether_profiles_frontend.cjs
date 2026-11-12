const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('app/static/page-settings.js', 'utf8');
const code = source.slice(source.indexOf('function formatVpnGateUptime('), source.indexOf('function hideVpnGateServers()'));
const core = fs.readFileSync('app/static/core.js', 'utf8');
const escapeSource = core.slice(core.indexOf('function escapeHtml('), core.indexOf('function formatSize('));

// Minimal DOM: elements by id, innerHTML captured as a string, and buttons
// parsed back from it so tests check what the user would actually get.
function setup({servers = [], profiles = [], details = {}, testMode = true} = {}) {
  const elements = {};
  const element = id => elements[id] || (elements[id] = {
    id, hidden: true, innerHTML: '', textContent: '', className: '',
    querySelectorAll: () => [],
  });
  const feedback = [];
  const context = vm.createContext({
    document: {getElementById: element, documentElement: {lang: 'it-IT'}},
    window: {_softEtherRefreshTimer: 1},
    Intl, Number, Set, String,
    setInterval: () => 1,
    t: (key, values) => `${key}${values ? JSON.stringify(values) : ''}`,
    _feedback: (id, text, kind) => feedback.push({id, text, kind}),
    _settingsText: (node, text) => { node.textContent = text; },
    _softetherWizard: () => {},
    _softetherError: error => String(error),
    _softetherBlockDetected: false,
    _softetherTestMode: testMode,
    _softetherIpRefreshTimer: 1,
    softetherApi: {
      vpngate: async () => ({servers}),
      publicIp: async () => { throw new Error('offline'); },
      vpngateProfiles: async () => ({profiles, details, raw: profiles.join('\n')}),
    },
  });
  vm.runInContext(escapeSource + code, context);
  return {context, element, feedback};
}

function buttons(html) {
  return [...html.matchAll(/<button\b([^>]*)>([^<]*)<\/button>/g)].map(([, attrs, label]) => ({
    attrs, label,
    disabled: /\sdisabled(\s|$|>)/.test(attrs + ' '),
    kind: (attrs.match(/data-vpngate-(connect|disconnect|delete|index)=/) || [])[1],
    target: (attrs.match(/data-vpngate-(?:connect|disconnect|delete)="([^"]*)"/) || [])[1],
  }));
}

function rows(html) {
  return html.split('<div class="softether-vpngate-row').slice(1);
}

test('registered, not connected: connect and delete on, disconnect off', () => {
  const {context} = setup();
  const html = vm.runInContext('renderVpnGateProfileRow("VPN Gate IT", {host: "1.2.3.4", port: 443, country: "Italy", connected: false, ping: 42})', context);
  const byKind = Object.fromEntries(buttons(html).map(b => [b.kind, b]));
  assert.equal(byKind.connect.disabled, false);
  assert.equal(byKind.disconnect.disabled, true);
  assert.equal(byKind.delete.disabled, false);
  assert.match(html, /data-vpngate-state="offline"/);
  assert.match(html, /softether\.profileOffline/);
  assert.match(html, /1\.2\.3\.4:443/);
  assert.match(html, /Italy/);
  assert.match(html, /softether\.profilePing\{&quot;ms&quot;:42\}/);
});

test('connected profile: delete and connect off, disconnect on', () => {
  const {context} = setup();
  const html = vm.runInContext('renderVpnGateProfileRow("VPN Gate IT", {host: "1.2.3.4", connected: true})', context);
  const byKind = Object.fromEntries(buttons(html).map(b => [b.kind, b]));
  assert.equal(byKind.delete.disabled, true);
  assert.equal(byKind.connect.disabled, true);
  assert.equal(byKind.disconnect.disabled, false);
  assert.match(html, /data-vpngate-state="connected"/);
  assert.match(html, /softether\.profileConnected/);
});

test('unknown state never allows deleting', () => {
  const {context} = setup();
  const html = vm.runInContext('renderVpnGateProfileActions("VPN Gate IT", {connected: null})', context);
  const byKind = Object.fromEntries(buttons(html).map(b => [b.kind, b]));
  assert.equal(byKind.delete.disabled, true);
});

test('server without profile shows only "Crea profilo"', () => {
  const {context} = setup();
  const html = vm.runInContext('renderVpnGateServerRow({host: "5.6.7.8", port: 443, countryCode: "JP", ping: 10, speed: 1e6, registered_profile: null}, 3, {}, true)', context);
  const found = buttons(html);
  assert.equal(found.length, 1);
  assert.equal(found[0].kind, 'index');
  assert.match(found[0].label, /softether\.vpngateCreate/);
  assert.doesNotMatch(html, /data-vpngate-registered/);
  assert.match(html, /softether\.reachablePort/);
});

test('server with a registered profile shows the badge and that profile\'s buttons', () => {
  const {context} = setup();
  context.details = {'VPN Gate IT': {connected: false}};
  const html = vm.runInContext('renderVpnGateServerRow({host: "1.2.3.4", countryCode: "IT", ping: 10, speed: 1e6, registered_profile: "VPN Gate IT"}, 0, details, true)', context);
  assert.match(html, /data-vpngate-registered="VPN Gate IT"/);
  assert.match(html, /softether\.serverRegistered/);
  const found = buttons(html);
  assert.deepEqual(found.map(b => b.kind), ['connect', 'disconnect', 'delete']);
  assert.ok(found.every(b => b.target === 'VPN Gate IT'));
});

test('"VPN Gate IT" never activates the buttons of "VPN Gate ITA-2" (no substring match)', async () => {
  const app = setup({
    servers: [
      {host: '9.9.9.9', port: 443, countryCode: 'IT', country: 'Italy', ping: 5, speed: 2e6, registered_profile: null},
      {host: '1.2.3.45', port: 443, countryCode: 'IT', country: 'Italy', ping: 6, speed: 2e6, registered_profile: null},
    ],
    // Both names are in the raw AccountList text the old code searched.
    profiles: ['VPN Gate IT', 'VPN Gate ITA-2'],
    details: {
      'VPN Gate IT': {host: '1.2.3.4', port: 443, connected: false},
      'VPN Gate ITA-2': {host: '8.8.8.8', port: 443, connected: true},
    },
  });
  await vm.runInContext('loadVpnGateServers()', app.context);
  const html = app.element('softether-vpngate-list').innerHTML;
  const serverRows = rows(html).filter(row => row.includes('data-vpngate-server='));
  assert.equal(serverRows.length, 2);
  for (const row of serverRows) {
    const found = buttons(row);
    assert.deepEqual(found.map(b => b.kind), ['index'], row);
    assert.doesNotMatch(row, /data-vpngate-registered/);
  }
  // Each profile row drives only its own profile, with its own state.
  const profileRows = rows(html).filter(row => row.includes('data-vpngate-profile='));
  assert.equal(profileRows.length, 2);
  const ita2 = profileRows.find(row => row.includes('data-vpngate-profile="VPN Gate ITA-2"'));
  const it = profileRows.find(row => row.includes('data-vpngate-profile="VPN Gate IT"'));
  assert.ok(buttons(ita2).every(b => b.target === 'VPN Gate ITA-2'));
  assert.equal(buttons(ita2).find(b => b.kind === 'delete').disabled, true);
  assert.equal(buttons(it).find(b => b.kind === 'delete').disabled, false);
  assert.doesNotMatch(html, />Raggiungibile</);
});

test('names and hosts are escaped', () => {
  const {context} = setup();
  const html = vm.runInContext('renderVpnGateServerRow({host: "<img src=x>", country: "<b>", name: "\\"x", ping: 1, speed: 1, registered_profile: null}, 0, {}, true)', context);
  assert.doesNotMatch(html, /<img|<b>/);
  assert.match(html, /&lt;img src=x&gt;/);
});

test('a second server of the same country gets a distinct profile name', () => {
  const {context} = setup();
  assert.equal(vm.runInContext('vpnGateProfileNameFor({countryCode: "IT", host: "1.2.3.4"}, [])', context), 'VPN Gate IT');
  assert.equal(vm.runInContext('vpnGateProfileNameFor({countryCode: "IT", host: "5.6.7.8"}, ["VPN Gate IT"])', context), 'VPN Gate IT 5.6.7.8');
});

test('every new SoftEther message has Italian and English text', () => {
  const i18n = fs.readFileSync('app/static/i18n.js', 'utf8');
  const keys = [...new Set(code.match(/softether\.[a-zA-Z]+/g))];
  for (const key of keys) {
    const line = i18n.split('\n').find(row => row.trimStart().startsWith(`'${key}':`));
    assert.ok(line, key);
    assert.match(line, /\[\s*'[^']+',\s*'[^']+'\s*\]/, key);
  }
});

// ── Source block: "IP blocked" notice ────────────────────────────────────────

function between(source, start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + 1);
  assert.ok(from >= 0 && to > from, `markers not found: ${start} … ${end}`);
  return source.slice(from, to);
}

class FakeEvent { constructor(type) { this.type = type; } }

function fakeWindow() {
  const listeners = {};
  const dispatched = [];
  return {
    listeners, dispatched,
    addEventListener: (type, fn) => (listeners[type] ||= []).push(fn),
    dispatchEvent: event => {
      dispatched.push(event.type);
      for (const fn of listeners[event.type] || []) fn(event);
      return true;
    },
    t: key => key,
  };
}

const gateCode = between(core, '// A blocked source answers', '// ── Artwork');
const noticeCode = between(source, '// The visible "IP blocked" notice', 'async function openSoftEtherManager(');

function blockSetup({settingsVisible = false, sorgenteVisible = true, publicIp} = {}) {
  const win = fakeWindow();
  let now = 1_000_000_000_000;
  const toasts = [];
  const nodes = {
    'page-settings': {style: {display: settingsVisible ? '' : 'none'}},
    // Laid out only on the Settings page, on the Sorgente tab.
    'softether-block-notice': {hidden: true, offsetParent: settingsVisible && sorgenteVisible ? {} : null},
    'softether-block-ip-check': {disabled: false},
  };
  const feedback = [];
  const ipCalls = [];
  const context = vm.createContext({
    window: win, CustomEvent: FakeEvent,
    Date: {now: () => now},
    document: {getElementById: id => nodes[id] || null},
    showToast: (message, type, link) => toasts.push({message, type, link}),
    t: (key, values) => `${key}${values ? JSON.stringify(values) : ''}`,
    _feedback: (id, text = '', kind) => feedback.push({id, text, kind}),
    _softetherError: error => `err:${error.message}`,
    softetherApi: {publicIp: async () => {
      ipCalls.push(1);
      if (publicIp) return publicIp();
      return {current: '203.0.113.7', original: '203.0.113.7', changed: false};
    }},
  });
  vm.runInContext(gateCode + noticeCode, context);
  const block = () => win.dispatchEvent(new FakeEvent('softether:block-detected'));
  return {context, win, nodes, toasts, feedback, ipCalls, block, advance: ms => { now += ms; }};
}

test('a block signal shows the notice and the toast, without reading the IP', () => {
  const app = blockSetup();
  assert.equal(app.nodes['softether-block-notice'].hidden, true);
  app.block();
  assert.equal(app.nodes['softether-block-notice'].hidden, false);
  assert.equal(app.toasts.length, 1);
  assert.equal(app.toasts[0].message, 'softether.blockDetected');
  assert.equal(app.toasts[0].link.page, 'settings');
  assert.equal(app.ipCalls.length, 0);
});

test('on the Settings page the notice is shown without a duplicate toast', () => {
  const app = blockSetup({settingsVisible: true});
  app.block();
  assert.equal(app.nodes['softether-block-notice'].hidden, false);
  assert.equal(app.toasts.length, 0);
});

test('on another Settings tab the notice is not laid out, so the toast goes out and opens Sorgente', () => {
  const app = blockSetup({settingsVisible: true, sorgenteVisible: false});
  app.block();
  assert.equal(app.toasts.length, 1);
  assert.equal(app.toasts[0].link.page, 'settings');
  assert.equal(app.toasts[0].link.tab, 'sorgente');
});

test('a cascade of 403s gives one notice per interval', () => {
  const app = blockSetup();
  assert.match(gateCode, /const SOFTETHER_BLOCK_NOTICE_INTERVAL_MS = 10 \* 60 \* 1000;/);
  app.block();
  app.nodes['softether-block-notice'].hidden = true;   // dismissed by a reload
  for (let i = 0; i < 20; i += 1) app.block();
  app.advance(9 * 60 * 1000);
  app.block();
  assert.equal(app.toasts.length, 1);
  assert.equal(app.win.dispatched.filter(type => type === 'softether:block-notice').length, 1);
  assert.equal(app.nodes['softether-block-notice'].hidden, true);
  app.advance(60 * 1000);
  app.block();
  assert.equal(app.toasts.length, 2);
  assert.equal(app.nodes['softether-block-notice'].hidden, false);
  assert.equal(app.ipCalls.length, 0);
});

test('nothing in the notice path reads the IP or the source on its own', () => {
  assert.doesNotMatch(gateCode, /setInterval|setTimeout|softetherApi|api\.get|fetch\(/);
  const showOnly = between(source, 'function showSoftEtherBlockNotice(', '// One public-IP read per click');
  assert.doesNotMatch(showOnly, /publicIp|setInterval|setTimeout|api\.|fetch\(/);
  const blockListener = between(source, "window.addEventListener('softether:block-detected'", '// The visible "IP blocked" notice');
  assert.doesNotMatch(blockListener, /publicIp|setInterval/);
});

test('"Controlla IP pubblico" reads the public IP once per click and shows it', async () => {
  const app = blockSetup();
  app.block();
  await vm.runInContext('checkSoftEtherBlockIp()', app.context);
  assert.equal(app.ipCalls.length, 1);
  const last = app.feedback.filter(f => f.id === 'softether-block-ip').at(-1);
  assert.match(last.text, /203\.0\.113\.7/);
  assert.equal(app.nodes['softether-block-ip-check'].disabled, false);
  const failing = blockSetup({publicIp: () => { throw new Error('offline'); }});
  await vm.runInContext('checkSoftEtherBlockIp()', failing.context);
  assert.equal(failing.ipCalls.length, 1);
  assert.equal(failing.feedback.at(-1).kind, 'danger');
});

test('the notice buttons are wired to existing actions', () => {
  const html = fs.readFileSync('app/templates/pages/_settings.html', 'utf8');
  const box = between(html, 'id="softether-block-notice"', 'id="softether-status"');
  assert.match(box, /data-action="cfg:softetherOpen"/);
  assert.match(box, /id="softether-block-ip-check"[^>]*data-action="cfg:softetherBlockIp"/);
  assert.match(source, /'cfg:softetherBlockIp': \(\) => checkSoftEtherBlockIp\(\)/);
  assert.doesNotMatch(box, /connect|vpngate/i);
});

test('the block notice messages have Italian and English text', () => {
  const i18n = fs.readFileSync('app/static/i18n.js', 'utf8');
  for (const key of ['softether.blockNotice', 'softether.blockDetected', 'softether.checkPublicIp',
    'softether.checkingPublicIp', 'softether.openManager', 'softether.publicIp']) {
    const line = i18n.split('\n').find(row => row.trimStart().startsWith(`'${key}':`));
    assert.ok(line, key);
    assert.match(line, /\[\s*'[^']+',\s*'[^']+'\s*\]/, key);
  }
});
