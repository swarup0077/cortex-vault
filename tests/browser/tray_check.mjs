// Review before upload: the tray (tests/test_ui_browser.py), against a running Cortex Vault in demo mode.
//
//   node tray_check.mjs '<json options>'
//
// Options: url, browser, first (file paths picked first), more (picked with Add more), again (picked after the tray
// was cancelled), phone, shot (a folder: save a screenshot of the tray there). Picks the first files, reads the tray;
// removes the first refused file with its ×; Add more (through the browser's file chooser); tabs round the tray
// and cancels it with Esc; picks again, clears it, adds again with Add more, and uploads with the keyboard (Enter on
// Upload); a phone taps instead. Counts every request to the upload endpoint. Prints one JSON object.
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { launch, openApp, sleep, waitFor } from "./cdp.mjs";

const opt = JSON.parse(process.argv[2]);
const READ = `(() => {
  const t = document.querySelector('[data-tray]');
  const st = document.querySelector('[data-status-src]').dataset;
  let mine = null;
  try { mine = Object.values(JSON.parse(st.counts || '{}')['My uploads'] || {}).reduce((a, n) => a + n, 0); } catch (e) {}
  const go = t.querySelector('[data-act="tray-upload"]');
  const a = document.activeElement;
  const panel = t.querySelector('.cv-tray__panel').getBoundingClientRect();
  const bar = document.querySelector('[data-bar]');
  return { open: !t.hidden, sum: t.querySelector('[data-tray-sum]').textContent,
    rows: Array.from(t.querySelectorAll('.cv-tray__item')).map((li) => ({
      name: li.querySelector('.cv-tray__name').textContent, meta: li.querySelector('.cv-tray__meta').textContent,
      why: (li.querySelector('.cv-tray__why') || {}).textContent || '', out: li.classList.contains('is-out') })),
    empty: !t.querySelector('[data-tray-empty]').hidden, go: go.textContent, goDisabled: go.disabled,
    focus: a ? (a.dataset.act || (a.dataset.trayDrop !== undefined ? 'drop:' + a.dataset.trayDrop : a.id || a.tagName)) : null,
    inTray: t.contains(a), panel: { width: Math.round(panel.width), height: Math.round(panel.height) },
    viewport: { width: innerWidth, height: innerHeight }, stage: st.stage, upload: st.upload, myUploads: mine,
    bar: bar && !bar.hidden ? document.querySelector('[data-bar-text]').textContent : null };
})()`;

let b;
try {
  b = await launch(opt.browser, { phone: !!opt.phone });
  let sent = 0;
  const choosers = [];
  b.on((m) => {
    if (m.method === "Network.requestWillBeSent" && m.params.request.url.includes("gradio_api/upload")) sent += 1;
    if (m.method === "Page.fileChooserOpened") choosers.push(m.params.backendNodeId);
  });
  await b.send("Network.enable");
  await openApp(b, opt.url);
  await sleep(1000);
  await b.send("DOM.enable");
  await b.send("Page.setInterceptFileChooserDialog", { enabled: true });
  const read = () => b.js(READ);
  const pick = async (files) => {  // what choosing files in the picker does
    const { root } = await b.send("DOM.getDocument", { depth: -1 });
    const { nodeId } = await b.send("DOM.querySelector", { nodeId: root.nodeId, selector: "[data-files]" });
    await b.send("DOM.setFileInputFiles", { nodeId, files });
    await sleep(300);
  };
  const key = async (k, shift = false) => {
    const codes = { Tab: 9, Enter: 13, Escape: 27 };
    const base = { key: k, code: k, windowsVirtualKeyCode: codes[k], modifiers: shift ? 8 : 0 };
    await b.send("Input.dispatchKeyEvent", { type: "keyDown", ...base, ...(k === "Enter" ? { text: "\r" } : {}) });
    await b.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    await sleep(120);
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
  const out = {};
  // the page's own Add files button opens the picker
  await press(opt.phone ? ".cv-drop" : ".cv-rail [data-act=\"add\"]");
  out.addOpensPicker = choosers.length === 1;
  await pick(opt.first);
  out.picked = await read();
  out.sentAfterPick = sent;
  if (opt.shot) {
    const { data } = await b.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(opt.shot, `tray-${opt.phone ? "phone" : "desktop"}.png`), Buffer.from(data, "base64"));
  }
  const firstOut = out.picked.rows.findIndex((r) => r.out);
  await press(`[data-tray-drop="${firstOut}"]`);
  out.removed = await read();
  await press("[data-act=\"tray-add\"]");
  out.moreOpensPicker = choosers.length === 2;
  await b.send("DOM.setFileInputFiles", { backendNodeId: choosers[1], files: opt.more });
  await sleep(300);
  out.more = await read();
  if (!opt.phone) {
    await b.js("document.querySelector('[data-act=\"tray-upload\"]').focus()");
    const order = [];
    for (let i = 0; i < out.more.rows.length + 6; i += 1) { await key("Tab"); order.push((await read()).focus); }
    out.tabOrder = order;
    out.trapped = (await read()).inTray;
    await key("Escape");
  } else {
    await press("[data-act=\"tray-cancel\"]");
  }
  out.cancelled = await read();
  out.sentAfterCancel = sent;
  await pick(opt.again);
  out.again = await read();
  await press("[data-act=\"tray-clear\"]");
  out.cleared = await read();
  await press("[data-act=\"tray-add\"]");
  await b.send("DOM.setFileInputFiles", { backendNodeId: choosers[choosers.length - 1], files: opt.again });
  await sleep(300);
  out.readd = await read();
  out.sentBeforeUpload = sent;
  if (opt.phone) await press("[data-act=\"tray-upload\"]");
  else { await b.js("document.querySelector('[data-act=\"tray-upload\"]').focus()"); await key("Enter"); }
  out.uploading = await read();
  await waitFor(async () => { const s = await read(); return s.stage === "done" && s.upload; }, 120000, "the upload");
  await sleep(1500);
  out.done = await read();
  out.sent = sent;
  process.stdout.write(JSON.stringify(out));
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
} finally {
  if (b) await b.close();
}
