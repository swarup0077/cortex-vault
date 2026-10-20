// Manage uploads: choose which uploads to delete (tests/test_ui_browser.py), against a running Cortex Vault in demo
// mode.
//
//   node manage_check.mjs '<json options>'
//
// Options: url, browser, files (paths to upload), query (one all of them match), pick (indexes of result rows whose
// files to delete), phone, shot (a folder: save a screenshot of the panel there), cleanup (delete the rest after).
// Uploads the files through the tray, searches, selects the second row (desktop: ↓), opens Manage uploads, toggles
// Select all with the keyboard, ticks the picked rows' files, answers Cancel then OK to Delete selected, and reads
// the results, the panel and The Archive after; then closes the panel with Esc (desktop) and searches again. Prints
// one JSON object.
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { launch, openApp, sleep, upload, waitFor } from "./cdp.mjs";

const opt = JSON.parse(process.argv[2]);
const READ = `(() => {
  const root = document.querySelector('[data-view]');
  const st = document.querySelector('[data-status-src]').dataset;
  let counts = {};
  try { counts = JSON.parse(st.counts || '{}'); } catch (e) {}
  const sum = (scope) => Object.values(counts[scope] || {}).reduce((a, n) => a + n, 0);
  const m = document.querySelector('[data-manage]');
  const btn = document.querySelector('[data-act="manage-uploads"]');
  const sel = document.querySelector('[role="option"][aria-selected="true"]');
  const toast = document.querySelector('.cv-toast');
  const a = document.activeElement;
  const all = m.querySelector('[data-manage-all]');
  return { view: root.dataset.view, query: root.dataset.query || '', render: root.dataset.render, scrollY: Math.round(scrollY),
    rows: Array.from(document.querySelectorAll('[role="option"]')).filter((o) => !o.hidden).map((o) => ({ id: o.dataset.id,
      title: o.dataset.title, number: o.querySelector('.cv-card__rank').textContent })),
    allIds: Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.dataset.id),
    selected: sel ? sel.dataset.title : null, pane: (document.querySelector('[data-pv] .cv-detail__title') || {}).textContent || null,
    count: (document.querySelector('[data-count-n]') || {}).textContent || null,
    myUploads: sum('My uploads'), samples: sum('Samples'),
    manageButton: btn && !btn.hidden && getComputedStyle(btn).display !== 'none' ? btn.textContent : null,
    panel: !m.hidden, sum: m.querySelector('[data-manage-sum]').textContent,
    list: Array.from(m.querySelectorAll('[data-manage-id]')).map((x) => ({ id: x.dataset.manageId,
      name: x.closest('label').querySelector('.cv-tray__name').textContent, checked: x.checked })),
    all: { checked: all.checked, indeterminate: all.indeterminate }, go: m.querySelector('[data-act="delete-selected"]').textContent,
    goDisabled: m.querySelector('[data-act="delete-selected"]').disabled,
    focus: a ? (a.dataset.act || (a.matches('[data-manage-all]') ? 'select-all' : a.dataset.manageId ? 'box:' + a.dataset.manageId : a.id || a.tagName)) : null,
    inPanel: m.contains(a), toast: toast && toast.classList.contains('is-on') ? toast.textContent : null };
})()`;

let b;
const out = {};
try {
  b = await launch(opt.browser, { phone: !!opt.phone });
  const dialogs = [];
  const answers = [false, true, true];  // Cancel, then OK (and OK for the cleanup)
  b.on((m) => {
    if (m.method !== "Page.javascriptDialogOpening") return;
    dialogs.push(m.params.message);
    b.send("Page.handleJavaScriptDialog", { accept: answers.shift() ?? false });
  });
  await openApp(b, opt.url);
  await sleep(800);
  const read = () => b.js(READ);
  const key = async (k) => {
    const codes = { Tab: 9, Enter: 13, Escape: 27, ArrowDown: 40, " ": 32 };
    const base = { key: k, code: k === " " ? "Space" : k, windowsVirtualKeyCode: codes[k] };
    await b.send("Input.dispatchKeyEvent", { type: "keyDown", ...base, ...(k === " " ? { text: " " } : {}) });
    await b.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    await sleep(150);
  };
  const press = async (selector) => {  // a click, or a tap on a phone
    const box = await b.js(`(() => { const el = document.querySelector(${JSON.stringify(selector)});
      el.scrollIntoView({block: 'center'}); const r = el.getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()`);
    if (opt.phone) {
      await b.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: box.x, y: box.y }] });
      await b.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    } else {
      for (const type of ["mousePressed", "mouseReleased"]) {
        await b.send("Input.dispatchMouseEvent", { type, x: box.x, y: box.y, button: "left", clickCount: 1 });
      }
    }
    await sleep(300);
  };
  const search = async (q) => {
    await b.js("(() => { const x = document.querySelector('#cv-q'); x.value = ''; x.focus(); })()");
    for (const ch of q) { await b.send("Input.insertText", { text: ch }); await sleep(15); }
    await waitFor(async () => (await read()).query === q, 30000, `results for ${q}`);
    await sleep(400);
  };
  await upload(b, opt.files);
  await waitFor(async () => (await read()).myUploads === opt.files.length, 120000, "the upload");
  await sleep(1500);
  await search(opt.query);
  if (!opt.phone) await key("ArrowDown");  // the second row
  await sleep(300);
  out.before = await read();
  await press('[data-act="manage-uploads"]');
  await waitFor(async () => (await read()).list.length === opt.files.length, 15000, "the list");
  await sleep(200);
  out.opened = await read();
  if (opt.shot) {
    const { data } = await b.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(opt.shot, `manage-${opt.phone ? "phone" : "desktop"}.png`), Buffer.from(data, "base64"));
  }
  if (!opt.phone) {
    await b.js("document.querySelector('[data-manage-all]').focus()");
    await key(" ");
    out.allOn = await read();
    await key(" ");
    out.allOff = await read();
  }
  const picked = opt.pick.map((i) => out.before.rows[i].id);
  for (const id of picked) await press(`[data-manage-id="${id}"]`);
  out.ticked = await read();
  await press('[data-act="delete-selected"]');  // answered Cancel
  await sleep(800);
  out.cancelled = await read();
  await press('[data-act="delete-selected"]');  // answered OK
  await waitFor(async () => !(await read()).allIds.some((id) => picked.includes(id)), 30000, "the rows to go");
  await waitFor(async () => (await read()).list.length === opt.files.length - picked.length, 15000, "the list to follow");
  out.deleted = await read();
  if (!opt.phone) { await key("Escape"); out.closed = await read(); }
  else { await press('[data-act="manage-close"]'); out.closed = await read(); }
  await search(`${opt.query} again`);
  out.searchedAgain = await read();
  if (opt.cleanup) {
    await press('[data-act="manage-uploads"]');
    await waitFor(async () => (await read()).list.length > 0, 15000, "the list");
    await press('[data-manage] [data-act="delete-uploads"]');
    await waitFor(async () => (await read()).myUploads === 0, 30000, "Delete all");
    out.cleaned = await read();
  }
  out.dialogs = dialogs;
  process.stdout.write(JSON.stringify(out));
} catch (e) {
  process.stdout.write(JSON.stringify({ ...out, error: String((e && e.stack) || e) }));
  process.exitCode = 1;
} finally {
  if (b) await b.close();
}
