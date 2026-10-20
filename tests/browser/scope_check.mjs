// The Archive's counts follow "Search in" (tests/test_ui_browser.py), against a running Cortex Vault in demo mode.
//
//   node scope_check.mjs '<json options>'
//
// Options: url, browser, uploads (two lists of file paths), query (one the first upload's files match), phone.
// Desktop: upload the first list, then for each scope click Search in and read The Archive, then search the query
// in each scope, then switch to Both, upload the second list and read The Archive again without a new render.
// Phone (no Search in: the scope is Both): upload the first list and read the filter chips. Prints one JSON object.
import { launch, openApp, sleep, waitFor } from "./cdp.mjs";

const opt = JSON.parse(process.argv[2]);
let b;
const READ = `(() => {
  const root = document.querySelector('[data-view]');
  const n = (el) => Number((el && el.textContent.match(/\\d+/) || ['-1'])[0]);
  const types = {};
  document.querySelectorAll('.cv-rail [data-filter]').forEach((f) => {
    types[f.dataset.filter] = n(f.querySelector('.cv-filter__n'));
  });
  const cards = Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.dataset.title);
  return { scope: root.dataset.scope, view: root.dataset.view, render: root.dataset.render,
    total: n(document.querySelector('.cv-rail__count')), types,
    chips: getComputedStyle(document.querySelector('.cv-filters')).display,
    scopeShown: !!document.querySelector('.cv-rail__scope') && getComputedStyle(document.querySelector('.cv-rail__scope')).display !== 'none',
    matches: document.querySelector('[data-count-n]') ? Number(document.querySelector('[data-count-n]').textContent) : null,
    cards };
})()`;

try {
  b = await launch(opt.browser, { phone: !!opt.phone });
  await openApp(b, opt.url);
  await b.send("DOM.enable");
  const read = () => b.js(READ);
  const upload = async (files, last) => {
    const { root } = await b.send("DOM.getDocument", { depth: -1 });
    const { nodeId } = await b.send("DOM.querySelector", { nodeId: root.nodeId, selector: "[data-files]" });
    await b.send("DOM.setFileInputFiles", { nodeId, files });
    return waitFor(() => b.js(`(() => { const d = document.querySelector('[data-status-src]').dataset;
      return d.stage === 'done' && d.upload && d.upload !== ${JSON.stringify(last)} ? d.upload : ''; })()`),
    120000, "the upload to finish");
  };
  const rendered = (scope, render) => waitFor(() => b.js(`(() => { const r = document.querySelector('[data-view]');
    return r.dataset.scope === ${JSON.stringify(scope)} && r.dataset.render !== ${JSON.stringify(render)}; })()`),
  30000, `a render in ${scope}`);
  const out = { start: await read() };
  const first = await upload(opt.uploads[0], "");
  await sleep(1500);  // home switches to My uploads after a session's first upload (desktop)
  out.afterFirst = await read();
  if (!opt.phone) {
    out.scopes = {};
    for (const scope of ["Samples", "Both", "My uploads"]) {
      const before = (await read()).render;
      await b.js(`document.querySelector('[data-scope-btn="${scope}"]').click()`);
      await rendered(scope, before);
      out.scopes[scope] = await read();
    }
    out.results = {};
    await b.js("(() => { const x = document.querySelector('#cv-q'); x.value = ''; x.focus(); })()");
    for (const ch of opt.query) { await b.send("Input.insertText", { text: ch }); await sleep(15); }
    await waitFor(() => b.js(`(document.querySelector('[data-view]').dataset.query || '') === ${JSON.stringify(opt.query)}`),
      30000, "the results");
    for (const scope of ["My uploads", "Samples", "Both"]) {
      const before = (await read()).render;
      if ((await read()).scope !== scope) {
        await b.js(`document.querySelector('[data-scope-btn="${scope}"]').click()`);
        await rendered(scope, before);
      }
      out.results[scope] = await read();
    }
    const beforeSecond = await read();  // results in Both on screen
    await upload(opt.uploads[1], first);
    await sleep(1500);
    out.afterSecond = { before: beforeSecond, after: await read() };
  }
  process.stdout.write(JSON.stringify(out));
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
} finally {
  if (b) await b.close();
}
