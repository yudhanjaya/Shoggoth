// Zest's approval gate for Pi. Loaded with `pi -e`; it is what makes Cowork ask before it acts.
//
// Modes (switch live with the /zest-mode command, start with the ZEST_APPROVAL variable):
//   ask    reading is free; every edit, new file and shell command asks first (default)
//   edits  edits inside a folder you gave it go through; shell commands and anything outside the folders ask
//   auto   nothing asks (the folder boundary is not enforced either)
//   plan   read-only: edits and shell commands are refused so the model can only look and propose
//
// Folders: the working folder plus any others the person added (ZEST_FOLDERS, a JSON list of {path, access}), where
// access is "write" or "read" ("Look only": edits there are refused in every mode except auto).
//
// Commands the person has said to "always allow" (ZEST_ALLOW, a JSON list of keys such as "pytest" or "npm test") run
// without asking in ask and edits modes, but only when the whole command line is made of allowed commands joined by
// && || ; or |, with nothing that could hide another command (substitution, redirection, backslashes, newlines).
//
// Questions reach Zest as Pi's extension_ui_request "select" dialogs; Zest draws them as approval cards.
import path from "node:path";

const READ_ONLY = new Set(["read", "grep", "find", "ls"]);
const WRITES = new Set(["edit", "write"]);
const ALLOW = "Allow", SESSION = "Allow for this session", DENY = "Don't allow", ALWAYS = "Always allow ";

// Programs that can run anything or reach other machines are never remembered, whatever was approved before.
const NEVER = new Set(["rm", "rmdir", "sudo", "su", "doas", "dd", "mkfs", "chmod", "chown", "sh", "bash", "zsh", "fish", "dash",
  "eval", "exec", "ssh", "scp", "sftp", "rsync", "curl", "wget", "nc", "ncat", "xargs", "find", "env", "nohup", "timeout",
  "watch", "docker", "podman", "kubectl", "systemctl", "kill", "pkill", "killall", "crontab", "mv", "cp", "ln", "shred"]);
// Programs whose first word alone says too little: "python" runs anything, "python -m pytest" does not.
const RUNNERS = new Set(["python", "python3", "node", "npx", "npm", "pnpm", "yarn", "bun", "deno", "pip", "pip3", "uv", "cargo", "go",
  "make", "dotnet", "mvn", "gradle", "poetry", "pipx", "ruby", "bundle", "rake", "php", "composer", "git", "tox", "nox"]);
const SCRIPT_RUNNERS = new Set(["npm", "pnpm", "yarn", "bun"]);

function parseList(name) {
  try { const v = JSON.parse(process.env[name] || "[]"); return Array.isArray(v) ? v : []; } catch { return []; }
}

// "pytest -q tests/" -> "pytest";  "python -m pytest -q" -> "python -m pytest";  "npm run build" -> "npm run build"
export function commandKey(segment) {
  const t = segment.trim().split(/\s+/).filter(Boolean);
  if (!t.length || t[0].includes("=")) return null;
  const head = t[0];
  if (NEVER.has(path.basename(head))) return null;
  if (!RUNNERS.has(head)) return head;
  if (SCRIPT_RUNNERS.has(head) && t[1] === "run" && t[2]) return `${head} run ${t[2]}`;
  if (t[1] === "-m" && t[2]) return `${head} -m ${t[2]}`;
  if (t[1] && !t[1].startsWith("-")) return `${head} ${t[1]}`;
  return head;
}

// null when the line can't be reasoned about, else the keys of its segments (null entries are never-allowed programs).
export function lineKeys(cmd) {
  const s = String(cmd || "");
  if (/[`<>\\\n\r]|\$\(|\$\{|\(|\)|&(?!&)/.test(s.replace(/&&/g, ""))) return null;
  const parts = s.split(/&&|\|\||;|\|/).map((x) => x.trim());
  if (parts.some((x) => !x)) return null;
  return parts.map(commandKey);
}

export default function (pi) {
  let mode = process.env.ZEST_APPROVAL || "ask";
  const home = path.resolve(process.cwd());
  const extra = parseList("ZEST_FOLDERS").map((f) => ({ path: path.resolve(String(f.path || "")), access: f.access === "read" ? "read" : "write" }));
  const folders = [{ path: home, access: "write" }, ...extra.filter((f) => f.path !== home)];
  const granted = new Set();                 // "edit" / "write" approved for the rest of the session
  const allowed = new Set(parseList("ZEST_ALLOW").map(String));

  // The folder a path belongs to (the deepest one), or null when it is outside all of them.
  const folderOf = (p) => {
    const abs = path.resolve(home, String(p || ""));
    let best = null;
    for (const f of folders) {
      const rel = path.relative(f.path, abs);
      if (!rel.startsWith("..") && !path.isAbsolute(rel) && (!best || f.path.length > best.path.length)) best = f;
    }
    return best;
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
    // A web search reads nothing on this computer but sends the person's words to a search engine, so it asks in every mode
    // except "auto" (including read-only plan mode), and "Allow for this session" covers the rest of the conversation.
    if (tool === "web_search" && mode !== "auto" && !granted.has(tool)) {
      const query = String((event.input && event.input.query) || "");
      const choice = await ctx.ui.select(JSON.stringify({ tool, what: query, outside: false, folder: home, key: null }), [ALLOW, SESSION, DENY]);
      if (choice === SESSION) granted.add(tool);
      if (choice === ALLOW || choice === SESSION) return undefined;
      return { block: true, reason: "The user did not allow this search. Answer from what you know, or ask what they would like instead." };
    }
    if (READ_ONLY.has(tool) || mode === "auto") return undefined;

    if (mode === "plan") {
      return { block: true, reason: "Zest is in read-only (plan) mode. Describe what you would change instead of changing it." };
    }
    const target = event.input && (event.input.path || event.input.file_path);
    const where = WRITES.has(tool) && target ? folderOf(target) : null;
    const outside = WRITES.has(tool) && target && !where;

    if (WRITES.has(tool) && where && where.access === "read") {
      return { block: true, reason: `${where.path} is set to Look only for this chat, so nothing there can be changed. Tell the user, or suggest the change instead of making it.` };
    }
    if (mode === "edits" && WRITES.has(tool) && !outside) return undefined;
    if (granted.has(tool) && !outside) return undefined;

    let key = null, unallowed = [];
    if (tool === "bash") {
      const keys = lineKeys(event.input && event.input.command);
      const ok = (k) => k && (k === "cd" || allowed.has(k));       // changing directory is harmless on its own
      if (keys && keys.every(ok)) return undefined;
      if (keys) {
        unallowed = [...new Set(keys.filter((k) => !ok(k)))];
        if (unallowed.length === 1 && unallowed[0]) key = unallowed[0];
      }
    }

    const what = tool === "bash" ? String(event.input.command || "")
      : WRITES.has(tool) ? String(target || "")
      : JSON.stringify(event.input || {}).slice(0, 400);
    const title = JSON.stringify({ tool, what, outside: !!outside, folder: home, key });
    const options = [ALLOW];
    if (tool === "bash") { if (key) options.push(ALWAYS + key); }
    else if (!outside) options.push(SESSION);
    options.push(DENY);
    const choice = await ctx.ui.select(title, options);
    if (choice === SESSION) granted.add(tool);
    if (typeof choice === "string" && choice.startsWith(ALWAYS) && key) { allowed.add(key); return undefined; }
    if (choice === ALLOW || choice === SESSION) return undefined;
    return { block: true, reason: "The user did not allow this action. Ask what they would like instead." };
  });
}
