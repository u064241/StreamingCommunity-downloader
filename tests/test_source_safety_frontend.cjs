// RaiPlay's read-only title page.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function escapeHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function slice(source, start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + 1);
  assert.ok(from >= 0 && to > from, `markers not found: ${start} … ${end}`);
  return source.slice(from, to);
}

// ── Title page: RaiPlay is read-only ─────────────────────────────────────────

const title = fs.readFileSync('app/static/page-title.js', 'utf8');
const titleCode = [
  slice(title, 'const TP_DOWNLOAD_SOURCES', 'function _tpApplyMeta(meta)'),
  slice(title, 'function _tpRenderCta()', 'function _tpRenderTabsBar()'),
  slice(title, 'function tpPrimary()', '// Back to wherever the visitor came from'),
].join('\n');

function titleSetup(get) {
  const nodes = {'th-cta': {innerHTML: ''}};
  const calls = [];
  const context = vm.createContext({
    document: {getElementById: id => nodes[id] || null},
    api: {get: async (path, params) => { calls.push(path); return get(path, params); }},
    escapeHtml, t: key => key, can: () => true,
    _tpRender: () => {}, _tpToken: 1, _tp: null,
    startFilmDownload: () => { throw new Error('download started'); },
    downloadAllAnime: () => { throw new Error('download started'); },
    downloadWholeSeries: () => { throw new Error('download started'); },
    downloadWholeSeason: () => { throw new Error('download started'); },
    tpRequestSeason: () => { throw new Error('request made'); },
  });
  vm.runInContext(titleCode + '\nthis.setTp = v => { _tp = v; }; this.getTp = () => _tp;', context);
  return {context, nodes, calls};
}

test('only the two download engines are downloadable; anything else is metadata only', () => {
  const app = titleSetup(() => ({}));
  const only = (src, seed) => vm.runInContext(`_tpIsMetadataOnly(${JSON.stringify(src)}, ${JSON.stringify(seed)})`, app.context);
  assert.equal(only('streamingcommunity', null), false);
  assert.equal(only('animeunity', {downloadable: true}), false);
  assert.equal(only('raiplay', null), true);
  assert.equal(only('raiplay', {downloadable: true}), true);   // never re-enabled
  assert.equal(only('pilot', null), true);
  assert.equal(only('streamingcommunity', {downloadable: false}), true);
});

test('a RaiPlay card loads from /api/titles only and offers the download when not DRM', async () => {
  // Policy since a1f10b2 (2026-11-15): RaiPlay provider has download=True and
  // the parser sets downloadable=not drm, so a non-DRM title is downloadable.
  const app = titleSetup(() => ({name: 'Serie <b>Esempio</b>', description: 'Trama', genres: ['Drama'],
    poster: 'https://www.raiplay.it/dl/img/p.jpg', page_url: 'https://www.raiplay.it/programmi/serieesempio',
    downloadable: true, download_disabled_reason: null}));
  app.context.setTp({id: 'programmi:serieesempio', source: 'raiplay', type: 'tv', name: '', genres: [],
    metadataOnly: true, downloadDisabledReason: ''});
  await vm.runInContext('_tpLoadMetadataOnly(1)', app.context);
  assert.deepEqual(app.calls, ['/api/titles/raiplay/programmi%3Aserieesempio']);
  const tp = app.context.getTp();
  assert.equal(tp.metadataOnly, true);
  assert.equal(tp.plot, 'Trama');
  vm.runInContext('_tpRenderCta()', app.context);
  const html = app.nodes['th-cta'].innerHTML;
  assert.match(html, /data-action="tp:primary"/);
  assert.doesNotMatch(html, /<button[^>]*\sdisabled/);
  assert.match(html, /href="https:\/\/www\.raiplay\.it\/programmi\/serieesempio"/);
});

test('a RaiPlay title with DRM stays read-only: disabled button and the reason', async () => {
  const app = titleSetup(() => ({name: 'Film', description: 'Trama', genres: [], poster: '',
    page_url: 'https://www.raiplay.it/programmi/filmdrm',
    downloadable: false, download_disabled_reason: 'Contenuto protetto da DRM'}));
  app.context.setTp({id: 'programmi:filmdrm', source: 'raiplay', type: 'movie', name: '', genres: [],
    metadataOnly: true, downloadDisabledReason: ''});
  await vm.runInContext('_tpLoadMetadataOnly(1)', app.context);
  vm.runInContext('_tpRenderCta()', app.context);
  const html = app.nodes['th-cta'].innerHTML;
  assert.match(html, /<button[^>]*\sdisabled/);
  assert.match(html, /Contenuto protetto da DRM/);
});

test('the disabled reason is escaped and a foreign page link is not shown', () => {
  const app = titleSetup(() => ({}));
  app.context.setTp({source: 'raiplay', metadataOnly: true, pageUrl: 'javascript:alert(1)',
    downloadDisabledReason: '<script>x</script>'});
  vm.runInContext('_tpRenderCta()', app.context);
  const html = app.nodes['th-cta'].innerHTML;
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /javascript:/);
});

// ── Search page: RaiPlay is a selectable source ──────────────────────────────

test('RaiPlay is in the source selector and sends no type filter', () => {
  const html = fs.readFileSync('app/templates/index.html', 'utf8');
  assert.match(html, /id="src-rai"[^>]*data-source="raiplay"/);
  const search = fs.readFileSync('app/static/page-search.js', 'utf8');
  assert.match(search, /raiplay: \[\['', 'catalog\.all'\]\]/);
  assert.match(search, /media_type: \(currentSource !== 'raiplay' && _kindFilter\) \|\| undefined/);
  assert.match(search, /\['all', 'streamingcommunity', 'animeunity', 'raiplay'\]\.includes\(params\.src\)/);
});
