// Uploads survive a reload, per browser (tests/test_ui_browser.py), against a running Cortex Vault in demo mode.
//
//   node session_check.mjs '<json options>'
//
// Options: url, browser, files (paths to upload), query (one those files match), phone, expiry (seconds: then only
// upload, wait that long without searching, and reload). Otherwise: search (no cookie yet), upload, read the
// cookie as the browser keeps it, reload, open a second tab, then open the app in another browser, and there set a
// forged cookie and reload. Prints one JSON object: the state after each step.
import { deleteAll, launch, sleep, upload as uploadFiles, waitFor } from "./cdp.mjs";

const opt = JSON.parse(process.argv[2]);
const READ = `(() => {
  const root = document.querySelector('[data-view]');
  const st = document.querySelector('[data-status-src]').dataset;
  let mine = null;
  try { mine = Object.values(JSON.parse(st.counts || '{}')['My uploads'] || {}).reduce((a, n) => a + n, 0); } catch (e) {}
  const bar = document.querySelector('[data-bar]');
  const report = document.querySelector('[data-act="report"]');
  const toast = document.querySelector('.cv-toast');
  return { scope: root.dataset.scope, view: root.dataset.view, render: root.dataset.render,
    total: Number((document.querySelector('.cv-rail__count') || {textContent: '-1'}).textContent.match(/\\d+/)[0]),
    myUploads: mine, stage: st.stage, bar: bar && !bar.hidden ? document.querySelector('[data-bar-text]').textContent : null,
    report: !!report && !report.hidden, toast: toast && toast.classList.contains('is-on') ? toast.textContent : null,
    docCookie: document.cookie, recent: Array.from(document.querySelectorAll('.cv-recent:not([hidden]) .cv-recent__name')).map((n) => n.textContent),
    cards: Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.dataset.title) };
})()`;

const read = (t) => t.js(READ);
const ready = "!!document.querySelector('#cv-q') && !!document.querySelector('[data-view]') && !!window.Cortex";

async function open(t) {  // a fresh document: navigate, or reload the one there
  await t.js("window.__cvOld = true");
  await t.send("Page.navigate", { url: opt.url });
  await waitFor(() => t.js(`!window.__cvOld && ${ready}`), 30000, "the page");
  await sleep(1500);  // the page's load event, and on a reattached session the switch to My uploads
}

async function cookie(t) {
  await t.send("Network.enable");
  const { cookies } = await t.send("Network.getCookies", { urls: [opt.url] });
  const c = cookies.find((x) => x.name === "cv_session");
  return c ? { httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite, session: c.session, path: c.path,
    length: c.value.length } : null;
}

async function search(t, q) {
  await t.js("(() => { const x = document.querySelector('#cv-q'); x.value = ''; x.focus(); })()");
  for (const ch of q) { await t.send("Input.insertText", { text: ch }); await sleep(15); }
  await waitFor(async () => (await t.js("(document.querySelector('[data-view]').dataset.query || '')")) === q, 30000,
    `results for ${q}`);
  await sleep(300);
  return read(t);
}

async function upload(t, files) {
  await uploadFiles(t, files);
  await waitFor(() => t.js("document.querySelector('[data-status-src]').dataset.stage === 'done'"), 120000, "the upload");
  await sleep(1500);  // the claim, and home switching to My uploads
}

const browsers = [];
try {
  const a = await launch(opt.browser, { phone: !!opt.phone });
  browsers.push(a);
  const out = {};
  await open(a);
  if (opt.expiry) {
    await upload(a, opt.files);
    out.uploaded = { ...(await read(a)), cookie: await cookie(a) };
    await sleep(opt.expiry * 1000);  // no search, no upload
    await a.js("window.__cvOld = true");
    await a.send("Page.reload");
    await waitFor(() => a.js(`!window.__cvOld && ${ready}`), 30000, "the page");
    out.toast = await waitFor(() => a.js("(document.querySelector('.cv-toast.is-on') || {}).textContent || ''"), 15000,
      "the toast");
    await sleep(1000);
    out.expired = { ...(await read(a)), cookie: await cookie(a) };
  } else {
    out.start = { ...(await read(a)), cookie: await cookie(a) };
    out.searched = { ...(await search(a, opt.query)), cookie: await cookie(a) };
    await a.js("document.querySelector('[data-act=\"clear\"]').click()");
    await waitFor(async () => (await read(a)).view === "home", 30000, "home");
    await upload(a, opt.files);
    out.uploaded = { ...(await read(a)), cookie: await cookie(a) };
    await a.js("window.__cvOld = true");
    await a.send("Page.reload");
    await waitFor(() => a.js(`!window.__cvOld && ${ready}`), 30000, "the reloaded page");
    await waitFor(async () => (await read(a)).myUploads === opt.files.length, 30000, "the uploads after the reload");
    await sleep(1500);
    out.reloaded = await read(a);
    out.reloadedSearch = await search(a, opt.query);
    const tab = await a.newTab();
    await open(tab);
    out.secondTab = await read(tab);
    out.secondTabSearch = await search(tab, opt.query);
    const b = await launch(opt.browser, { phone: !!opt.phone });
    browsers.push(b);
    await open(b);
    out.other = { ...(await read(b)), cookie: await cookie(b) };
    out.otherSearch = await search(b, opt.query);
    await b.send("Network.setCookie", { name: "cv_session", value: "forged-0123456789abcdef0123456789abcdef",
      url: opt.url, secure: true, httpOnly: true, sameSite: "Strict" });
    out.forgedSet = await cookie(b);
    await open(b);
    out.forged = { ...(await read(b)), cookie: await cookie(b) };
    out.forgedSearch = await search(b, opt.query);
  }
  if (opt.cleanup) {  // a live check: delete the uploads again, answering OK to the confirmation
    a.on((m) => { if (m.method === "Page.javascriptDialogOpening") a.send("Page.handleJavaScriptDialog", { accept: true }); });
    await deleteAll(a);
    await waitFor(async () => (await read(a)).myUploads === 0, 30000, "the delete");
    await sleep(1000);
    out.cleaned = { ...(await read(a)), cookie: await cookie(a) };
  }
  process.stdout.write(JSON.stringify(out));
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
} finally {
  for (const b of browsers) await b.close();
}
