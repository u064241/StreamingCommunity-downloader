/* StreamingCommunity Web Panel — page-settings.js */
//
// The settings page: source, libraries, naming, performance, access,
// notification channels and post-download hooks. Each section saves itself
// and reports into its own feedback line.

// ── Settings ───────────────────────────────────────────────────────────────────

// Every section in the settings modal saves itself, so each one reports into its
// own feedback line rather than sharing one status area.
const _SETTINGS_FEEDBACK_IDS = [
  'domain-feedback', 'libraries-feedback', 'perf-settings-feedback',
  'jf-connect-feedback', 'jf-reconnect-feedback', 'notif-channels-feedback',
  'domain-recovery-feedback', 'hosts-feedback',
  'jf-refresh-feedback', 'hooks-feedback', 'naming-feedback',
  'output-feedback', 'mcp-feedback', 'video-settings-feedback', 'logs-feedback', 'download-directory-feedback',
];

function _settingsError(error, fallback = t('common.error')) {
  return scLanguage.message(errText(error, fallback));
}

// Remember translated output rather than reloading settings: a language change
// must preserve unsaved values, generated tokens and pending operations.
const _settingsTextBindings = new Map();
function _settingsText(node, text) {
  if (!node) return;
  node.textContent = text;
  _settingsTextBindings.set(node, scLanguage.describe(text));
}

function _feedback(id, message = '', kind = 'muted') {
  const el = document.getElementById(id);
  if (!el) return;
  _settingsText(el, message);
  el.className = 'form-text' + (message ? ` text-${kind}` : '');
}

let _softetherBusy = false;
let _softetherBlockDetected = false;
let _softetherIpRefreshTimer = null;
// Local-only test path: localhost can exercise the installer without first
// provoking a real source block. Remote panels remain block-gated unless the
// explicit ?sc-vpn-test=1 query flag is used.
const _softetherTestMode = ['localhost', '127.0.0.1', '::1'].includes(location.hostname)
  || new URLSearchParams(location.search).get('sc-vpn-test') === '1';

function _softetherError(error) {
  const key = error instanceof ApiError ? error.data?.detail : null;
  if (typeof key === 'string' && key.startsWith('softether.')) return t(key);
  if (error?.status === 403) return t('softether.denied');
  if (error?.status === 401) return t('softether.authentication');
  // Keep the translated fallback for network failures, but expose a useful
  // local JavaScript error while diagnosing rendering/API integration issues.
  // Without this, a successful 200 response followed by a render exception
  // is incorrectly reported as a panel connectivity problem.
  const detail = error instanceof ApiError ? error.message : error?.message;
  return detail ? `${t('softether.requestFailed')} (${detail})` : t('softether.requestFailed');
}

async function loadSoftEtherStatus(openManager = false) {
  if (_softetherBusy) return;
  _softetherBusy = true;
  const open = document.getElementById('softether-open');
  const close = document.getElementById('softether-close');
  const refresh = document.getElementById('softether-refresh');
  const install = document.getElementById('softether-install');
  const updateCheck = document.getElementById('softether-update-check');
  open.disabled = true;
  refresh.disabled = true;
  _feedback('softether-status', t('softether.checking'));
  let available = false;
  let running = false;
  try {
    const status = await softetherApi.status();
    available = status.supported && status.installed;
    running = Boolean(status.running);
    if (close) close.disabled = !running;
    if (install) install.hidden = available || !status.vpngate_available || (!_softetherBlockDetected && !_softetherTestMode);
    if (updateCheck) updateCheck.disabled = !available;
    const bundledHint = document.getElementById('softether-bundled-hint');
    if (bundledHint) bundledHint.hidden = available || !status.vpngate_available || (!_softetherBlockDetected && !_softetherTestMode);
    const key = !status.supported ? 'softether.unsupported'
      : status.installed ? 'softether.ready' : 'softether.missing';
    _feedback('softether-status', t(key), available ? 'success' : 'warning');
    if (openManager && available) {
      await softetherApi.openManager(status.token);
      _feedback('softether-status', t('softether.opened'), 'success');
    }
  } catch (error) {
    _feedback('softether-status', _softetherError(error), 'danger');
  } finally {
    open.disabled = !available || running;
    if (close && !available) close.disabled = true;
    refresh.disabled = false;
    _softetherBusy = false;
  }
}

async function installSoftEther() {
  const button = document.getElementById('softether-install');
  const progress = document.getElementById('softether-install-progress');
  const bar = document.getElementById('softether-install-progress-bar');
  const label = document.getElementById('softether-install-progress-label');
  if (window._softEtherInstallJobId) {
    try { await softetherApi.cancelInstall(window._softEtherInstallJobId); }
    catch (error) { _feedback('softether-status', _softetherError(error), 'danger'); }
    return;
  }
  if (button) { button.disabled = false; button.textContent = 'Interrompi installazione'; }
  if (progress) progress.hidden = false;
  if (bar) { bar.value = 0; bar.removeAttribute('aria-valuenow'); }
  if (label) _settingsText(label, t('softether.installQueued'));
  _feedback('softether-status', t('softether.startingInstall'), 'warning');
  try {
    const status = await softetherApi.status();
    const started = await softetherApi.install(status.token);
    window._softEtherInstallJobId = started.job_id;
    let current;
    do {
      current = await softetherApi.installStatus(started.job_id);
      const percent = Number.isFinite(current.percent) ? current.percent : null;
      if (bar && percent !== null) { bar.value = percent; bar.setAttribute('aria-valuenow', String(percent)); }
      if (label) {
        const key = current.state === 'downloading' ? 'softether.installProgress'
          : current.state === 'extracting' ? 'softether.extracting'
          : current.state === 'starting' ? 'softether.startingInstaller'
          : current.state === 'installing' ? 'softether.startingInstaller'
          : current.state === 'completed' ? 'softether.installComplete' : 'softether.installQueued';
        _settingsText(label, key === 'softether.installProgress'
          ? t(key, { percent: percent === null ? '…' : Math.round(percent) }) : t(key));
      }
      if (current.state === 'error') throw new Error(current.error || 'softether.installFailed');
      if (current.state === 'cancelled') throw new Error('Installazione interrotta');
      if (!['completed', 'error', 'cancelled'].includes(current.state)) await new Promise(resolve => setTimeout(resolve, 700));
    } while (current.state !== 'completed');
    _feedback('softether-status', t('softether.installStarted'), 'success');
    // The external installer needs user interaction. Once it exits, refresh
    // the local detection so the UI reflects the newly installed Client Manager.
    await new Promise(resolve => setTimeout(resolve, 1200));
  } catch (error) {
    _feedback('softether-status', _softetherError(error), 'danger');
  } finally {
    window._softEtherInstallJobId = null;
    if (button) { button.disabled = false; button.textContent = t('softether.install'); }
    if (progress) progress.hidden = true;
    await loadSoftEtherStatus();
  }
}

async function closeSoftEtherManager() {
  const close = document.getElementById('softether-close');
  if (close) close.disabled = true;
  try {
    const status = await softetherApi.status();
    await softetherApi.closeManager(status.token);
    _feedback('softether-status', 'VPN chiusa.', 'success');
    await loadSoftEtherStatus();
  } catch (error) {
    _feedback('softether-status', _softetherError(error), 'danger');
    await loadSoftEtherStatus();
  }
}

window.addEventListener('softether:block-detected', async () => {
  _softetherBlockDetected = true;
  const page = document.getElementById('page-settings');
  if (!page || page.style.display === 'none') return;
  await loadSoftEtherStatus();
  // A blocked source is an actionable install flow. Keep the bundled action
  // visible even when the initial status check raced with page/tab loading.
  const install = document.getElementById('softether-install');
  const bundledHint = document.getElementById('softether-bundled-hint');
  if (install && !document.getElementById('softether-open')?.disabled) {
    install.hidden = true;
  } else if (install) {
    install.hidden = false;
    if (bundledHint) bundledHint.hidden = false;
  }
  _feedback('softether-status', t('softether.blockDetectedSettings'), 'warning');
});

async function openSoftEtherManager() {
  await loadSoftEtherStatus(true);
}

async function checkSoftEtherUpdates() {
  const button = document.getElementById('softether-update-check');
  if (button) button.disabled = true;
  _feedback('softether-updates', t('softether.checkingUpdates'));
  try {
    const result = await softetherApi.updates();
    const key = result.status === 'update_available' ? 'softether.updateAvailable'
      : result.status === 'up_to_date' ? 'softether.upToDate' : 'softether.updateUnavailable';
    _feedback('softether-updates', result.status === 'update_available'
      ? t(key, { version: result.version }) : t(key), result.status === 'available' ? 'warning' : 'muted');
    const updateButton = document.getElementById('softether-update-install');
    if (updateButton) updateButton.hidden = result.status !== 'available';
    const remoteHint = document.getElementById('softether-remote-hint');
    if (remoteHint) remoteHint.hidden = result.status !== 'available';
  } catch (error) {
    _feedback('softether-updates', _softetherError(error), 'danger');
  } finally {
    if (button) button.disabled = false;
  }
}

