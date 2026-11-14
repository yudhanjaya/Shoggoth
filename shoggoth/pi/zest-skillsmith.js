// Lets the model propose a new skill with save_skill. Nothing is saved without the person's yes: the tool ALWAYS asks, in every
// mode (including "auto" and read-only "plan"), through the same select dialog the approval gate uses, showing the whole SKILL.md.
//
// Environment (set by Shoggoth): SHOGGOTH_OWN_SKILLS is the folder the person's own skills live in, SHOGGOTH_TASK_ID the chat,
// SHOGGOTH_MODEL the model name. Without SHOGGOTH_OWN_SKILLS the tool is not offered.
// The gate must let save_skill through (it is not a read-only tool to it); this file does the asking itself.
import fs from "node:fs";
import path from "node:path";

const SAVE = "Save", SAVE_ON = "Save and turn on", REPLACE = "Replace", REPLACE_ON = "Replace and turn on", NO = "Don't save";
export const LIMITS = { name: 40, description: 300, body: 8 * 1024, files: 5, file: 16 * 1024 };
const bytes = (s) => Buffer.byteLength(s, "utf8");
const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const FILE_PATH = /^[A-Za-z0-9][A-Za-z0-9._-]*(\/[A-Za-z0-9][A-Za-z0-9._-]*){0,3}$/;

// Checks what the model sent. Returns {error} or {name, description, body, files: [{path, content}]}. Exported so it can be tested.
export function validate(input) {
  const p = input || {};
  const name = typeof p.name === "string" ? p.name.trim() : "";
  if (!name || name.length > LIMITS.name || !SLUG.test(name)) return { error: `name must be lowercase letters, digits and single hyphens, at most ${LIMITS.name} characters (for example "release-notes").` };
  const description = typeof p.description === "string" ? p.description.replace(/\s+/g, " ").trim() : "";
  if (!description) return { error: "description is required: one or two sentences saying when to use the skill." };
  if (description.length > LIMITS.description) return { error: `description is ${description.length} characters; the limit is ${LIMITS.description}. Shorten it.` };
  const body = typeof p.body === "string" ? p.body.replace(/\r\n/g, "\n").trim() : "";
  if (!body) return { error: "body is required: the steps of the skill." };
  if (bytes(body) > LIMITS.body) return { error: `body is ${bytes(body)} bytes; the limit is ${LIMITS.body}. Make it shorter.` };
  if (/^---\s*\n/.test(body)) return { error: "body must not start with its own --- header; name and description are added for you." };
  if (body.includes("\0")) return { error: "body must be plain text." };
  const files = [];
  if (p.files != null) {
    if (!Array.isArray(p.files)) return { error: "files must be a list of {path, content}." };
    if (p.files.length > LIMITS.files) return { error: `At most ${LIMITS.files} files.` };
    const seen = new Set();
    for (const f of p.files) {
      const rel = f && typeof f.path === "string" ? f.path.trim() : "";
      const content = f && typeof f.content === "string" ? f.content : null;
      if (!rel || content === null) return { error: "each file needs a path and text content." };
      if (path.isAbsolute(rel) || rel.includes("\\") || rel.split("/").includes("..") || !FILE_PATH.test(rel)) {
        return { error: `file path "${rel}" must be a relative path of plain names (letters, digits, . _ -), no "..", at most 4 levels.` };
      }
      if (rel === "SKILL.md" || rel === "source.json") return { error: `${rel} is written for you; use another file name.` };
      if (seen.has(rel)) return { error: `file "${rel}" is listed twice.` };
      if (content.includes("\0")) return { error: `file "${rel}" must be plain text.` };
      if (bytes(content) > LIMITS.file) return { error: `file "${rel}" is ${bytes(content)} bytes; the limit is ${LIMITS.file}.` };
      seen.add(rel);
      files.push({ path: rel, content });
    }
  }
  return { name, description, body, files };
}

// The SKILL.md that will be written. The description is quoted so any text stays valid YAML.
export function skillText(s) {
  return `---\nname: ${s.name}\ndescription: ${JSON.stringify(s.description)}\n---\n\n${s.body}\n`;
}

