// Shared primitives: the local identity, the fetch wrapper, the small
// formatters, the modal and toast plumbing. Everything here is used by more than one page.
//
// Loaded before app.js and panel.js, and a classic script on purpose, not a
// module: the markup still carries inline onclick handlers, which resolve
// against global scope. See the rework plan.


// ── Local identity ─────────────────────────────────────────────────────────────
//
// The panel is local and single-user: no login, no session, no logout.
// /api/auth/me names the one local user and its permissions, which drive the
// menu below. Cross-site writes are refused server-side by the Origin /
// Sec-Fetch-Site guard (app/auth/deps.py), not by a token.

let _me = null;           // { user, csrf_token: null, auth_enabled: false, version }
let _csrf = '';

function can(permission) {
  return !!_me && _me.user.permission_names.includes(permission);
}

// Wrapping fetch once covers every call site. The CSRF header is only sent if
// the server ever hands out a token again; today /api/auth/me answers null.
const _nativeFetch = window.fetch.bind(window);

function _withCsrfHeader(opts, method) {
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && _csrf) {
    opts.headers = { ...(opts.headers || {}), 'X-CSRF-Token': _csrf };
  }
  return opts;
}

/** Fetch the local identity. Returns false if the panel did not answer. */
async function _refreshIdentity() {
  const res = await _nativeFetch('/api/auth/me');
  if (!res.ok) return false;
  _me = await res.json();
  _csrf = _me.csrf_token;
  return true;
}

window.fetch = async (input, init) => {
  init = init || {};
  const method = (init.method || 'GET').toUpperCase();
  return _nativeFetch(input, _withCsrfHeader(init, method));
};

async function initAuth() {
  // There is no login page to send anyone to: if the panel itself does not
  // answer, say so where the page would have been.
  let ok = false;
  try { ok = await _refreshIdentity(); } catch (_) { ok = false; }
  if (!ok) {
    showToast('Pannello non raggiungibile: ricarica la pagina.', 'danger');
    return false;
  }

  const version = document.getElementById('app-version');
  if (version && _me.version) version.textContent = `SC Panel v${_me.version}`;

  // Menu entries follow the permissions. This is cosmetic only — every one of
  // these endpoints is checked server-side as well.
  document.querySelectorAll('[data-perm]').forEach(el => {
    const needed = el.dataset.perm.split('|');
    el.style.display = needed.some(can) ? '' : 'none';
  });
  // The inverse: hidden *because* the user holds a permission. Someone who
  // downloads directly never files a request, so the queue of their own
  // requests is an empty page with a name.
  //
  // Applied second so it can override a data-perm that just showed the
  // element.
  document.querySelectorAll('[data-hide-perm]').forEach(el => {
    if (el.dataset.hidePerm.split('|').some(can)) el.style.display = 'none';
  });
  return true;
}

// ── Utilities ──────────────────────────────────────────────────────────────────

// Whether an AnimeUnity kind (its own media_type: Movie, TV, ONA, …) is a film.
// Mirrors paths._ANIME_FILM_KINDS: everything else is a series.
function isAnimeFilm(kind) {
  return ['movie', 'film'].includes(String(kind || 'tv').trim().toLowerCase());
}

function scConfirm(msg) {
  if (window._confirmationsEnabled === false) return Promise.resolve(true);
  return new Promise(resolve => {
    document.getElementById('sc-confirm-msg').textContent = msg;
    const ok = document.getElementById('sc-confirm-ok');
    const cancel = document.getElementById('sc-confirm-cancel');
    function cleanup() {
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
    }
    function onOk()     { cleanup(); hideModal('sc-confirm-modal'); resolve(true); }
    function onCancel() { cleanup(); hideModal('sc-confirm-modal'); resolve(false); }
    ok.addEventListener('click', onOk, {once:true});
    cancel.addEventListener('click', onCancel, {once:true});
    showModal('sc-confirm-modal');
  });
}