async function downloadSoftEtherUpdate() {
  const button = document.getElementById('softether-update-install');
  if (button) button.disabled = true;
  _feedback('softether-updates', t('softether.startingDownload'), 'warning');
  try {
    const status = await softetherApi.status();
    const result = await softetherApi.downloadUpdate(status.token);
    _feedback('softether-updates', result.source === 'bundled'
      ? t('softether.installStarted') : t('softether.downloadStarted'), 'success');
  } catch (error) {
    _feedback('softether-updates', _softetherError(error), 'danger');
  } finally { if (button) button.disabled = false; }
}

function formatVpnGateUptime(hours) {
  const totalDays = Math.max(0, Math.round(Number(hours || 0) / 24));
  if (totalDays < 1) return `${Math.max(0, Math.round(Number(hours || 0)))} h`;
  const years = Math.floor(totalDays / 365);
  const days = totalDays % 365;
  if (years && days) return `${years} a ${days} g`;
  if (years) return `${years} a`;
  return `${totalDays} g`;
}

function formatVpnGateNumber(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return '0';
  try {
    return new Intl.NumberFormat(document.documentElement.lang || 'it-IT', {
      notation: 'compact', maximumFractionDigits: 2
    }).format(number);
  } catch (_) {
    return number.toLocaleString('it-IT');
  }
}

async function loadVpnGateServers() {
  const target = document.getElementById('softether-vpngate-list');
  const hide = document.getElementById('softether-vpngate-hide');
  const feedback = document.getElementById('softether-vpngate-feedback');
  if (!target) return;
  _feedback('softether-vpngate-feedback');
  _settingsText(target, t('softether.vpngateChecking'));
  try {
    const result = await softetherApi.vpngate();
    const servers = result.servers || [];
    let ipInfo = null;
    try { ipInfo = await softetherApi.publicIp(); } catch (_) { /* IP service unavailable */ }
    let profilesRaw = '';
    let localProfiles = [];
    let profileDetails = {};
    try {
      const profileData = await softetherApi.vpngateProfiles();
      profilesRaw = profileData.raw || '';
      localProfiles = profileData.profiles || [];
      profileDetails = profileData.details || {};
    } catch (_) { /* service may be stopped */ }
    if (!servers.length && !localProfiles.length) { _settingsText(target, t('softether.vpngateEmpty')); return; }
    target.hidden = false;
    _feedback('softether-vpngate-feedback');
    const ipBanner = ipInfo ? `<div id="softether-vpngate-ip" class="softether-vpngate-ip mb-2"><strong>IP pubblico</strong>: ${escapeHtml(ipInfo.current)}${ipInfo.changed ? ` · originale: ${escapeHtml(ipInfo.original)} · uscita VPN rilevata` : ' · nessuna variazione rilevata'}</div>` : '';
    const listedNames = new Set(servers.map(s => `VPN Gate ${s.countryCode || s.host}`));
    const orphanProfiles = localProfiles.filter(name => !listedNames.has(name));
    const profileActions = name => `<button type="button" class="btn btn-sm btn-primary" data-vpngate-connect="${escapeHtml(name)}">Connetti</button> <button type="button" class="btn btn-sm btn-secondary" data-vpngate-disconnect="${escapeHtml(name)}">Disconnetti</button> <button type="button" class="btn btn-sm btn-danger" data-vpngate-delete="${escapeHtml(name)}">Elimina</button>`;
    const orphanHtml = orphanProfiles.map(name => { const detail = profileDetails[name] || {}; const live = detail.country ? `${escapeHtml(detail.country)} · ` : ''; const metrics = detail.ping != null ? `${detail.ping} ms · ${(Number(detail.speed || 0) / 1000000).toFixed(1)} Mbps · ${formatVpnGateNumber(detail.sessions)} sessioni` : `Profilo locale non presente nell’elenco VPN Gate attuale${detail.status ? ' · ' + escapeHtml(detail.status) : ''}`; return `<div class="softether-vpngate-row"><span><strong>${live}${escapeHtml(detail.host || 'server locale')}${detail.port ? ':' + detail.port : ''}</strong><small class="d-block text-muted">${detail.name ? escapeHtml(detail.name) + ' · ' : ''}${metrics}</small></span><span>${profileActions(name)}</span></div>`; }).join('');
    target.innerHTML = ipBanner + `<div class="softether-vpngate-list">${orphanHtml}${servers.slice(0, 15).map((s, i) =>
      `<div class="softether-vpngate-row"><span><strong>${escapeHtml(s.country || s.countryCode)}</strong> · ${escapeHtml(s.host)}:${s.port || 443}<small class="d-block text-muted">${s.name ? escapeHtml(s.name) + ' · ' : ''}Raggiungibile · ${formatVpnGateNumber(s.sessions)} sessioni</small></span><span>${s.ping} ms · ${(s.speed / 1000000).toFixed(1)} Mbps${(_softetherBlockDetected || _softetherTestMode) ? ` ${profilesRaw.includes(`VPN Gate ${s.countryCode || s.host}`) ? profileActions(`VPN Gate ${s.countryCode || s.host}`) : `<button type="button" class="btn btn-sm btn-outline-secondary" data-vpngate-index="${i}">${escapeHtml(t('softether.vpngateCreate'))}</button>`}` : ''}</span></div>`
    ).join('')}</div><small>${escapeHtml(t('softether.vpngateNotice'))}</small>`;
    if (hide) hide.hidden = false;
    if (!window._softEtherRefreshTimer) {
      window._softEtherRefreshTimer = setInterval(() => {
        if (!target.hidden) loadVpnGateServers();
      }, 5 * 60 * 1000);
    }
    if (!_softetherIpRefreshTimer) {
      _softetherIpRefreshTimer = setInterval(refreshVpnGateIp, 15000);
    }
    target.querySelectorAll('[data-vpngate-index]').forEach(button => button.addEventListener('click', async () => {
      const server = servers[Number(button.dataset.vpngateIndex)];
      button.disabled = true;
      try {
        const status = await softetherApi.status();
        await softetherApi.createVpnGateProfile({host: server.host, port: server.port || 443,
          name: `VPN Gate ${server.countryCode || server.host}`}, status.token);
        _feedback('softether-vpngate-feedback');
        _feedback('softether-vpngate-feedback', t('softether.vpngateCreated'), 'success');
        await loadVpnGateServers();
      } catch (error) { _feedback('softether-vpngate-feedback', _softetherError(error), 'danger'); }
      finally { button.disabled = false; }
    }));
    target.querySelectorAll('[data-vpngate-delete]').forEach(button => button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const status = await softetherApi.status();
        await softetherApi.deleteVpnGateProfile({name: button.dataset.vpngateDelete}, status.token);
        _feedback('softether-vpngate-feedback', 'Profilo eliminato', 'success'); await loadVpnGateServers();
      } catch (error) { _feedback('softether-vpngate-feedback', _softetherError(error), 'danger'); }
      finally { button.disabled = false; }
    }));
    target.querySelectorAll('[data-vpngate-connect], [data-vpngate-disconnect]').forEach(button => button.addEventListener('click', async () => {
      const connect = button.dataset.vpngateConnect !== undefined;
      const name = connect ? button.dataset.vpngateConnect : button.dataset.vpngateDisconnect;
      button.disabled = true;
      _feedback('softether-vpngate-feedback', connect ? 'Connessione in corso…' : 'Disconnessione in corso…', 'warning');
      try {
        const status = await softetherApi.status();
        const result = connect
          ? await softetherApi.connectVpnGateProfile({name}, status.token)
          : await softetherApi.disconnectVpnGateProfile({name}, status.token);
        _feedback('softether-vpngate-feedback', connect && result.connected ? 'Connesso. Aggiornamento IP in corso…' : connect ? 'Connessione non confermata.' : 'Disconnesso.', connect && !result.connected ? 'danger' : 'success');
        await refreshVpnGateIp();
      } catch (error) { _feedback('softether-vpngate-feedback', _softetherError(error), 'danger'); }
      finally { button.disabled = false; }
    }));
  } catch (error) { _feedback('softether-vpngate-feedback', _softetherError(error), 'danger'); }
}

async function refreshVpnGateIp() {
  const target = document.getElementById('softether-vpngate-list');
  const banner = document.getElementById('softether-vpngate-ip');
  if (!target || target.hidden || !banner) return;
  try {
    const ipInfo = await softetherApi.publicIp();
    banner.innerHTML = `<strong>IP pubblico</strong>: ${escapeHtml(ipInfo.current)}${ipInfo.changed ? ` · originale: ${escapeHtml(ipInfo.original)} · uscita VPN rilevata` : ' · nessuna variazione rilevata'}`;
  } catch (_) {
    // The server list remains usable if the external public-IP service is
    // temporarily unavailable.
  }
}

function hideVpnGateServers() {
  const target = document.getElementById('softether-vpngate-list');
  const hide = document.getElementById('softether-vpngate-hide');
  if (target) target.hidden = true;
  if (hide) hide.hidden = true;
  if (_softetherIpRefreshTimer) {
    clearInterval(_softetherIpRefreshTimer);
    _softetherIpRefreshTimer = null;
  }
}

