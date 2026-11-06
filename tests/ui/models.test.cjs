// The Models page shows every recommendation whatever the graphics card, and its cards read cleanly. Run: node tests/ui/models.test.cjs
const assert = require("node:assert/strict");
const { test, after } = require("node:test");
const { boot, closeAll, html } = require("./page.cjs");
after(closeAll);

const GTX_1080_TI = {
  "/ui/gpu": { gpus: [{ nv_index: 0, uuid: "GPU-1080", name: "NVIDIA GeForce GTX 1080 Ti", key: "GPU-1080", vendor: "NVIDIA", mem_used_mb: 600, mem_total_mb: 11264, util: 0, temp: 40, procs: [] }] },
  "/ui/info": { device: "", devices: [{ id: "cuda0", name: "NVIDIA GeForce GTX 1080 Ti", mib: 11264, gpu_key: "GPU-1080", nv_index: 0 }], backend: { current: "auto", resolved: "cuda" }, mode: "builtin" },
};

test("a computer with 11 GB of graphics memory is still shown every recommendation", async () => {
  const { w, wait } = await boot(GTX_1080_TI);
  w.eval('S.gpus = ' + JSON.stringify(GTX_1080_TI["/ui/gpu"].gpus) + '; S.info = Object.assign({}, S.info, ' + JSON.stringify({ device: "", devices: GTX_1080_TI["/ui/info"].devices }) + ');');
  const fit = w.eval("[5.2, 23.8].map(g => (fitInfo({size: g, recipe: 'llamacpp'}) || {}).cls)");
  assert.deepEqual(Array.from(fit), ["ok", "over"], "the test card must really be too small for the biggest pick");
  w.eval('LIB.tab = "recommended"; show("models"); renderModels()');
  await wait(300);
  const shown = Array.from(w.document.querySelectorAll("#v-models .card[data-id]")).map(c => c.dataset.id);
  const recs = w.eval("recModels().recommended.map(r => r.id)");
  assert.ok(recs.length >= 5, "the fixture should carry the recommendations");
  for (const id of recs) assert.ok(shown.includes(id), id + " is hidden");
});

test("a pick that is too big is shown with a plain note and can still be fetched", async () => {
  const { w, wait } = await boot(GTX_1080_TI);
  w.eval('S.gpus = ' + JSON.stringify(GTX_1080_TI["/ui/gpu"].gpus) + '; S.info = Object.assign({}, S.info, {device: "", devices: ' + JSON.stringify(GTX_1080_TI["/ui/info"].devices) + '}); LIB.tab = "recommended"; renderModels()');
  await wait(200);
  const big = w.document.querySelector('#v-models .card[data-id="Qwen3.6-35B-A3B-MTP-GGUF"]');
  assert.ok(big, "the biggest pick is missing");
  assert.match(big.querySelector(".fitnote").textContent, /too big|slow/i);
  assert.ok(big.querySelector("[data-dl],[data-recget]"), "no way to get it");
});

test("the first-run screen offers a card for every size", async () => {
  const { w } = await boot(GTX_1080_TI);
  w.eval('S.gpus = ' + JSON.stringify(GTX_1080_TI["/ui/gpu"].gpus) + '; S.info = Object.assign({}, S.info, {device: "", devices: ' + JSON.stringify(GTX_1080_TI["/ui/info"].devices) + '})');
  const sizes = w.eval("wFirstModels().map(o => o.tier)");
  assert.deepEqual(Array.from(sizes), ["Small", "Medium"]);
  assert.ok(w.eval("wFirstModels().every(o => o.m)"));
});

test("names read as names, and two models never share a title", async () => {
  const { w } = await boot();
  const t = w.eval("friendly");
  assert.equal(t("gemma-3-270m-it-UD-IQ2_M"), "Gemma 3 270M UD IQ2 M");
  assert.equal(t("gpt-oss-20b"), "GPT OSS 20B");
  assert.equal(t("bge-reranker-v2-m3-GGUF"), "BGE Reranker V2 M3");
  assert.equal(t("nomic-embed-text-v1-GGUF"), "Nomic Embed Text V1");
  assert.equal(t("Qwen3-8B-GGUF"), "Qwen3 8B");                      // already fine: unchanged
  const titles = w.eval("libTitles([{id:'SD-Turbo'},{id:'SD-Turbo-GGUF'},{id:'Qwen3-8B-GGUF'}])");
  assert.equal(new Set(Object.values(titles)).size, 3);
  assert.equal(titles["Qwen3-8B-GGUF"], "Qwen3 8B");
});

test("saved settings wrap instead of being cut off", () => {
  const rule = html.match(/\.card \.optline\{[^}]*\}/)[0];
  assert.doesNotMatch(rule, /nowrap/);
  assert.doesNotMatch(rule, /text-overflow/);
});

test("the folder chooser can't make the page wider than the screen", () => {
  assert.match(html, /\.libplaces[^{]*\{[^}]*min-width:\s*0/);
});
