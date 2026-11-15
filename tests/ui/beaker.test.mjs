// The beaker at the top of the chat's right sidebar, the chat's controls in the page header, and the sidebar shown by default.
// Also the portrait frame the creature generator makes for the beaker.
// Run: node tests/ui/beaker.test.mjs
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
  assert.ok(portrait.h > square.h, "the portrait frame has the room the beaker needs");
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

// ---- the beaker itself: empty when nothing is loaded
{
  const { w, wait } = await boot();
  await wait(300);
  const M = { id: "Qwen3-8B-GGUF", title: "Qwen3 8B", live: true, form: 3, tone: 1, quant: "Q4_K_M", fit: "fits", ctx: 32768 };
  const empty = w.eval("beakerSvg(null, 'none')");
  assert.doesNotMatch(empty, /class="tb-in"/, "an empty beaker has no creature in it");
  assert.match(empty, /class="tb-liq"[^>]*height="0\.0"/, "an empty beaker has no liquid in it");
  assert.match(empty, /class="trace"/, "the beaker stands on a circuit board");
  w.eval("S.gpus = [{mem_total_mb: 10000, mem_used_mb: 5000}]");
  const full = w.eval(`beakerSvg(${JSON.stringify(M)}, 'idle')`);
  assert.match(full, /class="tb-in"/, "a loaded model stands in the beaker");
  assert.match(full, /class="tb-liq"[^>]*height="100\.0"/, "the liquid shows how full graphics memory is (half of 200)");

  // ---- on the page: the sidebar is open, the beaker is at its top, and the bars are in the header
  w.innerWidth = 1440;
  w.eval('show("cowork")'); await wait(150);
  const $ = q => w.document.querySelector(q);
  assert.equal($("#beakbox").parentElement.id, "cwside", "the beaker is not in the right sidebar");
  assert.equal($("#cwside").firstElementChild.id, "beakbox", "the beaker is not at the top of the sidebar");
  assert.ok($("#beakbox svg.vbeak"), "no beaker drawn");
  assert.doesNotMatch($("#beakbox").innerHTML, /class="tb-in"/, "nothing is loaded, but the beaker has a creature in it");
  assert.equal($("#cwbar").parentElement.id, "topctl", "the folder bar is not in the header");
  assert.equal($("#cwmbar").parentElement.id, "topctl", "the model bar is not in the header");
  assert.ok($(".top").classList.contains("cwtop"), "the header doesn't know it is the chat's");
  assert.ok(!$("#ptube") && !$("#tubecol") && !$("#harness"), "the old tube is still on the page");
  assert.ok($("#cwsidebody").innerHTML.includes("Folder"), "the sidebar's details are not under the beaker");

  // the sidebar is shown by default; Details hides it and that is remembered
  assert.equal($("#v-cowork").classList.contains("sidehidden"), false, "the sidebar starts hidden");
  assert.equal($("#cwtoggle-side").getAttribute("aria-expanded"), "true");
  $("#cwtoggle-side").click(); await wait(50);
  assert.equal($("#v-cowork").classList.contains("sidehidden"), true, "Details did not hide the sidebar");
  assert.equal(w.localStorage.getItem("shoggoth.sideHidden"), "true", "hiding the sidebar is not remembered");
  $("#cwtoggle-side").click(); await wait(50);
  assert.equal($("#v-cowork").classList.contains("sidehidden"), false, "Details did not show the sidebar again");

  // a narrow screen opens it over the chat instead, without touching the remembered setting
  w.innerWidth = 800; w.eval("cwSideApply()");
  $("#cwtoggle-side").click(); await wait(50);
  assert.ok($("#cwside").classList.contains("open"), "Details did not open the sidebar on a narrow screen");
  assert.equal(w.localStorage.getItem("shoggoth.sideHidden"), "false");

  // other pages leave the header alone
  w.eval('show("models")'); await wait(80);
  assert.ok(!$(".top").classList.contains("cwtop"), "the chat's controls stay in the header on another page");

  // pictures off: the beaker goes, the switch to bring it back stays
  w.eval('show("cowork"); store.set("shoggoth.pictures", false); hdrPaint()'); await wait(50);
  assert.ok(!$("#beakbox svg"), "the beaker is drawn with pictures off");
  assert.ok($("#beakbox #picsw"), "the switch to turn pictures back on is gone");
  closeAll();
}

// ---- the CSS: the bars sit in the header, the sidebar stands beside the chat from 1000 px
const css = html.slice(html.indexOf("<style>"), html.indexOf("</style>"));
assert.match(css, /\.topctl \.cwbar,\.topctl \.mbar\{display:contents\}/, "the bars don't join the header row");
assert.match(css, /\.view\.cw\.sidehidden \.cwside\{display:none\}/, "the sidebar can't be hidden");
assert.match(css, /@media \(max-width:999\.98px\)\{\s*\.cwside,/, "the sidebar is not a drawer below 1000 px");
assert.doesNotMatch(css, /@media \(max-width:1180px\)\{\s*\.cwside\{position:absolute/, "the sidebar is still a drawer at 1000 to 1180 px");

console.log("ok: the beaker, the header controls and the sidebar");