// Each tab fetches its own data the first time it is opened, so opening the
// modal no longer waits on the slowest section (disk usage stats every library
// path, on an NFS mount that can be asleep).
const _SETTINGS_TAB_LOADERS = {
  sorgente: () => Promise.all([loadDomainRecoverySettings(), loadHosts(), loadSoftEtherStatus()]),
  // Loaded here rather than at boot. It was the one settings pane fed by a
  // prefetch the whole application waited on before it could show any page
  // at all, for data only this tab reads.
  librerie: () => Promise.all([loadLibraries().then(renderLibrariesList), loadDownloadDirectory()]),
  log: () => loadApplicationLogs(),
  generali: () => loadGeneralSettings(),
  associazioni: () => loadLibraryAssociations(),
  nomi: () => loadNamingTemplates(),
  // Both read the same endpoint, and _loadAppSettings() shares the promise, so
  // this is still one fetch.
  download: () => Promise.all([loadPerfSettings(), loadOutputSettings(), loadVideoSettings()]),
  accesso: () => loadJellyfinSettings(),
  notifiche: () => loadNotificationChannels(),
  hook: () => Promise.all([loadJellyfinRefresh(), loadHooks()]),
  mcp: () => loadMcpSettings(),
};

// Two panes read the same endpoint. Shared per modal-open so switching between
// them does not fetch it twice; cleared alongside _settingsLoaded.
let _appSettingsPromise = null;

function _loadAppSettings() {
  if (!_appSettingsPromise) {
    _appSettingsPromise = api.get('/api/domain/settings').then(data => {
      window._confirmationsEnabled = data.confirmations_enabled !== false;
      return data;
    }).catch(() => null);
  }
  return _appSettingsPromise;
}

async function loadGeneralSettings() {
  const data = await _loadAppSettings();
  const input = document.getElementById('setting-confirmations-enabled');
  if (input) input.checked = data?.confirmations_enabled !== false;
}

async function saveGeneralSettings() {
  const input = document.getElementById('setting-confirmations-enabled');
  try {
    await api.put('/api/domain/settings', { confirmations_enabled: !!input?.checked });
    window._confirmationsEnabled = !!input?.checked;
    _feedback('general-settings-feedback', 'Impostazioni salvate', 'success');
  } catch (e) { _feedback('general-settings-feedback', errText(e), 'danger'); }
}

async function runDiagnostics() {
  const target = document.getElementById('diagnostics-results');
  if (!target) return;
  target.textContent = 'Raccolta diagnostica in corso…';
  try {
    const data = await api.get('/api/domain/diagnostics');
    target.textContent = JSON.stringify(data, null, 2);
  } catch (e) { target.textContent = errText(e); }
}

async function exportSettingsBackup() {
  try {
    const response = await api.request('GET', '/api/domain/settings/backup', { raw: true });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = 'streamingcommunity-settings.json';
    document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    _feedback('backup-settings-feedback', 'Backup esportato.', 'success');
  } catch (e) { _feedback('backup-settings-feedback', errText(e), 'danger'); }
}

function chooseSettingsRestore() {
  document.getElementById('settings-restore-file')?.click();
}

async function restoreSettingsBackup(file) {
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    if (!payload || payload.schema !== 1 || !payload.data) throw new Error('Backup non valido.');
    if (window._confirmationsEnabled !== false && !await scConfirm('Sostituire le impostazioni applicative con quelle del backup?')) return;
    await api.post('/api/domain/settings/restore', payload);
    _feedback('backup-settings-feedback', 'Impostazioni ripristinate. Ricarica la pagina per applicarle ovunque.', 'success');
    _settingsLoaded.clear(); _appSettingsPromise = null;
  } catch (e) { _feedback('backup-settings-feedback', errText(e), 'danger'); }
}

// Tabs whose panes only talk to MANAGE_SETTINGS endpoints: without it they would
// render as empty panes fed by 403s.
const _SETTINGS_TABS_NEED_MANAGE = ['sorgente', 'librerie', 'associazioni', 'nomi', 'download', 'notifiche', 'hook', 'mcp', 'log', 'generali'];

let _settingsTab = 'sorgente';
const _settingsLoaded = new Set();

function setupSettingsTabs() {
  const tabs = document.getElementById('settings-tabs');
  if (!tabs) return;
  document.getElementById('settings-restore-file')?.addEventListener('change', e => {
    restoreSettingsBackup(e.target.files?.[0]);
    e.target.value = '';
  });
  tabs.addEventListener('click', (e) => {
    const link = e.target.closest('[data-settings-tab]');
    if (!link) return;
    e.preventDefault();
    switchSettingsTab(link.dataset.settingsTab);
  });
}

function _visibleSettingsTabs() {
  return [...document.querySelectorAll('#settings-tabs [data-settings-tab]')]
    .filter(a => a.closest('.nav-item').style.display !== 'none')
    .map(a => a.dataset.settingsTab);
}

async function switchSettingsTab(name) {
  document.querySelectorAll('#settings-tabs [data-settings-tab]').forEach(a =>
    a.classList.toggle('active', a.dataset.settingsTab === name));
  document.querySelectorAll('[data-settings-pane]').forEach(pane => {
    pane.style.display = pane.dataset.settingsPane === name ? '' : 'none';
  });
  _settingsTab = name;
  // The tab belongs in the address. syncHash uses replaceState, which fires
  // nothing — assigning location.hash would route straight back into here.
  if (document.getElementById('page-settings')?.style.display !== 'none') syncHash('settings');
  window.scrollTo({ top: 0 });

  // Marked before awaiting, so a double click cannot fire two fetches.
  if (!_settingsLoaded.has(name)) {
    _settingsLoaded.add(name);
    await _SETTINGS_TAB_LOADERS[name]?.();
  }
}

// The sidebar's entry point. Settings is a page with an address now, so this
// navigates; the router calls openSettingsPage() back.
function openSettings() {
  navigate('settings');
}

// Reached through the router, either from openSettings() or from a pasted
// link naming a tab.
async function openSettingsPage(tab) {
  document.getElementById('domain-input').value = currentDomain;
  _SETTINGS_FEEDBACK_IDS.forEach(id => _feedback(id));
  renderLibrariesList();

  const manage = can('MANAGE_SETTINGS');
  document.querySelectorAll('#settings-tabs [data-settings-tab]').forEach(a => {
    const restricted = _SETTINGS_TABS_NEED_MANAGE.includes(a.dataset.settingsTab);
    a.closest('.nav-item').style.display = restricted && !manage ? 'none' : '';
  });

  // Cleared on every arrival so a value changed elsewhere is picked up; moving
  // between tabs while here does not refetch.
  _settingsLoaded.clear();
  _appSettingsPromise = null;
  showPage('settings');

  const tabs = _visibleSettingsTabs();
  // A tab named in the URL wins, unless the visitor cannot see it - linking
  // someone to a tab their permissions hide must not leave them on a blank
  // pane.
  const wanted = tab && tabs.includes(tab) ? tab
    : (tabs.includes('sorgente') ? 'sorgente' : tabs[0]);
  if (wanted) await switchSettingsTab(wanted);
}

// ── Canali di notifica (Apprise) ─────────────────────────────────────────────

let _notifChannels = [];

async function loadNotificationChannels() {
  try {
    const data = await api.get('/api/notification-channels');
    _notifChannels = data.channels || [];
    renderNotificationChannelsList();
  } catch (e) { console.error('loadNotificationChannels:', e); }
}

// The URL carries the bot token, so the list shows only enough of it to tell two
// channels apart. The full value stays behind the MANAGE_SETTINGS endpoint.
function _maskAppriseUrl(url) {
  const scheme = url.split('://')[0];
  return `${scheme}://…${url.slice(-4)}`;
}

// Which channels have their event picker open. Kept outside the render so
// rebuilding the list does not collapse what the user was editing.
const _expandedChannels = new Set();

// An empty list means "every event" on the server, so the picker needs a master
// switch: without it, unchecking the last box would silently mean the opposite
// of what it looks like.
function _eventSummary(ch) {
  if (!ch.events.length) return t('settings.all.events');
  return ch.events.length === 1 ? t('settings.1.event') : t('settings.events', {count: ch.events.length});
}

function _renderEventPicker(ch) {
  const all = ch.events.length === 0;
  const groups = NOTIFICATION_EVENT_GROUPS.map(group => {
    const boxes = group.events.map(event => `
      <label class="form-check form-check-inline" style="min-width:200px">
        <input class="form-check-input" type="checkbox" value="${event}"
               data-channel="${ch.id}"
               ${all || ch.events.includes(event) ? 'checked' : ''}
               ${all ? 'disabled' : ''}
               data-change="cfg:channelEvents" data-id="${ch.id}">
        <span class="form-check-label" style="font-size:12px">
          <i class="ti ${NOTIFICATION_ICONS[event] || 'ti-bell'} me-1"></i>${escapeHtml(scLanguage.message(NOTIFICATION_LABELS[event]))}
        </span>
      </label>`).join('');
    return `
      <div class="mb-2">
        <p class="settings-section-label mb-1">${escapeHtml(scLanguage.message(group.label))}</p>
        ${boxes}
      </div>`;
  }).join('');

  return `
    <div class="ps-4 pb-2" id="notif-events-${ch.id}">
      <label class="form-check form-switch mb-2">
        <input class="form-check-input" type="checkbox" ${all ? 'checked' : ''}
               id="notif-all-events-${ch.id}"
               data-change="cfg:channelAllEvents" data-id="${ch.id}">
        <span class="form-check-label" style="font-size:12px">${escapeHtml(t('settings.all.events'))}</span>
      </label>
      ${groups}
    </div>`;
}

