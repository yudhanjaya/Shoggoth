// save_skill: what it accepts, that it always asks, and what it writes. Run: node tests/ui/skillsmith.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const own = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "skillsmith-")), "own");
process.env.SHOGGOTH_OWN_SKILLS = own;
process.env.SHOGGOTH_TASK_ID = "chat-42";
process.env.SHOGGOTH_MODEL = "tiny-model";
process.env.SHOGGOTH_APPROVAL = "auto";                       // the tool must ask even in auto mode
const { default: ext, validate, skillText } = await import("../../shoggoth/pi/zest-skillsmith.js");

// ---- validation
const ok = { name: "release-notes", description: "Use when writing release notes.", body: "1. Do it." };
assert.equal(validate(ok).error, undefined);
const bad = (patch, re) => { const r = validate({ ...ok, ...patch }); assert.match(r.error || "", re, JSON.stringify(patch).slice(0, 80)); };
bad({ name: "" }, /name/);
bad({ name: "Has Caps" }, /name/);
bad({ name: "a_b" }, /name/);
bad({ name: "../x" }, /name/);
bad({ name: "-a" }, /name/);
bad({ name: "a--b" }, /name/);
bad({ name: "a".repeat(41) }, /name/);
assert.equal(validate({ ...ok, name: "a".repeat(40) }).error, undefined);
bad({ description: "" }, /description/);
bad({ description: "x".repeat(301) }, /300/);
assert.equal(validate({ ...ok, description: "x".repeat(300) }).error, undefined);
bad({ body: "" }, /body/);
bad({ body: "x".repeat(8 * 1024 + 1) }, /body/);
assert.equal(validate({ ...ok, body: "x".repeat(8 * 1024) }).error, undefined);
bad({ body: "---\nname: other\n---\nhi" }, /header/);
for (const p of ["../x.md", "/etc/passwd", "a/../../b.md", "a\\b.md", ".hidden", "a//b", "SKILL.md", "source.json", ""]) {
  bad({ files: [{ path: p, content: "x" }] }, /file|path|written/);
}
bad({ files: [{ path: "a.md", content: "x" }, { path: "a.md", content: "y" }] }, /twice/);
bad({ files: Array.from({ length: 6 }, (_, i) => ({ path: `f${i}.md`, content: "x" })) }, /At most 5/);
bad({ files: [{ path: "big.md", content: "x".repeat(16 * 1024 + 1) }] }, /limit/);
bad({ files: [{ path: "a.md" }] }, /content/);
assert.equal(validate({ ...ok, files: [{ path: "templates/report.md", content: "x".repeat(16 * 1024) }] }).error, undefined);
assert.match(skillText(validate(ok)), /^---\nname: release-notes\ndescription: "Use when writing release notes."\n---\n\n1\. Do it\.\n$/);

// ---- the tool, with a fake Pi and a fake dialog
let tool;
ext({ registerTool: (t) => { tool = t; } });
assert.equal(tool.name, "save_skill");
const run = async (params, answer) => {
  const asked = [];
  const ctx = { hasUI: true, ui: { select: async (title, options) => { asked.push({ card: JSON.parse(title), options }); return typeof answer === "function" ? answer(options) : answer; } } };
  const res = await tool.execute("id", params, undefined, undefined, ctx);
  return { res, asked };
};

// declined: asked once, nothing written
let r = await run({ ...ok, files: [{ path: "t/x.md", content: "hello" }] }, "Don't save");
assert.equal(r.asked.length, 1);
assert.deepEqual(r.asked[0].options, ["Save", "Save and turn on", "Don't save"]);
assert.equal(r.asked[0].card.tool, "save_skill");
assert.match(r.asked[0].card.preview, /^---\nname: release-notes\n/);
assert.match(r.asked[0].card.preview, /1\. Do it\./);
assert.equal(r.res.details.saved, false);
assert.equal(fs.existsSync(path.join(own, "release-notes")), false);

// a dismissed dialog counts as no
r = await run(ok, undefined);
assert.equal(r.res.details.saved, false);
assert.equal(fs.existsSync(path.join(own, "release-notes")), false);

// nothing is written when validation fails, and the person is not asked
let asked = 0;
await assert.rejects(tool.execute("id", { ...ok, name: "../evil" }, undefined, undefined, { hasUI: true, ui: { select: async () => { asked++; return "Save"; } } }), /name/);
assert.equal(asked, 0);

// no way to ask: refuse
await assert.rejects(tool.execute("id", ok, undefined, undefined, { hasUI: false, ui: {} }), /Can't ask/);
assert.equal(fs.existsSync(own), false);

// saved
r = await run({ ...ok, files: [{ path: "t/x.md", content: "hello" }] }, "Save");
assert.equal(r.res.details.saved, true);
const dir = path.join(own, "release-notes");
assert.equal(fs.readFileSync(path.join(dir, "t", "x.md"), "utf8"), "hello");
assert.equal(fs.readFileSync(path.join(dir, "SKILL.md"), "utf8"), skillText(validate(ok)));
const src = JSON.parse(fs.readFileSync(path.join(dir, "source.json"), "utf8"));
assert.equal(src.chat, "chat-42");
assert.equal(src.model, "tiny-model");
assert.equal(src.turn_on, false);
assert.ok(!Number.isNaN(Date.parse(src.date)));
assert.equal(fs.statSync(path.join(dir, "SKILL.md")).mode & 0o111, 0);
assert.deepEqual(fs.readdirSync(own), ["release-notes"]);        // no temp folders left

// an existing skill is never overwritten without "Replace"
r = await run({ ...ok, body: "1. New way." }, (options) => { assert.deepEqual(options, ["Replace", "Replace and turn on", "Don't save"]); return "Don't save"; });
assert.match(fs.readFileSync(path.join(dir, "SKILL.md"), "utf8"), /Do it/);
assert.equal(r.asked[0].card.exists, true);
r = await run({ ...ok, body: "1. Save sneaks in." }, "Save");              // "Save" is not an offered choice here
assert.equal(r.res.details.saved, false);
assert.match(fs.readFileSync(path.join(dir, "SKILL.md"), "utf8"), /Do it/);
r = await run({ ...ok, body: "1. New way." }, "Replace and turn on");
assert.equal(r.res.details.replaced, true);
assert.match(fs.readFileSync(path.join(dir, "SKILL.md"), "utf8"), /New way/);
assert.equal(fs.existsSync(path.join(dir, "t")), false);          // the old files went with the old skill
assert.equal(JSON.parse(fs.readFileSync(path.join(dir, "source.json"), "utf8")).turn_on, true);

// without the folder variable the tool is not offered
delete process.env.SHOGGOTH_OWN_SKILLS;
const again = await import("../../shoggoth/pi/zest-skillsmith.js?off");
let n = 0;
again.default({ registerTool: () => { n++; } });
assert.equal(n, 0);

fs.rmSync(path.dirname(own), { recursive: true, force: true });
console.log("ok: save_skill");
