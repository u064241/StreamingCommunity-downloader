/* StreamingCommunity Web Panel — page-watches.js */
//
// Followed series and anime. The follow button lives on the title page and
// calls in here; the list is its own page.

// ── Serie seguite ────────────────────────────────────────────────────────────

let _watches = [];

window.addEventListener('sc:language-changed', () => {
  renderWatchesList();
  for (const kind of ['page', 'anime', 'tv']) {
    const id = kind === 'page' ? 'th-follow-btn' : kind === 'anime' ? 'follow-anime-btn' : 'follow-tv-btn';
    const button = document.getElementById(id);
    if (button) {
      const following = !!button.dataset.following;
      button.innerHTML = `<i class="ti ${following ? 'ti-bell-check' : 'ti-bell-plus'} me-1"></i>${window.t(following ? 'title.following' : 'title.follow')}`;
      button.title = window.t(following ? 'title.followingHint' : 'title.followHint');
    }
  }
});

function fmtLastChecked(iso) {
  if (!iso) return window.t("dynamic.ui41");
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return window.t("dynamic.ui42");
  if (minutes < 60) return window.t('dynamic.checkedMinutes', {count: minutes});
  const hours = Math.round(minutes / 60);
  if (hours < 24) return window.t('dynamic.checkedHours', {count: hours});
  return window.t('dynamic.checkedDays', {count: Math.round(hours / 24)});
}

async function loadWatches() {
  const c = document.getElementById('watches-list');
  if (!c) return;
  try {
    // An approver sees every followed series, not only their own: arming a
    // series is their decision, and they cannot make it on a list that hides
    // the follows waiting for it.
    const data = await api.get(can('MANAGE_REQUESTS') ? '/api/watches' : '/api/watches/mine');
    _watches = data.watches || [];
    renderWatchesList();
  } catch (e) {
    c.innerHTML = `<p class="text-muted">${escapeHtml(errText(e, window.t("dynamic.ui43")))}</p>`;
  }
}

// Whether a new episode lands by itself or waits for an approver is the whole
// question this page answers, so it is what the head counts.
function renderWatchesStats() {
  const el = document.getElementById('watches-stats');
  if (!el) return;
  const auto = _watches.filter(w => _watchIsAutomatic(w)).length;
  const chips = [
    [_watches.length, window.t("dynamic.ui44"), 'pg-stat-live'],
    [auto, window.t("dynamic.ui45"), 'pg-stat-ok'],
    [_watches.length - auto, window.t("dynamic.ui46"), 'pg-stat-warn'],
  ];
  el.innerHTML = chips.map(([value, label, cls]) =>
    `<span class="pg-stat ${value ? cls : 'pg-stat-zero'}"><b>${value}</b><span>${label}</span></span>`
  ).join('');
}

// Computed by the server with the poller's rule: the owner's live DOWNLOAD
// permission is not something the browser can see for someone else's series.
function _watchIsAutomatic(w) {
  return !!w.automatic;
}