function renderNotificationChannelsList() {
  const c = document.getElementById('notif-channels-list');
  if (!c) return;
  if (!_notifChannels.length) {
    c.innerHTML = `<p class="text-muted small mb-0">${escapeHtml(t('settings.no.channels.configured'))}</p>`;
    return;
  }
  c.innerHTML = _notifChannels.map(ch => {
    const open = _expandedChannels.has(ch.id);
    return `
    <div class="border-bottom pb-1 mb-1">
      <div class="d-flex align-items-center gap-2 py-1">
        <label class="form-check form-switch mb-0">
          <input class="form-check-input" type="checkbox" ${ch.enabled ? 'checked' : ''}
                 data-change="cfg:channelEnabled" data-id="${ch.id}">
        </label>
        <div class="flex-fill text-truncate">
          <span style="color:var(--text)">${escapeHtml(ch.name)}</span>
          <span class="text-muted small ms-2">${escapeHtml(_maskAppriseUrl(ch.apprise_url))}</span>
        </div>
        <button type="button" class="btn btn-sm btn-ghost-secondary"
                data-action="cfg:channelPicker" data-id="${ch.id}" title="${escapeHtml(t('settings.choose.which.notifications.to.receive'))}">
          <i class="ti ti-${open ? 'chevron-up' : 'chevron-down'} me-1"></i>${_eventSummary(ch)}
        </button>
        <button type="button" class="btn btn-sm btn-outline-secondary" data-action="cfg:testChannel" data-id="${ch.id}">
          <i class="ti ti-send me-1"></i>Test
        </button>
        <button type="button" class="btn btn-sm btn-outline-danger" data-action="cfg:deleteChannel" data-id="${ch.id}">
          <i class="ti ti-trash"></i>
        </button>
      </div>
      ${open ? _renderEventPicker(ch) : ''}
    </div>`;
  }).join('');
}

function toggleChannelEvents(id) {
  if (_expandedChannels.has(id)) _expandedChannels.delete(id);
  else _expandedChannels.add(id);
  renderNotificationChannelsList();
}

function _checkedEvents(id) {
  return [...document.querySelectorAll(`#notif-events-${id} input[data-channel="${id}"]`)]
    .filter(box => box.checked).map(box => box.value);
}

async function toggleAllChannelEvents(id, all) {
  // Turning "all" off pre-selects everything, so the user removes what they do
  // not want rather than starting from nothing.
  await _saveChannelEvents(id, all ? [] : ALL_NOTIFICATION_EVENTS.slice());
}

async function updateChannelEvents(id) {
  const chosen = _checkedEvents(id);
  if (!chosen.length) {
    // [] would be stored as "every event" — the opposite of an empty selection.
    _feedback('notif-channels-feedback', t('settings.select.at.least.one.event.or.enable.all'), 'danger');
    renderNotificationChannelsList();
    return;
  }
  await _saveChannelEvents(id, chosen);
}

async function _saveChannelEvents(id, events) {
  try {
    const data = await api.patch(`/api/notification-channels/${id}`, {events});
    // Patched locally instead of refetching: a full reload would rebuild the
    // open picker under the cursor while the user is still clicking.
    const channel = _notifChannels.find(c => c.id === id);
    if (channel) channel.events = data.events || [];
    _feedback('notif-channels-feedback', t('settings.events.updated'), 'success');
    renderNotificationChannelsList();
  } catch (e) {
    _feedback('notif-channels-feedback', _settingsError(e, t('settings.failed.to.update.events')), 'danger');
    // The list is refetched only on failure: local state may now disagree
    // with the server.
    if (e instanceof ApiError) await loadNotificationChannels();
  }
}

function toggleNotificationChannelForm() {
  const form = document.getElementById('notif-channel-form');
  form.style.display = form.style.display === 'none' ? '' : 'none';
}

async function saveNotificationChannel() {
  const btn = document.getElementById('notif-channel-save-btn');
  const nameEl = document.getElementById('notif-channel-name');
  const urlEl = document.getElementById('notif-channel-url');
  const name = nameEl.value.trim();
  const apprise_url = urlEl.value.trim();
  if (!name || !apprise_url) {
    _feedback('notif-channels-feedback', t('settings.enter.a.name.and.url'), 'danger');
    return;
  }
  btn.disabled = true;
  _feedback('notif-channels-feedback', t('settings.saving'));
  try {
    await api.post('/api/notification-channels', {name, apprise_url});
    nameEl.value = '';
    urlEl.value = '';
    document.getElementById('notif-channel-form').style.display = 'none';
    _feedback('notif-channels-feedback', t('settings.channel.added'), 'success');
    await loadNotificationChannels();
  } catch (e) {
    _feedback('notif-channels-feedback', _settingsError(e, t('settings.failed.to.save')), 'danger');
  }
  finally { btn.disabled = false; }
}

async function toggleNotificationChannel(id, enabled) {
  try {
    await api.patch(`/api/notification-channels/${id}`, {enabled});
    _feedback('notif-channels-feedback', enabled ? t('settings.channel.enabled') : t('settings.channel.disabled'), 'success');
    await loadNotificationChannels();
  } catch (e) {
    _feedback('notif-channels-feedback', _settingsError(e, t('settings.update.failed')), 'danger');
    await loadNotificationChannels();
  }
}

async function deleteNotificationChannel(id) {
  const channel = _notifChannels.find(c => c.id === id);
  if (!await scConfirm(t('settings.delete.channel', {name: channel ? channel.name : id}))) return;
  try {
    await api.del(`/api/notification-channels/${id}`);
    _feedback('notif-channels-feedback', t('settings.channel.deleted'), 'success');
    await loadNotificationChannels();
  } catch (e) {
    _feedback('notif-channels-feedback', _settingsError(e, t('settings.deletion.failed')), 'danger');
  }
}

async function testNotificationChannel(id) {
  _feedback('notif-channels-feedback', t('settings.sending.test.notification'));
  try {
    const data = await api.post(`/api/notification-channels/${id}/test`);
    if (data.ok) {
      _feedback('notif-channels-feedback', t('settings.test.notification.sent'), 'success');
      showToast(t('settings.test.notification.sent.189'), 'success');
    } else {
      _feedback('notif-channels-feedback', t('settings.sending.failed.check.the.url'), 'danger');
    }
  } catch (e) {
    _feedback('notif-channels-feedback', _settingsError(e, t('settings.sending.failed.check.the.url')), 'danger');
  }
}

// ── Jellyfin connection ──────────────────────────────────────────────────────

async function loadJellyfinSettings() {
  try {
    const data = await api.get('/api/auth/status');
    const connected = !!data.jellyfin_url;
    document.getElementById('jf-not-connected').style.display = connected ? 'none' : '';
    document.getElementById('jf-connected').style.display = connected ? '' : 'none';
    if (connected) {
      document.getElementById('jf-connected-url').textContent = data.jellyfin_url;
      document.getElementById('jf-reconfigure-wrap').style.display = can('MANAGE_USERS') ? '' : 'none';
    }
  } catch (e) { console.error('loadJellyfinSettings:', e); }
}

function toggleJellyfinReconfigure() {
  const form = document.getElementById('jf-reconfigure-form');
  form.style.display = form.style.display === 'none' ? '' : 'none';
}

async function connectJellyfin(reconfigure) {
  const prefix = reconfigure ? 'jf-reconf-' : 'jf-';
  const btn = document.getElementById(reconfigure ? 'jf-reconnect-btn' : 'jf-connect-btn');
  const fbId = reconfigure ? 'jf-reconnect-feedback' : 'jf-connect-feedback';
  const url = document.getElementById(prefix + 'url').value.trim();
  const username = document.getElementById(prefix + 'username').value.trim();
  const password = document.getElementById(prefix + 'password').value;
  if (!url || !username) {
    _feedback(fbId, t('settings.enter.the.url.and.administrator.username'), 'danger');
    return;
  }

  btn.disabled = true;
  _feedback(fbId, t('login.connecting'));
  try {
    try {
      await api.post('/api/auth/jellyfin-connect', { url, username, password });
    } catch (e) {
      _feedback(fbId, _settingsError(e, t('settings.connection.failed')), 'danger');
      btn.disabled = false;
      return;
    }
    // A full reload re-runs initAuth() against the now-real permission set,
    // which is simpler than patching _me and the nav in place.
    _feedback(fbId, t('settings.connected.reloading'), 'success');
    window.location.reload();
  } catch (e) {
    _feedback(fbId, t('settings.network.error'), 'danger');
    btn.disabled = false;
  }
}

async function loadPerfSettings() {
  const data = await _loadAppSettings();
  if (!data) return;
  document.getElementById('setting-max-concurrent').value = data.max_concurrent_downloads ?? 3;
  document.getElementById('setting-max-workers').value = data.max_segment_workers ?? 16;
  document.getElementById('setting-watch-interval').value =
    data.series_watch_interval_minutes ?? 240;
}

// ── Output format ─────────────────────────────────────────────
//
// The defaults are spelled in the markup's <option> values rather than fetched:
// there are two of each and the server rejects anything else outright, so a
// round trip would buy nothing. Unlike the naming templates, where the default
// is a string the server owns and the placeholder has to be synced from it.

