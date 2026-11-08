const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/page-settings.js', 'utf8');
const code = source.slice(source.indexOf('let _hardwareData = null;'), source.indexOf('async function loadVideoSettings()'));

function setup(get) {
  const button = {disabled: false};
  const target = {children: [], replaceChildren() { this.children = []; }, appendChild(node) { this.children.push(node); }};
  let language = 'it';
  const context = vm.createContext({
    document: {getElementById: id => id === 'probe-hardware' ? button : target,
      createElement: () => ({})},
    api: {get},
    t: (key, values) => `${language}:${key}:${JSON.stringify(values || {})}`,
    _settingsText: (node, text) => { node.textContent = text; },
    _settingsTextBindings: new Map(),
  });
  vm.runInContext(code, context);
  return {context, button, target, language: value => { language = value; }};
}

function data() {
  return {cpu: {name: 'CPU', physical_cores: 4, logical_threads: 8},
    memory: {total_bytes: 16 * 1024 ** 3, available_bytes: 8 * 1024 ** 3},
    gpus: [{name: '<img src=x onerror=alert(1)>', vram_bytes: null}],
    encoders: [{encoder: 'h264_nvenc', available: true}, {encoder: 'h264_amf', available: false}],
    recommendations: {video_encoder: 'nvenc', video_codec: 'h264', max_parallel_conversions: 1,
      max_concurrent_downloads: 3, max_segment_workers: 16}, warnings: []};
}

test('on-demand read, busy state, safe text and no automatic settings write', async () => {
  let resolve;
  const calls = [];
  const app = setup(path => {calls.push(path); return new Promise(done => {resolve = done;});});
  const pending = vm.runInContext('probeHardware()', app.context);
  assert.equal(app.button.disabled, true);
  resolve(data());
  await pending;
  assert.equal(app.button.disabled, false);
  assert.deepEqual(calls, ['/api/domain/hardware']);
  const text = app.target.children.map(row => row.textContent).join('\n');
  assert.match(text, /<img src=x onerror=alert\(1\)>/);
  assert.match(text, /h264_nvenc/);
  assert.doesNotMatch(text, /h264_amf/);
  assert.ok(app.target.children.every(row => !('innerHTML' in row)));
});

test('failed request restores the button and displays translated feedback', async () => {
  const app = setup(async () => {throw new Error('offline');});
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
