// Boots shoggoth/index.html in jsdom against the recorded answers in api.json (plus overrides), for tests that need the real page.
const fs = require("fs"), path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "shoggoth", "index.html"), "utf8");
const open = [];
const wait = ms => new Promise(r => setTimeout(r, ms));
async function boot(overrides = {}) {
  const api = Object.assign(JSON.parse(fs.readFileSync(path.join(__dirname, "api.json"), "utf8")), overrides), problems = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => problems.push("jsdom: " + (e.detail && e.detail.message || e.message)));
  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:13306/", runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = async (u, opts = {}) => {
        const k = String(u).replace(/^https?:\/\/[^/]+/, "").replace(/[?&]_=\d+/, ""), method = (opts.method || "GET").toUpperCase();
        const body = method === "GET" && (k in api || k.split("?")[0] in api) ? JSON.stringify(api[k] || api[k.split("?")[0]]) : "{}";
        return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => JSON.parse(body), text: async () => body };
      };
      w.EventSource = class { constructor() { this.readyState = 1; } close() {} addEventListener() {} };
      w.matchMedia = w.matchMedia || (q => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
      w.ResizeObserver = w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
      w.HTMLElement.prototype.scrollIntoView = function () {};
      Object.defineProperty(w.navigator, "clipboard", { value: { writeText: async () => {} } });
    },
  });
  open.push(dom.window);
  await wait(900);
  return { w: dom.window, problems, wait };
}
const closeAll = () => open.splice(0).forEach(w => w.close());      // stops the page's timers so node can exit
module.exports = { boot, closeAll, html };
