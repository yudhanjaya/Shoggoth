// What the page says while Shoggoth gets llama.cpp ready, and what is left of Lemonade.
// Run: node tests/ui/setup.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../../shoggoth/index.html", import.meta.url), "utf8");

// the words and the state the 4.x page had: each one is gone
for (const gone of ["lemReady", "backendsInstalled", "SU.st.lemonade", "st.existing", "arch_supported", "ids.lemonade",
                    "mode-existing", "SET.defaults", "lemonade pull", "Download Lemonade", "Lemonade, the engine"])
  assert.ok(!html.includes(gone), "the page still says " + gone);

// the one thing left is the 4.x delete offer
const offer = /Delete Lemonade|Lemonade's own|Lemonade's folder|Lemonade's files/i;
const lines = html.split("\n").filter(l => /lemonade/i.test(l));
assert.ok(lines.length > 3, "the delete offer is missing");
for (const line of lines) assert.match(line, offer, "only the delete offer may mention Lemonade: " + line.trim().slice(0, 140));

// the two helpers the setup steps use
const a = html.indexOf("const engineNeedsBuild"), b = html.indexOf("function wRows");
assert.ok(a > 0 && b > a, "engineNeedsBuild and engineBuildBackend are not where the test expects them");
const { engineNeedsBuild, engineBuildBackend } = new Function(html.slice(a, b) + "; return {engineNeedsBuild, engineBuildBackend};")();

assert.equal(engineNeedsBuild({installed: false, bundled: false, recommended: "cpu"}), true, "nothing installed needs a build");
assert.equal(engineNeedsBuild({installed: true, bundled: false, recommended: "cuda13"}), false, "a downloaded build is done");
assert.equal(engineNeedsBuild({installed: true, bundled: true, recommended: "cuda13"}), true, "the build that came with Shoggoth is not the fast one");
assert.equal(engineNeedsBuild({installed: true, bundled: true, recommended: "cpu"}), false, "a processor build is the right one");
assert.equal(engineBuildBackend({recommended: "cuda13"}), "cuda13", "the build Shoggoth recommends");
assert.equal(engineBuildBackend({}), "cpu", "no recommendation: the processor");

// "Update everything" follows the three things Shoggoth keeps up to date
const c = html.indexOf("function updatePanel"), d = html.indexOf("async function refreshUpdate");
assert.ok(c > 0 && d > c, "updatePanel not found");
const panel = html.slice(c, d);
assert.ok(panel.includes('["zest", "llamacpp", "pi"]'), "the update list names llama.cpp");
assert.ok(!/lemonade/i.test(panel), "the update list still mentions Lemonade");

console.log("ok: " + lines.length + " Lemonade lines, all the delete offer; " + 6 + " helper cases");