// Written beside the final folder and moved into place, so a half-written skill is never seen.
function write(dir, s, source) {
  const tmp = `${dir}.tmp-${process.pid}`;
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  try {
    fs.writeFileSync(path.join(tmp, "SKILL.md"), skillText(s), { mode: 0o644 });
    for (const f of s.files) {
      const target = path.resolve(tmp, f.path);
      if (!target.startsWith(tmp + path.sep)) throw new Error("unsafe file path");
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, f.content, { mode: 0o644 });
    }
    fs.writeFileSync(path.join(tmp, "source.json"), JSON.stringify(source, null, 2) + "\n", { mode: 0o644 });
    fs.rmSync(dir, { recursive: true, force: true });
    fs.renameSync(tmp, dir);
  } catch (e) {
    fs.rmSync(tmp, { recursive: true, force: true });
    throw e;
  }
}

export default function (pi) {
  const own = process.env.SHOGGOTH_OWN_SKILLS;
  if (!own) return;
  const reply = (t, details) => ({ content: [{ type: "text", text: t }], details });
  pi.registerTool({
    name: "save_skill",
    label: "Save a skill",
    description: "Propose a new skill: a short set of steps for a kind of task that will come up again. The person is always shown the full skill and decides whether to keep it; nothing is saved without their yes.",
    promptSnippet: "save_skill: propose a reusable skill (name, description, body) for the person to keep or discard",
    promptGuidelines: [
      "Use save_skill only for a procedure that worked and will come up again, or when the person asks for a skill. Keep it short: numbered steps and one example.",
      "The description must say when to use the skill. If the person says no, do not propose it again.",
    ],
    parameters: {
      type: "object",
      properties: {
        name: { type: "string", description: "Short slug: lowercase letters, digits and hyphens, at most 40 characters" },
        description: { type: "string", description: "One or two sentences saying when to use this skill, at most 300 characters" },
        body: { type: "string", description: "The skill's instructions in Markdown, at most 8 KB. Numbered steps and one example." },
        files: {
          type: "array",
          description: "Optional extra text files (at most 5, 16 KB each) with relative paths such as templates/report.md",
          items: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] },
        },
      },
      required: ["name", "description", "body"],
    },
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const s = validate(params);
      if (s.error) throw new Error(s.error);
      if (!ctx || !ctx.hasUI) throw new Error("Can't ask the person here, so nothing was saved.");
      const dir = path.join(own, s.name);
      const exists = fs.existsSync(dir);
      const card = JSON.stringify({
        tool: "save_skill", what: s.name, preview: skillText(s), files: s.files.map((f) => ({ path: f.path, bytes: bytes(f.content) })),
        exists, outside: false, folder: process.cwd(), key: null, risks: [],
      });
      const options = exists ? [REPLACE, REPLACE_ON, NO] : [SAVE, SAVE_ON, NO];
      const choice = await ctx.ui.select(card, options);
      if (choice === NO || !options.includes(choice)) {            // only an answer that was offered counts
        return reply(`The person chose not to save "${s.name}". Do not propose it again unless they ask. Carry on with what they asked.`, { saved: false, name: s.name });
      }
      const turnOn = choice === SAVE_ON || choice === REPLACE_ON;
      try {
        fs.mkdirSync(own, { recursive: true });
        write(dir, s, { chat: process.env.SHOGGOTH_TASK_ID || "", model: process.env.SHOGGOTH_MODEL || "", date: new Date().toISOString(), turn_on: turnOn });
      } catch (e) {
        throw new Error(`Couldn't save the skill (${e && e.message ? e.message : e}).`);
      }
      return reply(
        `Saved the skill "${s.name}". ${turnOn ? "It is turned on and will be used from the next conversation." : "It is saved but off; the person can turn it on on the Skills page."} Tell them in one sentence.`,
        { saved: true, name: s.name, turn_on: turnOn, replaced: exists },
      );
    },
  });
}