function scPrompt(msg, defaultVal='') {
  return new Promise(resolve => {
    document.getElementById('sc-prompt-msg').textContent = msg;
    const input = document.getElementById('sc-prompt-input');
    input.value = defaultVal;
    const ok = document.getElementById('sc-prompt-ok');
    const cancel = document.getElementById('sc-prompt-cancel');
    function cleanup() {
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
      input.removeEventListener('keydown', onKey);
    }
    function onOk()     { cleanup(); hideModal('sc-prompt-modal'); resolve(input.value); }
    function onCancel() { cleanup(); hideModal('sc-prompt-modal'); resolve(null); }
    function onKey(e)   { if (e.key === 'Enter') onOk(); }
    ok.addEventListener('click', onOk, {once:true});
    cancel.addEventListener('click', onCancel, {once:true});
    input.addEventListener('keydown', onKey);
    showModal('sc-prompt-modal');
    setTimeout(() => input.focus(), 50);
  });
}

function escapeHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function formatSize(bytes) {
  if (bytes == null) return '—';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes/1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes/1048576).toFixed(1) + ' MB';
  return (bytes/1073741824).toFixed(2) + ' GB';
}
function fmtEta(sec) {
  if (sec == null || sec <= 0) return '';
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return `${m}m ${s.toString().padStart(2,'0')}s`;
  const h = Math.floor(m / 60), rm = m % 60;
  return `${h}h ${rm}m`;
}

// ── Modal helpers ──────────────────────────────────────────────────────────────

function showModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = 'block';
  el.classList.add('show');
  el.setAttribute('aria-modal', 'true');
  el.removeAttribute('aria-hidden');
  if (!document.querySelector('.modal-backdrop')) {
    const bd = document.createElement('div');
    bd.className = 'modal-backdrop fade show';
    document.body.appendChild(bd);
  }
  document.body.classList.add('modal-open');
}
function hideModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = 'none';
  el.classList.remove('show');
  el.setAttribute('aria-hidden', 'true');
  el.removeAttribute('aria-modal');
  document.querySelector('.modal-backdrop')?.remove();
  document.body.classList.remove('modal-open');
}
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal') && e.target.classList.contains('show') && e.target.id !== 'player-modal')
    hideModal(e.target.id);
  if (e.target.closest('[data-bs-dismiss="modal"]')) {
    const modal = e.target.closest('.modal');
    if (modal) hideModal(modal.id);
  }
});

// ── Toast ──────────────────────────────────────────────────────────────────────

// `link` ({label, page}) offers a way somewhere instead of taking the user
// there: queueing a season used to jump to the downloads page, and the title
// you were picking seasons from was gone (#20).
function showToast(message, type = 'info', link = null) {
  const colors = { success:'bg-success', danger:'bg-danger', info:'bg-info', warning:'bg-warning' };
  const toast = document.createElement('div');
  toast.className = 'sc-toast-wrap';
  const go = link
    ? ` <a href="#" class="alert-link text-white ms-1" data-action="toast:go"
          data-page="${escapeHtml(link.page)}">${escapeHtml(link.label)}</a>`
    : '';
  toast.innerHTML = `<div class="alert sc-toast-alert ${colors[type]||'bg-info'} alert-dismissible text-white mb-0 shadow" role="alert">
    ${escapeHtml(message)}${go}
    <button type="button" class="btn-close btn-close-white" data-action="toast:dismiss"></button>
  </div>`;
  document.body.appendChild(toast);
  // Longer with a link: it has to be read and then reached.
  setTimeout(() => toast.remove(), link ? 7000 : 4000);
}