async function loadOutputSettings() {
  const data = await _loadAppSettings();
  if (!data) return;
  document.getElementById('setting-output-container').value = data.output_container || 'mkv';
  document.getElementById('setting-subtitle-mode').value = data.subtitle_mode || 'embed';
}

async function saveOutputSettings() {
  const btn = document.getElementById('save-output-btn');
  const outputContainer = document.getElementById('setting-output-container').value;
  const subtitleMode = document.getElementById('setting-subtitle-mode').value;
  btn.disabled = true;
  _feedback('output-feedback', t('settings.saving'));
  try {
    // Only the two keys this section owns: set_app_settings merges over what is
    // stored, so it cannot clobber a change made seconds ago in another pane.
    await api.put('/api/domain/settings', {
      output_container: outputContainer,
      subtitle_mode: subtitleMode,
    });
    _feedback('output-feedback', t('settings.saved'), 'success');
    showToast(t('settings.output.format.saved'), 'success');
  } catch (e) { _feedback('output-feedback', _settingsError(e, t('settings.failed.to.save')), 'danger'); }
  finally { btn.disabled = false; }
}

async function loadDomainRecoverySettings() {
  const data = await _loadAppSettings();
  if (!data) return;
  document.getElementById('domain-auto-check').checked =
    data.domain_auto_check_enabled !== false;
  document.getElementById('domain-auto-apply').checked = !!data.domain_auto_apply;
  document.getElementById('domain-check-interval').value =
    data.domain_check_interval_minutes ?? 1;
}


// AnimeUnity's host and the video host override. A blank field means the
// default, which the placeholder shows; the server reads "" as "reset".
function _fillHosts(data) {
  const au = document.getElementById('animeunity-host-input');
  au.placeholder = data.animeunity_default;
  au.value = data.animeunity_saved || '';
  document.getElementById('video-host-input').value = data.video_host || '';
}

async function loadHosts() {
  try { _fillHosts(await api.get('/api/domain/hosts')); } catch (e) { /* 403: tab hidden */ }
}

async function saveHosts() {
  const btn = document.getElementById('save-hosts-btn');
  btn.disabled = true;
  _feedback('hosts-feedback', t('settings.verifying'));
  try {
    const data = await api.put('/api/domain/hosts', {
      animeunity: document.getElementById('animeunity-host-input').value.trim(),
      video_host: document.getElementById('video-host-input').value.trim(),
    });
    _fillHosts(data);
    _feedback('hosts-feedback', t('settings.saved'), 'success');
    showToast(t('settings.hosts.saved'), 'success');
  } catch (e) { _feedback('hosts-feedback', _settingsError(e, t('settings.failed.to.save')), 'danger'); }
  finally { btn.disabled = false; }
}

// ── Naming templates ───────────────────────────────────────────────────────────
//
// The preview is rendered by the server, using the same engine the downloader
// uses. Reimplementing it here would give two renderers that drift, and this way
// an invalid template shows its real validation error while it is being typed.

let _namingDefaults = null;
let _namingPreviewTimer = null;

function _namingInputs() {
  return [...document.querySelectorAll('[data-naming-slot]')];
}

async function loadNamingTemplates() {
  // The defaults come first: they are what every placeholder shows, and the
  // markup's hardcoded ones are only a fallback for when this fetch fails.
  // Without this the two copies drift the day a default changes server-side.
  if (!_namingDefaults) {
    try {
      _namingDefaults = (await api.get('/api/domain/settings/naming-defaults')).templates;
    } catch (e) { /* the markup's placeholders stand in */ }
  }

  const data = await _loadAppSettings();
  const templates = (data && data.naming_templates) || {};
  _namingInputs().forEach(input => {
    const slot = input.dataset.namingSlot;
    if (_namingDefaults && _namingDefaults[slot]) input.placeholder = _namingDefaults[slot];
    // Left blank when it matches the default, so the placeholder — which *is*
    // the default — stays visible, and the field reads as "nothing changed
    // here" rather than as a value somebody chose.
    const stored = templates[slot] || '';
    input.value = stored === input.placeholder ? '' : stored;
    if (!input.dataset.wired) {
      input.addEventListener('input', scheduleNamingPreview);
      input.dataset.wired = '1';
    }
  });
  refreshNamingPreview();
}

function scheduleNamingPreview() {
  clearTimeout(_namingPreviewTimer);
  _namingPreviewTimer = setTimeout(refreshNamingPreview, 200);
}

function _collectNamingTemplates() {
  // An empty field means the default, which is what its placeholder shows. The
  // server rejects an empty template outright, so the substitution happens here
  // rather than turning a blank box into a validation error.
  const templates = {};
  _namingInputs().forEach(i => {
    templates[i.dataset.namingSlot] = i.value.trim() || i.placeholder;
  });
  return templates;
}

async function refreshNamingPreview() {
  try {
    const data = await api.post('/api/domain/settings/naming-preview',
                                {templates: _collectNamingTemplates()});
    _namingInputs().forEach(input => {
      const slot = data.slots[input.dataset.namingSlot];
      const line = document.getElementById(`naming-preview-${input.dataset.namingSlot}`);
      if (!line || !slot) return;
      if (slot.error) {
        line.className = 'form-text text-danger';
        _settingsText(line, scLanguage.message(slot.error));
      } else {
        line.className = 'form-text';
        _settingsText(line, t('settings.example', {preview: slot.preview}));
      }
    });
  } catch (e) { /* previews are a convenience; saving still validates */ }
}

async function saveNamingTemplates() {
  const btn = document.getElementById('save-naming-btn');
  btn.disabled = true;
  _feedback('naming-feedback', t('settings.saving'));
  try {
    await api.put('/api/domain/settings', {naming_templates: _collectNamingTemplates()});
    _feedback('naming-feedback', t('settings.saved'), 'success');
    showToast(t('settings.naming.templates.saved'), 'success');
  } catch (e) { _feedback('naming-feedback', t('settings.network.error'), 'danger'); }
  finally { btn.disabled = false; }
}

async function resetNamingTemplates() {
  if (!await scConfirm(t('settings.restore.the.default.naming.templates'))) return;
  // Blank means default, so restoring is emptying every field.
  _namingInputs().forEach(input => { input.value = ''; });
  refreshNamingPreview();
  _feedback('naming-feedback', t('settings.defaults.restored.press.save.to.apply.them'));
}


// ── Post-download hooks ────────────────────────────────────────────────────────
//
// Webhooks only: open mode grants MANAGE_SETTINGS to every anonymous visitor, so
// a shell hook would be remote code execution for whoever can reach the panel.
// See app/downloads_hooks.py.

let _hooks = [];

const HOOK_EVENT_LABELS = {
  get done() { return t('settings.completed'); },
  get error() { return t('settings.failed'); },
  get cancelled() { return t('settings.cancelled'); },
};

async function loadJellyfinRefresh() {
  const data = await _loadAppSettings();
  if (!data) return;
  document.getElementById('jf-refresh-on-download').checked =
    !!data.jellyfin_refresh_on_download;

}

// Set from the hooks payload, which reports whether the refresh has credentials
// to use. Inferring it from the auth status would report an installation that
// skipped the wizard and connected Jellyfin later as unconnected.
function renderJellyfinRefreshAvailability(connected) {
  const toggle = document.getElementById('jf-refresh-on-download');
  if (!toggle) return;
  toggle.disabled = !connected;
  _settingsText(document.getElementById('jf-refresh-status'), connected
    ? ''
    : t('settings.jellyfin.is.not.connected.connect.it.in.access'));
}

async function saveJellyfinRefresh() {
  const btn = document.getElementById('save-jf-refresh-btn');
  btn.disabled = true;
  _feedback('jf-refresh-feedback', t('settings.saving'));
  try {
    await api.put('/api/domain/settings', {
      jellyfin_refresh_on_download:
        document.getElementById('jf-refresh-on-download').checked,
    });
    _feedback('jf-refresh-feedback', t('settings.saved'), 'success');
    showToast(t('settings.setting.saved'), 'success');
  } catch (e) { _feedback('jf-refresh-feedback', _settingsError(e, t('settings.failed.to.save')), 'danger'); }
  finally { btn.disabled = false; }
}

async function loadHooks() {
  try {
    const data = await api.get('/api/download-hooks');
    _hooks = data.hooks || [];
    renderJellyfinRefreshAvailability(!!data.jellyfin_connected);
    renderHooksList();
  } catch (e) { /* the list simply stays as it was */ }
}

function _hookEventSummary(hook) {
  // An empty list means every event — the same convention the notification
  // channels use, and the one thing here that is easy to read backwards.
  if (!hook.events || !hook.events.length) return t('settings.all.outcomes');
  return hook.events.map(e => HOOK_EVENT_LABELS[e] || e).join(', ');
}

