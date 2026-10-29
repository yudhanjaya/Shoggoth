// Puts a few skills' instructions at the start of every conversation (SHOGGOTH_INJECT is a JSON list of {name, path}).
//
// Two uses: Superpowers' entry skill, which only works if the model has read it before it answers anything, and the
// short Shoggoth skills in a plain chat, where the model has no tools to open skill files with. The injection is
// repeated after Pi compacts the conversation. The approach follows the Pi extension in obra/superpowers (MIT).
import { readFileSync } from "node:fs";

const MARK = "zest:injected-skills";
const tools = `## How skills work here

Skills are listed by name and description. When one applies and you have a \`read\` tool, read its SKILL.md first and follow it. Pi's tools are lowercase: \`read\`, \`write\`, \`edit\`, \`bash\`, \`grep\`, \`find\`, \`ls\`. There are no subagents and no task-list tool: do the work yourself in this conversation, and keep a checklist in a plan file or TODO.md when you need one.`;

function load() {
  let list = [];
  try { list = JSON.parse(process.env.SHOGGOTH_INJECT || "[]"); } catch { return null; }
  const parts = [];
  for (const s of list) {
    try {
      const body = readFileSync(s.path, "utf8").replace(/^---\n[\s\S]*?\n---\n/, "").trim();
      parts.push(`### Skill: ${s.name}\n\n${body}`);
    } catch { /* a skill that vanished is skipped */ }
  }
  return parts.length ? `<EXTREMELY_IMPORTANT>\n${MARK}\n\nThese skills are already loaded for this conversation. Follow them when they apply; do not try to load them again.\n\n${parts.join("\n\n")}\n\n${tools}\n</EXTREMELY_IMPORTANT>` : null;
}

const has = (m) => {
  const c = m && m.content;
  if (typeof c === "string") return c.includes(MARK);
  return Array.isArray(c) && c.some((p) => p && p.type === "text" && typeof p.text === "string" && p.text.includes(MARK));
};

export default function (pi) {
  const text = load();
  if (!text) return;
  pi.on("context", async (event) => {
    if (event.messages.some(has)) return undefined;
    let at = 0;
    while (event.messages[at] && event.messages[at].role === "compactionSummary") at += 1;
    const msg = { role: "user", content: [{ type: "text", text }], timestamp: Date.now() };
    return { messages: [...event.messages.slice(0, at), msg, ...event.messages.slice(at)] };
  });
}
