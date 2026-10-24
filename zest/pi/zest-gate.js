// Zest's approval gate for Pi. Loaded with `pi -e`; it is what makes Cowork ask before it acts.
//
// Modes (switch live with the /zest-mode command, start with the ZEST_APPROVAL variable):
//   ask    reading is free; every edit, new file and shell command asks first (default)
//   edits  edits inside the working folder go through; shell commands and anything outside the folder ask
//   auto   nothing asks (the folder boundary is not enforced either)
//   plan   read-only: edits and shell commands are refused so the model can only look and propose
//
// Questions reach Zest as Pi's extension_ui_request "select" dialogs; Zest draws them as approval cards.
import path from "node:path";

const READ_ONLY = new Set(["read", "grep", "find", "ls"]);
const WRITES = new Set(["edit", "write"]);
const ALLOW = "Allow", SESSION = "Allow for this session", DENY = "Don't allow";

export default function (pi) {
  let mode = process.env.ZEST_APPROVAL || "ask";
  const cwd = () => path.resolve(process.cwd());
  const granted = new Set();   // "bash", "edit", "write" approved for the rest of the session

  const inside = (p) => {
    const rel = path.relative(cwd(), path.resolve(cwd(), String(p || "")));
    return !rel.startsWith("..") && !path.isAbsolute(rel);
  };

  pi.registerCommand("zest-mode", {
    description: "Set how often Zest asks before acting: ask, edits, auto or plan",
    handler: async (arg, ctx) => {
      const next = String(arg || "").trim();
      if (!["ask", "edits", "auto", "plan"].includes(next)) {
        ctx.ui.notify("Mode must be ask, edits, auto or plan", "error");
        return;
      }
      mode = next;
      if (next === "ask" || next === "plan") granted.clear();
      ctx.ui.setStatus("zest-mode", next);
    },
  });

  pi.on("tool_call", async (event, ctx) => {
    const tool = event.toolName;
    if (READ_ONLY.has(tool) || mode === "auto") return undefined;

    if (mode === "plan") {
      return { block: true, reason: "Zest is in read-only (plan) mode. Describe what you would change instead of changing it." };
    }
    const target = event.input && (event.input.path || event.input.file_path);
    const outside = WRITES.has(tool) && target && !inside(target);
    if (mode === "edits" && WRITES.has(tool) && !outside) return undefined;
    if (granted.has(tool) && !outside) return undefined;

    const what = tool === "bash" ? String(event.input.command || "")
      : WRITES.has(tool) ? String(target || "")
      : JSON.stringify(event.input || {}).slice(0, 400);
    const title = JSON.stringify({ tool, what, outside: !!outside, folder: cwd() });
    const choice = await ctx.ui.select(title, outside ? [ALLOW, DENY] : [ALLOW, SESSION, DENY]);
    if (choice === SESSION) granted.add(tool);
    if (choice === ALLOW || choice === SESSION) return undefined;
    return { block: true, reason: "The user did not allow this action. Ask what they would like instead." };
  });
}