function renderWatchesList() {
  const c = document.getElementById('watches-list');
  if (!c) return;
  renderWatchesStats();
  if (!_watches.length) {
    c.innerHTML = `<div class="empty-panel">
      <i class="ti ti-bell-off"></i>
      <p>${window.t('dynamic.extra14')}</p>
      <p class="text-muted" style="font-size:12px;margin-top:6px">
        Apri una serie o un anime dalla ricerca e premi «Segui» per scaricare
        i nuovi episodi appena escono.
      </p>
    </div>`;
    return;
  }
  c.innerHTML = _watches.map(w => {
    const auto = _watchIsAutomatic(w);
    const badge = auto
      ? `<span class="badge bg-green-lt">${window.t('dynamic.extra12')}</span>`
      : `<span class="badge bg-yellow-lt">${window.t('dynamic.extra13')}</span>`;
    const kind = w.media_type === 'anime' ? 'Anime' : window.t("dynamic.ui47");
    const audio = w.audio_languages.length ? w.audio_languages.map(langName).join(', ') : window.t("dynamic.ui48");
    const subs = w.subtitle_languages.length ? w.subtitle_languages.map(langName).join(', ') : window.t("dynamic.ui49");
    const langButton = _canEditWatchLanguages(w)
      ? `<button class="btn btn-sm btn-outline-secondary" data-action="watch:languages" data-id="${w.id}"
                 title="${window.t('dynamic.hint5')}">
           <i class="ti ti-language me-1"></i>Lingue
         </button>`
      : '';
    // Arming is the approver's decision, and only worth offering where it would
    // change something: a series that already downloads by itself — its owner
    // holds DOWNLOAD — has nothing to approve. An armed one keeps its button,
    // or the flag could never be taken off again.
    const canArm = can('MANAGE_REQUESTS') && w.created_by !== null && (w.auto_approve || !auto);
    const armButton = !canArm ? '' : w.auto_approve
      ? `<button class="btn btn-sm btn-outline-secondary" data-action="watch:arm" data-id="${w.id}" data-on="0"
                 title="${window.t('dynamic.hint6')}">
           <i class="ti ti-bell-x me-1"></i>Togli automatico
         </button>`
      : `<button class="btn btn-sm btn-outline-success" data-action="watch:arm" data-id="${w.id}" data-on="1"
                 title="${window.t('dynamic.hint7')}">
           <i class="ti ti-bell-check me-1"></i>Approva automatico
         </button>`;
    // Followers who asked for tracks the series does not download. Shown to
    // whoever can act on it, with the button that does.
    const gap = w.missing_languages || {audio: [], subtitles: [], by: []};
    const gapParts = [
      gap.audio.length ? `audio ${gap.audio.map(langName).join(', ')}` : '',
      gap.subtitles.length ? `sottotitoli ${gap.subtitles.map(langName).join(', ')}` : '',
    ].filter(Boolean).join(window.t("dynamic.ui53"));
    const gapHtml = gapParts && _canEditWatchLanguages(w)
      ? `<div class="req-problem mt-1">
           <i class="ti ti-language"></i> ${gap.by.length ? escapeHtml(gap.by.join(', ')) : window.t("dynamic.ui50")}
           ${gap.by.length === 1 ? window.t("dynamic.ui51") : window.t("dynamic.ui52")}
           anche ${escapeHtml(gapParts)}
         </div>`
      : '';
    const mergeButton = gapHtml
      ? `<button class="btn btn-sm btn-outline-warning" data-action="watch:merge" data-id="${w.id}"
                 title="${window.t('dynamic.hint8')}">
           <i class="ti ti-arrows-join me-1"></i>Unisci lingue
         </button>`
      : '';
    const who = can('MANAGE_REQUESTS') && w.followers && w.followers.length
      ? `<span class="req-dot">·</span><i class="ti ti-user"></i> ${escapeHtml(w.followers.join(', '))}`
      : '';
    return `
      <div class="req-row">
        <div class="req-main">
          <div class="req-title">${escapeHtml(w.title)}${w.year ? ` <span class="text-muted">(${escapeHtml(w.year)})</span>` : ''}</div>
          <div class="req-meta">
            <i class="ti ti-device-tv"></i> ${kind}
            <span class="req-dot">·</span>
            <i class="ti ti-volume"></i> ${escapeHtml(audio)}
            <span class="req-dot">·</span>
            <i class="ti ti-badge-cc"></i> ${escapeHtml(subs)}
            <span class="req-dot">·</span>
            <i class="ti ti-refresh"></i> ${escapeHtml(fmtLastChecked(w.last_checked_at))}
            ${who}
          </div>
          ${gapHtml}
        </div>
        <div class="req-side">
          ${badge}
          <div class="req-actions">
            ${armButton}
            ${mergeButton}
            ${langButton}
            <button class="btn btn-sm btn-outline-secondary" id="watch-check-${w.id}"
                    data-action="watch:check" data-id="${w.id}"
                    title="${window.t('dynamic.hint9')}">
              <i class="ti ti-refresh me-1"></i>Controlla ora
            </button>
            <button class="btn btn-sm btn-outline-secondary" data-action="watch:unfollow" data-id="${w.id}">
              <i class="ti ti-bell-off me-1"></i>Non seguire più
            </button>
          </div>
        </div>
      </div>`;
  }).join('');
}

