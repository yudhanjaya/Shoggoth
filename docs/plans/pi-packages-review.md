# Curated Pi packages: review

Review of the five packages named in Phase 6.1 of the Shoggoth 5 plan, against the
runtime Shoggoth actually runs: Pi `1.4.x`, started as `pi --mode rpc … --no-approve
-e shoggoth/pi/zest-gate.js -e … --no-skills --skill <skills-live>` (`shoggoth/agent.py:677`,
`681`, `685`).

Basis of the review: npm registry metadata (`registry.npmjs.org`), the published tarball
contents (jsDelivr's file listing and raw files for the exact versions below), resolved
dependency graphs (deps.dev), and each project's repository docs. No package was installed
and no Pi process was run, so every claim about behaviour is read from the published source;
the "must test" items at the end are the parts a runtime can still surprise us on.

Date: 14 November 2026.

## Summary

| Package | Latest | Licence | Deps (direct / resolved) | Install scripts | What it puts in Pi | Runtime needs | Gate / child-process risk | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bun-docx` | 0.29.0 | MIT | 17 / 110 | none (`prepack`+`prepare` are publish/git-only) | 1 skill `docx-cli`; **no extension, no tool** | **Bun ≥ 1.3** for the npm bin; or ~100 MB GitHub release binary; network + `~/.local/bin` write at agent runtime | Gate covers the `bash` calls. No child Pi. Installs an unverified-by-npm binary by itself. | **Don't bundle** (npm package). Bundle the pinned binary only if Word support is in scope. |
| `@sttronn/pi-sheets` | 0.1.2 | MIT | 0 / 0 | none | 1 extension that only registers a skill path; skill `xlsx` | Python ≥ 3.10 + venv (`openpyxl`, `formualizer`), pip/PyPI network at setup | Gate covers the `python3` calls. No child Pi. Skill is 22.7 KB. | **Bundle with conditions** |
| `@tintinweb/pi-subagents` | 0.25.0 | MIT | 2 / 2 | none (`prepublishOnly`) | `Agent`, `SubagentWorkflow`, `get_subagent_result`, `steer_subagent`; `/agents`; widgets/FleetView; scheduler | Node only (worker threads, `node:vm`, `git` via `pi.exec`) | **Child Pi sessions in-process; the `-e` gate is not forwarded**, and `skills: true` re-enables skill discovery. No new OS process. | **Don't bundle in 5.0** |
| `billion-context` | 0.1.213 | MIT **+ attribution term** | 3 / 90 | none (`prepare` is git-only); self-updates at run time | `compress`, `decompress`, `search_context`, `acp_status`; `acp_delegate*` subagents; `/acp`; global `fetch`/WS patch | Node ≥ 20; own proxy process (or in-process engine); npm + GitHub network; 48 MB unpacked | Adds a second proxy and its own subagents (ungated). Cancels Pi compaction. Auto-update defeats pinning. | **Don't bundle** |
| `@dietrichgebert/ponytail` | 5.3.3 | MIT | 0 / 0 | none | 6 skills (1.7–5.3 KB each); commands `/ponytail`, `-review`, `-audit`, `-gain`, `-debt`, `-help`; system-prompt section hook | Node only; no network; writes `~/.config/ponytail/config.json` on demand | No child Pi, no processes. Normal tools still gated. | **Bundle with conditions** |

## The approval gate, and why "child Pi" is the deciding question

The gate is an ordinary Pi extension passed with `-e` (`shoggoth/agent.py:677`), alongside
Pi's own approvals being off (`--no-approve`). It registers one command and one hook,
`tool_call`, and blocks edits/writes/commands according to the mode
(`shoggoth/pi/zest-gate.js:113-163`). Its view of the world comes from `process.cwd()` and
the process environment (`SHOGGOTH_APPROVAL`, `SHOGGOTH_FOLDERS`, `SHOGGOTH_ALLOW`), which
`agent.py:345-347` sets per task.

That means the gate covers anything that reaches Pi's `tool_call` in **that** process. Two
things do not:

1. **A second Pi session constructed in-process with its own resource loader.** Its loader
   discovers extensions from Pi's `settings.json`/agent dir/project `.pi`, not from the
   parent's `-e` arguments.
2. **A second OS process** (`spawn('pi', …)`), which starts from scratch and inherits
   neither `-e` nor the gate.

Only `pi-subagents` and `billion-context` do (1). Neither packaged extension does (2) — the
proxy `billion-context` can spawn is its own Node process, not a Pi one.

Also worth recording for `pipkgs.py`: **none of the five has a `postinstall`/`preinstall`**,
so "never run npm lifecycle scripts" costs us nothing here. The install-time risk is at
agent runtime instead (`bun-docx`'s bootstrap, `pi-sheets`' `doctor --install`,
`billion-context`'s self-updater).

---

## 1. `bun-docx` 0.29.0

**1. Version, licence, dependencies, scripts.** 0.29.0, MIT, author Kirill Klimuk
(`kklimuk/docx-cli`). 17 direct dependencies, **110 resolved modules** (93 transitive;
deps.dev graph: `@hyzyla/pdfium`, `diff`, `jszip`, `heic-convert`, the `unified`/`remark`/
`micromark` chain, `libxml2-wasm`, …). Unpacked 10.77 MB, of which `dist/index.js` is
6.7 MB and `dist/pdfium-jj0zq23w.wasm` is 4.0 MB. No `postinstall`/`preinstall`;
`prepack` (`bun run build`) and `prepare` (`husky`) only run for published/git installs, so
the tarball is prebuilt.

**2. What it registers.** `package.json` has `"pi": {"skills": ["./skills"]}` only — **no
extension and no tool**. The payload is one skill, `skills/docx-cli/SKILL.md` (9.7 KB),
plus `references/commands.md` (6.7 KB), `references/troubleshooting.md` (5.5 KB),
`scripts/install.sh` (7.3 KB) and `scripts/bootstrap.sh` (9.5 KB). The skill tells the model
to drive the `docx` CLI through `bash`.

**3. Runtime needs.** The npm package is a **Bun** program: `"engines": {"bun": ">=1.3.0"}`
and `dist/index.js` is built with `bun build --target bun` (`package.json` `scripts.build`).
Shoggoth bundles Node 22 only (`docs/AGENT-RUNTIME.md`, *Packaging*). There are two escape
hatches, both from `skills/docx-cli/SKILL.md` §0:

- `bun add -g bun-docx` — adds Bun too;
- `sh scripts/bootstrap.sh` — `bootstrap.sh` resolves the newest GitHub release, calls
  `install.sh`, and drops a ~100 MB binary in `~/.local/bin`. It hits
  `github.com/kklimuk/docx-cli/releases` and re-checks on every run unless
  `DOCX_CLI_NO_UPDATE=1` or `DOCX_CLI_VERSION` is set. It refuses to install without a
  checksum (the GitHub release's, not npm's).

There are no native `.node` modules; the PDF/XML work is WASM.

**4. Risks for Shoggoth.**

- **Bun, as the plan suspected.** Either we bundle Bun (~90 MB) or we do not use the npm
  package as published.
- **A skill that installs software on its own.** The skill's first instruction is to make
  the binary appear, and the fallback downloads and writes outside the working folder.
  `pipkgs.py` verifying the npm tarball does not cover that download, and the checksum is
  the release's, not one we pinned. The gate will prompt (a write outside the folder) in
  `ask`/`edits`, but `auto` mode just lets it run.
- **Cost.** 110 modules and 10.8 MB for one CLI, when a single prebuilt binary would do.
- No child Pi, no other Pi instance. Tool calls are ordinary `bash` calls, so the approval
  gate works.

**5. Verdict: don't bundle the npm package.** If Word files are a 5.0 feature, bundle
**with conditions**: ship the prebuilt release binary in the AppImage (URL + sha256 pinned in
`packaging/build-appimage.sh`, like `PI_URL`), put it on `PATH`, set
`DOCX_CLI_VERSION`/`DOCX_CLI_NO_UPDATE=1` in the agent environment (`agent.py:345`) so the
skill never downloads, and trim `SKILL.md` to the verbs we advertise. Otherwise defer to
6.2's PDF/text path and a plain "read the document as text" fallback.

---

## 2. `@sttronn/pi-sheets` 0.1.2

**1. Version, licence, dependencies, scripts.** 0.1.2, MIT, author StTronn. **Zero runtime
dependencies** (deps.dev returns only self). `@earendil-works/pi-coding-agent` is an
*optional peer*, i.e. host-provided. Unpacked 80.6 KB, 18 files. `scripts` contains only
`typecheck`; no lifecycle scripts.

**2. What it registers.** `"pi": {"extensions": ["./src/index.ts"]}`. The extension is 16
lines (`src/index.ts`) and does exactly one thing:

```ts
export default function piSheets(pi: ExtensionAPI) {
  pi.on("resources_discover", async () => ({ skillPaths: [SKILLS_DIR] }));
}
```

No tools, no commands. The real payload is `skills/xlsx/` — `SKILL.md` 22.7 KB, nine Python
scripts (~46 KB total), `requirements.txt` (`openpyxl`, `formualizer`).

**3. Runtime needs.** Node is fine; the work happens in Python. `README.md` requires Python
≥ 3.10 and a one-shot setup that creates `~/.local/share/pi-sheets/.venv` and pip-installs
`openpyxl` and `formualizer` (a Rust extension with prebuilt wheels). The model then calls
`python3 <skill>/scripts/xlsx.py inspect|validate|…`. If `python3 -m venv` is broken, the
README suggests `uv`, which downloads its own CPython. So: **PyPI network at setup**, and
Python ≥ 3.10 on the host (Ubuntu 22.04 has 3.10, fine).

**4. Risks for Shoggoth.**

- **Skill size.** 22.7 KB is ~3× the largest bundled Shoggoth skill and well over the ~8 KB
  budget the plan sets for small models (`docs/AGENT-RUNTIME.md:93` notes third-party
  skills "run up to 33 KB and suit larger models"). A 4B model will not follow it.
- **Setup is not install.** Nothing happens at unpack time; the venv is created when the
  model (or the user) runs `doctor --install`. That is a network + `pip` step hidden inside
  a tool call. Upstream calls it idempotent; we should surface it as a one-time "Prepare
  spreadsheet support" action, not leave the model to discover it.
- **Skill discovery.** Whether `resources_discover` survives `--no-skills` is the one thing
  I could not settle from source. Shoggoth starts Pi with `--no-skills --skill <live>`
  (`agent.py:681-685`). If Pi's `--no-skills` suppresses the extension's contributed path
  too, the extension is inert and the skill must be copied into `skills-live/` like a
  normal skill (which 6.1 already plans for package skills). **Test this first.**
- No child Pi, no spawned processes. Every operation is a `bash` call, so the gate sees it
  (and `python3 -m …` is remembered as a subcommand, per `docs/AGENT-RUNTIME.md:44`).

**5. Verdict: bundle with conditions.** (a) Copy `skills/xlsx/` into `skills-live/` rather
than relying on the extension, or verify `resources_discover` wins over `--no-skills`;
(b) gate it to models ≥ ~20B, or write a 6–8 KB Shoggoth `spreadsheets` skill that points at
the scripts; (c) make the venv creation an explicit, visible setup step with a checksum-pinned
`requirements.txt`; (d) vendor or pin `formualizer` (the wheel is the fragile part).

---

## 3. `@tintinweb/pi-subagents` 0.25.0

**1. Version, licence, dependencies, scripts.** 0.25.0, MIT, author tintinweb. Two direct
dependencies, `croner` (scheduler) and `nanoid`; both are dependency-free, so **2 modules
total**. Unpacked 2.95 MB, 209 files (`dist/` is checked in; `src/` ships too). Only
`prepublishOnly`; no install scripts. Peer deps: `@earendil-works/pi-coding-agent`,
`pi-ai`, `pi-tui` ≥ 1.1.0 (Shoggoth runs 1.4.x, inside the supported range).

**2. What it registers.** `"pi": {"extensions": ["./src/index.ts"]}`.

- **Tools:** `Agent`, `SubagentWorkflow`, `get_subagent_result`, `steer_subagent`
  (`src/agent-runner.ts`, `SUBAGENT_TOOL_NAMES`).
- **Command:** `/agents` — an interactive menu (agent types, running agents, scheduled jobs,
  workflows, settings).
- **CLI flag:** `--subagents-workflow-file=<path>`.
- **UI:** an above-editor widget, FleetView, a live conversation viewer, completion
  notifications; lifecycle events on Pi's event bus (`subagents:created|started|completed|
  failed|steered|compacted|usage|rpc:*`).
- **State:** `.pi/subagents.json` and `~/.pi/agent/subagents.json`; scheduled jobs in
  `<cwd>/.pi/subagent-schedules/<sessionId>.json` (PID-locked); per-agent `.output`
  transcripts in `<tmp>/pi-subagents-<uid>/…` (0700); subagent Pi sessions persisted by
  default (`rememberAgents: true`).
- **Built-in agents:** `general-purpose` (all 7 tools, `extensions: true`, `skills: true`,
  `allowedSubagents: "all"`), `Explore` and `Plan` (read-only built-ins, `extensions: true`,
  `skills: true`) — `src/default-agents.ts`.

**3. Runtime needs.** Node only. No native modules, no network of its own. Workflow scripts
run in a `node:vm` sandbox on a **worker thread** (README/docs); `git` is called through
`pi.exec` for branch detection (`src/env.ts`) and worktree isolation. Children are not OS
processes: `runAgent()` calls `createAgentSession()` from the Pi SDK in the same process
(`src/agent-runner.ts`).

**4. Risks for Shoggoth — the important one.**

**Children do not inherit the `-e` gate.** `runAgent()` builds a *fresh*
`DefaultResourceLoader` for each child:

```ts
const loader = new DefaultResourceLoader({
  settingsManager, cwd: configCwd, agentDir,
  additionalExtensionPaths,           // only from the agent file's `extensions:` list
  extensionFactories: [codemode, tool-search, mcp, scopeGate],  // all built-ins
  noExtensions, noSkills, …
});
await runInChildSessionContext(() => loader.reload());
```

`additionalExtensionPaths` comes from `parseExtensionsSpec(agentConfig.extensions)` — it is
never seeded from the parent's loaded extensions. The parent's `-e shoggoth/pi/zest-gate.js`
is a CLI-only extension source, and the Pi package docs confirm that shape: "a `--extension`
install is resolved for that one run and never written to `packages[]`"
(`docs/packages.md`). The default agent configs set `extensions: true`
(`src/default-agents.ts`), so a child loads whatever Pi's *discovery* finds (settings
packages, agent dir, project `.pi`) — and **not** the gate. The only tool control a child
gets is its own `tools:` allowlist, enforced by `installExtensionToolScope()` and
`beforeToolCall` (`src/agent-runner.ts`), which is a capability list, not an approval
prompt.

Two more consequences in the same code path:

- **`skills: true` re-enables skill discovery for children**, even though Shoggoth starts
  Pi with `--no-skills` on purpose (`docs/AGENT-RUNTIME.md:88` — "a folder cannot add
  skills"). A child with `skills: true` and `noSkills = false` does exactly that.
- **`general-purpose` nests** (`allowedSubagents: "all"`, `maxSubagentDepth` default 2), so
  the ungated surface is not one level deep.

The rest of the risk list:

- **Display.** Shoggoth's page understands Pi's message/tool events
  (`docs/AGENT-RUNTIME.md:18-24`); the widget/FleetView display is TUI-only. "Show children
  as tool cards in the parent chat" (plan 6.1) is work in `agent.py`/`index.html`, not a
  package feature.
- **Prompts from a child have no route.** The gate asks through `ctx.ui.select`, which
  arrives as `extension_ui_request` and `agent.py` correlates per task. A child session's
  dialogs have no task id in Shoggoth's stream; at best they dangle, at worst they block the
  child forever.
- **Even if we fix inheritance**, the gate takes `home = process.cwd()` and the
  *process-wide* `SHOGGOTH_ALLOW`/`SHOGGOTH_FOLDERS`. A child spawned with `cwd` or a git
  worktree gets the parent's folder boundary, which is wrong for worktree isolation.
- npm peer range is fine (Pi 1.4.x). No lifecycle scripts.

**5. Verdict: don't bundle in 5.0.** It is the one package that genuinely breaks the
approval story, and the fix is not a config flag — it is per-child extension injection plus
UI plumbing. Reconsider when (a) Shoggoth writes agent definitions into
`$PI_CODING_AGENT_DIR/agents/` with `extensions: ["<abs>/pi/zest-gate.js"]`,
`skills: false`, `isolated`-free, and (b) the gate can key folders/env per child session
rather than per process. Then bundle **with conditions**: custom agent files only (disable
the defaults' `*` toolset), `maxSubagentDepth: 1`, `worktreeIsolation: false`,
`outputTranscript: false`, `backgroundByDefault: false` so the parent stays interactive.

---

## 4. `billion-context` 0.1.213

**1. Version, licence, dependencies, scripts.** 0.1.213, MIT **plus an additional
attribution term** in `LICENSE`: any product whose users interact with it must state that it
uses billion-context and link the repository on its home page, docs or About page. That is
not a plain MIT dependency for Shoggoth — it would add a credit line to the site and
`docs/`. Dependencies: `zod` 4.6.7, `mysql2` ^3.15.3, optional `jimp` 1.6.1 → **90 resolved
modules** (the jimp plugin family accounts for most of them). Unpacked **48.0 MB**; the Pi
entry alone, `dist/agent/pi-native.js`, is 6.2 MB, and the package also ships 2.4–5.7 MB
bundles for OpenCode/omp/Codex/Claude. No install scripts (`prepare` is git-install only) —
but at run time it self-updates from npm every 3 minutes and force-installs "critical
advisories" (`README.md`, *Self-update*).

**2. What it registers.** `"pi": {"extensions": ["./dist/agent/pi-native.js"]}`.

- **Tools:** `compress`, `decompress`, `search_context`, `acp_status` (`README.md`,
  *Context-management tools*).
- **Pi-only subagents, on by default:** `acp_delegate`, `acp_delegate_wait`,
  `acp_delegate_cancel` (`README.md`, *Pi built-in subagents*).
- **Commands:** `/acp`, `/acp-cache`.
- **Mechanism:** the extension spawns/attaches a proxy, sets `BILLION_CONTEXT_PROXY`, and
  **patches `globalThis.fetch` and WebSocket** so every model request is rewritten to
  `<proxy>/bili/<upstream-url>` (`src/agent/pi-native.ts`, header and
  `installNativeFetchIntercept`). The shared plugin also performs a **compaction cancel** —
  `pi-native.ts`: "tools, headers, /acp and the compaction cancel all reuse the launcher-mode
  code paths".

**3. Runtime needs.** Node ≥ 20 (`engines`), and its storage path prefers the built-in
`node:sqlite` (Node 22 is what Shoggoth bundles). Default posture since #5428 is *inline* —
compression runs in-process, no listening socket — but the `spawn` and `attach` postures
start a real proxy process (`dist/index.js start`, ephemeral port, parent-pid watchdog), so
the capability is there. Network: npm self-update, the `billion-context-advisories` package,
GitHub announcement feed, and upstream model hosts. `mysql2` is an alternative storage
backend, not required.

**4. Risks for Shoggoth.**

- **It fights our context handling by design.** Shoggoth sets `maxTokens` to a quarter of
  the window capped at 32k and relies on Pi's compaction (`docs/AGENT-RUNTIME.md:22-23`).
  billion-context exists to replace the host's compaction, cancels it, rewrites the
  conversation before it leaves the process, and nudges every ~50k tokens of growth. On a
  4k–32k local model those thresholds never fire; on a 128k model the model's view and
  Shoggoth's stored transcript diverge. Phase 6.1's "test with a 32k-context small model"
  is the wrong test — the right one is "does this ever help at 32k", and the answer from its
  own docs is no.
- **A second proxy in front of our proxy.** Pi already talks to Shoggoth's `/api/v1` so
  requests are recorded (`docs/AGENT-RUNTIME.md:15`). This inserts another hop and a
  fetch-level patch that can silently degrade to direct traffic.
- **Auto-update vs D5.** Shoggoth installs pinned versions with sha512 and never runs
  lifecycle scripts. A package that updates itself from npm and force-installs advisories
  makes the pin meaningless and adds an unscheduled network fetch on the user's machine.
- **Its own subagents** (`acp_delegate`) are additional Pi sessions and carry the same gate
  gap as `pi-subagents`, with no agent files we control.
- **Size and licence.** 48 MB, 90 modules, plus an attribution obligation, for a problem
  the curated small-model path does not have.
- No `postinstall`, so `pipkgs` would install it cleanly — which is exactly the problem:
  nothing would stop it.

**5. Verdict: don't bundle.** If long-context handling is needed on big local models, that
is a Shoggoth-side compaction policy decision in `agent.py`, not a 48 MB proxy with a
self-updater.

---

## 5. `@dietrichgebert/ponytail` 5.3.3

**1. Version, licence, dependencies, scripts.** 5.3.3, MIT, author Dietrich Gebert. **Zero
dependencies**. Unpacked 281 KB, 46 files. `scripts` is only `test`; `scripts/uninstall.js`
is a helper, not a lifecycle hook.

**2. What it registers.** `"pi": {"skills": ["./skills"], "extensions": ["./pi-extension/index.js"]}`.

Skills, with the exact `SKILL.md` sizes from the tarball:

| Skill | Size |
| --- | --- |
| `skills/ponytail/SKILL.md` | 3.4 KB |
| `skills/ponytail-review/SKILL.md` | 5.0 KB |
| `skills/ponytail-audit/SKILL.md` | 5.3 KB |
| `skills/ponytail-help/SKILL.md` | 3.1 KB |
| `skills/ponytail-gain/SKILL.md` | 2.1 KB |
| `skills/ponytail-debt/SKILL.md` | 1.7 KB |
| **total (all six)** | **20.8 KB** |

Every individual skill is **under 8 KB** — the small-model constraint in 6.1 is met, and
`ponytail` + `ponytail-review` together are 8.4 KB.

The extension (`pi-extension/index.js`) registers commands: `/ponytail` (`lite|full|ultra|off`,
`status`, `default <mode>`), `/ponytail-review`, `/ponytail-audit`, `/ponytail-gain`,
`/ponytail-debt`, `/ponytail-help`. Each of the last five simply sends
`/skill:ponytail-…` as a user message with `expandPromptTemplates: true`
(`sendAlias()`). Hooks: `input` (turns off on a standalone "stop ponytail"/"normal mode"),
`session_start`, `session_tree`/`session_switch`/`session_branch` (restore mode),
`agent_start`/`agent_end` (status indicator), and `before_agent_start`, which writes a
`ponytail` section into `event.systemPromptOptions.sections` — a few hundred bytes of
rules appended to the system prompt, kept as a separate section so Pi can retain the cached
prefix (`pi-extension/index.js`, `before_agent_start`).

**3. Runtime needs.** Plain Node/CJS, no native modules, **no network**. Reads
`PONYTAIL_DEFAULT_MODE` / `PONYTAIL_QUIET_STARTUP` / `PONYTAIL_HIDE_STATUS` and
`~/.config/ponytail/config.json`; only `/ponytail default <mode>` writes that file
(`hooks/ponytail-config.js`, `writeDefaultMode`). No child processes, no Pi sessions.

**4. Risks for Shoggoth.**

- **The commands need the skills loaded.** `sendAlias()` sends `/skill:ponytail-review`; Pi
  only expands that if the skill is on the loaded skill path. Under
  `--no-skills --skill skills-live`, the package's `pi.skills` is not what loads — the six
  directories must be linked into `skills-live/` (which 6.1 already plans to do for every
  package). If they are not, the buttons send a literal `/skill:…` string to the model.
- **Prompt change per mode.** The `before_agent_start` section changes the system prompt
  when the user switches level; that invalidates the prompt cache once per switch. Small,
  but worth knowing given the local-engine cache story.
- **Skill fit** is good for small models; the `review` and `audit` skills are the two that
  ask for real reasoning, so they suit the ≥20B rule in the plan, while the base
  `ponytail` skill is fine everywhere.
- No gate implications: the skills only shape how the model uses the normal tools, which
  are already gated.

**5. Verdict: bundle with conditions.** (a) Link all six skills into `skills-live/` at task
start (do not rely on the extension's `pi.skills`); (b) either drop the extension and expose
the skills only — the commands are just aliases — or forward `/ponytail*` from the page;
(c) expose `ponytail-review` and `ponytail-audit` as buttons in a folder chat as planned;
(d) enable by default, but let the Skills page turn off `review`/`audit` for sub-20B models.
The extension itself is optional: the skills are the value and they are small, dependency-
free and MIT.

---

## What to test before anything ships

1. **`pi-sheets` under `--no-skills`.** Does the extension's `resources_discover` still
   contribute `skills/xlsx`? If not, copy it into `skills-live/` and treat the extension as
   unnecessary.
2. **`pi-subagents` gate inheritance.** Start Pi with `-e zest-gate.js` in `ask` mode, put
   an agent file in `$PI_CODING_AGENT_DIR/agents/` with `extensions: ["<abs>/pi/zest-gate.js"]`
   and `skills: false`, and confirm a `write` from the child raises the Shoggoth card (and
   that the card reaches the right task). Then check the wrong-folder case with `cwd`/
   worktree spawn.
3. **`ponytail` commands without its extension**, with the skills copied to `skills-live/`
   — `/skill:ponytail-review` must expand.
4. **`bun-docx` binary, not package.** If pursued: does the pinned release binary run on
   Ubuntu 22.04's glibc, and does the `docx` skill work with `DOCX_CLI_NO_UPDATE=1` and no
   network?
5. **`billion-context`.** Nothing to test unless the verdict is revisited; if it is, verify
   that Pi compaction is actually cancelled and that `maxTokens` still behaves.
