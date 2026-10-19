/* Cortex Vault behaviour (SPEC §8), "The Brass Index" look: the combobox search box, selection and the detail view,
   the type filter, recent searches, the theme toggle, the dial, uploads.

   The decisions (when to search, what Enter and Esc do, which results the type filter and "Show more" leave
   visible, what the count line says, how recent searches are kept) are pure functions, exported for the tests
   (Node). The browser part wires them to the page. It never builds HTML from strings: the server escapes
   everything into the page, and this file only creates elements and sets textContent, attributes and numbers.
   Recent searches live in this browser's localStorage only: they are never sent anywhere. */
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

  const api = { MIN_CHARS, DELAY_MS, PAGE, SAVE_MS, RECENT_MAX, PLURALS, normalize, searchFor, enterAction, escAction,
    visibility, countWords, remember, forget, parseRecent };
  if (!win) return api;

  /* ------------------------------------------------------------------ browser */
  const doc = win.document;
  const wide = win.matchMedia('(min-width: 1200px)');   // the detail view is a column; below, a drawer or sheet
  const phone = win.matchMedia('(max-width: 760px)');
  const S = {
    top: null, main: null, topTrigger: null, mainTrigger: null, upload: null,
    scope: 'Both', last: '', shown: null, timer: 0, filter: 'all', expanded: false,
    selected: null, text: '', toast: null, toastTimer: 0, saveTimer: 0, turn: 0, recent: [], status: null,
    uploadsFull: false, busyText: '', lastRender: null,
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

  function search(q) {
    S.last = q;
    win.clearTimeout(S.saveTimer);
    if (q) turnDial();
    if (S.topTrigger) S.topTrigger('submit', { q, scope: effectiveScope() });
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

  function fillDetail(opt) {
    const root = viewRoot();
    const body = root && $('[data-pv]', root);
    const tpl = root && $(`template[data-preview="${opt.dataset.id}"]`, root);
    if (!body || !tpl) return;
    S.selected = opt.dataset.id;
    S.text = '';
    body.replaceChildren(tpl.content.cloneNode(true));
    const copy = $('[data-act="copy"]', body);
    win.fetch(opt.dataset.detail, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || S.selected !== opt.dataset.id) return;
        S.text = typeof data.text === 'string' ? data.text : '';
        if (copy) copy.disabled = !S.text;
        addBoxes(body, Array.isArray(data.boxes) ? data.boxes : []);
      })
      .catch(() => {});
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
      S.mainTrigger('click', { action: 'open', file_id: Number(opt.dataset.id) });  // local: vault/platform.py
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
      $('.cv-dot', slot).classList.toggle('cv-dot--busy', st.busy);
      slot.classList.toggle('is-busy', st.busy);
    }
    const dial = S.top && $('[data-dial-state]', S.top);
    if (dial) dial.textContent = st.dial;
    doc.body.classList.toggle('cv-reading', st.busy);
    const line = $('[data-index-line]');
    if (line) line.textContent = st.index;
    const box = S.main && $('[data-progress]', S.main);
    if (box) {
      box.hidden = !st.busy;
      $('[data-progress-pct]', box).textContent = `${st.pct}%`;
      $('[data-progress-bar]', box).style.width = `${st.pct}%`;
    }
  }

  /* ------------------------------------------------------------------ uploads */
  function pickFiles() {
    if (S.uploadsFull) { say(S.busyText); return; }  // max sessions open: don't make them pick files for nothing
    const picker = $('[data-files]', S.top);
    if (picker) picker.click();
  }

  async function uploadFiles(files) {
    const list = Array.from(files || []);
    if (!list.length || !S.upload || !S.topTrigger) return;
    if (S.uploadsFull) { say(S.busyText); return; }
    say(`Uploading ${list.length} file${list.length === 1 ? '' : 's'}…`);
    const paths = [];
    for (const file of list) {
      try {
        const res = await S.upload(file);
        if (res && res.path) paths.push(res.path);
      } catch (e) { /* the server lists what arrived */ }
    }
    if (paths.length) S.topTrigger('upload', { paths, scope: effectiveScope() });
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
      else if (act && act.dataset.act === 'theme') toggleTheme();
      else if (act && act.dataset.act === 'clear') { clearBox(); box.focus(); }
      else if (act && act.dataset.act === 'go') searchNow();
      else if (act && act.dataset.act === 'forget-all') keepRecent([]);
      else if (q) setQuery(q.dataset.q);
      else if (t.closest('[data-home]')) { e.preventDefault(); clearBox(); }
    });
    const picker = $('[data-files]', element);
    if (picker) picker.addEventListener('change', () => { uploadFiles(picker.files); picker.value = ''; });
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
      else if (act && act.dataset.act === 'folders' && S.topTrigger) S.topTrigger('click', { action: 'folders' });
      else if (q) setQuery(q.dataset.q);
    });
    element.addEventListener('dragover', (e) => {
      const zone = e.target.closest && e.target.closest('.cv-drop, .cv-add');
      if (!zone) return;
      e.preventDefault();  // always, or a dropped file opens in the browser
      if (!S.uploadsFull) zone.classList.add('is-over');
    });
    element.addEventListener('dragleave', (e) => {
      const zone = e.target.closest && e.target.closest('.cv-drop, .cv-add');
      if (zone) zone.classList.remove('is-over');
    });
    element.addEventListener('drop', (e) => {
      const zone = e.target.closest && e.target.closest('.cv-drop, .cv-add');
      if (!zone) return;
      e.preventDefault();
      zone.classList.remove('is-over');
      uploadFiles(e.dataTransfer && e.dataTransfer.files);
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
      S.status = { text: src.dataset.text || '', index: src.dataset.index || '', dial: src.dataset.dial || '',
        busy: src.dataset.busy === 'true', pct: Number(src.dataset.pct) || 0 };
      S.uploadsFull = src.dataset.uploads === 'full';
      S.busyText = src.dataset.busyText || '';
      paintStatus();
    };
    new win.MutationObserver(sync).observe(element, { childList: true, subtree: true, attributes: true });
    sync();
  }

  return Object.assign(api, { mountTop, mountMain, mountStatus });
});
