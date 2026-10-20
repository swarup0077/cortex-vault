// Drag and drop anywhere (tests/test_ui_browser.py), against a running Cortex Vault in demo mode.
//
//   node drop_check.mjs '<json options>'
//
// Options: url, browser, files (two lists of file paths), query (one the first list matches), phone, shot (a folder:
// save a screenshot of the overlay there). Desktop: reads the home drop zone, drags the first list over the middle of
// the page and drops it there, then searches, drags the second list over the results, cancels one drag, and drops
// it on the results page. Dropped files go into the upload tray, and Upload there sends them. Phone: reads the
// drop zone's text only. Prints one JSON object.
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { launch, openApp, sleep, waitFor } from "./cdp.mjs";

const opt = JSON.parse(process.argv[2]);
const READ = `(() => {
  const root = document.querySelector('[data-view]');
  const st = document.querySelector('[data-status-src]').dataset;
  let mine = null;
  try { mine = Object.values(JSON.parse(st.counts || '{}')['My uploads'] || {}).reduce((a, n) => a + n, 0); } catch (e) {}
  const over = document.querySelector('[data-dropover]');
  const zone = document.querySelector('.cv-drop');
  const visible = (el) => !!el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().height > 0;
  const box = zone ? zone.getBoundingClientRect() : null;
  const note = zone && zone.nextElementSibling;
  return { view: root.dataset.view, query: root.dataset.query || '', render: root.dataset.render, stage: st.stage,
    upload: st.upload, myUploads: mine, overlay: !!over && !over.hidden && visible(over) ? over.textContent : null,
    tray: Array.from(document.querySelectorAll('[data-tray]:not([hidden]) .cv-tray__name')).map((n) => n.textContent),
    zone: zone ? { height: Math.round(box.height), width: Math.round(box.width),
      text: Array.from(zone.querySelectorAll('span')).filter((s) => visible(s) && !s.querySelector('span'))
        .map((s) => s.textContent).join(' | '),
      next: note ? note.className : null, nextText: note ? note.textContent : '' } : null,
    cards: Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.dataset.title) };
})()`;

let b;
try {
  b = await launch(opt.browser, { phone: !!opt.phone });
  await openApp(b, opt.url);
  await sleep(1000);
  const read = () => b.js(READ);
  const out = { home: await read() };
  if (!opt.phone) {
    const drag = (type, files, x = 720, y = 450) => b.send("Input.dispatchDragEvent",
      { type, x, y, data: { items: [], files, dragOperationsMask: 1 } });
    const dropAll = async (files, x, y, name) => {
      const before = (await read()).upload;
      await drag("dragEnter", files, x, y);
      await drag("dragOver", files, x, y);
      await sleep(200);
      const during = await read();
      if (opt.shot) {
        const { data } = await b.send("Page.captureScreenshot", { format: "png" });
        writeFileSync(join(opt.shot, `drop-${name}.png`), Buffer.from(data, "base64"));
      }
      await drag("drop", files, x, y);
      await sleep(300);
      const dropped = await read();
      await b.js("document.querySelector('[data-act=tray-upload]').click()");
      await waitFor(async () => { const s = await read(); return s.stage === "done" && s.upload !== before; }, 120000,
        "the upload");
      await sleep(1500);
      return { during, dropped, after: await read() };
    };
    out.first = await dropAll(opt.files[0], 900, 250, "home");  // over the hero, nowhere near the drop zone
    await b.js("(() => { const x = document.querySelector('#cv-q'); x.value = ''; x.focus(); })()");
    for (const ch of opt.query) { await b.send("Input.insertText", { text: ch }); await sleep(15); }
    await waitFor(async () => (await read()).query === opt.query, 30000, "the results");
    await sleep(300);
    out.results = await read();
    await drag("dragEnter", opt.files[1], 500, 600);
    await drag("dragOver", opt.files[1], 500, 600);
    await sleep(200);
    out.cancelDuring = await read();
    await drag("dragCancel", opt.files[1], 500, 600);
    await sleep(1500);
    out.cancelled = await read();
    out.second = await dropAll(opt.files[1], 500, 600, "results");  // on the results list
    if (opt.cleanup) {  // a live check: delete the uploads again, answering OK to the confirmation
      b.on((m) => { if (m.method === "Page.javascriptDialogOpening") b.send("Page.handleJavaScriptDialog", { accept: true }); });
      await b.js("document.querySelector('[data-act=delete-uploads]').click()");
      await waitFor(async () => (await read()).myUploads === 0, 30000, "the delete");
      out.cleaned = await read();
    }
  }
  process.stdout.write(JSON.stringify(out));
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
} finally {
  if (b) await b.close();
}
