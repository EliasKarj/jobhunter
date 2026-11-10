// Kuvakaappaukset README:hen: node scripts/screenshots.mjs [http://127.0.0.1:8765]
// Käynnistää Chromen headless-tilassa ja ohjaa sitä DevTools-protokollalla (ei lisäriippuvuuksia, Node 22+).
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const OUT = new URL("../../docs/screenshots/", import.meta.url).pathname.replace(/^\/([A-Z]:)/, "$1");
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PORT = 9333;
const RUST = "https://duunitori.fi/tyopaikat/tyo/junioriohjelmistokehittaja-harjoittelija-tai-opinnaytetyontekija-rust-ja-tekoalysovellukset-stsyo-20634488";
const ICT = "https://duunitori.fi/tyopaikat/tyo/ict-asiantuntija-vt13-svser-20695919";

mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "jh-"))}`,
  "--no-first-run", "--hide-scrollbars", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function target() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const p = list.find((t) => t.type === "page");
      if (p) return p.webSocketDebuggerUrl;
    } catch { /* käynnistyy vielä */ }
    await sleep(200);
  }
  throw new Error("Chrome ei käynnistynyt");
}

const ws = new WebSocket(await target());
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let id = 0;
const pending = new Map();
ws.addEventListener("message", (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;

async function waitFor(expr, ms = 120000) {
  const t = Date.now();
  while (Date.now() - t < ms) { if (await evaluate(`!!(${expr})`)) return true; await sleep(300); }
  console.warn("  ! aikakatkaisu:", expr);
  return false;
}

async function shot(name, { path, width = 1440, height = 1000, dark = false, ready = "document.querySelector('main')", before, full = false }) {
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 600 });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }] });
  await send("Page.navigate", { url: BASE + path });
  await sleep(800);
  await waitFor(ready);
  if (before) await before();
  await sleep(600);
  const clip = full ? { x: 0, y: 0, width, height: Math.min(4000, await evaluate("document.documentElement.scrollHeight")), scale: 1 } : undefined;
  const { result } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: full, ...(clip ? { clip } : {}) });
  writeFileSync(join(OUT, name + ".png"), Buffer.from(result.data, "base64"));
  console.log("  ✓", name);
}

await send("Page.enable");
await send("Runtime.enable");
try {
  await shot("etusivu", { path: "/", ready: "document.querySelectorAll('.job-row').length > 5" });
  await shot("etusivu-tumma", { path: "/", dark: true, ready: "document.querySelectorAll('.job-row').length > 5" });
  await shot("etusivu-kapea", { path: "/", width: 400, height: 860, ready: "document.querySelectorAll('.job-row').length > 5" });
  await shot("ilmoitus", {
    path: "/ilmoitus?url=" + encodeURIComponent(ICT), ready: "document.querySelector('.verdict') || document.querySelector('#fit-title')",
    before: async () => { await evaluate("document.querySelector('.refbtn')?.click()"); await sleep(900); },
  });
  await shot("hakemuspaketti", { path: "/hakemus?url=" + encodeURIComponent(RUST), ready: "document.querySelector('.pkg-section')", full: true, height: 1000 });
  await shot("chat", {
    path: "/kysy", ready: "document.querySelector('.suggest button')", height: 1100,
    before: async () => {
      await evaluate("document.querySelector('.suggest button').click()");
      await waitFor("document.querySelector('.sources')", 300000);
    },
  });
  await shot("seuranta", { path: "/seuranta", ready: "document.querySelector('.board') || document.querySelector('.empty')" });
  await shot("evaluointi", { path: "/evaluointi", ready: "document.querySelector('table.data')" });
  await shot("profiili", { path: "/profiili", ready: "document.querySelector('.pkg-section')" });
  await shot("profiili-haku", { path: "/profiili?tab=haku", ready: "document.querySelector('.settings textarea')" });
  await shot("yritykset", { path: "/yritykset", ready: "document.querySelector('.section-head')" });
  await shot("asetukset", { path: "/asetukset", ready: "document.querySelector('.settings')", full: true });
} finally {
  ws.close();
  chrome.kill();
}
