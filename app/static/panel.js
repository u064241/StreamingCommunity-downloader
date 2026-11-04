'use strict';

let _updateStatus = null;

async function checkForkUpdate(force = false) {
  try {
    _updateStatus = await api[force ? 'post' : 'get'](
      force ? '/api/updates/check' : '/api/updates', force ? {} : undefined);
    const badge = document.getElementById('update-badge');
    if (badge) badge.style.display = _updateStatus.status === 'update_available' ? '' : 'none';
  } catch (e) { /* update checks are best-effort */ }
  return _updateStatus;
}

function showForkUpdate() {
  if (!_updateStatus || _updateStatus.status !== 'update_available') return;
  document.getElementById('update-version').textContent =
    `Versione ${_updateStatus.version} disponibile (attuale ${_updateStatus.current_version})`;
  document.getElementById('update-notes').textContent = _updateStatus.notes || 'Nessuna nota pubblicata.';
  document.getElementById('update-release-link').href = _updateStatus.url || '#';
  const modal = document.getElementById('updates-modal');
  if (modal && window.bootstrap) bootstrap.Modal.getOrCreateInstance(modal).show();
}

registerActions({ 'updates:show': () => showForkUpdate() });

// The shell's live parts: the in-app notification bell and the sidebar's
// pending-requests badge. Both run on every page, which is why they are not
// in any page-*.js. Relies on core.js for escapeHtml/showToast/can and on
// api.js for the calls themselves.


// ── Sidebar "Richieste" badge ───────────────────────────────────────────────────
//
// Hidden entirely at zero, shown with the real count from the moment the app
// loads — not just after the user happens to open the queue page.

async function refreshQueueBadge() {
  if (!can('MANAGE_REQUESTS')) return;
  const badge = document.getElementById('queue-pending-count');
  if (!badge) return;
  try {
    const counts = await api.get('/api/requests/counts');
    const n = counts.action_required || 0;
    badge.textContent = n || '';
    badge.style.display = n ? '' : 'none';
  } catch (e) { /* leave the badge as it was */ }
}

// ── Notifications ──────────────────────────────────────────────────────────────

let _notifRefreshGeneration = 0;
let _notifDeleting = false;

async function refreshNotifications() {
  if (_notifDeleting) return;
  const generation = ++_notifRefreshGeneration;
  try {
    const payload = await api.get('/api/notifications');
    if (_notifDeleting || generation !== _notifRefreshGeneration) return;
    const badge = document.getElementById('notif-badge');
    badge.textContent = payload.unread || '';
    badge.style.display = payload.unread ? '' : 'none';

    const list = document.getElementById('notif-list');
    list.innerHTML = payload.items.length
      ? payload.items.map(n => `
          <div class="notif-item ${n.read_at ? '' : 'notif-unread'}">
            <i class="ti ${NOTIFICATION_ICONS[n.event] || 'ti-bell'}"></i>
            <div class="notif-body">
              <div class="notif-text">${escapeHtml(n.message)}</div>
              <div class="notif-time">${fmtDate(n.created_at)}</div>
            </div>
            <button class="notif-del" data-action="notif:delete" data-id="${n.id}"
                    title="Elimina questa notifica" aria-label="Elimina">
              <i class="ti ti-x"></i>
            </button>
          </div>`).join('')
      : '<div class="notif-empty">Nessuna notifica.</div>';

    // Nothing to act on means nothing to offer.
    const actions = document.getElementById('notif-actions');
    if (actions) actions.style.display = payload.items.length ? '' : 'none';
  } catch (e) { /* the bell just stays as it was */ }
}

function toggleNotifications() {
  const panel = document.getElementById('notif-panel');
  const opening = panel.style.display === 'none' || !panel.style.display;
  panel.style.display = opening ? 'block' : 'none';
  if (opening) refreshNotifications();
}

async function markAllNotificationsRead() {
  try { await api.post('/api/notifications/read', {}); }
  catch (e) { /* the badge will correct itself on the next poll */ }
  refreshNotifications();
}

async function _deleteNotifications(body) {
  if (_notifDeleting) return null;
  _notifDeleting = true;
  ++_notifRefreshGeneration; // Discard GET responses started before the deletion.
  const buttons = document.querySelectorAll('#notif-panel button');
  buttons.forEach(button => { button.disabled = true; });
  let result = null;
  try {
    result = await api.post('/api/notifications/delete', body);
  } catch (e) {
    showToast(errText(e, 'Eliminazione fallita'), 'danger');
  } finally {
    _notifDeleting = false;
    buttons.forEach(button => { button.disabled = false; });
  }
  await refreshNotifications();
  return result;
}

// One row: no confirmation. It is a single line of history, and asking every
// time would cost more than the mistake.
async function deleteNotification(id) {
  await _deleteNotifications({ ids: [id] });
}

async function clearAllNotifications() {
  const panel = document.getElementById('notif-panel');
  panel.style.display = 'none';
  const confirmed = await scConfirm('Eliminare tutte le notifiche?');
  panel.style.display = 'block';
  if (!confirmed) return;
  const result = await _deleteNotifications({});
  if (result) showToast('Notifiche eliminate', 'info');
}

document.addEventListener('click', event => {
  const panel = document.getElementById('notif-panel');
  if (!panel || panel.style.display === 'none') return;
  if (!event.target.closest('#notif-panel') && !event.target.closest('#notif-button')) {
    panel.style.display = 'none';
  }
});


// ── Delegated handlers ───────────────────────────────────────────────────────

registerActions({
  'notif:toggle':   () => toggleNotifications(),
  'notif:readAll':  () => markAllNotificationsRead(),
  'notif:clearAll': () => clearAllNotifications(),
  'notif:delete':   d => deleteNotification(Number(d.id)),
});