function renderHooksList() {
  const container = document.getElementById('hooks-list');
  if (!_hooks.length) {
    container.innerHTML = `<p class="text-muted small mb-0">${escapeHtml(t('settings.no.webhooks.configured'))}</p>`;
    return;
  }
  container.innerHTML = _hooks.map(hook => `
    <div class="border-bottom pb-1 mb-1">
      <div class="d-flex align-items-center gap-2 py-1">
        <label class="form-check form-switch mb-0">
          <input class="form-check-input" type="checkbox" ${hook.enabled ? 'checked' : ''}
                 data-change="cfg:hookEnabled" data-id="${hook.id}">
        </label>
        <div class="flex-fill text-truncate">
          <span style="color:var(--text)">${escapeHtml(hook.name)}</span>
          <span class="text-muted small ms-2">${escapeHtml(hook.method)} ${escapeHtml(hook.url_masked || '')}</span>
        </div>
        <span class="badge bg-secondary-lt">${escapeHtml(_hookEventSummary(hook))}</span>
        <button class="btn btn-sm btn-outline-secondary" data-action="cfg:testHook" data-id="${hook.id}">
          <i class="ti ti-send me-1"></i>Test
        </button>
        <button class="btn btn-sm btn-outline-danger" data-action="cfg:deleteHook" data-id="${hook.id}">
          <i class="ti ti-trash"></i>
        </button>
      </div>
    </div>`).join('');
}

function toggleHookForm() {
  const form = document.getElementById('hook-form');
  form.style.display = form.style.display === 'none' ? '' : 'none';
}

async function saveHook() {
  const btn = document.getElementById('hook-save-btn');
  const name = document.getElementById('hook-name').value.trim();
  const url = document.getElementById('hook-url').value.trim();
  if (!name || !url) {
    _feedback('hooks-feedback', t('settings.name.and.url.are.required'), 'danger'); return;
  }
  btn.disabled = true;
  _feedback('hooks-feedback', t('settings.saving'));
  try {
    await api.post('/api/download-hooks', {
      name, url,
      method: document.getElementById('hook-method').value,
      body_template: document.getElementById('hook-body').value,
    });
    document.getElementById('hook-name').value = '';
    document.getElementById('hook-url').value = '';
    document.getElementById('hook-body').value = '';
    document.getElementById('hook-form').style.display = 'none';
    _feedback('hooks-feedback', t('settings.added'), 'success');
    await loadHooks();
  } catch (e) { _feedback('hooks-feedback', _settingsError(e, t('settings.failed.to.save')), 'danger'); }
  finally { btn.disabled = false; }
}

async function toggleHook(id, enabled) {
  try {
    await api.patch(`/api/download-hooks/${id}`, {enabled});
  } catch (e) { _feedback('hooks-feedback', _settingsError(e), 'danger'); }
  await loadHooks();
}

async function deleteHook(id) {
  if (!await scConfirm(t('settings.delete.this.webhook'))) return;
  try {
    await api.del(`/api/download-hooks/${id}`);
    await loadHooks();
  } catch (e) { _feedback('hooks-feedback', _settingsError(e), 'danger'); }
}

async function testHook(id) {
  _feedback('hooks-feedback', t('settings.sending.test.request'));
  try {
    const data = await api.post(`/api/download-hooks/${id}/test`);
    // Only an outcome and a status code come back: the panel never relays what
    // the other end said.
    if (data.ok) {
      _feedback('hooks-feedback', t('settings.succeeded.http', {status: data.status}), 'success');
      showToast(t('settings.webhook.reached'), 'success');
    } else {
      _feedback('hooks-feedback',
        data.status ? t('settings.failed.http', {status: data.status}) : t('settings.no.response'), 'danger');
    }
  } catch (e) { _feedback('hooks-feedback', t('settings.network.error'), 'danger'); }
}

// ── Post-download hooks end ────────────────────────────────────────────────────

async function saveDomainRecovery() {
  const btn = document.getElementById('save-domain-recovery-btn');
  const interval = parseInt(document.getElementById('domain-check-interval').value, 10);
  if (!(interval >= 1 && interval <= 1440)) {
    _feedback('domain-recovery-feedback', t('settings.the.interval.must.be.between.1.and.1440'), 'danger');
    return;
  }
  const autoApply = document.getElementById('domain-auto-apply').checked;
  // Turning this on hands a page we do not control the ability to move the
  // panel's source. Worth one deliberate click.
  if (autoApply && !await scConfirm(
      t('settings.automatic.application.adopts.the.discovered.domain.without.asking'))) {
    return;
  }
  btn.disabled = true;
  _feedback('domain-recovery-feedback', t('settings.saving'));
  try {
    await api.put('/api/domain/settings', {
      domain_auto_check_enabled: document.getElementById('domain-auto-check').checked,
      domain_auto_apply: autoApply,
      domain_check_interval_minutes: interval,
    });
    _feedback('domain-recovery-feedback', t('settings.saved'), 'success');
    showToast(t('settings.settings.saved'), 'success');
  } catch (e) { _feedback('domain-recovery-feedback', _settingsError(e, t('settings.failed.to.save')), 'danger'); }
  finally { btn.disabled = false; }
}

async function checkDomainNow() {
  const btn = document.getElementById('domain-check-btn');
  btn.disabled = true;
  _feedback('domain-recovery-feedback', t('settings.checking'));
  try {
    let data;
    try {
      data = await api.post('/api/domain/check');
    } catch (e) {
      _feedback('domain-recovery-feedback', _settingsError(e, t('settings.check.failed')), 'danger');
      return;
    }
    if (data.applied) {
      _feedback('domain-recovery-feedback', t('settings.applied', {domain: data.candidate}), 'success');
      await loadDomainStatus();
    } else if (data.candidate) {
      _feedback('domain-recovery-feedback', t('settings.found.awaiting.application', {domain: data.candidate}), 'success');
    } else if (data.current_ok) {
      _feedback('domain-recovery-feedback', t('settings.the.current.domain.is.responding'), 'success');
    } else {
      // Rejections are shown rather than swallowed: a rebranded source and an
      // edited page look identical from here, and only a person can tell them
      // apart.
      const why = (data.rejected || []).map(r => `${r.host} (${scLanguage.message(r.reason)})`).join(', ');
      _feedback('domain-recovery-feedback',
        why ? t('settings.no.suitable.domain.rejected', {rejected: why}) : t('settings.no.domain.found'),
        'danger');
    }
    await loadDomainCandidate();
  } catch (e) { _feedback('domain-recovery-feedback', t('settings.network.error'), 'danger'); }
  finally { btn.disabled = false; }
}

async function savePerfSettings() {
  const btn = document.getElementById('save-perf-btn');
  const concurrent = parseInt(document.getElementById('setting-max-concurrent').value, 10);
  const workers = parseInt(document.getElementById('setting-max-workers').value, 10);
  const watchInterval = parseInt(document.getElementById('setting-watch-interval').value, 10);
  if (!concurrent || !workers || !watchInterval) {
    _feedback('perf-settings-feedback', t('settings.invalid.values'), 'danger'); return;
  }
  btn.disabled = true;
  _feedback('perf-settings-feedback', t('settings.saving'));
  try {
    await api.put('/api/domain/settings', {
      max_concurrent_downloads: concurrent,
      max_segment_workers: workers,
      series_watch_interval_minutes: watchInterval,
    });
    _feedback('perf-settings-feedback', t('settings.saved'), 'success');
    showToast(t('settings.performance.settings.saved'), 'success');
  } catch (e) { _feedback('perf-settings-feedback', _settingsError(e, t('settings.failed.to.save')), 'danger'); }
  finally { btn.disabled = false; }
}

async function saveDomain() {
  const domain = document.getElementById('domain-input').value.trim();
  const btn = document.getElementById('save-domain-btn');
  if (!domain) { _feedback('domain-feedback', t('settings.enter.a.domain'), 'danger'); return; }
  btn.disabled = true;
  _feedback('domain-feedback', t('settings.verifying'));
  try {
    const data = await api.put('/api/domain', {domain});
    currentDomain = data.domain; currentVersion = data.version;
    _feedback('domain-feedback', t('settings.ok.version', {version: data.version}), 'success');
    const badge = document.getElementById('domain-badge');
    badge.className = 'badge bg-success';
    badge.textContent = data.domain;
  } catch(e) {
    _feedback('domain-feedback', _settingsError(e, t('settings.network.error.233')), 'danger');
  } finally { btn.disabled = false; }
}

// ── Libraries ──────────────────────────────────────────────────────────────────

