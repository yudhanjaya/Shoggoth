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

// ---- unslop polish: what the audit found on All models and Connect a server
const WORLD = {
  "/api/v1/models?show_all=true": { data: [
    { id: "Phishing-Email-Detection-ONNX", recipe: "onnxruntime", labels: ["classification"], size: 0.27, downloaded: false },
    { id: "MOSS-TTS-Local", recipe: "openmoss", labels: ["tts"], size: 4, downloaded: false },
    { id: "TRELLIS-3D", recipe: "trellis", labels: ["3d"], size: 6, downloaded: false },
    { id: "Qwen3-8B-GGUF", recipe: "llamacpp", labels: ["chat", "hot", "tool-calling"], size: 5, downloaded: false, checkpoint: "unsloth/Qwen3-8B-GGUF:Q4_K_M" },
    { id: "Qwen3-4B-GGUF", recipe: "llamacpp", labels: ["chat"], size: 2.5, downloaded: true },
  ] },
};

test("a model that isn't for chat says what it does, not 'everyday chat'", async () => {
  const { w } = await boot(WORLD);
  const say = id => w.eval(`whatFor(S.models.find(m => m.id === ${JSON.stringify(id)}))`);
  assert.equal(say("Phishing-Email-Detection-ONNX"), "Sorts text into categories");
  assert.equal(say("MOSS-TTS-Local"), "Reads text aloud");
  assert.equal(say("TRELLIS-3D"), "Makes 3D models");
  assert.equal(say("Qwen3-8B-GGUF"), "Can use tools");
});

test("tags are words, not engine ids", async () => {
  const { w } = await boot(WORLD);
  assert.equal(w.eval('tagWord("llamacpp")'), "llama.cpp");
  assert.equal(w.eval('tagWord("tool-calling")'), "Tool use");
  assert.equal(w.eval('tagWord("chat")'), "Chat");
  assert.equal(w.eval('tagWord("sd-cpp")'), "stable-diffusion.cpp");
});

test("All models doesn't light up a hundred yellow buttons, and doesn't repeat the engine name on every card", async () => {
  const { w, wait } = await boot(WORLD);
  w.eval('SU.st = Object.assign({}, SU.st || {}, {lemonade: {reachable: true}}); S.dlSupported = true; LIB.tab = "everything"; LIB.kind = "all"; show("models"); renderModels()');
  await wait(300);
  const doc = w.document;
  assert.equal(doc.querySelectorAll("#v-models .card [data-dl].primary").length, 0, "Get it should be quiet in the long list");
  assert.ok(doc.querySelectorAll("#v-models .card [data-dl]").length >= 1);
  assert.ok(doc.querySelector('#v-models .card [data-chat].primary'), "Chat with it stays the one bright button on a card you have");
  assert.equal(doc.querySelectorAll("#v-models .card summary .eng").length, 0);
  // and on Recommended, the pick you came for still gets the bright button
  w.eval('LIB.tab = "recommended"; renderModels()');
  await wait(200);
  assert.ok(doc.querySelectorAll("#v-models .card .btn.primary").length >= 1);
});

test("cards stay put on hover and sit on the 4px grid", () => {
  assert.doesNotMatch(html.match(/\.card:hover\{[^}]*\}/)[0], /transform/);
  assert.doesNotMatch(html.match(/\.card\.loaded\{[^}]*\}/)[0], /gradient/);
  assert.match(html.match(/\.card \.ctop\{[^}]*\}/)[0], /gap:8px 12px/);
});

test("the connect form's controls are one height and one shape", () => {
  const css = html.match(/#srvform select[^{]*\{[^}]*\}/g).join(" ");
  assert.match(css, /min-height:40px/);
  assert.match(css, /border-radius:10px/);
  assert.match(html, /#srvresult:empty\{display:none\}/);
});

test("the Large models group says where very large models run better, with the three GitHub links", async () => {
  const { w, wait } = await boot();
  for (const tab of ["recommended", "everything"]) {
    w.eval(`LIB.tab = "${tab}"; show("models"); renderModels()`);
    await wait(250);
    const note = w.document.querySelector("#v-models .largenote");
    assert.ok(note, tab + ": no note above the Large models group");
    assert.ok(note.closest("section").querySelector("h3").textContent.includes("Large models"), tab + ": note is not in the Large group");
    const hrefs = Array.from(note.querySelectorAll("a")).map(a => a.href);
    assert.deepEqual(hrefs, ["https://github.com/Niko1221/Strata", "https://github.com/jesdga95/basalt", "https://github.com/mw00/project-maya"]);
    assert.match(note.textContent, /Lemonade and llama\.cpp are not yet as good as Strata, Basalt and Maya/);
    assert.ok(!/, and /.test(note.textContent.replace("Lemonade and llama.cpp", "")), "a sentence is joined with , and");
  }
});
