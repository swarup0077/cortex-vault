/* Cortex Vault behaviour (SPEC §8): the combobox search box, selection and preview, file-type chips, uploads.

   The decisions (when to search, what Enter and Esc do, which results a chip and "Show more" leave visible) are
   pure functions, exported for the tests (Node). The browser part wires them to the page. It never builds HTML
   from strings: the server escapes everything into the page, and this file only sets textContent and numbers. */
(function (root, factory) {
  const api = factory(typeof window !== 'undefined' ? window : null);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.Cortex = api;
})(typeof window !== 'undefined' ? window : null, function (win) {
  'use strict';

  const MIN_CHARS = 2;    // shorter text doesn't search
  const DELAY_MS = 300;   // after the last key
  const PAGE = 20;        // results shown before "Show N more"

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

  /* Esc: closes the phone sheet if it's open, and only that; otherwise clears the box. */
  function escAction(sheetOpen, text) {
    if (sheetOpen) return 'close-sheet';
    return normalize(text) ? 'clear' : 'none';
  }

  /* Which options show under a chip (`filter`) and "Show more" (`expanded`): the top `page` matching ones by
     fused rank (`ranks`, one per option in display order), wherever their group sits on the page; and how many
     more the button offers. */
  function visibility(kinds, ranks, filter, expanded, page = PAGE) {
    const matching = kinds.map((kind) => filter === 'all' || kind === filter);
    const top = new Set(ranks.filter((r, i) => matching[i]).sort((a, b) => a - b).slice(0, page));
    const visible = matching.map((m, i) => m && (expanded || top.has(ranks[i])));
    const total = matching.filter(Boolean).length;
    return { visible, more: expanded ? 0 : Math.max(0, total - page) };
  }

  const api = { MIN_CHARS, DELAY_MS, PAGE, normalize, searchFor, enterAction, escAction, visibility };
  if (!win) return api;

  /* ------------------------------------------------------------------ browser */
  const doc = win.document;
  const narrow = win.matchMedia('(max-width: 720px)');
  const S = {
    top: null, main: null, topTrigger: null, mainTrigger: null, upload: null,
    scope: 'Both', last: '', shown: null, timer: 0, filter: 'all', expanded: false,
    selected: null, text: '', toast: null, toastTimer: 0,
  };

  const $ = (sel, el) => (el || doc).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || doc).querySelectorAll(sel));
  const input = () => $('#cv-q');
  const viewRoot = () => (S.main ? $('[data-view]', S.main) : null);
  const options = () => (viewRoot() ? $$('[role="option"]', viewRoot()) : []);
  const visibleOptions = () => options().filter((o) => !o.hidden);
  const sheet = () => $('.cv-preview', S.main || doc);
  const sheetOpen = () => !!(sheet() && sheet().classList.contains('is-open'));
  const editable = (el) => !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const effectiveScope = () => (narrow.matches ? 'Both' : S.scope);

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

  function search(q) {
    S.last = q;
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
    search(normalize(q));
  }

  function setScope(scope, rerun) {
    S.scope = scope;
    $$('[data-scope]', S.top).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.scope === scope)));
    if (rerun) search(normalize(input().value));
  }

  /* ------------------------------------------------------------------ selection and preview */
  function current() {
    return visibleOptions().find((o) => o.getAttribute('aria-selected') === 'true') || null;
  }

  function select(opt, scroll) {
    if (!opt) return;
    options().forEach((o) => o.setAttribute('aria-selected', String(o === opt)));
    input().setAttribute('aria-activedescendant', opt.id);
    if (scroll) opt.scrollIntoView({ block: 'nearest' });
    fillPreview(opt);
  }

  function fillPreview(opt) {
    const root = viewRoot();
    const pane = root && $('[data-pv]', root);
    const tpl = root && $(`template[data-preview="${opt.dataset.id}"]`, root);
    if (!pane || !tpl) return;
    S.selected = opt.dataset.id;
    S.text = '';
    pane.replaceChildren(tpl.content.cloneNode(true));
    const copy = $('[data-act="copy"]', root);
    if (copy) copy.disabled = true;
    win.fetch(opt.dataset.detail, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || S.selected !== opt.dataset.id) return;
        S.text = typeof data.text === 'string' ? data.text : '';
        if (copy) copy.disabled = !S.text;
        addBoxes(pane, Array.isArray(data.boxes) ? data.boxes : []);
      })
      .catch(() => {});
  }

  function addBoxes(pane, boxes) {
    const frame = $('.cv-frame', pane);
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
    const media = $('.cv-preview__media', pane);
    const img = $('img', frame);
    const reveal = () => {
      if (first && media && media.scrollHeight > media.clientHeight) {
        media.scrollTop = Math.max(0, first.offsetTop - media.clientHeight / 3);
      }
    };
    if (img && !img.complete) img.addEventListener('load', reveal, { once: true }); else reveal();
  }

  function openSheet() { if (narrow.matches && sheet()) sheet().classList.add('is-open'); }
  function closeSheet() { if (sheet()) sheet().classList.remove('is-open'); }

  function open(opt) {
    if (!opt) return;
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

  /* ------------------------------------------------------------------ chips and Show more */
  function applyView() {
    const root = viewRoot();
    if (!root) return;
    const opts = options();
    const page = Number(root.dataset.page) || PAGE;
    const { visible, more } = visibility(opts.map((o) => o.dataset.kind), opts.map((o) => Number(o.dataset.rank)),
      S.filter, S.expanded, page);
    opts.forEach((o, i) => { o.hidden = !visible[i]; });
    $$('[data-group]', root).forEach((g) => { g.hidden = !$$('[role="option"]', g).some((o) => !o.hidden); });
    $$('[data-filter]', root).forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.filter === S.filter)));
    const btn = $('[data-more-btn]', root);
    if (btn && root.dataset.view === 'results') {
      btn.hidden = more === 0;
      btn.textContent = `Show ${more} more`;
    }
    if (!current()) select(visibleOptions()[0], false);
  }

  /* ------------------------------------------------------------------ uploads */
  function pickFiles() {
    const picker = $('[data-files]', S.top);
    if (picker) picker.click();
  }

  async function uploadFiles(files) {
    const list = Array.from(files || []);
    if (!list.length || !S.upload || !S.topTrigger) return;
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
      else if (action === 'search') { win.clearTimeout(S.timer); const q = normalize(input().value); if (q !== S.last) search(q); }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      const action = escAction(sheetOpen(), input().value);
      if (action === 'close-sheet') closeSheet();
      else if (action === 'clear') { input().value = ''; win.clearTimeout(S.timer); search(''); }
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
    } else if (e.key === 'Escape' && e.target !== input() && sheetOpen()) {
      closeSheet();
    }
  }

  /* ------------------------------------------------------------------ mounting (called from gr.HTML js_on_load) */
  function mountTop(element, trigger, upload) {
    S.top = element;
    S.topTrigger = trigger;
    S.upload = upload;
    const box = input();
    box.addEventListener('input', () => { win.clearTimeout(S.timer); S.timer = win.setTimeout(typed, DELAY_MS); });
    box.addEventListener('keydown', onSearchKey);
    element.addEventListener('click', (e) => {
      const scope = e.target.closest('[data-scope]');
      const act = e.target.closest('[data-act]');
      if (scope) setScope(scope.dataset.scope, true);
      else if (act && act.dataset.act === 'add') pickFiles();
      else if (act && act.dataset.act === 'folders') trigger('click', { action: 'folders' });
      else if (e.target.closest('[data-home]')) { e.preventDefault(); setQuery(''); }
    });
    const picker = $('[data-files]', element);
    if (picker) picker.addEventListener('change', () => { uploadFiles(picker.files); picker.value = ''; });
    doc.addEventListener('keydown', onGlobalKey);
    if (S.syncStatus) S.syncStatus();  // the parts mount in any order
    if (S.main) rendered();
  }

  function rendered() {
    const root = viewRoot();
    // Only a new render from the server (Gradio patches the DOM in place, so the root element itself can stay the
    // same): our own edits to it (selection, the preview, hidden items) never change its data-render token.
    if (!root || !S.top || root.dataset.render === S.lastRender) return;
    S.lastRender = root.dataset.render;
    const view = root.dataset.view;
    doc.body.classList.toggle('cv-home-mode', view === 'home');
    const meta = $('[data-meta]', S.top);
    if (meta) meta.textContent = root.dataset.meta || '';
    const label = $('[data-home-label]', S.top);
    if (label && root.dataset.label) label.textContent = root.dataset.label;
    if (root.dataset.scope) setScope(root.dataset.scope, false);
    S.filter = 'all';
    S.expanded = false;
    S.shown = view === 'results' ? (root.dataset.query || '') : null;
    const box = input();
    if (view === 'results' && box && !box.value && S.shown) { box.value = S.shown; S.last = S.shown; }
    if (box) box.setAttribute('aria-expanded', String(options().length > 0));
    if (view === 'results') {
      applyView();
      const wanted = root.dataset.select && $(`[role="option"][data-id="${Number(root.dataset.select)}"]`, root);
      if (wanted && wanted.hidden) { S.expanded = true; applyView(); }
      select(wanted || visibleOptions()[0], !!wanted);
    }
  }

  function mountMain(element, trigger) {
    S.main = element;
    S.mainTrigger = trigger;
    element.addEventListener('click', (e) => {
      const t = e.target;
      const chip = t.closest('[data-filter]');
      const opt = t.closest('[role="option"]');
      const act = t.closest('[data-act]');
      const q = t.closest('[data-q]');
      if (chip && !chip.disabled) { S.filter = chip.dataset.filter; S.expanded = false; applyView(); }
      else if (t.closest('[data-more-btn]')) {
        const root = viewRoot();
        if (root && root.dataset.view === 'results') { S.expanded = true; applyView(); }
        else if (root) { $$('[data-more]', root).forEach((x) => { x.hidden = false; }); t.closest('[data-more-btn]').hidden = true; }
      } else if (opt) { select(opt, false); openSheet(); }
      else if (t.closest('[data-close]')) closeSheet();
      else if (act && act.dataset.act === 'open') open(current());
      else if (act && act.dataset.act === 'copy') copy(S.text, 'Text copied');
      else if (act && act.dataset.act === 'link' && current()) {
        copy(win.location.origin + win.location.pathname + current().dataset.link, 'Link copied');
      } else if (act && act.dataset.act === 'add') pickFiles();
      else if (q) setQuery(q.dataset.q);
    });
    element.addEventListener('dragover', (e) => {
      const zone = e.target.closest && e.target.closest('.cv-drop');
      if (zone) { e.preventDefault(); zone.classList.add('is-over'); }
    });
    element.addEventListener('dragleave', (e) => {
      const zone = e.target.closest && e.target.closest('.cv-drop');
      if (zone) zone.classList.remove('is-over');
    });
    element.addEventListener('drop', (e) => {
      const zone = e.target.closest && e.target.closest('.cv-drop');
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
      const slot = S.top && $('[data-status]', S.top);
      if (!src || !slot) return;
      $('[data-status-text]', slot).textContent = src.dataset.text || '';
      $('.cv-dot', slot).classList.toggle('cv-dot--busy', src.dataset.busy === 'true');
    };
    S.syncStatus = sync;
    new win.MutationObserver(sync).observe(element, { childList: true, subtree: true, attributes: true });
    sync();
  }

  return Object.assign(api, { mountTop, mountMain, mountStatus });
});