// Mirrors the server: one watch serves every follower, so its languages are the
// owner's call or an approver's. Without accounts the watch is the panel's.
function _canEditWatchLanguages(w) {
  return w.created_by === null || can('MANAGE_REQUESTS')
    || (!!_me && _me.user.id === w.created_by);
}

let _langWatchId = null;

// Offers the tracks the series really has, read from its latest episode on
// open. A language the watch keeps but the source no longer carries stays on
// the list, flagged: dropping it silently would hide why its episodes fail.
async function openWatchLanguages(watchId) {
  const w = _watches.find(x => x.id === watchId);
  if (!w) return;
  _langWatchId = watchId;
  document.getElementById('watch-lang-title').textContent = w.title;
  const audioBox = document.getElementById('watch-lang-audio');
  const subsBox = document.getElementById('watch-lang-subs');
  const saveBtn = document.getElementById('watch-lang-save');
  const loading = `<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>${window.t('dynamic.hint10')}</span>`;
  audioBox.innerHTML = loading;
  subsBox.innerHTML = '';
  saveBtn.disabled = true;
  showModal('watch-lang-modal');

  let tracks;
  try {
    tracks = await api.get(`/api/watches/${watchId}/tracks`);
  } catch (e) {
    if (_langWatchId !== watchId) return;
    audioBox.innerHTML = `<div class="alert alert-danger mb-0">${escapeHtml(errText(e, 'Tracce non leggibili dalla fonte'))}</div>`;
    return;
  }
  if (_langWatchId !== watchId) return;

  const boxes = (available, current, cls) => {
    const codes = [...new Set([...available, ...current])];
    if (!codes.length) return `<span class="text-muted">${window.t('dynamic.extra15')}</span>`;
    return codes.map(c => {
      const gone = !available.includes(c);
      return `<label class="me-2 mb-1" style="cursor:pointer"
                     ${gone ? 'title="Non presente nell\'ultimo episodio pubblicato"' : ''}>
        <input type="checkbox" class="${cls} me-1" value="${escapeHtml(c)}" ${current.includes(c) ? 'checked' : ''}>
        <span class="badge ${gone ? 'bg-red-lt' : 'bg-blue-lt'}">${escapeHtml(langName(c))}${gone ? ' · non disponibile' : ''}</span></label>`;
    }).join('');
  };
  audioBox.innerHTML = boxes(tracks.audio, w.audio_languages, 'watch-lang-audio-check');
  subsBox.innerHTML = boxes(tracks.subtitles, w.subtitle_languages, 'watch-lang-subs-check');
  saveBtn.disabled = false;
}

async function saveWatchLanguages() {
  const picked = cls => [...document.querySelectorAll(`.${cls}:checked`)].map(c => c.value);
  const audio = picked('watch-lang-audio-check');
  if (!audio.length) { showToast(window.t("dynamic.ui54"), 'warning'); return; }
  try {
    await api.put(`/api/watches/${_langWatchId}/languages`, {
      audio_languages: audio, subtitle_languages: picked('watch-lang-subs-check'),
    });
  } catch (e) { showToast(errText(e), 'danger'); return; }
  hideModal('watch-lang-modal');
  showToast(window.t("dynamic.ui55"), 'success');
  loadWatches();
}

async function mergeWatchLanguages(watchId) {
  try {
    await api.post(`/api/watches/${watchId}/merge-languages`);
  } catch (e) { showToast(errText(e), 'danger'); return; }
  showToast(window.t("dynamic.ui56"), 'success');
  loadWatches();
}

