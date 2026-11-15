// U5: the Skills page's model-written set and Tools, the save_skill card, and files attached in the composer.
// Run: node tests/ui/skills-attach.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../../shoggoth/index.html", import.meta.url), "utf8");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const slice = (from, to) => {
  const a = html.indexOf("function " + from), b = html.indexOf("function " + to, a + 1);
  assert.ok(a > 0 && b > a, from + " not found in index.html");
  return html.slice(a, b);
};
const build = (src, names) => new Function("esc", src + "; return {" + names.join(", ") + "};")(esc);

// ---- an attachment chip says what the chat will do with the file
const { cwAttachLabel, cwAtchPrompt } = build(slice("cwAttachLabel", "cwAtchPaint"), ["cwAttachLabel", "cwAtchPrompt"]);
assert.equal(cwAttachLabel({ handler: "text", label: "Read as text" }, true), "Read as text");
assert.equal(cwAttachLabel({ handler: "pdf-text", label: "PDF, read as text" }, false), "PDF, read as text");
assert.match(cwAttachLabel({ handler: "image", label: "Image" }, true), /picture/);
assert.match(cwAttachLabel({ handler: "image", label: "Image" }, false), /can't look at pictures/);
assert.match(cwAttachLabel({ handler: "image", label: "Image" }, false), /won't be sent/);

// a text file's path goes to the model with its handler label
const atts = [{ handler: "text", path: "/chat/attachments/notes.txt", label: "Read as text" },
              { handler: "pdf-text", path: "/chat/attachments/paper.pdf", label: "PDF, read as text" }];
const prompt = cwAtchPrompt(atts, false);
assert.match(prompt, /Attached files:/);
assert.match(prompt, /\/chat\/attachments\/notes\.txt \(Read as text\)/);
assert.match(prompt, /\/chat\/attachments\/paper\.pdf \(PDF, read as text\)/);
// a picture is sent as picture content, or not at all: never as a path
assert.equal(cwAtchPrompt([{ handler: "image", path: "/chat/attachments/x.png", label: "Image" }], false), "");
assert.equal(cwAtchPrompt([...atts, { handler: "image", path: "/x.png", label: "Image" }], true), prompt);
assert.equal(cwAtchPrompt([], true), "");

// ---- a model-written skill: the whole SKILL.md in a textarea, with save, delete, on/off and its chat
const { skOwnCard } = build(slice("skOwnCard", "skOwnPanel"), ["skOwnCard"]);
const own = skOwnCard({ name: "release-notes", key: "own/release-notes", title: "Release notes", enabled: true,
  description: "Use when writing release notes.", text: "---\nname: release-notes\n---\n\n1. Do it.\n",
  source: { chat: "chat-42", model: "tiny-model", date: "2026-11-14T10:00:00+05:30" } });
assert.match(own, /<textarea[^>]*id="ow-release-notes"[^>]*>/);
assert.match(own, /1\. Do it\./);
assert.match(own, /data-owsave="release-notes"/);
assert.match(own, /data-owdel="release-notes"/);
assert.match(own, /data-owon="own\/release-notes"/);
assert.match(own, /data-ownchat="chat-42"/);
assert.match(own, /From its chat/);
assert.match(own, /Written by tiny-model/);
assert.match(own, /2026-11-14/);
// everything from the model is escaped
const nasty = skOwnCard({ name: "x", key: "own/x", title: "<img onerror=1>", text: "<script>bad()</script>", source: {} });
assert.ok(!nasty.includes("<script>") && !nasty.includes("<img onerror"));
assert.match(nasty, /&lt;script&gt;/);

// ---- a Tools row: the plain warning, and the right button for each state
const { skToolRow } = build(slice("skToolRow", "skToolsPanel"), ["skToolRow"]);
const blocked = skToolRow({ name: "bun-docx", version: "0.29.0", enabled: false, installed: false, runs_code: "Runs code on your computer", note: "Not for 5.0." });
assert.match(blocked, /Runs code on your computer/);
assert.match(blocked, /can't be installed/);
assert.ok(!blocked.includes("data-pkginstall"), "a disabled package must have no install button");
const ready = skToolRow({ name: "@dietrichgebert/ponytail", version: "5.3.3", enabled: true, installed: false, runs_code: "Runs code on your computer", note: "Skills" });
assert.match(ready, /data-pkginstall="@dietrichgebert\/ponytail"/);
const installed = skToolRow({ name: "bun-docx", version: "0.29.0", enabled: true, installed: true, runs_code: "Runs code on your computer", note: "" });
assert.match(installed, /data-pkgremove="bun-docx"/);
assert.ok(!installed.includes("data-pkginstall"));

// ---- the save_skill card: the full SKILL.md and every file, before anyone says yes
const { saveSkillCardHtml } = build(slice("saveSkillCardHtml", "cwUiRequest"), ["saveSkillCardHtml"]);
const info = { tool: "save_skill", what: "release-notes", preview: "---\nname: release-notes\n---\n\n1. Do it.\n", exists: false,
  files: [{ path: "templates/report.md", bytes: 5, content: "hello" }], risks: [] };
const card = saveSkillCardHtml(info, ["Save", "Save and turn on", "Don't save"]);
assert.match(card, /Save a new skill/);
assert.match(card, /name: release-notes/);
assert.match(card, /1\. Do it\./);
assert.match(card, /templates\/report\.md/);
assert.match(card, />hello</);
assert.match(card, /data-ans="Save and turn on"/);
assert.match(card, /Don&#39;t save/);
const replace = saveSkillCardHtml({ ...info, exists: true }, ["Replace", "Replace and turn on", "Don't save"]);
assert.match(replace, /Replace the skill/);
assert.match(replace, /data-ans="Replace"/);

// ---- the page wires all of it up
for (const part of ["id=\"cwattachbtn\"", "id=\"cwattachfile\"", "id=\"cwatches\"", "/ui/attach", "data-owon",
  "Make a skill from this chat", "/ui/chat/make-skill", "/ui/skills/own/save", "/ui/skills/own/delete",
  "/ui/packages/install", "/ui/packages/remove", "Written by your models", "Runs code on your computer"])
  assert.ok(html.includes(part), "index.html is missing " + part);
// ---- the own set is drawn once, by the editor, not again as an ordinary set
assert.ok(html.includes("SK.sets.filter(set => !set.own)"), "the own set must not be drawn twice");

// ---- the real page draws all of it from the server's answers (api.json carries one model-written skill and the packages)
const { createRequire } = await import("node:module");
const require = createRequire(import.meta.url);
const { boot, closeAll } = require("./page.cjs");
{
  const { w, wait } = await boot();
  w.eval('show("skills")');
  await wait(600);
  const skills = w.document.getElementById("skills").innerHTML;
  assert.match(skills, /Written by your models/);
  assert.match(skills, /id="ow-release-notes"/);
  assert.match(skills, /From its chat/);
  assert.match(skills, /data-owsave="release-notes"/);
  assert.match(skills, /Tool/);
  assert.match(skills, /data-pkginstall="@dietrichgebert\/ponytail"/);
  assert.match(skills, /data-pkgremove="@sttronn\/pi-sheets"/);
  assert.match(skills, /Runs code on your computer/);
  assert.ok(!skills.includes('data-set="own"'), "the own set is drawn twice");
  // a paperclip attachment renders as a chip with what the chat will do with it
  w.eval('CW.id = "chat-42"; CW.atts = [{name: "notes.txt", path: "/tmp/notes.txt", handler: "text", label: "Read as text"}]; cwAtchPaint();');
  const chips = w.document.getElementById("cwatches").textContent;
  assert.match(chips, /notes\.txt/);
  assert.match(chips, /Read as text/);
  closeAll();
}

console.log("ok: own skills, tools, save_skill card and attachments");