// A blocked source answers every queued request with a 403: one notice per
// interval, whatever the cascade. Only the event is gated here; nothing is
// re-requested on a timer.
const SOFTETHER_BLOCK_NOTICE_INTERVAL_MS = 10 * 60 * 1000;
let _softEtherOfferAt = 0;
window.addEventListener('softether:block-detected', () => {
  const now = Date.now();
  if (_softEtherOfferAt && now - _softEtherOfferAt < SOFTETHER_BLOCK_NOTICE_INTERVAL_MS) return;
  _softEtherOfferAt = now;
  // The Settings notice (page-settings.js) stays up until reload; the toast
  // is for whoever is on another page.
  window.dispatchEvent(new CustomEvent('softether:block-notice'));
  // The notice only counts as seen when it is laid out: on another page, or
  // on another Settings tab, offsetParent is null and the toast is needed.
  const notice = document.getElementById('softether-block-notice');
  if (notice && !notice.hidden && notice.offsetParent !== null) return;
  showToast(window.t('softether.blockDetected'), 'warning', {
    label: window.t('softether.openSettings'), page: 'settings', tab: 'sorgente',
  });
});

// ── Artwork ───────────────────────────────────────────────────────────────────
//
// Stills and posters travel through /api/image/, which reaches the source's
// CDN on the server's behalf. A single failure there is usually transient, so
// a missing image should not collapse the row it belongs to: the slot stays,
// a placeholder holds it, and the fetch is retried a couple of times before
// giving up quietly.

const _THUMB_RETRIES = 2;

function thumbHtml(src, cls, alt = '') {
  if (!src) return `<span class="${cls} thumb is-failed" aria-hidden="true"></span>`;
  // No onload: the image is visible from the start and the placeholder simply
  // sits behind it. Gating visibility on a handler meant a missed load event
  // hid the picture permanently.
  return `<span class="${cls} thumb">
    <img src="${escapeHtml(src)}" alt="${escapeHtml(alt)}" loading="lazy"
         data-src="${escapeHtml(src)}" onerror="thumbError(this)">
  </span>`;
}

function thumbError(img) {
  const tries = Number(img.dataset.tries || 0);
  if (tries >= _THUMB_RETRIES) { img.parentElement?.classList.add('is-failed'); return; }
  img.dataset.tries = String(tries + 1);
  // A cache-busting query, not a fragment: a fragment change does not make
  // the browser ask again.
  const base = img.dataset.src;
  const sep = base.includes('?') ? '&' : '?';
  img.parentElement?.classList.remove('is-failed');
  setTimeout(() => { img.src = `${base}${sep}_retry=${tries + 1}`; }, 500 * (tries + 1));
}

// A poster that will not load: reveal the placeholder already sitting behind
// it and take the broken image out. Named, rather than three statements
// written into an onerror attribute — error does not bubble, so this one
// genuinely cannot be delegated, but it does not have to be inline JS either.
function posterError(img) {
  const wrap = img.closest('.poster-wrap');
  const placeholder = wrap && wrap.querySelector('.poster-noimg');
  if (placeholder) placeholder.style.display = 'flex';
  img.style.display = 'none';
}

// Artwork URL for a bare filename. The generation changes on a manual refresh,
// so a new value makes the browser fetch the image again.
function imageUrl(name) {
  return `/api/image/${name}?g=${encodeURIComponent(window.IMAGE_GEN || '0')}`;
}

// A title's poster. AnimeUnity sends an absolute URL; StreamingCommunity sends
// a bare filename that /api/image/ resolves against the current source host -
// which is why it must not be hardcoded anywhere.
function posterUrl(item) {
  const p = item && item.poster;
  if (!p) return '';
  return p.startsWith('http') ? p : imageUrl(p);
}


// ── Delegated actions ────────────────────────────────────────────────────────
//
// One listener on the document, instead of an onclick= per button. Two reasons
// beyond tidiness: an inline handler interpolates its arguments into an HTML
// attribute with no escaping, so a title containing an apostrophe used to break
// the button it was rendered into; and a row that is re-rendered mid-interaction
// keeps working, because nothing is bound to the node that went away.
//
// Additive on purpose: it coexists with the inline handlers that have not been
// converted yet. A page converts its own markup when it is reworked, never in a
// sweep across all of them at once.
//
// Arguments travel as data-* attributes and arrive as the element's dataset, so
// they are strings: a handler that wants a number converts it.
const _ACTIONS = Object.create(null);

