// The propose_job tool: it only fills a form in, it clamps what the model sends, and it is absent inside a job. Run: node tests/ui/job.test.mjs
import assert from "node:assert/strict";
import register from "../../shoggoth/pi/zest-job.js";

const load = (env) => {
  process.env.SHOGGOTH_JOBS = env;
  const tools = [];
  register({ registerTool: (t) => tools.push(t) });
  return tools;
};

assert.equal(load("0").length, 0, "no tool inside a job");
const [tool] = load("1");
assert.equal(tool.name, "propose_job");

let r = await tool.execute("1", { goal: "  Make the importer tests pass ", check: "python3 -m pytest -q", rounds: 500 });
assert.deepEqual(r.details.job, { goal: "Make the importer tests pass", check: "python3 -m pytest -q", check_kind: "command", rounds: 100, copy: true });
assert.match(r.content[0].text, /press Start themselves/);

r = await tool.execute("2", { goal: "Tidy the docs", done_when: "every page has a title", rounds: 0, work_on_copy: false });
assert.deepEqual(r.details.job, { goal: "Tidy the docs", check: "every page has a title", check_kind: "words", rounds: 1, copy: false });

r = await tool.execute("3", { goal: "x", rounds: "lots" });
assert.equal(r.details.job.rounds, 10, "a default when rounds is not a number");

await assert.rejects(() => tool.execute("4", { goal: "   " }), /goal/);
await assert.rejects(() => tool.execute("5", {}), /goal/);
console.log("ok: propose_job");
