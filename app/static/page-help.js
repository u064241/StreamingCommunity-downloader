/* Help page: content is translated at render time so language switching does
   not require a reload. The small visual blocks are intentionally CSS-native,
   keeping the guide useful without shipping screenshots that can go stale. */

const HELP_SECTIONS = [
  ['search', 'ti-search', 'help.search'],
  ['download', 'ti-download', 'help.download'],
  ['queue', 'ti-list-check', 'help.queue'],
  ['settings', 'ti-settings', 'help.settings'],
  ['sources', 'ti-world-search', 'help.sources'],
  ['seasons', 'ti-list-numbers', 'help.seasons'],
  ['protection', 'ti-shield-check', 'help.protection'],
  ['library', 'ti-folder', 'help.library'],
  ['notifications', 'ti-bell', 'help.notifications'],
  ['troubleshooting', 'ti-tool', 'help.troubleshooting'],
];

function helpMock(kind, icon) {
  if (kind === 'search') return `<div class="help-mock help-search-demo"><span class="help-pill">StreamingCommunity</span><span class="help-input">Film, serie TV...</span><span class="help-button">${escapeHtml(t('search.button'))}</span><div class="help-demo-cards"><span></span><span></span><span></span></div></div>`;
  if (kind === 'download') return `<div class="help-mock help-download-demo"><span class="help-thumb"></span><span class="help-lines"></span><span class="help-action"><i class="ti ti-download"></i></span><span class="help-action help-action-muted"><i class="ti ti-check"></i></span></div>`;
  if (kind === 'queue') return `<div class="help-mock help-queue"><b>S01E01</b><span>${escapeHtml(t('downloads.running'))}</span><span class="help-progress"><i></i></span><i class="ti ti-circle-check help-queue-ok"></i></div>`;
  if (kind === 'settings') return `<div class="help-mock"><i class="ti ${icon}"></i><span class="help-setting">${escapeHtml(t('nav.settings'))}</span><span class="help-select">FFmpeg · Profilo sorgente</span></div>`;
  if (kind === 'seasons') return `<div class="help-mock help-season-demo"><span class="help-select">${escapeHtml(t('title.season', {season: 1}))}</span><i class="ti ti-chevron-down"></i><span class="help-episode">S01E01</span><span class="help-episode">S01E02</span><i class="ti ti-chevron-right help-download"></i></div>`;
  return `<div class="help-mock"><i class="ti ${icon} help-download"></i><span class="help-lines"></span></div>`;
}

function helpPageEnter() {
  const target = document.getElementById('help-content');
  if (!target) return;
  target.innerHTML = `<div class="pg-head"><div class="pg-head-main"><h1 class="pg-h1" data-i18n="help.title">${escapeHtml(t('help.title'))}</h1><p class="pg-sub" data-i18n="help.subtitle">${escapeHtml(t('help.subtitle'))}</p></div></div>
    <div class="row g-3">${HELP_SECTIONS.map(([kind, icon, key]) => `<div class="col-12 col-md-6 col-xl-4"><article class="help-card p-3"><h3><i class="ti ${icon}"></i>${escapeHtml(t(`${key}.title`))}</h3><p>${escapeHtml(t(`${key}.text`))}</p>${helpMock(kind, icon)}</article></div>`).join('')}</div>
    <div class="help-source-note mt-4"><i class="ti ti-info-circle"></i><div><strong>${escapeHtml(t('help.note.title'))}</strong><span>${escapeHtml(t('help.note'))}</span></div></div>`;
}

window.addEventListener('sc:language-changed', () => {
  if (document.getElementById('page-help')?.style.display !== 'none') helpPageEnter();
});
