// The upload bar's report shows the latest upload's files (tests/test_ui_browser.py), against a running Cortex
// Vault in demo mode.
//
//   node report_check.mjs '<json options>'
//
// Options: url, browser, uploads (lists of file paths: each list is one upload, through the Add files picker).
// After each upload is done, opens "Upload report" and reads the file names in it. Prints one JSON object.
import { launch, openApp, sleep, waitFor } from "./cdp.mjs";

const opt = JSON.parse(process.argv[2]);
let b;
try {
  b = await launch(opt.browser);
  await openApp(b, opt.url);
  await b.send("DOM.enable");
  const reports = [];
  let last = "";
  for (const files of opt.uploads) {
    const { root } = await b.send("DOM.getDocument", { depth: -1 });
    const { nodeId } = await b.send("DOM.querySelector", { nodeId: root.nodeId, selector: "[data-files]" });
    await b.send("DOM.setFileInputFiles", { nodeId, files });
    const upload = await waitFor(() => b.js(`(() => { const d = document.querySelector('[data-status-src]').dataset;
      return d.stage === 'done' && d.upload && d.upload !== ${JSON.stringify(last)} ? d.upload : ''; })()`),
    60000, "the upload to finish");
    last = upload;
    await sleep(300);
    await b.js(`(() => { const r = document.querySelector('[data-act="report"]');
      if (r.getAttribute('aria-expanded') !== 'true') r.click(); return true; })()`);
    await sleep(300);
    reports.push(await b.js(`(() => { const p = document.querySelector('[data-bar-report]');
      return { upload: ${JSON.stringify(upload)}, shown: !p.hidden, text: p.textContent }; })()`));
  }
  process.stdout.write(JSON.stringify({ reports }));
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
} finally {
  if (b) await b.close();
}
