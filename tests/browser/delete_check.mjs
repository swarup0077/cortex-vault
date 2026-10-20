// Delete my uploads (tests/test_ui_browser.py), against a running Cortex Vault in demo mode.
//
//   node delete_check.mjs '<json options>'
//
// Options: url, browser, files (paths to upload), query (one those files match), phone. Uploads the files,
// searches, clicks Delete my uploads and answers Cancel to the confirmation, then clicks again and answers OK,
// then searches again. Prints one JSON object: the state after each step and the confirmation's text.
import { launch, openApp, sleep, upload, waitFor } from "./cdp.mjs";

const opt = JSON.parse(process.argv[2]);
const READ = `(() => {
  const root = document.querySelector('[data-view]');
  const del = document.querySelector('[data-act="delete-uploads"]');
  const st = document.querySelector('[data-status-src]').dataset;
  let mine = null;
  try { mine = Object.values(JSON.parse(st.counts || '{}')['My uploads'] || {}).reduce((a, n) => a + n, 0); } catch (e) {}
  const toast = document.querySelector('.cv-toast');
  return { scope: root.dataset.scope, view: root.dataset.view, query: root.dataset.query || '', render: root.dataset.render,
    total: Number((document.querySelector('.cv-rail__count') || {textContent: '-1'}).textContent.match(/\\d+/)[0]),
    deleteShown: !!del && !del.hidden && getComputedStyle(del).display !== 'none', myUploads: mine,
    toast: toast && toast.classList.contains('is-on') ? toast.textContent : null,
    cards: Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.dataset.title) };
})()`;

let b;
try {
  b = await launch(opt.browser, { phone: !!opt.phone });
  const dialogs = [];
  const answers = [false, true];  // Cancel, then OK
  b.on((m) => {
    if (m.method !== "Page.javascriptDialogOpening") return;
    dialogs.push({ type: m.params.type, message: m.params.message });
    b.send("Page.handleJavaScriptDialog", { accept: answers.shift() ?? false });
  });
  await openApp(b, opt.url);
  await b.send("DOM.enable");
  const read = () => b.js(READ);
  const out = { start: await read() };
  await upload(b, opt.files);
  await waitFor(() => b.js("document.querySelector('[data-status-src]').dataset.stage === 'done'"), 120000, "the upload");
  await waitFor(async () => (await read()).deleteShown, 10000, "the delete button");
  await sleep(1200);  // home switches to My uploads after the first upload (desktop)
  out.uploaded = await read();
  const search = async (q) => {
    await b.js("(() => { const x = document.querySelector('#cv-q'); x.value = ''; x.focus(); })()");
    for (const ch of q) { await b.send("Input.insertText", { text: ch }); await sleep(15); }
    await waitFor(async () => (await read()).query === q, 30000, `results for ${q}`);
    await sleep(300);
  };
  await search(opt.query);
  out.searched = await read();
  const press = async () => {  // a real click, or a tap on a phone
    const box = await b.js(`(() => { const d = document.querySelector('[data-act="delete-uploads"]');
      d.scrollIntoView({block: 'center'}); const r = d.getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()`);
    if (opt.phone) {
      await b.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: box.x, y: box.y }] });
      await b.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    } else {
      for (const type of ["mousePressed", "mouseReleased"]) {
        await b.send("Input.dispatchMouseEvent", { type, x: box.x, y: box.y, button: "left", clickCount: 1 });
      }
    }
  };
  await press();  // answered Cancel
  await sleep(1500);
  out.cancelled = await read();
  const before = out.cancelled.render;
  await press();  // answered OK
  await waitFor(async () => { const s = await read(); return s.render !== before && s.scope === "Samples"; }, 30000,
    "the samples after the delete");
  await sleep(300);
  out.deleted = await read();
  if (!opt.phone) {  // search again, in Both
    const r = (await read()).render;
    await b.js(`document.querySelector('[data-scope-btn="Both"]').click()`);
    await waitFor(async () => { const s = await read(); return s.scope === "Both" && s.render !== r; }, 30000, "Both");
  }
  await search(`${opt.query} again`);
  out.searchedAgain = await read();
  out.dialogs = dialogs;
  process.stdout.write(JSON.stringify(out));
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
} finally {
  if (b) await b.close();
}
