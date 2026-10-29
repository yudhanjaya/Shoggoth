// The creature generator in shoggoth/index.html: same card, same creature; the form decides the body; eyes follow memory.
// Run: cd tests/ui && node --test creature.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync(new URL("../../shoggoth/index.html", import.meta.url), "utf8");
const a = html.indexOf("/* ======================= creatures"), b = html.indexOf("const crCache");
const ctx = {}; vm.createContext(ctx);
vm.runInContext(html.slice(a, b) + "\nthis.generate = generate; this.hashStr = hashStr;", ctx);
const card = o => ({ seed: "qwen3.6-35b-a3b", params: 35, quant: "Q4_K_M", ctx: 128, vision: true, reasoning: true, tools: true, tone: 0, form: 3, ...o });

test("the same card grows the same creature", () => {
  assert.equal(JSON.stringify(ctx.generate(card())), JSON.stringify(ctx.generate(card())));
});

test("the form decides the body", () => {
  const bodies = new Set([0, 1, 2, 3, 4].map(form => JSON.stringify(ctx.generate(card({ form })).runs)));
  assert.equal(bodies.size, 5);
});

test("a longer memory grows more eyes", () => {
  const eyes = c => +ctx.generate(card({ ctx: c })).label.match(/with (\d+) eyes/)[1];
  assert.ok(eyes(256) > eyes(16));
});

test("the page and the server hash names alike", () => {
  assert.equal(ctx.hashStr(""), 2166136261);
  assert.equal(ctx.hashStr("qwen"), 919884438);            // the same number as library.fnv("qwen")
});
