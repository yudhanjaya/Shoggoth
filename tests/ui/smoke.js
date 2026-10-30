// Loads shoggoth/index.html in jsdom against recorded API answers, opens every page and exits 1 on any script error.
//   node smoke.js                       replay tests/ui/api.json
//   node smoke.js --record URL TOKEN    walk the pages against a running Shoggoth and write api.json (paths are made anonymous)
const fs = require("fs"), path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");
const html = fs.readFileSync(process.env.SHOGGOTH_HTML || path.join(__dirname, "..", "..", "shoggoth", "index.html"), "utf8");
const recording = process.argv[2] === "--record";
const base = recording ? process.argv[3] : "", token = recording ? process.argv[4] : "";
const apiFile = path.join(__dirname, "api.json");
const api = recording ? {} : JSON.parse(fs.readFileSync(apiFile, "utf8"));
const problems = [], unknown = new Set();
const clean = t => t.replace(/\/home\/[^/"]+/g, "/home/user").replace(/\/media\/[^/"]+/g, "/media/user").replace(/\/tmp\/claude-[^"]*?\/scratchpad\/[^/"]*/g, "/home/user/zest");

const vc = new VirtualConsole();
vc.on("jsdomError", e => problems.push("jsdom: " + (e.detail && e.detail.message || e.message).slice(0, 200)));
vc.on("error", (...a) => problems.push("console.error: " + a.map(String).join(" ").slice(0, 200)));

const dom = new JSDOM(html, {
  url: "http://127.0.0.1:13306/", runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    const key = u => String(u).replace(/^https?:\/\/[^/]+/, "").replace(/[?&]_=\d+/, "");
    w.fetch = async (u, opts = {}) => {
      const k = key(u), method = (opts.method || "GET").toUpperCase();
      let status = 200, body;
      if (recording) {
        const r = await fetch(base + k, { method, headers: Object.assign({ Authorization: "Bearer " + token }, opts.headers || {}), body: method === "GET" ? undefined : opts.body });
        status = r.status; body = await r.text();
        if (method === "GET" && status === 200 && (r.headers.get("content-type") || "").includes("json")) api[k] = JSON.parse(clean(body));
      } else if (method === "GET" && k in api) body = JSON.stringify(api[k]);
      else if (method === "GET" && k.split("?")[0] in api) body = JSON.stringify(api[k.split("?")[0]]);
      else { unknown.add(method + " " + k.split("?")[0]); body = "{}"; }
      return { ok: status < 400, status, headers: { get: () => "application/json" }, json: async () => JSON.parse(body || "{}"), text: async () => body, body: null, clone() { return this; } };
    };
    w.EventSource = class { constructor() { this.readyState = 1; } close() {} addEventListener() {} };
    w.matchMedia = w.matchMedia || (q => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    w.ResizeObserver = w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.HTMLElement.prototype.scrollIntoView = function () {};
    w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    w.HTMLDialogElement.prototype.show = w.HTMLDialogElement.prototype.showModal;
    w.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
    Object.defineProperty(w.navigator, "clipboard", { value: { writeText: async () => {} } });
    w.addEventListener("error", e => problems.push("error: " + e.message + " @" + (e.lineno || "")));
    w.addEventListener("unhandledrejection", e => problems.push("rejection: " + String(e.reason && e.reason.message || e.reason).slice(0, 200)));
  },
});
const w = dom.window, wait = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  await wait(700);
  const views = w.eval("Object.keys(TITLES)");
  const seen = [];
  for (const v of views) {
    try { w.eval(`show(${JSON.stringify(v)})`); } catch (e) { problems.push(`show(${v}): ${e.message}`); }
    const el = w.document.getElementById("v-" + v);
    for (let i = 0; i < 12 && el && !el.textContent.trim(); i++) await wait(250);       // some pages fill in after their first answers arrive
    seen.push(v + ":" + (el ? el.textContent.trim().length : "no-element"));
    if (el && !el.textContent.trim()) problems.push(`page "${v}" is empty`);
  }
  // open every <details> on the engine and settings pages and every dialog opener that needs no input
  for (const v of ["backends", "models"]) { w.eval(`show(${JSON.stringify(v)})`); await wait(300); w.document.querySelectorAll(`#v-${v} details`).forEach(d => (d.open = true)); await wait(200); }
  const add = w.document.getElementById("libadd"); if (add) add.click();
  await wait(300);
  // the Hardware bench draws one mainframe per card, for one card and for four
  for (const n of [1, 4]) {
    w.eval(`S.gpus = Array.from({length: ${n}}, (_, i) => ({key: "g" + i, name: "NVIDIA GeForce RTX 4090", mem_total_mb: 24576, mem_used_mb: 9000, util: 40, temp: 55, power: 200, procs: [], models: [{model: "Qwen3.6 35B", mem_mb: 9000}]})); show("hardware"); hwPaint();`);
    const frames = w.document.querySelectorAll("#hwrig .hwframe").length;
    if (frames !== n) problems.push(`hardware bench: ${frames} mainframes for ${n} cards`);
  }
  // and every page again with model pictures turned off (the Model pictures switch)
  w.localStorage.setItem("shoggoth.pictures", "false");
  for (const v of views) { try { w.eval(`show(${JSON.stringify(v)})`); } catch (e) { problems.push(`pictures off, show(${v}): ${e.message}`); } await wait(60); }
  w.localStorage.removeItem("shoggoth.pictures");
  if (recording) { fs.writeFileSync(apiFile, JSON.stringify(api, null, 1)); console.log(`recorded ${Object.keys(api).length} answers`); }
  console.log("pages: " + seen.join(" "));
  if (unknown.size && !recording) console.log("answers not recorded (served as {}): " + [...unknown].slice(0, 12).join(", "));
  if (problems.length) { console.log("PROBLEMS:\n  " + [...new Set(problems)].slice(0, 15).join("\n  ")); process.exit(1); }
  console.log("ok: every page opened with no script errors");
  process.exit(0);
})();
