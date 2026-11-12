const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/page-settings.js', 'utf8');
const code = source.slice(source.indexOf('let _hardwareData = null;'), source.indexOf('async function loadVideoSettings()'));

function setup(get, post = async () => ({})) {
  const button = {disabled: false};
  const target = {children: [], replaceChildren() { this.children = []; }, appendChild(node) { this.children.push(node); }};
  const inputs = {'setting-max-concurrent': {value: ''}, 'setting-max-workers': {value: ''},
    'reset-conservative': {hidden: true, disabled: false},
    'source-protection-status': {hidden: true, textContent: ''}};
  const feedback = [];
  let language = 'it';
  const context = vm.createContext({
    document: {getElementById: id => id === 'probe-hardware' ? button : (inputs[id] || target),
      createElement: () => ({})},
    // get() is the diagnosis' read-only sibling and the protection poll;
    // post() runs the diagnosis that applies its preset server-side.
    api: {get, post, put: async () => { throw new Error('the diagnosis must not PUT settings'); }},
    _feedback: (id, text, kind) => { feedback.push({id, text, kind}); },
    t: (key, values) => `${language}:${key}:${JSON.stringify(values || {})}`,
    _settingsText: (node, text) => { node.textContent = text; },
    _settingsTextBindings: new Map(),
  });
  vm.runInContext(code, context);
  return {context, button, target, inputs, feedback, language: value => { language = value; }};
}

function data() {
  return {cpu: {name: 'CPU', physical_cores: 4, logical_threads: 8},
    memory: {total_bytes: 16 * 1024 ** 3, available_bytes: 8 * 1024 ** 3},
    gpus: [{name: '<img src=x onerror=alert(1)>', vram_bytes: null}],
    encoders: [{encoder: 'h264_nvenc', available: true}, {encoder: 'h264_amf', available: false}],
    recommendations: {video_encoder: 'nvenc', video_codec: 'h264', max_parallel_conversions: 1,
      max_concurrent_downloads: 2, max_segment_workers: 8, preset: 'balanced'}, warnings: []};
}

function diagnosed() {
  return {...data(), ffmpeg_available: true,
    source: {pushbacks_last_24h: 0, requests_last_hour: 3, limit_per_hour: 120, backoff_active: false},
    applied: {preset: 'balanced', label: 'Bilanciato', reasons: ['Hardware medio.'],
      values: {max_concurrent_downloads: 2, max_segment_workers: 8, source_max_connections: 6}},
    settings: {max_concurrent_downloads: 2, max_segment_workers: 8, source_max_connections: 6}};
}

// The diagnosis applies its preset on the server (POST .../hardware/diagnose)
// and the page only reports what was set and why. The video encoder is still
// only suggested: nothing here writes it.
test('on-demand diagnosis, busy state, safe text and the applied preset explained', async () => {
  let resolve;
  const calls = [];
  const app = setup(async () => ({}), path => {calls.push(path); return new Promise(done => {resolve = done;});});
  const pending = vm.runInContext('probeHardware()', app.context);
  assert.equal(app.button.disabled, true);
  resolve(diagnosed());
  await pending;
  assert.equal(app.button.disabled, false);
  assert.deepEqual(calls, ['/api/domain/hardware/diagnose']);
  assert.equal(app.inputs['setting-max-concurrent'].value, 2);
  assert.equal(app.inputs['setting-max-workers'].value, 8);
  assert.equal(app.inputs['reset-conservative'].hidden, false);
  assert.equal(app.feedback.length, 1);
  assert.match(app.feedback[0].text, /settings.hardware.presetApplied/);
  assert.match(app.feedback[0].text, /Bilanciato/);
  assert.match(app.feedback[0].text, /Hardware medio/);
  const text = app.target.children.map(row => row.textContent).join('\n');
  assert.match(text, /<img src=x onerror=alert\(1\)>/);
  assert.match(text, /h264_nvenc/);
  assert.doesNotMatch(text, /h264_amf/);
  assert.match(text, /settings.hardware.ffmpegOk/);
  assert.match(text, /settings.hardware.source/);
  assert.ok(app.target.children.every(row => !('innerHTML' in row)));
});

test('"Ripristina Conservativo" asks the server for the conservative preset', async () => {
  const posts = [];
  const app = setup(async () => ({}), async (path, body) => {
    posts.push([path, JSON.parse(JSON.stringify(body))]);
    return {preset: 'conservative', settings: {max_concurrent_downloads: 1, max_segment_workers: 4, source_max_connections: 4}};
  });
  await vm.runInContext('resetConservativePreset()', app.context);
  assert.deepEqual(posts, [['/api/domain/performance-preset', {preset: 'conservative'}]]);
  assert.equal(app.inputs['setting-max-concurrent'].value, 1);
  assert.equal(app.inputs['setting-max-workers'].value, 4);
  assert.match(app.feedback[0].text, /settings.hardware.conservativeRestored/);
});