// The follow toggle lives in two modals whose contexts are shaped differently,
// so both are flattened to the same shape here rather than in each caller.
function _followTarget(kind) {
  if (kind === 'page') {
    // The title page, which serves both sources from one screen.
    const anime = _tp.type === 'anime';
    return {
      btnId: 'th-follow-btn',
      source: anime ? 'animeunity' : 'streamingcommunity',
      media_type: anime ? 'anime' : 'tv',
      external_id: String(_tp.id ?? ''),
      title: _tp.name,
      slug: _tp.slug,
      year: _tp.year,
      poster: _tp.poster,
      anime_type: _tp.animeType,
      audio_languages: _tpPicked('audio'),
      subtitle_languages: _tpPicked('subs'),
      // A film has no next episode to wait for.
      followable: _tp.type !== 'movie' && (!anime || !isAnimeFilm(_tp.animeType)),
    };
  }
  if (kind === 'anime') {
    return {
      btnId: 'follow-anime-btn',
      source: 'animeunity',
      media_type: 'anime',
      external_id: String(_animeCtx.animeId ?? ''),
      title: _animeCtx.animeName,
      year: _animeCtx.animeYear,
      anime_type: _animeCtx.animeType,
      audio_languages: _animeCtx.audioLangs || [],
      subtitle_languages: _animeCtx.subLangs || [],
      // A one-shot anime film has no next episode to wait for.
      followable: !isAnimeFilm(_animeCtx.animeType),
    };
  }
  return {
    btnId: 'follow-tv-btn',
    source: 'streamingcommunity',
    media_type: 'tv',
    external_id: String(_epCtx.tvId ?? ''),
    title: _epCtx.tvName,
    slug: _epCtx.slug,
    year: _epCtx.year,
    poster: _epCtx.poster,
    audio_languages: _epCtx.audioLangs || [],
    subtitle_languages: _epCtx.subLangs || [],
    followable: true,
  };
}

function _renderFollowButton(kind, following, busy = false) {
  const target = _followTarget(kind);
  const btn = document.getElementById(target.btnId);
  if (!btn) return;
  // Available with or without Jellyfin: an ownerless watch downloads directly,
  // the way everything else does when the panel runs without accounts.
  if (!target.followable || !(can('REQUEST') || can('DOWNLOAD'))) {
    btn.style.display = 'none';
    return;
  }
  btn.style.display = '';
  btn.disabled = busy;
  btn.dataset.following = following ? '1' : '';
  // No margin utilities: the header is a flex row with a gap, and the title
  // takes the free space.
  btn.className = 'btn btn-sm flex-shrink-0 ' + (following ? 'btn-success' : 'btn-outline-secondary');
  btn.innerHTML = following
    ? '<i class="ti ti-bell-check me-1"></i>Seguita'
    : '<i class="ti ti-bell-plus me-1"></i>Segui';
  btn.title = following
    ? 'I nuovi episodi vengono cercati automaticamente. Premi per smettere.'
    : 'Cerca automaticamente i nuovi episodi di questa serie';
}

async function checkWatchStatus(kind) {
  const target = _followTarget(kind);
  if (!target.followable || !target.external_id) { _renderFollowButton(kind, false); return; }
  try {
    const data = await api.get('/api/watches/status', {
      source: target.source, media_type: target.media_type, external_id: target.external_id,
    });
    _renderFollowButton(kind, !!data.followed_by_me);
  } catch (e) { _renderFollowButton(kind, false); }
}

