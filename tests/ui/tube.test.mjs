// U6: the tube on wide screens. The layout the page picks at each width, the portrait frame the generator makes for the tall
// tube, and the collapsed column remembered in localStorage.
// Run: node tests/ui/tube.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { boot, closeAll } from "./page.cjs";

const html = readFileSync(new URL("../../shoggoth/index.html", import.meta.url), "utf8");
const slice = (from, to) => {
  const a = html.indexOf(from), b = html.indexOf(to, a + 1);
  assert.ok(a > 0 && b > a, from + " ... " + to + " not found in index.html");
  return html.slice(a, b);
};

// ---- the generator: one function, two frames
{
  const a = html.indexOf("/* ======================= creatures"), b = html.indexOf("const crCache");
  const ctx = {}; vm.createContext(ctx);
  vm.runInContext(slice("/* ======================= creatures", "const crCache") + "\nthis.generate = generate;", ctx);
  const card = o => ({ seed: "qwen3.6-35b-a3b", params: 35, quant: "Q4_K_M", ctx: 128, vision: true, reasoning: true, tools: true, tone: 0, form: 3, ...o });

  const square = ctx.generate(card());
  assert.equal(square.w, 480, "the default frame keeps its width");
  assert.equal(square.h, 420, "the default frame keeps its height (so every existing picture is unchanged)");

  const portrait = ctx.generate(card({ aspect: 0.62 }));
  assert.equal(portrait.w, 480, "the portrait frame keeps the width");
  assert.ok(portrait.h > 700, "the portrait frame is much taller: " + portrait.h);
  assert.ok(portrait.h > square.h, "the portrait frame has the room a tall tube needs");
  // the same body, moved to the foot of the taller frame: the paths are a pure translate of the square drawing, never a redraw
  const shift = portrait.shadow.y - square.shadow.y;
  const nums = d => (d.match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
  const moved = (a, b) => { const A = nums(a), B = nums(b); if(A.length !== B.length) return false;
    for(let i = 0; i < A.length; i++){ const dy = i % 2 ? shift : 0; if(Math.abs((A[i] - B[i]) - dy) > 0.2) return false; }
    return true; };
  assert.equal(portrait.runs.length, square.runs.length, "the portrait body has different faces");
  for(let i = 0; i < square.runs.length; i++){
    assert.equal(portrait.runs[i].c, square.runs[i].c, "face " + i + " changed colour");
    assert.ok(moved(portrait.runs[i].d, square.runs[i].d), "face " + i + " is not the same face, moved down");
  }
  assert.ok(shift > 0, "the creature does not sit at the foot of the taller frame");
  assert.equal(portrait.shadow.rx, square.shadow.rx, "the creature was scaled, not just reframed");
  // the eyes and brain travel with the body too: an eye's matrix is a translate of the square one
  const ty = m => +m.match(/matrix\(([-\d. ]+)\)/)[1].split(" ")[5];
  assert.ok(Math.abs((ty(portrait.eyes[0].m) - ty(square.eyes[0].m)) - shift) < 0.2, "eyes did not move with the body");
}

// ---- the layout the page picks at each width: column at 1280 and up, strip from 900, a panel below
{
  const src = slice("const TUBE_KEY", "function tubeHtml");
  const fake = width => { const f = new Function("innerWidth", "store", src + "; return {tubeLayout, tubeCollapsed, setTubeCollapsed};"); return f(width, { get: (k, d) => d, set() {} }); };
  const at = w => fake(w).tubeLayout();
  assert.equal(at(1440), "column", "a widescreen gets the column");
  assert.equal(at(1280), "column", "1280 is the column's first width");
  assert.equal(at(1279), "strip", "just under 1280 is the strip");
  assert.equal(at(900), "strip", "900 is the strip's first width");
  assert.equal(at(899), "panel", "just under 900 is the panel");
  assert.equal(at(640), "panel", "a phone gets the panel");
}

// ---- the CSS agrees with the code's breakpoints
const css = html.slice(html.indexOf("<style>"), html.indexOf("</style>"));
assert.match(css, /@media \(min-width:1280px\)\{/, "no column rule at 1280 px");
assert.match(css, /\.view\.cw \.tubecol\{display:flex;flex-direction:column/, "the column is not a flex column");
assert.match(css, /@media \(max-width:1279\.98px\) and \(min-width:900px\)/, "the strip's range is not 900 to 1279");
assert.match(css, /@media \(max-width:899\.98px\)\{/, "no panel rule below 900 px");
assert.match(css, /\.view\.cw \.col,[^{]*\.composer[^{]*\{max-width:72ch\}/, "the chat column is not 72ch");
assert.match(css, /\.cwbar \.tubebtn:not\(\[hidden\]\)\{display:inline-flex\}/, "no button below 900 px");

// ---- the page itself: each layout paints the right piece, and a collapsed column is remembered
const MODEL = { id: "Qwen3-8B-GGUF", title: "Qwen3 8B", live: true, form: 3, tone: 1, quant: "Q4_K_M", fit: "fits", ctx: 32768 };
{
  const { w, wait } = await boot();
  await wait(400);
  w.eval(`S.pk = S.pk || {}; S.pk.models = [${JSON.stringify(MODEL)}]; S.loaded = ${JSON.stringify(MODEL.id)}`);
  const col = () => w.document.querySelector("#tubecol");
  const btn = () => w.document.querySelector("#tubebtn");
  const paint = async width => { w.innerWidth = width; w.eval('show("cowork"); hdrPaint()'); await wait(120); };

  await paint(1440);
  assert.equal(w.eval("tubeLayout()"), "column");
  assert.equal(col().hidden, false, "the column is hidden on a widescreen");
  assert.ok(w.document.querySelector("#tubehost svg.vtube"), "the column has no tube");
  assert.match(w.document.querySelector("#tubehost svg.vtube").getAttribute("viewBox"), /^0 0 300 440$/, "the column's tube is not the tall one");
  assert.ok(w.document.querySelector("#tubebench svg"), "the bench is missing under the tube");
  assert.equal(w.document.querySelector("#ptube").innerHTML.trim(), "", "the header still draws the strip beside the column");
  assert.equal(btn().hidden, true, "the panel button is showing on a widescreen");

  await paint(1100);
  assert.equal(w.eval("tubeLayout()"), "strip");
  assert.equal(col().hidden, true, "the column is showing in the strip's range");
  assert.match(w.document.querySelector("#ptube").innerHTML, /class="vtube mood-/, "the header has no strip");
  assert.equal(btn().hidden, true, "the panel button is showing in the strip's range");

  await paint(800);
  assert.equal(w.eval("tubeLayout()"), "panel");
  assert.equal(col().hidden, true, "the panel is open before its button is pressed");
  assert.equal(btn().hidden, false, "no panel button below 900 px");
  btn().click(); await wait(80);
  assert.equal(col().hidden, false, "the panel did not open");
  assert.ok(col().classList.contains("open"), "the panel is not marked open");
  assert.equal(w.eval("tubeCollapsed()"), false, "opening the panel wrote the collapsed setting");
  w.document.querySelector("#tubecolbtn").click(); await wait(80);
  assert.equal(col().hidden, true, "the panel did not close");
  assert.equal(w.eval("tubeCollapsed()"), false, "closing the panel wrote the collapsed setting");

  await paint(1440);
  assert.equal(col().classList.contains("collapsed"), false, "a fresh column started collapsed");
  w.document.querySelector("#tubecolbtn").click(); await wait(80);
  assert.equal(col().classList.contains("collapsed"), true, "the bar did not collapse the column");
  assert.equal(w.eval("tubeCollapsed()"), true, "the collapsed column is not remembered");
  assert.equal(w.localStorage.getItem("shoggoth.tubeCollapsed"), "true", "the setting did not reach localStorage");
  assert.equal(w.document.querySelector("#tubecolbody").offsetParent, null, "a collapsed column still shows its body");

  // the setting survives a change of view and of width
  w.eval('show("models")'); await wait(80);
  assert.equal(col().hidden, true, "the tube stayed on another page");
  await paint(1440);
  assert.equal(col().classList.contains("collapsed"), true, "the column came back unfolded");

  // pictures off puts the tube away everywhere, including the button
  w.eval('store.set("shoggoth.pictures", false); hdrPaint()'); await wait(80);
  assert.equal(col().hidden, true, "the column is showing with pictures off");
  assert.equal(btn().hidden, true, "the panel button is showing with pictures off");
  w.eval('store.set("shoggoth.pictures", true); hdrPaint()'); await wait(80);
  closeAll();
}

// ---- the lamp and the bench read the same numbers the model bar does
assert.match(html, /TUBE\.tps = \(S\.live && S\.live\.tps\) \|\| S\.tps \|\| 0/, "the bench does not read the live speed");
assert.match(html, /if\(S\.view === "cowork"\) paintTubeColumn\(\)/, "the page never repaints the tube while it is open");

console.log("ok: the tube column, the strip, the panel, the portrait creature and the remembered collapse");
