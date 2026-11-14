// The approval gate stays out of save_skill's way (the tool asks the person itself, once), but only for the real tool from
// zest-skillsmith.js. Run: node tests/ui/gate-skillsmith.test.mjs
import assert from "node:assert/strict";
import gate from "../../shoggoth/pi/zest-gate.js";

async function run(mode, sourcePath, toolName = "save_skill") {
  process.env.SHOGGOTH_APPROVAL = mode;
  let handler;
  const pi = {
    on: (ev, fn) => { if (ev === "tool_call") handler = fn; },
    registerCommand: () => {},
    getAllTools: () => [{ name: "save_skill", sourceInfo: { path: sourcePath, source: "local" } }],
  };
  gate(pi);
  const asked = [];
  const ctx = { ui: { select: async (title, options) => { asked.push(JSON.parse(title)); return "Don't allow"; }, setStatus() {}, notify() {} } };
  const out = await handler({ toolName, input: { name: "x", description: "d", body: "b" } }, ctx);
  return { out, asked };
}

const REAL = "/app/shoggoth/pi/zest-skillsmith.js";
for (const mode of ["ask", "edits", "auto", "plan"]) {
  const r = await run(mode, REAL);
  assert.equal(r.out, undefined, `the gate must let save_skill through in ${mode} mode`);
  assert.equal(r.asked.length, 0, `the gate must not add its own card in ${mode} mode`);
}

// A tool of the same name from somewhere else is gated like any other tool.
let r = await run("ask", "/home/x/pi-packages/evil/extensions/save.js");
assert.equal(r.asked.length, 1, "an impersonator is asked about");
assert.deepEqual(r.out && Object.keys(r.out), ["block", "reason"]);
r = await run("plan", "/home/x/pi-packages/evil/extensions/save.js");
assert.ok(r.out && r.out.block, "an impersonator is refused in plan mode");
r = await run("ask", "");
assert.equal(r.asked.length, 1, "an unknown source is asked about");

// Other tools are unchanged.
r = await run("ask", REAL, "write");
assert.equal(r.asked.length, 1);
console.log("ok: gate and save_skill");
