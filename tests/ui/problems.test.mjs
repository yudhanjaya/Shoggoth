// What a person is told when a model won't load or download. Run: node tests/ui/problems.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../../shoggoth/index.html", import.meta.url), "utf8");
const a = html.indexOf("function friendlyProblem"), b = html.indexOf("function showProblem");
assert.ok(a > 0 && b > a, "friendlyProblem not found");
const friendlyProblem = new Function(html.slice(a, b) + "; return friendlyProblem;")();

const cases = [
  ["llama_model_load: error loading model: unknown model architecture: 'lfm3'", "LFM3", "This model needs a newer engine", /called lfm3/],
  ["error: unknown model architecture: 'lfm3'\nfailed to load model", "LFM3", "This model needs a newer engine", /Lemonade picks up the new llama.cpp/],
  ["CUDA error: out of memory\nfailed to load model", "Qwen", "Not enough memory", /Qwen needs more/],
  ["failed to load model", "Qwen", "The model wouldn't start", /couldn't be loaded/],
  ["llama-server exited with code 1", "Qwen", "The model wouldn't start", /Qwen/],
  ["invalid gguf: bad magic", "Qwen", "The model file looks damaged", /download/],
  ["write failed: no space left on device", "Qwen", "Not enough disk space", /free space/],
  ["there is not enough room", "Qwen", "Something went wrong", /./],
];
for (const [raw, what, title, body] of cases) {
  const p = friendlyProblem(raw, what);
  assert.equal(p.title, title, raw);
  assert.match(p.body, body, raw);
}
assert.equal(friendlyProblem("unknown model architecture: 'lfm3'", "LFM3").engines, true, "points at Engines");
console.log("ok: " + cases.length + " problems");
