// The preview pane shows the selected row (tests/test_ui_browser.py), against a running Cortex Vault.
//
//   node preview_check.mjs '<json options>'
//
// Options: url, browser (its path), queries (typed in turn; the last one is typed fast, with a pause halfway so a
// half-typed search runs too), row (1-based, among the visible rows), nav ("keys": ↓ from row 1; "updown": ↓ to
// the row after it, then ↑; "click": a click, or a tap with phone), phone (a 390 px wide touch screen: the preview
// is a sheet), hold (hold every /detail response until the row is selected, then let them arrive newest first, so
// an older one lands last), noAbort (the page's aborts do nothing, so those older responses really arrive). Prints
// one JSON object: the selected row and what the pane shows.
import { launch, openApp, sleep, waitFor } from "./cdp.mjs";

const opt = { queries: [], row: 3, nav: "keys", phone: false, hold: true, ...JSON.parse(process.argv[2]) };
let b;
try {
  b = await launch(opt.browser, { phone: opt.phone });
  const held = [];  // paused /detail requests, oldest first
  b.on((msg) => {
    if (msg.method !== "Fetch.requestPaused") return;
    if (opt.hold) held.push(msg.params.requestId);
    else b.send("Fetch.continueRequest", { requestId: msg.params.requestId });
  });
  if (opt.noAbort) {  // the page can't cancel a request it no longer wants: every held response really arrives late
    await b.send("Page.addScriptToEvaluateOnNewDocument", { source: "AbortController.prototype.abort = function () {};" });
  }
  await b.send("Fetch.enable", { patterns: [{ urlPattern: "*/detail/*", requestStage: "Request" }] });
  await openApp(b, opt.url);

  const shownQuery = () => b.js("(document.querySelector('[data-view]') || {dataset: {}}).dataset.query || ''");
  const typeText = async (text, gap) => {
    for (const ch of text) { await b.send("Input.insertText", { text: ch }); await sleep(gap); }
  };
  for (const [i, q] of opt.queries.entries()) {
    await b.js("(() => { const box = document.querySelector('#cv-q'); box.value = ''; box.focus(); })()");
    if (i < opt.queries.length - 1) {
      await typeText(q, 20);
    } else {  // fast, with one pause halfway: a half-typed search runs, then the full one
      const half = Math.max(2, Math.floor(q.length / 2));
      await typeText(q.slice(0, half), 15);
      await sleep(450);
      await typeText(q.slice(half), 15);
    }
    await waitFor(async () => (await shownQuery()) === q, 30000, `results for "${q}"`);
    await sleep(150);
  }

  const press = async (key) => {
    for (const type of ["keyDown", "keyUp"]) {
      await b.send("Input.dispatchKeyEvent", { type, key, code: key, windowsVirtualKeyCode: key === "ArrowDown" ? 40 : 38 });
    }
    await sleep(60);
  };
  if (opt.nav === "keys" || opt.nav === "updown") {  // updown: one row past it, then back up
    await b.js("document.querySelector('#cv-q').focus()");
    for (let k = 1; k < opt.row + (opt.nav === "updown" ? 1 : 0); k++) await press("ArrowDown");
    if (opt.nav === "updown") await press("ArrowUp");
  } else {
    const box = await b.js(`(() => { const o = Array.from(document.querySelectorAll('[role="option"]'))
      .filter((x) => !x.hidden)[${opt.row - 1}]; o.scrollIntoView({block: 'center'});
      const r = o.querySelector('.cv-card__name').getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()`);
    if (opt.phone) {
      await b.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: box.x, y: box.y }] });
      await b.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    } else {
      for (const type of ["mousePressed", "mouseReleased"]) {
        await b.send("Input.dispatchMouseEvent", { type, x: box.x, y: box.y, button: "left", clickCount: 1 });
      }
    }
  }
  await sleep(200);
  const before = held.length;
  let released = 0, gone = 0;
  while (held.length) {  // newest first: the oldest response lands last
    try {
      await b.send("Fetch.continueRequest", { requestId: held.pop() });
      released += 1;
    } catch (e) {
      gone += 1;  // the page aborted it (a newer request replaced it): nothing to let through
    }
    await sleep(40);
  }
  await sleep(800);

  const state = await b.js(`(() => {
    const root = document.querySelector('[data-view]');
    const opts = Array.from(document.querySelectorAll('[role="option"]'));
    const sel = opts.find((o) => o.getAttribute('aria-selected') === 'true');
    const body = document.querySelector('[data-pv]');
    const pane = document.querySelector('.cv-detail');
    const txt = (s) => { const e = body && body.querySelector(s); return e ? e.textContent.trim() : null; };
    const img = body && body.querySelector('.cv-frame img');
    return {
      query: root.dataset.query, render: root.dataset.render,
      selected: sel ? { id: sel.dataset.id, title: sel.dataset.title, type: sel.dataset.type,
                        number: sel.querySelector('.cv-card__rank').textContent.trim(),
                        visibleIndex: opts.filter((x) => !x.hidden).indexOf(sel) + 1 } : null,
      pane: { kicker: txt('.cv-detail__kicker'), title: txt('.cv-detail__title'), img: img ? img.getAttribute('src') : null,
              file: body ? (body.dataset.file || null) : null, render: body ? (body.dataset.render || null) : null,
              detailFor: body ? (body.dataset.detailFor || null) : null,
              boxes: body ? body.querySelectorAll('.cv-hl').length : 0,
              open: !!(pane && pane.classList.contains('is-open')) },
    };
  })()`);
  process.stdout.write(JSON.stringify({ ...state, held: before, released, aborted: gone }));
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
} finally {
  if (b) await b.close();
}
