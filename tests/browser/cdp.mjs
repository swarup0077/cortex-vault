// A headless Chromium-family browser (Edge or Chrome) driven through the DevTools protocol, for the browser checks
// in this folder (tests/test_ui_browser.py). Node 22+ only: Node's own WebSocket and fetch, no packages.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function waitFor(fn, ms = 20000, what = "condition") {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await sleep(50);
  }
}

/* One page (tab) over its DevTools WebSocket: { send, js, on, close }. */
async function connect(wsUrl, phone) {
  let seq = 0;
  const pending = new Map();
  const listeners = [];
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p.reject(new Error(`${p.method}: ${msg.error.message}`)); else p.resolve(msg.result);
    } else if (msg.method) listeners.forEach((f) => f(msg));
  };
  const send = (method, params = {}) => {
    const id = ++seq;
    ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject, method }));
  };
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", phone
    ? { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }
    : { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  if (phone) await send("Emulation.setTouchEmulationEnabled", { enabled: true });
  const js = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(`${expr.slice(0, 80)}: ${r.exceptionDetails.text}`);
    return r.result.value;
  };
  return { send, js, on: (f) => listeners.push(f), close: () => { try { ws.close(); } catch (e) { /* closing */ } } };
}

/* Start the browser with a throwaway profile and connect to its page. Returns { send, js, on, close, newTab }:
   newTab() opens another tab of the same browser (same cookies) and returns its own { send, js, on, close }. */
export async function launch(browser, { phone = false } = {}) {
  const profile = mkdtempSync(join(tmpdir(), "cv-browser-"));
  const proc = spawn(browser, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-gpu", "about:blank"],
    { stdio: "ignore" });
  const tabs = [];
  const close = async () => {
    tabs.forEach((t) => t.close());
    proc.kill();
    await sleep(300);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* the browser may still hold a file */ }
  };
  try {
    const portFile = join(profile, "DevToolsActivePort");
    await waitFor(() => existsSync(portFile) && readFileSync(portFile, "utf8").includes("\n"), 20000, "the browser");
    const port = readFileSync(portFile, "utf8").split("\n")[0].trim();
    const page = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === "page");
    const first = await connect(page.webSocketDebuggerUrl, phone);
    tabs.push(first);
    const newTab = async () => {
      const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
      const tab = await connect(target.webSocketDebuggerUrl, phone);
      tabs.push(tab);
      return tab;
    };
    return { ...first, close, newTab };
  } catch (e) {
    await close();
    throw e;
  }
}

/* Open the app and wait until cortex.js has mounted its parts. */
export async function openApp(b, url) {
  await b.send("Page.navigate", { url });
  await waitFor(() => b.js("!!document.querySelector('#cv-q') && !!document.querySelector('[data-view]')"), 30000,
    "the page");
}

/* Add files the way a visitor does: choose them in the page's file picker, then Upload in the review tray. */
export async function upload(b, files) {
  await b.send("DOM.enable");
  const { root } = await b.send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await b.send("DOM.querySelector", { nodeId: root.nodeId, selector: "[data-files]" });
  await b.send("DOM.setFileInputFiles", { nodeId, files });
  await waitFor(() => b.js("!document.querySelector('[data-tray]').hidden"), 10000, "the upload tray");
  await b.js("document.querySelector('[data-act=tray-upload]').click()");
}