async function loadLibraries() {
  try {
    const data = await api.get('/api/domain/libraries');
    _libraries = data.libraries || [];
    const excl = (data.excluded_folders || []).join(', ');
    const inp = document.getElementById('excluded-input');
    if (inp) inp.value = excl;
  } catch(e) { console.error('loadLibraries:', e); }
}
const _LIB_TYPE_OPTIONS = [{value:'film',get label() { return t('settings.films'); }},{value:'tv',get label() { return t('settings.tv.series'); }},{value:'anime',label:'Anime'}];
function renderLibrariesList() {
  const c = document.getElementById('libraries-list');
  if (!c) return;
  if (!_libraries.length) { c.innerHTML = `<p class="text-muted small mb-0">${escapeHtml(t('settings.no.libraries'))}</p>`; return; }
  const usedTypes = _libraries.map(l => l.type);
  c.innerHTML = _libraries.map((lib, i) => {
    const opts = _LIB_TYPE_OPTIONS.map(o => {
      const disabled = o.value !== lib.type && usedTypes.some((t,j) => j !== i && t === o.value) ? 'disabled' : '';
      const selected = o.value === lib.type ? 'selected' : '';
      return `<option value="${o.value}" ${selected} ${disabled}>${o.label}</option>`;
    }).join('');
    return `
    <div class="row g-2 mb-2 align-items-center">
      <div class="col-4"><select class="form-select form-select-sm" id="lib-type-${i}"><option value="">${escapeHtml(t('settings.type'))}</option>${opts}</select></div>
      <div class="col"><input type="text" class="form-control form-control-sm" id="lib-path-${i}" value="${escapeHtml(lib.path)}" placeholder="/srv/nfs/films"></div>
      <div class="col-auto"><button class="btn btn-sm btn-outline-danger" data-action="cfg:removeLibrary" data-index="${i}"><i class="ti ti-trash"></i></button></div>
    </div>`;
  }).join('');
}
function _syncLibs() {
  _libraries = _libraries.map((_,i) => ({
    type: document.getElementById(`lib-type-${i}`)?.value||'',
    path: document.getElementById(`lib-path-${i}`)?.value||'',
  }));
}
function addLibrary() {
  _syncLibs(); _libraries.push({type:'',path:''}); renderLibrariesList();
  document.getElementById(`lib-path-${_libraries.length-1}`)?.focus();
}
function removeLibrary(idx) { _syncLibs(); _libraries.splice(idx,1); renderLibrariesList(); }
async function saveLibraries() {
  const updated = _libraries.map((_,i) => ({
    type:(document.getElementById(`lib-type-${i}`)?.value||'').trim(),
    path:(document.getElementById(`lib-path-${i}`)?.value||'').trim(),
  })).filter(l => l.type && l.path);
  const excluded = (document.getElementById('excluded-input')?.value||'').split(',').map(s=>s.trim()).filter(Boolean);
  const btn = document.getElementById('save-libraries-btn');
  btn.disabled = true;
  _feedback('libraries-feedback', t('settings.saving'));
  try {
    await api.put('/api/domain/libraries', {libraries:updated, excluded_folders:excluded});
    _libraries = updated;
    renderLibrariesList();
    _feedback('libraries-feedback', t('settings.saved'), 'success');
    showToast(t('settings.libraries.saved'),'success');
  } catch(e) { _feedback('libraries-feedback', _settingsError(e), 'danger'); }
  finally { btn.disabled = false; }
}


// ── MCP server ───────────────────────────────────────────────────────────────

// The address an MCP client should use: wherever this page was reached from,
// which behind a reverse proxy is the public HTTPS name.
function _mcpUrl(path) {
  return `${window.location.origin}${path || '/mcp'}`;
}

function _renderMcpSnippet(path, token) {
  const snippet = {
    mcpServers: {
      streamingcommunity: {
        type: 'http',
        url: _mcpUrl(path),
        headers: { Authorization: `Bearer ${token || '<TOKEN>'}` },
      },
    },
  };
  document.getElementById('mcp-config-snippet').textContent = JSON.stringify(snippet, null, 2);
}

let _mcpPath = '/mcp';

async function loadMcpSettings() {
  _feedback('mcp-feedback', '');
  try {
    const status = await api.get('/api/mcp/status');
    _mcpPath = status.path;
    document.getElementById('setting-mcp-enabled').checked = !!status.enabled;
    const tokenStatus = document.getElementById('mcp-token-status');
    if (!status.has_token) {
      _settingsText(tokenStatus, t('settings.no.token.generate.one.to.allow.an.agent'));
    } else if (!status.owner) {
      // Owner disabled or removed, or minted before Jellyfin was connected.
      _settingsText(tokenStatus, t('settings.the.token.is.no.longer.valid.its.owner'));
    } else {
      _settingsText(tokenStatus, t('settings.active.token.generated.by.the.agent.uses.their', {owner: status.owner}));
    }
    document.getElementById('mcp-revoke-btn').style.display = status.has_token ? '' : 'none';
    document.getElementById('mcp-token-once').style.display = 'none';
    _renderMcpSnippet(_mcpPath, '');
  } catch (e) {
    _feedback('mcp-feedback', _settingsError(e, t('settings.failed.to.load')), 'danger');
  }
}

async function saveMcpSettings() {
  const btn = document.getElementById('save-mcp-btn');
  btn.disabled = true;
  _feedback('mcp-feedback', t('settings.saving'));
  try {
    await api.put('/api/domain/settings', {
      mcp_enabled: document.getElementById('setting-mcp-enabled').checked,
    });
    _appSettingsPromise = null;
    _feedback('mcp-feedback', t('settings.saved'), 'success');
    showToast(t('settings.setting.saved'), 'success');
  } catch (e) { _feedback('mcp-feedback', _settingsError(e, t('settings.failed.to.save')), 'danger'); }
  finally { btn.disabled = false; }
}

async function regenerateMcpToken() {
  const hadToken = document.getElementById('mcp-revoke-btn').style.display !== 'none';
  if (hadToken && !await scConfirm(
    t('settings.generate.a.new.token.the.current.one.will')
  )) return;
  try {
    const res = await api.post('/api/mcp/token', {});
    await loadMcpSettings();
    document.getElementById('mcp-token-display').value = res.token;
    document.getElementById('mcp-token-once').style.display = '';
    _renderMcpSnippet(_mcpPath, res.token);
  } catch (e) {
    _feedback('mcp-feedback', _settingsError(e, t('settings.failed.to.generate.the.token')), 'danger');
  }
}

async function revokeMcpToken() {
  if (!await scConfirm(
    t('settings.revoke.the.token.no.agent.will.be.able')
  )) return;
  try {
    await api.del('/api/mcp/token');
    await loadMcpSettings();
    showToast(t('settings.token.revoked'), 'success');
  } catch (e) {
    _feedback('mcp-feedback', _settingsError(e, t('settings.failed.to.revoke.the.token')), 'danger');
  }
}

// The clipboard API exists only in a secure context, and a panel reached at
// http://192.168.x.x is not one: there the selection-and-execCommand route is
// the only one left.
async function _copyText(text) {
  if (!text) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* fall through */ }
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

async function _copyWithToast(text, what) {
  const ok = await _copyText(text);
  showToast(ok ? t('settings.copied', {item: what}) : t('settings.unable.to.copy.select.it.manually'), ok ? 'success' : 'warning');
}


// ── Delegated handlers ───────────────────────────────────────────────────────

registerActions({
  'cfg:probeHardware': () => probeHardware(),
  'cfg:softetherStatus': () => loadSoftEtherStatus(),
  'cfg:softetherOpen': () => openSoftEtherManager(),
  'cfg:softetherClose': () => closeSoftEtherManager(),
  'cfg:softetherInstall': () => installSoftEther(),
  'cfg:saveGeneral': () => saveGeneralSettings(),
  'cfg:runDiagnostics': () => runDiagnostics(),
  'cfg:exportSettings': () => exportSettingsBackup(),
  'cfg:chooseRestore': () => chooseSettingsRestore(),
  'cfg:softetherUpdates': () => checkSoftEtherUpdates(),
  'cfg:softetherDownloadUpdate': () => downloadSoftEtherUpdate(),
  'cfg:softetherVpnGate': () => loadVpnGateServers(),
  'cfg:softetherVpnGateHide': () => hideVpnGateServers(),
  'cfg:refreshLogs': () => loadApplicationLogs(),
  'cfg:browseDownloadDirectory': () => browseDownloadDirectory(),
  'cfg:saveDownloadDirectory': () => saveDownloadDirectory(),
  'settings:open':        () => openSettings(),
  'cfg:saveDomain':       () => saveDomain(),
  'cfg:saveHosts':        () => saveHosts(),
  'cfg:saveRecovery':     () => saveDomainRecovery(),
  'cfg:checkDomain':      () => checkDomainNow(),
  'cfg:addLibrary':       () => addLibrary(),
  'cfg:saveLibraries':    () => saveLibraries(),
  'cfg:removeLibrary':    d => removeLibrary(Number(d.index)),
  'cfg:saveNaming':       () => saveNamingTemplates(),
  'cfg:resetNaming':      () => resetNamingTemplates(),
  'cfg:savePerf':         () => savePerfSettings(),
  'cfg:saveVideo': () => saveVideoSettings(),
  'cfg:refreshVideoEncoders': () => loadVideoEncoders(true),
  'cfg:saveOutput':       () => saveOutputSettings(),
  'cfg:jfConnect':        d => connectJellyfin(d.reconfigure === '1'),
  'cfg:jfReconfigure':    () => toggleJellyfinReconfigure(),
  'cfg:saveJfRefresh':    () => saveJellyfinRefresh(),
  'cfg:saveChannel':      () => saveNotificationChannel(),
  'cfg:toggleChannelForm': () => toggleNotificationChannelForm(),
  'cfg:channelPicker':    d => toggleChannelEvents(Number(d.id)),
  'cfg:channelEvents':    d => updateChannelEvents(Number(d.id)),
  'cfg:channelAllEvents': (d, el) => toggleAllChannelEvents(Number(d.id), el.checked),
  'cfg:channelEnabled':   (d, el) => toggleNotificationChannel(Number(d.id), el.checked),
  'cfg:testChannel':      d => testNotificationChannel(Number(d.id)),
  'cfg:deleteChannel':    d => deleteNotificationChannel(Number(d.id)),
  'cfg:saveHook':         () => saveHook(),
  'cfg:toggleHookForm':   () => toggleHookForm(),
  'cfg:hookEnabled':      (d, el) => toggleHook(Number(d.id), el.checked),
  'cfg:testHook':         d => testHook(Number(d.id)),
  'cfg:deleteHook':       d => deleteHook(Number(d.id)),
  'cfg:saveMcp':          () => saveMcpSettings(),
  'cfg:copyMcpToken':     () => _copyWithToast(document.getElementById('mcp-token-display').value, 'Token'),
  'cfg:copyMcpSnippet':   () => _copyWithToast(document.getElementById('mcp-config-snippet').textContent, t('settings.configuration')),
  'cfg:regenerateMcpToken': () => regenerateMcpToken(),
  'cfg:revokeMcpToken':   () => revokeMcpToken(),
});


