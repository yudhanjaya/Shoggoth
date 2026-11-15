// The Models page shows every recommendation whatever the graphics card, and its cards read cleanly. Run: node tests/ui/models.test.cjs
const assert = require("node:assert/strict");
const { test, after } = require("node:test");
const { boot, closeAll, html } = require("./page.cjs");
after(closeAll);

const GTX_1080_TI = {
  "/ui/gpu": { gpus: [{ nv_index: 0, uuid: "GPU-1080", name: "NVIDIA GeForce GTX 1080 Ti", key: "GPU-1080", vendor: "NVIDIA", mem_used_mb: 600, mem_total_mb: 11264, util: 0, temp: 40, procs: [] }] },
  "/ui/info": { device: "", devices: [{ id: "cuda0", name: "NVIDIA GeForce GTX 1080 Ti", mib: 11264, gpu_key: "GPU-1080", nv_index: 0 }], backend: { current: "auto", resolved: "cuda" }, mode: "builtin" },
};

// The server prices each pick and sends fit/need_gb/why with it. The page only draws the badge.
const PICKS = {
  "/ui/models/list": { data: [] },
  "/ui/library": { root: "", chosen: false, places: [], machine: { ram_gb: 32, vram_gb: 16, best_gpu_gb: 16, gpus: ["RTX"] },
    tiers: [], models: [], bytes: 0, disk: null, missing: false, views: {} },
  "/ui/recommendations": { models: { recommended: [
    { id: "Runs-Well-GGUF", title: "Runs Well", size_gb: 5.2, fit: "fits", need_gb: 5.7, ctx: 32768,
      ctx_why: "Set to 32k, the most that fits in 16 GB of graphics memory." },
    { id: "Tight-GGUF", title: "Tight One", size_gb: 14, fit: "tight", need_gb: 15.4,
      why: "Needs about 15 GB; it will run partly on the processor, so it will be slower." },
    { id: "Partial-GGUF", title: "Partial One", size_gb: 23.8, fit: "partial", need_gb: 26.2,
      why: "Needs about 26 GB; the experts run on the processor (10 GB of RAM), so it is slower than on the graphics card." },
    { id: "Too-Big-GGUF", title: "Too Big One", size_gb: 40, fit: "no", need_gb: 44 },
  ], coming_soon: [], first_run: [] }, tools: { tools: [], places: {} }, problems: [], files: {} },
};

test("every recommendation is shown whatever its fit, each with the server's badge", async () => {
  const { w, wait } = await boot(PICKS);
  w.eval('LIB.tab = "recommended"; show("models"); renderModels()');
  await wait(300);
  const card = id => w.document.querySelector(`#v-models .card[data-id="${id}"]`);
  const say = id => card(id) && card(id).querySelector(".fitnote").textContent;
  for (const id of ["Runs-Well-GGUF", "Tight-GGUF", "Partial-GGUF", "Too-Big-GGUF"]) assert.ok(card(id), id + " is hidden");
  assert.match(say("Runs-Well-GGUF"), /Runs well/);
  assert.match(say("Tight-GGUF"), /Tight/);
  assert.match(say("Partial-GGUF"), /Runs partly on the processor/);
  assert.match(say("Too-Big-GGUF"), /Too big for this computer, needs about 44 GB/);
  assert.match(card("Runs-Well-GGUF").textContent, /Context: 32k/);
  assert.match(card("Runs-Well-GGUF").textContent, /most that fits in 16 GB/);
  assert.equal(card("Runs-Well-GGUF").querySelector("h3").textContent, "Runs Well");   // the title, not the id
});

test("a pick that is too big is shown with a plain note and can still be fetched", async () => {
  const { w, wait } = await boot(PICKS);
  w.eval('LIB.tab = "recommended"; renderModels()');
  await wait(200);
  const big = w.document.querySelector('#v-models .card[data-id="Too-Big-GGUF"]');
  assert.ok(big, "the biggest pick is missing");
  assert.match(big.querySelector(".fitnote").textContent, /Too big for this computer/);
  assert.ok(big.querySelector("[data-dl],[data-recget]"), "no way to get it");
});

test("a search that matches nothing says so and offers Hugging Face, and changing tab clears it", async () => {
  const { w, wait } = await boot(PICKS);
  w.eval('LIB.tab = "recommended"; LIB.q = "zzzz"; show("models"); renderModels()');
  await wait(200);
  const note = w.document.querySelector("#v-models .empty-note");
  assert.ok(note, "no empty-state note");
  assert.match(note.textContent, /No recommended model matches .*zzzz/);
  assert.ok(note.querySelector("[data-hf]"), "no Search Hugging Face button");
  w.eval('document.querySelector("#filters [data-tab=installed]").click()');
  await wait(100);
  assert.equal(w.eval("LIB.q"), "", "the search filter was not cleared on tab change");
  w.eval('document.querySelector("#filters [data-tab=recommended]").click()');
  await wait(200);
  assert.ok(w.document.querySelector('#v-models .card[data-id="Runs-Well-GGUF"]'), "the picks did not come back");
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

test("a model is named by its title, then its GGUF name, then a readable id", async () => {
  const { w } = await boot();
  assert.equal(w.eval('modelName({id: "LFM2.5-8B-A1B", title: "LFM 2.5 8B"})'), "LFM 2.5 8B");        // models.yaml / GGUF name from the server
  assert.equal(w.eval('modelName({id: "gemma-3-270m-it-UD-IQ2_M", title: "gemma-3-270m-it-UD-IQ2_M"})'), "Gemma 3 270M UD IQ2 M");
  assert.equal(w.eval('libTitles([{id: "Qwen3-8B-GGUF", title: "Qwen 3, 8 billion"}])["Qwen3-8B-GGUF"]'), "Qwen 3, 8 billion");
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
  "/ui/models/list": { data: [
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
    assert.match(note.textContent, /llama\.cpp is not yet as fast as specialized inference engines like Strata, Basalt and Maya/);
    assert.ok(!/, and /.test(note.textContent), "a sentence is joined with , and");
  }
});