test('a source pause shows when the queue resumes, and hides once over', () => {
  const app = setup();
  const box = app.inputs['source-protection-status'];
  app.context.status = {paused: true, until_hhmm: '14:35', downgraded_at: null};
  vm.runInContext('renderSourceProtection(status)', app.context);
  assert.equal(box.hidden, false);
  assert.match(box.textContent, /settings.hardware.queuePaused/);
  assert.match(box.textContent, /14:35/);
  app.context.status = {paused: false, until_hhmm: null, downgraded_at: null};
  vm.runInContext('renderSourceProtection(status)', app.context);
  assert.equal(box.hidden, true);
});

test('failed request restores the button and displays translated feedback', async () => {
  const app = setup(async () => ({}), async () => {throw new Error('offline');});
  await vm.runInContext('probeHardware()', app.context);
  assert.equal(app.button.disabled, false);
  assert.match(app.target.textContent, /settings.hardware.failed/);
});

test('unknown data, no encoder and language refresh stay readable', () => {
  const app = setup();
  const result = data();
  result.cpu = {};
  result.memory = {};
  result.gpus = [];
  result.encoders = [];
  result.recommendations.video_encoder = null;
  result.warnings = ['encoders_unavailable'];
  app.context.result = result;
  vm.runInContext('renderHardwareResults(result)', app.context);
  assert.match(app.target.children.map(row => row.textContent).join('\n'), /it:settings.hardware.unknown/);
  app.language('en');
  vm.runInContext('renderHardwareResults(result)', app.context);
  const text = app.target.children.map(row => row.textContent).join('\n');
  assert.doesNotMatch(text, /it:/);
  assert.match(text, /en:settings.hardware.noEncoder/);
  assert.match(text, /en:settings.hardware.partial/);
});

test('every hardware message has an Italian and English translation', () => {
  const i18n = fs.readFileSync('app/static/i18n.js', 'utf8');
  const messages = [...new Set((source + fs.readFileSync('app/templates/pages/_settings.html', 'utf8'))
    .match(/settings\.hardware\.[a-zA-Z]+/g))];
  for (const key of messages) {
    const line = i18n.split('\n').find(row => row.startsWith(`"${key}":`));
    assert.ok(line, key);
    assert.match(line, /it:/);
    assert.match(line, /en:/);
  }
});

// The diagnosis explains the new limits and the 72h/7-day source history, and
// says so when an environment variable overrides the profile.
test('the diagnosis shows source connections, budget, 72h/7d history and env overrides', async () => {
  const result = diagnosed();
  result.source = {pushbacks_last_72h: 1, pushbacks_last_7d: 2, history_hours: 80.4,
    requests_last_hour: 3, limit_per_hour: 30, limit_from_env: true, backoff_active: false};
  result.recommendations = {...result.recommendations, max_concurrent_downloads: 1, max_segment_workers: 4,
    source_max_connections: 2, source_requests_per_hour: 60, preset: 'conservative'};
  result.applied = {preset: 'conservative', label: 'Conservativo',
    reasons: ['La sorgente ha rifiutato o rallentato richieste nelle ultime 72 ore (1).'],
    values: {max_concurrent_downloads: 1, max_segment_workers: 4, source_max_connections: 2, source_requests_per_hour: 60},
    source_cap_from_env: true, effective_source_max_connections: 9};
  result.settings = result.applied.values;
  const app = setup(async () => ({}), async () => result);
  await vm.runInContext('probeHardware()', app.context);
  const feedback = app.feedback[0].text;
  assert.match(feedback, /"connections":2/);
  assert.match(feedback, /"requests":60/);
  assert.match(feedback, /72 ore/);
  const text = app.target.children.map(row => row.textContent).join('\n');
  assert.match(text, /settings.hardware.source:\{"pushbacks":1,"week":2,"hours":80/);
  assert.match(text, /settings.hardware.capFromEnv:\{"cap":9\}/);
  assert.match(text, /settings.hardware.budgetFromEnv:\{"limit":30\}/);
  assert.match(text, /settings.hardware.downloads:\{"count":1,"workers":4,"connections":2,"requests":60\}/);
});

test('the protection box says when an environment variable sets the limits', () => {
  const app = setup();
  const box = app.inputs['source-protection-status'];
  app.context.status = {paused: false, downgraded_at: null, source_cap_from_env: true,
    effective_source_max_connections: 9, budget_from_env: false};
  vm.runInContext('renderSourceProtection(status)', app.context);
  assert.equal(box.hidden, false);
  assert.match(box.textContent, /settings.hardware.capFromEnv/);
  assert.doesNotMatch(box.textContent, /budgetFromEnv/);
});