// The tab is a path segment, not a query parameter: it names which page of
// settings you are on, the way the title's id does.
registerPageHash('settings', {
  read: () => ({ extra: [_settingsTab] }),
});


async function loadDownloadDirectory() {
  try {
    const data = await api.get('/api/domain/download-directory');
    document.getElementById('download-directory').value = data.path;
    document.getElementById('browse-download-directory').style.display = data.can_browse ? '' : 'none';
  } catch (e) { _feedback('download-directory-feedback', _settingsError(e), 'danger'); }
}

async function saveDownloadDirectory() {
  const btn = document.getElementById('save-download-directory');
  btn.disabled = true;
  try {
    const data = await api.put('/api/domain/download-directory', {
      path: document.getElementById('download-directory').value.trim(),
    });
    document.getElementById('download-directory').value = data.path;
    _feedback('download-directory-feedback', t('settings.folder.saved.for.new.downloads'), 'success');
  } catch (e) { _feedback('download-directory-feedback', _settingsError(e), 'danger'); }
  finally { btn.disabled = false; }
}

let _logsLoading = false;
async function loadApplicationLogs() {
  if (_logsLoading) return;
  _logsLoading = true;
  try {
    const data = await api.get('/api/domain/logs');
    const el = document.getElementById('application-log');
    const follow = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    if (data.lines.length) {
      _settingsTextBindings.delete(el);
      el.textContent = data.lines.join('\n');
    } else _settingsText(el, t('settings.no.events.recorded'));
    if (follow) el.scrollTop = el.scrollHeight;
    _feedback('logs-feedback');
  } catch (e) { _feedback('logs-feedback', _settingsError(e), 'danger'); }
  finally { _logsLoading = false; }
}
setInterval(() => {
  if (!document.hidden && _settingsTab === 'log' &&
      document.getElementById('page-settings')?.style.display !== 'none') loadApplicationLogs();
}, 3000);


async function browseDownloadDirectory() {
  const button = document.getElementById('browse-download-directory');
  const save = document.getElementById('save-download-directory');
  button.disabled = true;
  save.disabled = true;
  _feedback('download-directory-feedback', t('settings.select.a.folder.in.the.windows.dialog'));
  try {
    const result = await api.post('/api/domain/download-directory/browse', {
      path: document.getElementById('download-directory').value.trim(),
    });
    if (!result.cancelled && result.path) {
      document.getElementById('download-directory').value = result.path;
      _feedback('download-directory-feedback', t('settings.folder.selected.press.save.folder.to.apply.it'));
    } else {
      _feedback('download-directory-feedback', t('settings.selection.cancelled'));
    }
  } catch (e) { _feedback('download-directory-feedback', _settingsError(e), 'danger'); }
  finally { button.disabled = false; save.disabled = false; }
}


let _hardwareData = null;

function renderHardwareResults(data) {
    const target = document.getElementById('hardware-results');
    if (!target) return;
    target.replaceChildren();
    _settingsTextBindings.delete(target);
    const unknown = t('settings.hardware.unknown');
    const gib = value => value == null ? unknown : (value / 1024 ** 3).toFixed(1) + ' GiB';
    const line = (key, values) => {
      const node = document.createElement('div');
      _settingsText(node, t(key, values));
      target.appendChild(node);
    };
    line('settings.hardware.cpu', {name: data.cpu.name || unknown,
      cores: data.cpu.physical_cores ?? unknown, threads: data.cpu.logical_threads ?? unknown});
    line('settings.hardware.ram', {total: gib(data.memory.total_bytes), available: gib(data.memory.available_bytes)});
    if (!data.gpus.length) line('settings.hardware.noGpu');
    for (const gpu of data.gpus) line('settings.hardware.gpu', {name: gpu.name, vram: gib(gpu.vram_bytes)});
    line('settings.hardware.encoders', {encoders: data.encoders.filter(row => row.available).map(row => row.encoder).join(', ') || unknown});
    const r = data.recommendations;
    if (r.video_encoder) {
      line('settings.hardware.conversion', {encoder: r.video_encoder,
        codec: r.video_codec, count: r.max_parallel_conversions});
    } else line('settings.hardware.noEncoder');
    line('settings.hardware.downloads', {count: r.max_concurrent_downloads, workers: r.max_segment_workers});
    line('settings.hardware.advice');
    if (data.warnings.length) line('settings.hardware.partial');
}

async function probeHardware() {
  const button = document.getElementById('probe-hardware');
  const target = document.getElementById('hardware-results');
  button.disabled = true;
  _hardwareData = null;
  _settingsText(target, t('settings.hardware.scanning'));
  try {
    _hardwareData = await api.get('/api/domain/hardware');
    const recommendations = _hardwareData.recommendations || {};
    if (recommendations.max_concurrent_downloads && recommendations.max_segment_workers) {
      await api.put('/api/domain/settings', {
        max_concurrent_downloads: recommendations.max_concurrent_downloads,
        max_segment_workers: recommendations.max_segment_workers,
      });
      document.getElementById('setting-max-concurrent').value = recommendations.max_concurrent_downloads;
      document.getElementById('setting-max-workers').value = recommendations.max_segment_workers;
      _feedback('perf-settings-feedback', t('settings.hardware.applied', {
        count: recommendations.max_concurrent_downloads,
        workers: recommendations.max_segment_workers,
      }), 'success');
    }
    renderHardwareResults(_hardwareData);
  } catch (e) { _settingsText(target, t('settings.hardware.failed')); }
  finally { button.disabled = false; }
}

async function loadVideoSettings() {
  const data = await _loadAppSettings();
  if (data) {
    document.getElementById('video-conversion').value = data.video_conversion || 'copy';
    document.getElementById('video-encoder').value = data.video_encoder || 'auto';
    document.getElementById('video-quality').value = data.video_quality || 'balanced';
    document.getElementById('video-cpu-fallback').checked = data.video_cpu_fallback !== false;
  }
  await loadVideoEncoders();
}

async function loadVideoEncoders(refresh = false) {
  const button = document.getElementById('refresh-video-encoders');
  const status = document.getElementById('video-encoder-status');
  status.removeAttribute('data-i18n');
  button.disabled = true;
  _settingsText(status, t('settings.testing.encoders'));
  try {
    const data = refresh ? await api.post('/api/domain/video-encoders/refresh', {})
                         : await api.get('/api/domain/video-encoders');
    status.replaceChildren();
    _settingsTextBindings.delete(status);
    for (const encoder of data.encoders) {
      const line = document.createElement('div');
      _settingsText(line, t('settings.encoder.status', {
        label: scLanguage.message(encoder.label),
        codec: encoder.codec === 'hevc' ? 'H.265' : 'H.264',
        status: encoder.available ? t('settings.available.verified') : t('settings.unavailable'),
      }));
      line.title = encoder.reason || '';
      status.appendChild(line);
    }
  } catch (e) { _settingsText(status, _settingsError(e, t('settings.detection.failed'))); }
  finally { button.disabled = false; }
}

async function saveVideoSettings() {
  const button = document.getElementById('save-video-settings');
  button.disabled = true;
  _feedback('video-settings-feedback', t('settings.verifying.and.saving'));
  try {
    await api.put('/api/domain/settings', {
      video_conversion: document.getElementById('video-conversion').value,
      video_encoder: document.getElementById('video-encoder').value,
      video_quality: document.getElementById('video-quality').value,
      video_cpu_fallback: document.getElementById('video-cpu-fallback').checked,
    });
    _appSettingsPromise = null;
    _feedback('video-settings-feedback', t('settings.saved.applies.to.new.downloads'), 'success');
  } catch (e) { _feedback('video-settings-feedback', _settingsError(e), 'danger'); }
  finally { button.disabled = false; }
}

window.addEventListener('sc:language-changed', () => {
  // Refresh only generated lists; no settings endpoint is called here.
  const focused = document.activeElement;
  const focusId = focused?.id;
  const selection = typeof focused?.selectionStart === 'number'
    ? [focused.selectionStart, focused.selectionEnd] : null;
  if (_hardwareData && !document.getElementById('probe-hardware')?.disabled) renderHardwareResults(_hardwareData);
  if (document.querySelector('#libraries-list [id^="lib-path-"]')) _syncLibs();
  renderLibrariesList();
  renderNotificationChannelsList();
  renderHooksList();
  for (const [node, descriptor] of _settingsTextBindings) {
    if (!node.isConnected) { _settingsTextBindings.delete(node); continue; }
    node.textContent = scLanguage.resolve(descriptor);
  }
  const target = focusId && document.getElementById(focusId);
  if (target && target !== focused) {
    target.focus({preventScroll: true});
    if (selection) target.setSelectionRange(...selection);
  }
});