function registerActions(map) { Object.assign(_ACTIONS, map); }

// A checkbox's click event carries its own activation: cancelling it un-checks
// the box the user just checked. So the default is only suppressed for things
// that have no useful default of their own.
const _KEEPS_ITS_DEFAULT = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'OPTION', 'LABEL']);

function _dispatchAction(event, attribute) {
  const el = event.target.closest(`[${attribute}]`);
  if (!el || el.disabled) return;
  const handler = _ACTIONS[el.getAttribute(attribute)];
  if (!handler) return;
  // Only swallow the event once something is actually going to handle it: an
  // unregistered name must look broken, not silently eat the click.
  if (event.type === 'click' && !_KEEPS_ITS_DEFAULT.has(el.tagName)) event.preventDefault();
  handler(el.dataset, el, event);
}

document.addEventListener('click', e => _dispatchAction(e, 'data-action'));
document.addEventListener('change', e => _dispatchAction(e, 'data-change'));
document.addEventListener('input', e => _dispatchAction(e, 'data-input'));

// Two keys, because those are the two a text field answers: Enter runs the
// thing, Escape abandons it. Anything more belongs to the field itself.
document.addEventListener('keydown', e => {
  if (e.key === 'Enter') _dispatchAction(e, 'data-enter');
  else if (e.key === 'Escape') _dispatchAction(e, 'data-escape');

  // Cards and other action-bearing non-controls must be usable without a
  // mouse. Native buttons already dispatch their own click event, so only
  // synthesize activation for elements that are not native controls.
  if ((e.key === 'Enter' || e.key === ' ') &&
      e.target.closest?.('[data-action]:not(button):not(a):not(input):not(select):not(textarea)')) {
    const el = e.target.closest('[data-action]');
    if (el && !el.hasAttribute('disabled') && !el.getAttribute('aria-disabled')) {
      e.preventDefault();
      el.click();
    }
  }
});


// ── Language names ───────────────────────────────────────────────────────────
//
// Shared, not owned by a page: the title page names the tracks you are picking
// and the request pages name the tracks a request was made with. This lived in
// the detail view until splitting the scripts showed the request rows reaching
// across for it.
//
// Anything not listed is shown as the source spelled it, rather than dropped.

const LANG_NAMES = {
  ita:'Italiano', eng:'English', fra:'Français', spa:'Español',
  deu:'Deutsch', por:'Português', jpn:'日本語', zho:'中文',
  ara:'العربية', rus:'Русский', kor:'한국어',
};
const langName = c => c.startsWith('forced-')
  ? `${langName(c.slice(7))} (forzati)`
  : LANG_NAMES[c] || (() => {
      try { return new Intl.DisplayNames(['it'], { type: 'language' }).of(c) || c; }
      catch { return c; }
    })();


// formatSize() stops at GB and is used for file rows; volumes are routinely in
// terabytes, so the disk readout gets its own scale.
function fmtBytes(bytes) {
  if (bytes == null) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let value = bytes, i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1).replace('.', ',')} ${units[i]}`;
}


// How full is too full, in one place. The sidebar bar and the file page's bar
// were each carrying their own pair of numbers, and they had already drifted
// apart: the same volume read amber in one and fine in the other.
//
// Past 92% a 4K season may not fit, which is the number worth a red bar.
function diskLevel(pct) {
  if (pct >= 92) return 'is-critical';
  if (pct >= 80) return 'is-warning';
  return '';
}


registerActions({
  // The button sits inside the .alert; the positioned wrapper is its parent,
  // and that is what has to go, or an invisible box keeps covering the corner.
  'toast:dismiss':  (d, el) => el.closest('.alert')?.parentElement?.remove(),
  'toast:go':       (d, el) => {
    el.closest('.alert')?.parentElement?.remove();
    showPage(d.page);
    if (d.tab && typeof window.switchSettingsTab === 'function') window.switchSettingsTab(d.tab);
  },
});
