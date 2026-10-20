/* Cortex Vault behaviour (SPEC §8), "The Brass Index" look: the combobox search box, selection and the detail view,
   the type filter, recent searches, the theme toggle, the dial, uploads and the upload bar.

   The decisions (when to search, what Enter and Esc do, which results the type filter and "Show more" leave
   visible, what the count line says, how recent searches are kept) are pure functions, exported for the tests
   (Node). The browser part wires them to the page. It never builds HTML from strings: the server escapes
   everything into the page, and this file only creates elements and sets textContent, attributes and numbers.
   Recent searches live in this browser's localStorage only: they are never sent anywhere. A demo visitor's upload
   session is the one their browser's cookie names (HttpOnly: this file never sees it); a new session's page trades
   the one-time ticket it is given for that cookie, and nothing here names a session. */
(function (root, factory) {
  const api = factory(typeof window !== 'undefined' ? window : null);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.Cortex = api;
})(typeof window !== 'undefined' ? window : null, function (win) {
  'use strict';

  const MIN_CHARS = 2;     // shorter text doesn't search
  const DELAY_MS = 300;    // after the last key
  const PAGE = 20;         // results shown before "Show N more"
  const SAVE_MS = 1500;    // results on screen this long, box unchanged: a recent search
  const RECENT_MAX = 5;
  const RECENT_KEY = 'cv-recent-searches';
  const THEME_KEY = 'cv-theme';
  const NOTCH_DEG = 36;    // the dial turns one notch per search
  const PLURALS = { screenshot: 'Screenshots', pdf: 'PDFs', text: 'Text files', photo: 'Photos', doc: 'Documents',
    slides: 'Slides' };

  const normalize = (text) => String(text || '').trim().split(/\s+/).filter(Boolean).join(' ');

  /* What typing should search for now: the normalised text, '' to go back home, or null for nothing (too short,
     or the same as the last search). */
  function searchFor(text, last) {
    const q = normalize(text);
    if (q === last) return null;
    if (q === '') return '';
    return q.length >= MIN_CHARS ? q : null;
  }

  /* Enter opens the selected result only if the results on screen belong to the text in the box; otherwise it
     searches for that text right away and opens nothing. */
  function enterAction(text, shownQuery, hasSelection) {
    const q = normalize(text);
    if (q !== '' && q === shownQuery) return hasSelection ? 'open' : 'none';
    return q.length >= MIN_CHARS ? 'search' : 'none';
  }

  /* Esc: closes the drawer or the phone sheet if one is open, and only that; otherwise clears the box. */
  function escAction(overlayOpen, text) {
    if (overlayOpen) return 'close-sheet';
    return normalize(text) ? 'clear' : 'none';
  }

  /* Which results show under the type filter (`filter`) and "Show more" (`expanded`): the top `page` matching
     ones by fused rank (`ranks`, one per result in list order); and how many more the button offers. */
  function visibility(types, ranks, filter, expanded, page = PAGE) {
    const matching = types.map((type) => filter === 'all' || type === filter);
    const top = new Set(ranks.filter((r, i) => matching[i]).sort((a, b) => a - b).slice(0, page));
    const visible = matching.map((m, i) => m && (expanded || top.has(ranks[i])));
    const total = matching.filter(Boolean).length;
    return { visible, more: expanded ? 0 : Math.max(0, total - page) };
  }

  /* The count line after its number: "matches · 36 weaker", "match in Photos" (the server's views.count_words). */
  function countWords(matches, weaker, label) {
    let words = matches === 1 ? 'match' : 'matches';
    if (label) words += ` in ${label}`;
    return weaker ? `${words} · ${weaker} weaker` : words;
  }

  /* Recent searches: newest first, at most RECENT_MAX, one entry per text (case aside). */
  function remember(list, q, max = RECENT_MAX) {
    const query = normalize(q);
    if (query.length < MIN_CHARS) return list.slice(0, max);
    return [query, ...list.filter((x) => x.toLowerCase() !== query.toLowerCase())].slice(0, max);
  }

  function forget(list, q) { return list.filter((x) => x !== q); }

  /* What localStorage held, made safe: only short strings, at most RECENT_MAX. */
  function parseRecent(raw) {
    try {
      const value = JSON.parse(raw || '[]');
      if (!Array.isArray(value)) return [];
      return value.filter((x) => typeof x === 'string').map(normalize)
        .filter((x) => x.length >= MIN_CHARS && x.length <= 200).slice(0, RECENT_MAX);
    } catch (e) {
      return [];
    }
  }

  /* ------------------------------------------------------------------ the upload bar (SPEC §8) */
  /* Its line for a stage: real numbers only, never a time estimate. `upload` is the browser-to-server transfer
     (`pct` null when the browser can't measure it), `list` and `read` the server's two indexing phases. */
  function barText(state) {
    const { stage, done = 0, total = 0, pct = null } = state;
    const at = Math.min(done + 1, total);
    if (stage === 'upload') {
      return `Uploading ${at} of ${total} file${total === 1 ? '' : 's'}${pct === null ? '' : ` · ${pct}%`}`;
    }
    if (stage === 'list') return total ? `Listing ${at} of ${total} file${total === 1 ? '' : 's'}` : 'Listing files';
    if (stage === 'read') return total ? `Reading ${at} of ${total} · names searchable now` : 'Reading · names searchable now';
    if (stage === 'done') return state.summary ? `Upload done: ${state.summary}` : 'Upload done';
    return state.text || '';
  }

  /* The bar's value from 0 to 1, or null when there's no real number (an indeterminate bar). */
  function barValue(state) {
    if (state.stage === 'upload') return state.pct === null || state.pct === undefined ? null : state.pct / 100;
    if (state.stage === 'list' || state.stage === 'read') return state.total ? state.done / state.total : null;
    return state.stage === 'done' ? 1 : null;
  }

  /* Bytes sent as a whole percentage, never 100 before the last byte. */
  function percent(sent, total) {
    return total > 0 ? Math.min(100, Math.floor((100 * sent) / total)) : 0;
  }

  /* The results on screen are older than the index: files were listed or read since they were found. */
  function stale(resultsVersion, indexVersion) {
    return Number(indexVersion) > Number(resultsVersion);
  }

  /* A /detail response (Copy text, the boxes) lands only where it was asked for: `asked` is the request (its
     ticket, file id and render), `now` the page: the latest ticket, the selected row, the render on screen, and
     the file and render of the preview in the pane. Anything older or for another row is dropped. */
  function detailFits(asked, now) {
    return asked.ticket === now.ticket && asked.id === now.selected && asked.id === now.paneFile
      && asked.render === now.render && asked.render === now.paneRender;
  }

  /* The Archive's numbers for one scope, from the status part's counts for every scope (JSON): the files per
     type and their total, or null when there are none for that scope. Only whole numbers ≥ 0 are taken. */
  function scopeCounts(raw, scope) {
    let all;
    try { all = JSON.parse(raw || 'null'); } catch (e) { return null; }
    const counts = all && typeof all === 'object' ? all[scope] : null;
    if (!counts || typeof counts !== 'object') return null;
    const types = {};
    Object.keys(PLURALS).forEach((t) => {
      const n = Number(counts[t]);
      types[t] = Number.isInteger(n) && n >= 0 ? n : 0;
    });
    return { types, total: Object.values(types).reduce((a, n) => a + n, 0) };
  }

  /* The full-page overlay while files are dragged over the page (demo): how many, when the browser says. */
  function dropText(n) {
    return n > 0 ? `Drop to add ${n} file${n === 1 ? '' : 's'}` : 'Drop to add files';
  }

  /* ------------------------------------------------------------------ the upload tray (demo) */
  /* A file name's extension as the server reads it (Python's PurePath.suffix, lowercased): '' for none, and for a
     name that starts or ends with its only dot. */
  function suffix(name) {
    const base = String(name || '').split(/[\\/]/).pop();
    const i = base.lastIndexOf('.');
    return i > 0 && i < base.length - 1 ? base.slice(i).toLowerCase() : '';
  }

  /* The tray's checks before anything is sent, mirroring the server's (vault/session.py `_add`, in its order): a
     folder, the type, the size, then the session's file count and total over the files that will be added. One
     reason per file, '' for a file that will be added; `limits` is the status part's (SessionManager.limits). The
     server checks everything again, and also catches what the browser can't (a file already uploaded, an Office
     file too large unpacked). */
  function trayCheck(files, limits) {
    const exts = new Set(limits.exts || []);
    let count = Number(limits.files) || 0;
    let total = Number(limits.bytes) || 0;
    return files.map((f) => {
      const ext = suffix(f.name);
      if (f.folder) return 'a folder: add the files in it instead';
      if (!exts.has(ext)) return `file type not supported (${ext || 'no extension'})`;
      if (f.size > limits.maxBytes) return `larger than ${limits.maxBytesText}`;
      if (count >= limits.maxFiles) return `over the ${limits.maxFiles}-file limit`;
      if (total + f.size > limits.maxTotal) return `over the ${limits.maxTotalText} per-session total`;
      count += 1;
      total += f.size;
      return '';
    });
  }

  const TYPE_LABELS = { '.pdf': 'PDF', '.docx': 'Word document', '.pptx': 'PowerPoint', '.txt': 'Text',
    '.md': 'Markdown', '.png': 'PNG image', '.jpg': 'JPEG image', '.jpeg': 'JPEG image', '.webp': 'WebP image',
    '.heic': 'HEIC photo' };
  function typeLabel(name) {
    const ext = suffix(name);
    return TYPE_LABELS[ext] || (ext ? `${ext.slice(1).toUpperCase()} file` : 'No extension');
  }

  /* A size as the rest of the page writes it (views.human_bytes). */
  function fileSize(n) {
    let size = Number(n) || 0;
    for (const unit of ['bytes', 'KB', 'MB', 'GB']) {
      if (size < 1024 || unit === 'GB') {
        return unit === 'bytes' || unit === 'KB' ? `${Math.round(size)} ${unit}` : `${size.toFixed(1)} ${unit}`;
      }
      size /= 1024;
    }
    return '';
  }

  function uploadLabel(n) {
    return n > 0 ? `Upload ${n} file${n === 1 ? '' : 's'}` : 'Nothing to upload';
  }

  /* Delete all uploads and Delete selected ask first (SPEC §8). */
  function confirmDelete(n) {
    return `Delete all ${n} file${n === 1 ? '' : 's'} you uploaded? This can't be undone.`;
  }

  function confirmDeleteSome(k) {
    return `Delete ${k} file${k === 1 ? '' : 's'}? This can't be undone.`;
  }

  function deleteLabel(k) {
    return `Delete selected (${k})`;
  }

  const api = { MIN_CHARS, DELAY_MS, PAGE, SAVE_MS, RECENT_MAX, PLURALS, normalize, searchFor, enterAction, escAction,
    visibility, countWords, remember, forget, parseRecent, barText, barValue, percent, stale, detailFits, scopeCounts,
    confirmDelete, confirmDeleteSome, deleteLabel, dropText, suffix, trayCheck, typeLabel, fileSize, uploadLabel };
  if (!win) return api;

  /* ------------------------------------------------------------------ browser */
  const doc = win.document;
  const wide = win.matchMedia('(min-width: 1200px)');   // the detail view is a column; below, a drawer or sheet
  const phone = win.matchMedia('(max-width: 760px)');
  const S = {
    top: null, main: null, topTrigger: null, mainTrigger: null, upload: null,
    scope: 'Both', last: '', shown: null, timer: 0, filter: 'all', expanded: false,
    selected: null, ticket: 0, detailAbort: null, toastSeen: '',
    text: '', toast: null, toastTimer: 0, saveTimer: 0, turn: 0, recent: [], status: null,
    uploadsFull: false, busyText: '', lastRender: null,
    transfer: null, notSent: 0, dismissed: '', reportOpen: false, said: '', homeFor: '', awaiting: null,
    claiming: null, claimed: '', forgotId: '', dropTimer: 0, tray: [], trayReturn: null,
    manageFiles: null, manageSel: new Set(), manageReturn: null, manageBusy: false, removedSeen: '',
  };

  const $ = (sel, el) => (el || doc).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || doc).querySelectorAll(sel));
  const input = () => $('#cv-q');
  const viewRoot = () => (S.main ? $('[data-view]', S.main) : null);
  const options = () => (viewRoot() ? $$('[role="option"]', viewRoot()) : []);
  const visibleOptions = () => options().filter((o) => !o.hidden);
  const pane = () => (viewRoot() ? $('.cv-detail', viewRoot()) : null);
  const detailOpen = () => !!(pane() && pane().classList.contains('is-open'));
  const editable = (el) => !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const effectiveScope = () => (phone.matches ? 'Both' : S.scope);
  const store = {
    get(key) { try { return win.localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { win.localStorage.setItem(key, value); } catch (e) { /* storage blocked */ } },
    del(key) { try { win.localStorage.removeItem(key); } catch (e) { /* storage blocked */ } },
  };

  /* ------------------------------------------------------------------ the session cookie (demo) */
  /* A JSON POST to one of the app's own session routes (the server takes nothing else there). */
  function post(path, body) {
    return win.fetch(path, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }

  /* After the upload that created the session: trade its one-time ticket for the session's cookie. Events wait for
     it (`emit`), so the next search or upload already carries the cookie. */
  function claim(ticket) {
    if (!ticket || ticket === S.claimed) return;
    S.claimed = ticket;
    const done = post('session/claim', { ticket })
      .catch(() => post('session/claim', { ticket }))  // a network blip: once more
      .catch(() => null)
      .then(() => { if (S.claiming === done) S.claiming = null; });
    S.claiming = done;
  }

  /* Every event to the server goes after a pending claim. */
  function emit(trigger, name, data) {
    if (!trigger) return;
    if (S.claiming) S.claiming.then(() => trigger(name, data)); else trigger(name, data);
  }

  function say(message) {
    if (!S.toast) {
      S.toast = doc.createElement('div');
      S.toast.className = 'cv-toast';
      S.toast.setAttribute('role', 'status');
      S.toast.setAttribute('aria-live', 'polite');
      doc.body.appendChild(S.toast);
    }
    S.toast.textContent = message;
    S.toast.classList.add('is-on');
    win.clearTimeout(S.toastTimer);
    S.toastTimer = win.setTimeout(() => S.toast.classList.remove('is-on'), 1800);
  }

  /* The hero and the suggestions collapse while the box holds text (SPEC §8). */
  function syncQueryMode() {
    const box = input();
    const typed = !!box && normalize(box.value) !== '';
    doc.body.classList.toggle('cv-query', typed);
    const clear = S.top && $('[data-act="clear"]', S.top);
    if (clear) clear.hidden = !typed;
  }

  function turnDial() {
    S.turn += NOTCH_DEG;
    doc.documentElement.style.setProperty('--cv-turn', `${S.turn}deg`);
  }

  function search(q, select) {
    S.last = q;
    win.clearTimeout(S.saveTimer);
    if (q) turnDial();
    emit(S.topTrigger, 'submit', { q, scope: effectiveScope(), select: select || '' });
  }

  function typed() {
    const q = searchFor(input().value, S.last);
    if (q !== null) search(q);
  }

  function setQuery(q) {
    const box = input();
    box.value = q;
    box.focus();
    win.clearTimeout(S.timer);
    syncQueryMode();
    search(normalize(q));
  }

  function clearBox() {
    input().value = '';
    win.clearTimeout(S.timer);
    syncQueryMode();
    search('');
  }

  function searchNow() {
    const q = normalize(input().value);
    win.clearTimeout(S.timer);
    if (q.length >= MIN_CHARS && q !== S.last) search(q);
    input().focus();
  }

  function setScope(scope, rerun) {
    S.scope = scope;
    if (S.main) $$('[data-scope-btn]', S.main).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.scopeBtn === scope)));
    if (rerun) search(normalize(input().value));
  }

  /* ------------------------------------------------------------------ recent searches (this browser only) */
  function renderRecent() {
    const list = S.top && $('[data-recent]', S.top);
    if (!list) return;
    list.replaceChildren(...S.recent.map((q) => {
      const chip = doc.createElement('span');
      chip.className = 'cv-chip cv-chip--recent';
      const run = doc.createElement('button');
      run.type = 'button';
      run.className = 'cv-chip__q';
      run.dataset.q = q;
      run.textContent = q;
      const drop = doc.createElement('button');
      drop.type = 'button';
      drop.className = 'cv-chip__x';
      drop.dataset.forget = q;
      drop.setAttribute('aria-label', `Remove “${q}” from recent searches`);
      drop.textContent = '×';
      chip.append(run, drop);
      return chip;
    }));
    const any = S.recent.length > 0;
    list.hidden = !any;
    $('[data-examples]', S.top).hidden = any;
    $('[data-act="forget-all"]', S.top).hidden = !any;
    $('[data-queries-label]', S.top).textContent = any ? 'Recent searches' : 'Try a search';
  }

  function keepRecent(list) {
    S.recent = list;
    if (list.length) store.set(RECENT_KEY, JSON.stringify(list)); else store.del(RECENT_KEY);
    renderRecent();
  }

  /* Save the search on screen, if the box still holds it (results stayed up, or one was opened). */
  function rememberShown() {
    win.clearTimeout(S.saveTimer);
    if (S.shown && normalize(input().value) === S.shown) keepRecent(remember(S.recent, S.shown));
  }

  /* ------------------------------------------------------------------ theme */
  const isDark = () => doc.body.classList.contains('dark');

  function syncThemeButton() {
    const b = S.top && $('[data-act="theme"]', S.top);
    if (b) b.setAttribute('aria-pressed', String(isDark()));
  }

  function toggleTheme() {
    const theme = isDark() ? 'light' : 'dark';
    doc.body.classList.toggle('dark', theme === 'dark');
    store.set(THEME_KEY, theme);
    try {
      const u = new URL(win.location.href);
      u.searchParams.set('__theme', theme);
      win.history.replaceState(null, '', u);
    } catch (e) { /* the theme still switched */ }
    syncThemeButton();
  }

  /* ------------------------------------------------------------------ selection and the detail view */
  function current() {
    return visibleOptions().find((o) => o.getAttribute('aria-selected') === 'true') || null;
  }

  function select(opt, scroll) {
    if (!opt) return;
    options().forEach((o) => o.setAttribute('aria-selected', String(o === opt)));
    input().setAttribute('aria-activedescendant', opt.id);
    if (scroll) opt.scrollIntoView({ block: 'nearest' });
    fillDetail(opt);
  }

  /* Empty the pane and make any /detail response still on its way stale. */
  function clearPane(body) {
    S.ticket += 1;
    if (S.detailAbort) S.detailAbort.abort();
    S.detailAbort = null;
    S.selected = null;
    S.text = '';
    if (!body) return;
    body.replaceChildren();
    ['file', 'render', 'detailFor'].forEach((k) => { delete body.dataset[k]; });
  }

  /* The selected row's preview: a clone of its hidden copy in this render, found by file id and the render's
     token (views.preview), so the header, the picture and the text are all that row's. With no copy for it the
     pane stays empty rather than show another file. Then /detail adds Copy text and the boxes, if still wanted. */
  function fillDetail(opt) {
    const root = viewRoot();
    const body = root && $('[data-pv]', root);
    clearPane(body);
    if (!body || !opt) return;
    const id = opt.dataset.id;
    const render = root.dataset.render;
    S.selected = id;
    const src = $(`[data-previews] > [data-preview="${id}"][data-render="${render}"]`, root);
    if (!src) return;
    body.replaceChildren(...Array.from(src.childNodes, (n) => n.cloneNode(true)));
    $$('img[data-src]', body).forEach((img) => { img.src = img.dataset.src; img.removeAttribute('data-src'); });
    body.dataset.file = id;
    body.dataset.render = render;
    const asked = { ticket: S.ticket, id, render };
    const abort = typeof win.AbortController === 'function' ? new win.AbortController() : null;
    S.detailAbort = abort;
    const copy = $('[data-act="copy"]', body);
    win.fetch(opt.dataset.detail, { credentials: 'same-origin', signal: abort ? abort.signal : undefined })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const now = viewRoot();
        if (!data || !now || !detailFits(asked, { ticket: S.ticket, selected: S.selected, render: now.dataset.render,
          paneFile: body.dataset.file, paneRender: body.dataset.render })) return;
        S.text = typeof data.text === 'string' ? data.text : '';
        if (copy) copy.disabled = !S.text;
        addBoxes(body, Array.isArray(data.boxes) ? data.boxes : []);
        body.dataset.detailFor = `${id}:${render}`;
      })
      .catch(() => {});  // aborted (a newer request), or the network: the preview itself is already right
  }

  function addBoxes(body, boxes) {
    const frame = $('.cv-frame', body);
    if (!frame) return;
    const pct = (v) => `${Math.min(100, Math.max(0, Number(v) || 0))}%`;
    let first = null;
    boxes.slice(0, 10).forEach((b) => {
      const span = doc.createElement('span');
      span.className = b.kind === 'passage' ? 'cv-hl cv-hl--passage' : 'cv-hl';
      span.style.left = pct(b.x);
      span.style.top = pct(b.y);
      span.style.width = pct(b.w);
      span.style.height = pct(b.h);
      frame.appendChild(span);
      if (!first) first = span;
    });
    const media = $('.cv-detail__media', body);
    const img = $('img', frame);
    const reveal = () => {
      if (first && media && media.scrollHeight > media.clientHeight) {
        media.scrollTop = Math.max(0, first.offsetTop - media.clientHeight / 3);
      }
    };
    if (img && !img.complete) img.addEventListener('load', reveal, { once: true }); else reveal();
  }

  function openDetail() {
    const d = pane();
    if (!d || wide.matches) return;  // a column on wide screens: always there
    d.classList.add('is-open');
    const backdrop = $('.cv-backdrop', viewRoot());
    if (backdrop) backdrop.hidden = false;
    doc.body.classList.add('cv-overlay');
  }

  function closeDetail() {
    const d = pane();
    if (d) d.classList.remove('is-open');
    const backdrop = viewRoot() && $('.cv-backdrop', viewRoot());
    if (backdrop) backdrop.hidden = true;
    doc.body.classList.remove('cv-overlay');
  }

  function open(opt) {
    if (!opt) return;
    rememberShown();
    const title = opt.dataset.title || 'the file';
    if (opt.dataset.open) {
      win.location.assign(opt.dataset.open);  // demo: /file/<id>, sent as an attachment
      say(`Downloading ${title}`);
    } else if (S.mainTrigger) {
      emit(S.mainTrigger, 'click', { action: 'open', file_id: Number(opt.dataset.id) });  // local: vault/platform.py
      say(`Opening ${title}`);
    }
  }

  function copy(text, done) {
    if (!text) return;
    const fallback = () => {
      const area = doc.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.opacity = '0';
      doc.body.appendChild(area);
      area.select();
      try { doc.execCommand('copy'); say(done); } catch (e) { /* nothing to do */ }
      area.remove();
    };
    if (win.navigator.clipboard && win.isSecureContext) {
      win.navigator.clipboard.writeText(text).then(() => say(done), fallback);
    } else {
      fallback();
    }
  }

  /* ------------------------------------------------------------------ the type filter and Show more */
  function setFilter(filter) {
    S.filter = filter;
    S.expanded = false;
    applyView();
  }

  function applyView() {
    const root = viewRoot();
    if (!root) return;
    const chosen = $(`[data-filter="${S.filter}"]`, root);
    if (!chosen || chosen.disabled) S.filter = 'all';  // that type left the archive (an expired session)
    $$('[data-filter]', root).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.filter === S.filter)));
    if (root.dataset.view === 'home') applyHome(root);
    else if (root.dataset.view === 'results') applyResults(root);
  }

  function applyHome(root) {
    let shown = 0;
    $$('[data-type][data-top]', root).forEach((row) => {
      const on = S.filter === 'all' ? row.dataset.top === '1' : row.dataset.type === S.filter && shown < RECENT_MAX;
      row.hidden = !on;
      if (on) shown += 1;
    });
    const none = $('[data-recent-none]', root);
    if (none) none.hidden = shown > 0;
  }

  function applyResults(root) {
    const opts = options();
    const page = Number(root.dataset.page) || PAGE;
    const { visible, more } = visibility(opts.map((o) => o.dataset.type), opts.map((o) => Number(o.dataset.rank)),
      S.filter, S.expanded, page);
    opts.forEach((o, i) => { o.hidden = !visible[i]; });
    $$('[data-group]', root).forEach((g) => { g.hidden = !$$('[role="option"]', g).some((o) => !o.hidden); });
    const of = (o) => S.filter === 'all' || o.dataset.type === S.filter;
    const n = opts.filter((o) => of(o) && !('weaker' in o.dataset)).length;
    const w = opts.filter((o) => of(o) && 'weaker' in o.dataset).length;
    const label = S.filter === 'all' ? '' : PLURALS[S.filter] || '';
    const num = $('[data-count-n]', root);
    if (num) num.textContent = String(n);
    const words = $('[data-count-words]', root);
    if (words) words.textContent = countWords(n, w, label);
    const clear = $('.cv-count [data-clear-filter]', root);
    if (clear) clear.hidden = S.filter === 'all';
    const none = $('[data-filter-empty]', root);
    if (none) {
      none.hidden = S.filter === 'all' || n + w > 0;
      $('[data-filter-empty-text]', none).textContent = `No ${label.toLowerCase()} among the matches for “${S.shown}”`;
    }
    const btn = $('[data-more-btn]', root);
    if (btn) {
      btn.hidden = more === 0;
      btn.textContent = `Show ${more} more`;
    }
    if (!current()) select(visibleOptions()[0], false);
  }

  /* ------------------------------------------------------------------ status, progress and the dial */
  function paintStatus() {
    const st = S.status;
    if (!st) return;
    const slot = S.top && $('[data-status]', S.top);
    if (slot) {
      $('[data-status-text]', slot).textContent = st.text;
    }
    const dial = S.top && $('[data-dial-state]', S.top);
    if (dial) dial.textContent = st.dial;
    const line = $('[data-index-line]');
    if (line) line.textContent = st.index;
    paintBar();
  }

  /* The Archive's numbers (the rail, and the phone's filter chips, which are the same buttons) for the scope on
     screen, from the status part, so they follow each upload while the results stay put (SPEC §8). Called only when
     the status changes: a new render brings its own numbers, as fresh as the search. */
  function paintArchive() {
    const root = viewRoot();
    const mine = root && S.status && scopeCounts(S.status.counts, 'My uploads');
    const manageBtn = root && $('[data-act="manage-uploads"]', root);
    if (manageBtn && mine) {  // Manage uploads (N): only while this session has uploads
      manageBtn.hidden = !mine.total;
      $('[data-manage-n]', manageBtn).textContent = String(mine.total);
    }
    const now = root && S.status && scopeCounts(S.status.counts, root.dataset.scope || effectiveScope());
    if (!now) return;
    const head = $('.cv-rail__count', root);
    if (head) head.textContent = `${now.total} file${now.total === 1 ? '' : 's'}`;
    $$('.cv-rail [data-filter]', root).forEach((b) => {
      const all = b.dataset.filter === 'all';
      const n = all ? now.total : (now.types[b.dataset.filter] || 0);
      const slot = $('.cv-filter__n', b);
      if (slot) slot.textContent = String(n);
      if (!all) b.disabled = !n;
    });
    if (S.filter !== 'all' && !now.types[S.filter]) setFilter('all');  // that type left this scope (an expiry)
  }

  /* Delete all uploads: ask, then let the server wipe this browser's session (it knows which, by the cookie: nothing
     here names one) and show the samples. The count is what My uploads holds now. Whether it was sent. */
  function deleteUploads() {
    const mine = S.status && scopeCounts(S.status.counts, 'My uploads');
    if (!mine || !mine.total || !S.mainTrigger) return false;
    if (!win.confirm(confirmDelete(mine.total))) return false;
    emit(S.mainTrigger, 'click', { action: 'delete-uploads', q: S.shown || '' });
    return true;
  }

  /* ------------------------------------------------------------------ the upload bar */
  function barState() {
    if (S.transfer) {
      const t = S.transfer;
      return { stage: 'upload', done: t.done, total: t.total, pct: t.exact ? t.pct : null };
    }
    const st = S.status;
    if (S.awaiting !== null) return { stage: 'list', done: 0, total: 0 };  // sent; the server's first word is due
    if (!st || !st.upload || st.upload === S.dismissed) return null;
    if (st.message) return { stage: 'message', text: st.message };
    if (st.stage === 'idle') return null;
    return { stage: st.stage, done: st.done, total: st.total, summary: st.summary };
  }

  function paintBar() {
    const bar = S.top && $('[data-bar]', S.top);
    if (!bar) return;
    const state = barState();
    const reading = !!state && ['upload', 'list', 'read'].includes(state.stage);
    doc.body.classList.toggle('cv-reading', reading);
    const dot = $('[data-status] .cv-dot', S.top);
    if (dot) dot.classList.toggle('cv-dot--busy', reading);
    bar.hidden = !state;
    if (!state) return;
    let text = barText(state);
    if (state.stage === 'done' && S.notSent) text += ` · ${S.notSent} not sent`;
    $('[data-bar-text]', bar).textContent = text;
    const track = $('[data-bar-track]', bar);
    const value = barValue(state);
    track.hidden = state.stage === 'message';
    track.classList.toggle('is-indeterminate', value === null);
    $('[data-bar-fill]', bar).style.width = value === null ? '' : `${Math.round(value * 1000) / 10}%`;
    if (value === null) {
      ['aria-valuenow', 'aria-valuemin', 'aria-valuemax'].forEach((a) => track.removeAttribute(a));
    } else if (state.stage === 'upload') {
      track.setAttribute('aria-valuemin', '0');
      track.setAttribute('aria-valuemax', '100');
      track.setAttribute('aria-valuenow', String(state.pct));
    } else {  // real counts: files listed or read
      track.setAttribute('aria-valuemin', '0');
      track.setAttribute('aria-valuemax', String(state.total || 1));
      track.setAttribute('aria-valuenow', String(state.stage === 'done' ? state.total || 1 : state.done));
    }
    track.setAttribute('aria-valuetext', text);
    const st = S.status || {};
    const root = viewRoot();
    const update = $('[data-act="update-results"]', bar);
    update.hidden = !(root && root.dataset.view === 'results' && stale(root.dataset.version, st.version));
    const report = $('[data-act="report"]', bar);
    const src = S.statusEl && $('[data-report]', S.statusEl);  // the hidden copy, only if it's this upload's
    const rows = src && src.dataset.upload === st.upload ? src : null;
    report.hidden = !rows || state.stage === 'upload';
    report.textContent = S.reportOpen ? 'Hide report' : 'Upload report';
    report.setAttribute('aria-expanded', String(S.reportOpen));
    const panel = $('[data-bar-report]', bar);
    panel.hidden = !S.reportOpen || !rows;
    if (S.reportOpen && rows && panel.dataset.upload !== st.upload + st.stage) {  // rows as the server wrote them
      panel.replaceChildren(...Array.from(rows.childNodes, (n) => n.cloneNode(true)));
      panel.dataset.upload = st.upload + st.stage;
    }
    $('[data-act="dismiss"]', bar).hidden = reading;
    const said = state.stage === 'upload' ? 'upload' : state.stage;
    if (said !== S.said) {  // announce stage changes only, not every percent
      S.said = said;
      $('[data-bar-say]', S.top).textContent = state.stage === 'read' ? 'Reading the files. Their names are searchable now.'
        : text;
    }
  }

  /* After the first upload of a session, home switches to My uploads and lists them (results never move); so does
     a page opened on a session that has uploads (a reload, another tab), except on a phone, where the scope is
     always Both and the page already shows them. */
  function afterUpload() {
    const st = S.status;
    const root = viewRoot();
    const key = st && (st.newSession ? st.upload : (st.reattach && !phone.matches ? 'reattach' : ''));
    if (!key || key === S.homeFor || !root || root.dataset.view !== 'home') return;
    if (normalize(input().value)) return;
    S.homeFor = key;
    setScope('My uploads', false);
    search('');
  }

  /* ------------------------------------------------------------------ uploads */
  function pickFiles() {
    if (S.uploadsFull) { say(S.busyText); return; }  // max sessions open: don't make them pick files for nothing
    const picker = $('[data-files]', S.top);
    if (picker) picker.click();
  }

  /* One file to Gradio's upload endpoint, reporting the bytes sent (the helper Gradio hands to js_on_load can't). */
  function sendFile(file, onBytes) {
    return new Promise((resolve, reject) => {
      const xhr = new win.XMLHttpRequest();
      xhr.open('POST', 'gradio_api/upload');
      xhr.upload.onprogress = (e) => onBytes(e.loaded);
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)[0]); } catch (e) { reject(e); }
        } else {
          reject(Object.assign(new Error(`HTTP ${xhr.status}`), { status: xhr.status }));
        }
      };
      xhr.onerror = () => reject(new Error('network'));
      const form = new win.FormData();
      form.append('files', file, file.name);
      xhr.send(form);
    });
  }

  /* Send the files the tray kept (`left`: the ones it marked "won't be added", counted as not sent). */
  async function uploadFiles(files, left = 0) {
    const list = Array.from(files || []);
    if (!list.length || !S.topTrigger) return;
    if (S.transfer) { say('Your files are still being sent. Add more once they are.'); return; }
    if (S.uploadsFull) { say(S.busyText); return; }
    const limit = Number((win.gradio_config || {}).max_file_size) || 0;  // the server refuses larger files anyway
    const send = list.filter((f) => !limit || f.size <= limit);
    const t = { done: 0, total: send.length, sent: 0, bytes: send.reduce((a, f) => a + f.size, 0), pct: 0, exact: true,
      failed: list.length - send.length + left };
    S.transfer = t;
    S.reportOpen = false;
    S.dismissed = '';
    paintBar();
    const paths = [];
    for (const file of send) {
      const base = t.sent;
      try {
        let path = null;
        if (t.exact) {
          try {
            path = await sendFile(file, (loaded) => { t.pct = percent(base + loaded, t.bytes); paintBar(); });
          } catch (e) {
            if (e.status !== 404 && e.status !== 405) throw e;
            t.exact = false;  // no endpoint to measure: Gradio's helper, and an indeterminate bar
          }
        }
        if (!t.exact && S.upload) {
          const res = await S.upload(file);
          path = res && res.path;
        }
        if (path) paths.push(path); else t.failed += 1;
      } catch (e) {
        t.failed += 1;  // too large, or the connection dropped: said in the bar when the upload is done
      }
      t.sent = base + file.size;
      t.done += 1;
      t.pct = percent(t.sent, t.bytes);
      paintBar();
    }
    S.transfer = null;
    S.notSent = t.failed;
    if (trayOpen()) renderTray();  // Upload can be used again
    if (paths.length) {
      S.awaiting = (S.status && S.status.upload) || '';
      emit(S.topTrigger, 'upload', { paths, scope: effectiveScope() });
    } else say(`No file could be sent${limit ? ` (each must be under ${Math.round(limit / 1048576)} MB)` : ''}`);
    paintBar();
  }

  /* ------------------------------------------------------------------ the upload tray (demo) */
  const tray = () => S.top && $('[data-tray]', S.top);
  const trayOpen = () => !!tray() && !tray().hidden;

  function trayLimits() {
    try {
      const raw = JSON.parse((S.status && S.status.limits) || 'null');
      if (raw && typeof raw === 'object') return raw;
    } catch (e) { /* the server checks anyway */ }
    return { exts: [], maxBytes: Infinity, maxFiles: Infinity, maxTotal: Infinity, files: 0, bytes: 0 };
  }

  /* Picked or dropped files go into the tray, never straight to the server. The same file twice is listed once.
     `folders`: the dropped items that are folders. */
  function addToTray(files, folders) {
    if (S.uploadsFull) { say(S.busyText); return; }
    const have = new Set(S.tray.map((t) => t.key));
    Array.from(files || []).forEach((file, i) => {
      const key = `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
      if (have.has(key)) return;
      have.add(key);
      S.tray.push({ file, key, folder: !!(folders && folders.has(i)) });
    });
    if (!S.tray.length) return;
    if (!trayOpen()) {
      S.trayReturn = doc.activeElement;
      tray().hidden = false;
      doc.body.classList.add('cv-tray-open');
    }
    renderTray();
    const go = $('[data-act="tray-upload"]', tray());
    (go.disabled ? $('[data-act="tray-add"]', tray()) : go).focus();
  }

  function closeTray() {
    S.tray = [];
    const t = tray();
    if (!t) return;
    t.hidden = true;
    doc.body.classList.remove('cv-tray-open');
    $('[data-tray-list]', t).replaceChildren();
    $('[data-tray-sum]', t).textContent = '';
    const back = S.trayReturn;
    S.trayReturn = null;
    if (back && doc.contains(back) && typeof back.focus === 'function') back.focus(); else input().focus();
  }

  /* Each file with its size, type, and why it won't be added; the count and the Upload button. */
  function renderTray() {
    const t = tray();
    if (!t) return;
    const reasons = trayCheck(S.tray.map((x) => ({ name: x.file.name, size: x.file.size, folder: x.folder })),
      trayLimits());
    const rows = S.tray.map((x, i) => {
      const li = doc.createElement('li');
      li.className = reasons[i] ? 'cv-tray__item is-out' : 'cv-tray__item';
      const text = doc.createElement('span');
      text.className = 'cv-tray__text';
      const name = doc.createElement('span');
      name.className = 'cv-tray__name';
      name.textContent = x.file.name;
      const meta = doc.createElement('span');
      meta.className = 'cv-tray__meta';
      meta.textContent = x.folder ? 'Folder' : `${fileSize(x.file.size)} · ${typeLabel(x.file.name)}`;
      text.append(name, meta);
      if (reasons[i]) {
        const why = doc.createElement('span');
        why.className = 'cv-tray__why';
        why.textContent = `won't be added: ${reasons[i]}`;
        text.append(why);
      }
      const drop = doc.createElement('button');
      drop.type = 'button';
      drop.className = 'cv-iconbtn cv-tray__drop';
      drop.dataset.trayDrop = String(i);
      drop.setAttribute('aria-label', `Remove ${x.file.name}`);
      drop.textContent = '×';
      li.append(text, drop);
      return li;
    });
    $('[data-tray-list]', t).replaceChildren(...rows);
    const ok = reasons.filter((r) => !r).length;
    const out = reasons.length - ok;
    const bytes = S.tray.reduce((a, x) => a + (x.folder ? 0 : x.file.size), 0);
    $('[data-tray-sum]', t).textContent = S.tray.length
      ? `${S.tray.length} file${S.tray.length === 1 ? '' : 's'} · ${fileSize(bytes)}`
        + (out ? ` · ${out} won't be added` : '')
      : '';
    $('[data-tray-empty]', t).hidden = S.tray.length > 0;
    $('[data-act="tray-clear"]', t).disabled = !S.tray.length;
    const go = $('[data-act="tray-upload"]', t);
    go.textContent = S.transfer ? 'Sending the last files…' : uploadLabel(ok);
    go.disabled = !ok || !!S.transfer;
  }

  function trayClick(e) {
    const t = e.target;
    const drop = t.closest('[data-tray-drop]');
    const act = t.closest('[data-act]');
    if (drop) {
      const i = Number(drop.dataset.trayDrop);
      S.tray.splice(i, 1);
      renderTray();
      const next = $$('[data-tray-drop]', tray());
      (next[Math.min(i, next.length - 1)] || $('[data-act="tray-add"]', tray())).focus();
    } else if (act && act.dataset.act === 'tray-add') {
      const picker = $('[data-files]', S.top);
      if (picker) picker.click();
    } else if (act && act.dataset.act === 'tray-clear') {
      S.tray = [];
      renderTray();
      $('[data-act="tray-add"]', tray()).focus();
    } else if (act && act.dataset.act === 'tray-cancel') {
      closeTray();
    } else if (act && act.dataset.act === 'tray-upload') {
      const reasons = trayCheck(S.tray.map((x) => ({ name: x.file.name, size: x.file.size, folder: x.folder })),
        trayLimits());
      const send = S.tray.filter((x, i) => !reasons[i]).map((x) => x.file);
      const left = reasons.length - send.length;
      if (!send.length || S.transfer) return;
      closeTray();
      uploadFiles(send, left);
    } else if (t === tray()) {
      closeTray();  // a click on the backdrop
    }
  }

  /* In a dialog (the tray, Manage uploads): Esc closes it; Tab stays inside it while it's open. */
  function dialogKey(e, box, close) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === 'Tab') {
      const items = $$('button:not([disabled]), [href], input:not([type="file"]):not([disabled])', box)
        .filter((x) => x.offsetParent);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  /* ------------------------------------------------------------------ Manage uploads (demo) */
  const manage = () => S.top && $('[data-manage]', S.top);
  const manageOpen = () => !!manage() && !manage().hidden;

  function openManage() {
    const m = manage();
    if (!m) return;
    S.manageReturn = doc.activeElement;
    S.manageFiles = null;
    S.manageSel = new Set();
    m.hidden = false;
    doc.body.classList.add('cv-tray-open');
    renderManage();
    $('[data-act="manage-close"]', m).focus();
    loadManage().then(() => {
      const first = $('[data-manage-all]', m);
      if (manageOpen() && !first.disabled) first.focus();
    });
  }

  function closeManage() {
    const m = manage();
    if (!m || m.hidden) return;
    m.hidden = true;
    doc.body.classList.remove('cv-tray-open');
    $('[data-manage-list]', m).replaceChildren();
    S.manageFiles = null;
    S.manageSel = new Set();
    const back = S.manageReturn;
    S.manageReturn = null;
    const ok = back && doc.contains(back) && !back.hidden && typeof back.focus === 'function';
    (ok ? back : input()).focus();
  }

  /* This browser's uploads, from the server (by the session cookie). */
  function loadManage() {
    return win.fetch('session/uploads', { credentials: 'same-origin', cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : { files: [] }))
      .catch(() => ({ files: [] }))
      .then((data) => {
        S.manageFiles = Array.isArray(data.files) ? data.files : [];
        const ids = new Set(S.manageFiles.map((f) => String(f.id)));
        S.manageSel = new Set(Array.from(S.manageSel).filter((id) => ids.has(id)));
        renderManage();
      });
  }

  function renderManage() {
    const m = manage();
    if (!m) return;
    const files = S.manageFiles || [];
    const rows = files.map((f) => {
      const li = doc.createElement('li');
      li.className = 'cv-tray__item';
      const label = doc.createElement('label');
      label.className = 'cv-manage__row';
      const box = doc.createElement('input');
      box.type = 'checkbox';
      box.dataset.manageId = String(f.id);
      box.checked = S.manageSel.has(String(f.id));
      const text = doc.createElement('span');
      text.className = 'cv-tray__text';
      const name = doc.createElement('span');
      name.className = 'cv-tray__name';
      name.textContent = String(f.name);
      const meta = doc.createElement('span');
      meta.className = 'cv-tray__meta';
      meta.textContent = `${fileSize(f.size)} · ${typeLabel(f.name)}`;
      text.append(name, meta);
      label.append(box, text);
      li.append(label);
      return li;
    });
    $('[data-manage-list]', m).replaceChildren(...rows);
    const n = files.length;
    const k = S.manageSel.size;
    const bytes = files.reduce((a, f) => a + (Number(f.size) || 0), 0);
    $('[data-manage-sum]', m).textContent = S.manageFiles === null ? 'Loading…'
      : `${n} file${n === 1 ? '' : 's'} · ${fileSize(bytes)}`;
    $('[data-manage-empty]', m).hidden = S.manageFiles === null || n > 0;
    const all = $('[data-manage-all]', m);
    all.disabled = !n || S.manageBusy;
    all.checked = n > 0 && k === n;
    all.indeterminate = k > 0 && k < n;
    const go = $('[data-act="delete-selected"]', m);
    go.textContent = S.manageBusy ? 'Deleting…' : deleteLabel(k);
    go.disabled = !k || S.manageBusy;
    $('[data-act="delete-uploads"]', m).disabled = !n || S.manageBusy;
    const at = doc.activeElement;
    if (!m.hidden && (!at || !m.contains(at) || at.disabled)) {  // its button went (disabled, or the list was redrawn)
      (all.disabled ? $('[data-act="manage-close"]', m) : all).focus();
    }
  }

  function manageChange(e) {
    const t = e.target;
    if (t.matches('[data-manage-all]')) {
      S.manageSel = t.checked ? new Set((S.manageFiles || []).map((f) => String(f.id))) : new Set();
    } else if (t.dataset.manageId) {
      if (t.checked) S.manageSel.add(t.dataset.manageId); else S.manageSel.delete(t.dataset.manageId);
    }
    renderManage();
    if (t.dataset.manageId) {  // keep the keyboard on the same box after the list is drawn again
      const again = $(`[data-manage-id="${t.dataset.manageId}"]`, manage());
      if (again) again.focus();
    } else t.focus();
  }

  function manageClick(e) {
    const t = e.target;
    const act = t.closest('[data-act]');
    if (act && act.dataset.act === 'manage-close') closeManage();
    else if (act && act.dataset.act === 'delete-selected') {
      const ids = Array.from(S.manageSel).map(Number);
      if (!ids.length || S.manageBusy || !win.confirm(confirmDeleteSome(ids.length))) return;
      S.manageBusy = true;
      renderManage();
      emit(S.mainTrigger, 'click', { action: 'delete-files', ids, q: S.shown || '' });
    } else if (act && act.dataset.act === 'delete-uploads') {
      if (deleteUploads()) closeManage();
    } else if (t === manage()) closeManage();  // a click on the backdrop
  }

  /* Files deleted under Manage uploads leave the results on screen at once: their rows and preview copies go, the
     selection moves to the next row (or the one before, at the end), and the rest stays where it is, numbers and
     all. Home lists Recently added, so it is drawn again. */
  function dropRows(ids) {
    const root = viewRoot();
    if (!root || !ids.length) return;
    if (root.dataset.view === 'home') { search(''); return; }
    if (root.dataset.view !== 'results') return;
    const gone = new Set(ids.map(String));
    const shown = visibleOptions();
    const cur = current();
    let next = null;
    if (cur && gone.has(cur.dataset.id)) {
      const i = shown.indexOf(cur);
      next = shown.slice(i + 1).find((o) => !gone.has(o.dataset.id))
        || shown.slice(0, i).reverse().find((o) => !gone.has(o.dataset.id)) || null;
    }
    options().filter((o) => gone.has(o.dataset.id)).forEach((o) => o.remove());
    $$('[data-previews] > [data-preview]', root).filter((p) => gone.has(p.dataset.preview)).forEach((p) => p.remove());
    if (next) select(next, false);
    applyView();
    if (!options().length) clearPane($('[data-pv]', root));
  }

  /* ------------------------------------------------------------------ drag and drop anywhere (demo) */
  const dragsFiles = (e) => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf('Files') >= 0;
  const draggedCount = (e) => Array.from(e.dataTransfer.items || []).filter((i) => i.kind === 'file').length;

  /* The overlay on or off. A drag that leaves the window doesn't always say so, so it also goes once the browser
     stops reporting the drag (it does every few hundred ms while the pointer is over the page). */
  function showDrop(on, n) {
    const over = S.top && $('[data-dropover]', S.top);
    if (!over) return;
    win.clearTimeout(S.dropTimer);
    over.hidden = !on;
    doc.body.classList.toggle('cv-dragging', on);
    if (!on) return;
    over.classList.toggle('is-busy', S.uploadsFull);
    const text = S.uploadsFull ? S.busyText : dropText(n);
    const slot = $('[data-dropover-text]', over);
    if (slot.textContent !== text) slot.textContent = text;
    S.dropTimer = win.setTimeout(() => showDrop(false), 1000);
  }

  function wireDrop() {
    doc.addEventListener('dragenter', (e) => {
      if (!dragsFiles(e)) return;
      e.preventDefault();
      showDrop(true, draggedCount(e));
    });
    doc.addEventListener('dragover', (e) => {
      if (!dragsFiles(e)) return;
      e.preventDefault();  // always, or a file dropped on the page opens in the browser
      e.dataTransfer.dropEffect = S.uploadsFull ? 'none' : 'copy';
      showDrop(true, draggedCount(e));
    });
    doc.addEventListener('dragleave', (e) => { if (dragsFiles(e) && !e.relatedTarget) showDrop(false); });
    doc.addEventListener('drop', (e) => {
      if (!dragsFiles(e)) return;
      e.preventDefault();
      showDrop(false);
      const folders = new Set();
      Array.from(e.dataTransfer.items || []).filter((it) => it.kind === 'file').forEach((it, i) => {
        const entry = typeof it.webkitGetAsEntry === 'function' ? it.webkitGetAsEntry() : null;
        if (entry && entry.isDirectory) folders.add(i);
      });
      addToTray(e.dataTransfer.files, folders);
    });
  }

  /* ------------------------------------------------------------------ keys */
  function onSearchKey(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const opts = visibleOptions();
      if (!opts.length) return;
      e.preventDefault();
      const i = opts.indexOf(current());
      const next = e.key === 'ArrowDown' ? Math.min(i + 1, opts.length - 1) : Math.max(i - 1, 0);
      select(opts[next], true);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const action = enterAction(input().value, S.shown, !!current());
      if (action === 'open') open(current());
      else if (action === 'search') searchNow();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      const action = escAction(detailOpen(), input().value);
      if (action === 'close-sheet') closeDetail();
      else if (action === 'clear') clearBox();
    }
  }

  function onGlobalKey(e) {
    if (trayOpen() || manageOpen()) {  // a dialog has the keyboard until it closes
      if (e.key === 'Escape') {
        e.preventDefault();
        if (manageOpen()) closeManage(); else closeTray();
      }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      input().focus();
      input().select();
    } else if (e.key === '/' && !editable(e.target)) {
      e.preventDefault();
      input().focus();
    } else if (e.key === 'Escape' && e.target !== input() && detailOpen()) {
      closeDetail();
    }
  }

  /* ------------------------------------------------------------------ mounting (called from gr.HTML js_on_load) */
  function mountTop(element, trigger, upload) {
    S.top = element;
    S.topTrigger = trigger;
    S.upload = upload;
    const box = input();
    box.addEventListener('input', () => {
      win.clearTimeout(S.timer);
      win.clearTimeout(S.saveTimer);  // the results on screen no longer belong to the box
      syncQueryMode();
      S.timer = win.setTimeout(typed, DELAY_MS);
    });
    box.addEventListener('keydown', onSearchKey);
    element.addEventListener('click', (e) => {
      const t = e.target;
      const drop = t.closest('[data-forget]');
      const act = t.closest('[data-act]');
      const q = t.closest('[data-q]');
      if (drop) keepRecent(forget(S.recent, drop.dataset.forget));
      else if (act && act.dataset.act === 'update-results') {
        const sel = current();
        search(normalize(input().value), sel ? sel.dataset.id : '');
      } else if (act && act.dataset.act === 'report') { S.reportOpen = !S.reportOpen; paintBar(); }
      else if (act && act.dataset.act === 'dismiss') {
        S.dismissed = (S.status && S.status.upload) || '';
        S.reportOpen = false;
        paintBar();
      } else if (t.closest('[data-bar-report] [data-more-btn]')) {
        $$('[data-bar-report] [data-more]', element).forEach((x) => { x.hidden = false; });
        t.closest('[data-more-btn]').hidden = true;
      } else if (act && act.dataset.act === 'theme') toggleTheme();
      else if (act && act.dataset.act === 'clear') { clearBox(); box.focus(); }
      else if (act && act.dataset.act === 'go') searchNow();
      else if (act && act.dataset.act === 'forget-all') keepRecent([]);
      else if (q) setQuery(q.dataset.q);
      else if (t.closest('[data-home]')) { e.preventDefault(); clearBox(); }
    });
    const picker = $('[data-files]', element);
    if (picker) picker.addEventListener('change', () => { addToTray(picker.files); picker.value = ''; });
    if ($('[data-dropover]', element)) wireDrop();  // demo: drop files anywhere on the page
    if (tray()) {
      tray().addEventListener('click', trayClick);
      tray().addEventListener('keydown', (e) => dialogKey(e, tray(), closeTray));
    }
    if (manage()) {
      manage().addEventListener('click', manageClick);
      manage().addEventListener('change', manageChange);
      manage().addEventListener('keydown', (e) => dialogKey(e, manage(), closeManage));
    }
    if (/Mac|iPhone|iPad/.test(win.navigator.platform || '')) $$('[data-kbd]').forEach((k) => { k.textContent = '⌘ K'; });
    new win.MutationObserver(syncThemeButton).observe(doc.body, { attributes: true, attributeFilter: ['class'] });
    syncThemeButton();
    doc.addEventListener('keydown', onGlobalKey);
    S.recent = parseRecent(store.get(RECENT_KEY));
    renderRecent();
    syncQueryMode();
    paintStatus();  // the parts mount in any order
    if (S.main) rendered();
  }

  function rendered() {
    const root = viewRoot();
    // Only a new render from the server (Gradio patches the DOM in place, so the root element itself can stay the
    // same): our own edits to it (selection, the detail view, hidden items) never change its data-render token.
    if (!root || !S.top || root.dataset.render === S.lastRender) return;
    S.lastRender = root.dataset.render;
    const view = root.dataset.view;
    clearPane($('[data-pv]', root));  // new results: an empty pane until the new selection's preview is in
    doc.body.classList.toggle('cv-off-home', view !== 'home');  // the hero shows only on home
    doc.body.classList.remove('cv-overlay');
    if (root.dataset.scope) setScope(root.dataset.scope, false);
    S.expanded = false;
    S.shown = view === 'results' ? (root.dataset.query || '') : null;
    const box = input();
    if (view === 'results' && box && !box.value && S.shown) { box.value = S.shown; S.last = S.shown; }
    syncQueryMode();
    if (box) box.setAttribute('aria-expanded', String(options().length > 0));
    applyView();
    if (view === 'results') {
      const wanted = root.dataset.select && $(`[role="option"][data-id="${Number(root.dataset.select)}"]`, root);
      if (wanted && wanted.hidden) { S.expanded = true; applyView(); }
      select(wanted || visibleOptions()[0], !!wanted);
      win.clearTimeout(S.saveTimer);
      if (options().length) S.saveTimer = win.setTimeout(rememberShown, SAVE_MS);
    }
    paintStatus();
    afterUpload();
  }

  function mountMain(element, trigger) {
    S.main = element;
    S.mainTrigger = trigger;
    element.addEventListener('click', (e) => {
      const t = e.target;
      const filter = t.closest('[data-filter]');
      const scope = t.closest('[data-scope-btn]');
      const opt = t.closest('[role="option"]');
      const act = t.closest('[data-act]');
      const q = t.closest('[data-q]');
      if (filter && !filter.disabled) setFilter(filter.dataset.filter);
      else if (t.closest('[data-clear-filter]')) setFilter('all');
      else if (scope) setScope(scope.dataset.scopeBtn, true);
      else if (t.closest('[data-more-btn]')) {
        const root = viewRoot();
        if (root && root.dataset.view === 'results') { S.expanded = true; applyView(); }
        else if (root) { $$('[data-more]', root).forEach((x) => { x.hidden = false; }); t.closest('[data-more-btn]').hidden = true; }
      } else if (opt) { select(opt, false); rememberShown(); openDetail(); }
      else if (t.closest('[data-close]')) closeDetail();
      else if (act && act.dataset.act === 'open') open(current());
      else if (act && act.dataset.act === 'copy') copy(S.text, 'Text copied');
      else if (act && act.dataset.act === 'link' && current()) {
        copy(win.location.origin + win.location.pathname + current().dataset.link, 'Link copied');
      } else if (act && act.dataset.act === 'add') pickFiles();
      else if (act && act.dataset.act === 'manage-uploads') openManage();
      else if (act && act.dataset.act === 'folders') emit(S.topTrigger, 'click', { action: 'folders' });
      else if (q) setQuery(q.dataset.q);
    });
    // A part's HTML arrives after js_on_load and is replaced on every response: set up each new render once.
    new win.MutationObserver(rendered).observe(element, { childList: true, subtree: true, attributes: true,
      attributeFilter: ['data-render'] });
    rendered();
  }

  function mountStatus(element) {
    const sync = () => {
      const src = $('[data-status-src]', element);
      if (!src) return;
      const d = src.dataset;
      S.statusEl = src;
      S.status = { text: d.text || '', index: d.index || '', dial: d.dial || '', busy: d.busy === 'true',
        stage: d.stage || 'idle', done: Number(d.done) || 0, total: Number(d.total) || 0,
        version: Number(d.version) || 0, upload: d.upload || '', summary: d.summary || '', message: d.message || '',
        newSession: d.newSession === 'true', reattach: d.reattach === 'true', counts: d.counts || '',
        limits: d.limits || '' };
      S.uploadsFull = d.uploads === 'full';
      S.busyText = d.busyText || '';
      if (d.claim) claim(d.claim);
      if (d.forget && d.forget !== S.forgotId) {  // the cookie names no live session: clear it, once per message
        S.forgotId = d.forget;
        post('session/forget').catch(() => {});
      }
      if (S.awaiting !== null && S.status.upload && S.status.upload !== S.awaiting) S.awaiting = null;
      if (d.toast && d.toastId !== S.toastSeen) { S.toastSeen = d.toastId; say(d.toast); }  // once per message
      paintStatus();
      paintArchive();
      afterUpload();
      if (trayOpen()) renderTray();  // the session's counts changed: so may what will be added
      if (d.removed && d.removedId !== S.removedSeen) {  // files deleted under Manage uploads, once per message
        S.removedSeen = d.removedId;
        let ids = [];
        try { ids = JSON.parse(d.removed); } catch (e) { /* none */ }
        dropRows(Array.isArray(ids) ? ids : []);
      }
      if (S.manageBusy) {  // the server's answer to a delete: the list as it is now
        S.manageBusy = false;
        if (manageOpen()) loadManage();
      }
    };
    new win.MutationObserver(sync).observe(element, { childList: true, subtree: true, attributes: true });
    sync();
  }

  return Object.assign(api, { mountTop, mountMain, mountStatus });
});