async function toggleFollowSeries(kind) {
  const target = _followTarget(kind);
  const btn = document.getElementById(target.btnId);
  const following = !!(btn && btn.dataset.following);
  _renderFollowButton(kind, following, true);

  try {
    if (following) {
      const status = await api.get('/api/watches/status', {
        source: target.source, media_type: target.media_type, external_id: target.external_id,
      });
      await api.del(`/api/watches/${status.watch_id}`);
      _renderFollowButton(kind, false);
      showToast(window.t("dynamic.ui57"), 'success');
    } else {
      let data;
      try {
        data = await api.post('/api/watches', target);
      } catch (e) {
        _renderFollowButton(kind, false);
        showToast(errText(e, window.t("dynamic.ui58")), 'danger');
        return;
      }
      _renderFollowButton(kind, true);
      // Only true for someone who can start downloads. Without that permission
      // each new episode becomes a request an approver has to accept, and
      // saying otherwise sets up a wait for something that never arrives.
      // The languages are named because they are the ones the watch keeps —
      // taken from the pickers as they stood, or, when the series was already
      // followed, the ones it had.
      const langs = (data.audio_languages || []).map(langName).join(', ') || window.t("dynamic.ui48");
      const differs = !!data.languages_differ;
      showToast((can('DOWNLOAD')
        ? window.t("dynamic.ui59")
        : window.t("dynamic.ui60"))
        + (differs
          ? ` — la serie era già seguita con audio ${langs}: le lingue in più che hai scelto sono state segnalate a un amministratore`
          : ` (audio: ${langs}; modificabile da «Serie seguite»)`),
        differs ? 'warning' : 'success');
    }
    if (document.getElementById('page-watches').style.display !== 'none') loadWatches();
  } catch (e) {
    _renderFollowButton(kind, following);
    showToast(window.t("dynamic.ui61"), 'danger');
  }
}

// The automatic check runs every few hours; this is for when an episode has
// just dropped and waiting for the next cycle makes no sense.
async function checkWatchNow(watchId) {
  const btn = document.getElementById(`watch-check-${watchId}`);
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="ti ti-loader-2 ti-spin me-1"></i>Controllo...'; }
  try {
    let data;
    try {
      data = await api.post(`/api/watches/${watchId}/check`);
    } catch (e) {
      showToast(errText(e, window.t("dynamic.ui62")), 'danger');
      return;
    }
    if (data.new) {
      showToast(
        data.new === 1 ? window.t("dynamic.ui63") : window.t('dynamic.newEpisodes', {count: data.new}),
        'success',
      );
    } else {
      showToast(window.t("dynamic.ui64"), 'info');
    }
  } catch (e) {
    showToast(window.t("dynamic.ui61"), 'danger');
  } finally {
    // Redraws the row, which also refreshes "controllata ora".
    await loadWatches();
  }
}

// Arming a series before an episode exists, which is the whole point: waiting
// for the first request means waiting for the source to publish.
async function setWatchAutoApprove(watchId, enabled) {
  const watch = _watches.find(w => w.id === watchId);
  const name = watch ? watch.title : watchId;
  if (!enabled && !await scConfirm(window.t("watches.confirmQueue", {name}))) return;
  try {
    try {
      await api.post(`/api/watches/${watchId}/auto-approve`, {enabled});
    } catch (e) { showToast(errText(e, window.t("dynamic.ui65")), 'danger'); return; }
    showToast(enabled
      ? window.t("watches.autoEnabled", {name})
      : window.t("watches.autoDisabled", {name}), 'success');
    await loadWatches();
  } catch (e) {
    showToast(window.t("dynamic.ui61"), 'danger');
  }
}

async function unfollowWatch(watchId) {
  const watch = _watches.find(w => w.id === watchId);
  if (!await scConfirm(window.t("watches.confirmUnfollow", {name: watch ? watch.title : watchId}))) return;
  try {
    await api.del(`/api/watches/${watchId}`);
    showToast(window.t("dynamic.ui57"), 'success');
    await loadWatches();
  } catch (e) { showToast(errText(e), 'danger'); }
}


// ── Delegated handlers ───────────────────────────────────────────────────────

registerActions({
  'watch:reload':   () => loadWatches(),
  'watch:arm':      d => setWatchAutoApprove(Number(d.id), d.on === '1'),
  'watch:check':    d => checkWatchNow(Number(d.id)),
  'watch:unfollow': d => unfollowWatch(Number(d.id)),
  'watch:languages': d => openWatchLanguages(Number(d.id)),
  'watch:merge':     d => mergeWatchLanguages(Number(d.id)),
  'watch:saveLanguages': () => saveWatchLanguages(),
});
