# Agent runtime

The Chat page is a front end for [Pi](https://pi.dev), a minimal coding-agent harness. A conversation is either plain Q&A or an agent working in a folder. Long-running work is delegated to *jobs*.

## Process model

```
browser (index.html)
   │  POST /ui/cowork/cmd        commands: prompt, steer, abort, compact, set_thinking_level …
   │  POST /ui/cowork/answer     answers to approval questions
   │  GET  /ui/cowork/stream     server-sent events: everything Pi does
   ▼
shoggoth/agent.py ── one `pi --mode rpc` process per task (cwd = the task's folder) ── shoggoth/pi/zest-gate.js
   ▼
Shoggoth /api/v1 (proxy, recorded on the Server page) ──► engine ──► model
```

- **Transport.** Pi speaks JSON lines on stdin and stdout. Shoggoth forwards an allow-listed set of commands and keeps each task's event log in memory so the page can reconnect. After each run the log is replaced by a single `get_messages` snapshot to keep replays short.
- **Persistence.** The transcript lives in Pi's session file under `~/.local/share/shoggoth/cowork/sessions/<task>/`. Reopening a task starts Pi with `--continue`.
- **Provider configuration.** At every start Shoggoth regenerates Pi's `models.json` and `settings.json` (in `cowork/agent/`, selected through `PI_CODING_AGENT_DIR`) from the engines' chat-capable models and the context size each is loaded with.
- **Model loading.** Shoggoth loads chat models itself (`/ui/model/load`, passing `ctx_size`: 128k unless the user chose another size in the model bar, stored as `chat.ctx`). A prompt sent to a model that is not loaded, or is loaded with a smaller window, triggers a load first and emits `zest_loading`, `zest_loaded` or `zest_load_failed` events.
- **Output budget.** `maxTokens` per answer is one quarter of the context window, capped at 32k, because a reasoning model can spend many thousands of tokens before it answers.
- **Event size.** Pi's `message_update` events carry the full message on every token. Shoggoth strips that payload before relaying, which keeps long reasoning streams small.
- **Folderless chats.** A chat with no folder runs Pi with `--no-tools` and a short system prompt in a private scratch directory. Choosing a folder later enables tools in the same conversation.

## Approval gate

`zest-gate.js` hooks Pi's `tool_call`. Reads proceed. Edits, writes, and commands raise a `select` dialog, which the page renders as an *Allow / Allow for this session / Don't allow* card.

| Mode | Label in UI | Behavior |
| --- | --- | --- |
| `ask` | Ask first | Every edit, write, and command prompts. |
| `edits` | Edit freely | Edits inside the folder proceed. Commands and anything outside the folder prompt. |
| `auto` | Do everything | No prompts. |
| `plan` | Look only | Read-only. |

The mode can be changed during a run through the `/zest-mode` command. Writes outside the folder always prompt, except in `auto`.

**Per-folder trust.** `folders.py` stores trust in `cowork/folders.json`: the mode a new chat in that folder starts with, and the commands the user chose to *Always allow*. The gate receives them through `SHOGGOTH_ALLOW`.

- A remembered command is honored only if the entire line consists of allowed commands joined by `&&`, `||`, `;`, or `|`.
- Substitution, redirection, backticks, backslashes, newlines, and `&` make a line unparseable, so it prompts.
- Interpreters and wrappers that can execute arbitrary input (`sh`, `curl`, `rm`, `ssh`, `docker`, `env`, and similar) are never offered.
- Tools such as `python`, `npm`, and `git` are remembered together with their subcommand (`python3 -m pytest`, `npm run build`), not alone.

**Look-only folders.** Additional folders can be attached as *Can change* or *Look only*. Look-only is enforced in the gate in every mode except `auto`; a blocked edit returns a reason the model can relay.

This is a policy control, not a sandbox. See [SECURITY.md](SECURITY.md).

## Chats, jobs, and folders

A **chat** is a conversation, optionally bound to a folder plus extra folders. A **job** is a goal a chat hands off to run in the background. Each job is its own task, with its own Pi process and transcript, parented to the originating chat. Jobs appear under their chat in the sidebar and as a card in the thread, anchored at the message position where the job started (`after_msgs`).

### Job loop

A thread per job (`_job_loop` in `agent.py`) prompts Pi, waits for `agent_settled`, then runs the completion check itself. The model never decides when the job is finished.

- **Check.** Either a command run in the job's folder (exit code 0 means done; a trailing "N failed" count feeds the progress sparkline), or, for a free-text criterion, the model ending a round with `STATUS: DONE`.
- **Termination.** A job stops when it reaches its round limit, its time limit (only time spent working counts), or when the failure count has not decreased for three rounds.
- **Pause.** Pause aborts the round in progress. The same round runs again on resume. *Wait for my OK after each round* pauses with `wait_reason: review`, which the sidebar shows as awaiting the user.
- **Interruption.** If Shoggoth stops while a job is working, the job is marked *interrupted* at next start. **Continue** resumes it from the transcript with elapsed time preserved. The idle watchdog does not exit while a job is working or a question is unanswered.
- **Plan to job.** In Look only mode, a long answer offers **Run this plan as a job…**, which opens the job form with the plan as the goal.
- **Completion notice.** When a job ends, a short note is queued and prepended (hidden, in `<zest-note>` tags) to the chat's next message so the model knows the outcome.

### Execution targets

**Work on a copy** (default). `workcopy.py` copies the folder to `cowork/copies/<job>/work`, excluding `.git` (unless small), `node_modules`, virtual environments, and build output. It keeps a pristine copy of small files in `base/` and a signature of every file in `manifest.json`.

- **Changes** compares the copy to that record, so the user's repository is untouched and git is not required.
- **Apply** copies selected files back and refuses any file the user modified in the interim.
- Folders larger than 30,000 files or 300 MB are not copied. The job form offers to work in place instead.

**Work in place.** One such job per folder runs at a time; others wait in `queued`. Shoggoth records the folder state at start (`workcopy.snapshot`, without copying), so **Changes** can list what changed, read-only.

### Live state

`GET /ui/cowork/live` streams the full sidebar state on every change, with no polling. The tab title shows the number of items awaiting the user.

### Routes

`GET /ui/cowork/live`, `/ui/cowork/job`, `/ui/cowork/job/changes`, `/ui/cowork/job/diff`, `/ui/cowork/folder/info`.
`POST /ui/cowork/job/new`, `/job/ctl` (pause, resume, continue, stop, remove), `/job/apply`, `/job/discard`, `/ui/cowork/folders`, `/ui/cowork/seen`, `/ui/cowork/allow/remove`.

## Skills

`skills.py` owns skills. Bundled sets live in `shoggoth/skills/<set>/` (each with a `set.json` and a `skills/` directory). Sets installed from a link live in `~/.local/share/shoggoth/skills/sets/`, with enablement stored in `state.json`.

- **Loading.** At each task start Shoggoth rebuilds `cowork/skills-live/` as links to the enabled skills and starts Pi with `--no-skills --skill <that directory>`. Pi's own discovery (`~/.agents/skills` and project `.agents/skills`) is disabled deliberately, so the model sees exactly the list shown on the Skills page and a folder cannot add skills. Toggling a skill stops idle tasks so they restart with the new list.
- **Installing.** Accepted forms: `github.com/owner/repo`, `.../tree/<branch>/<folder>`, `git:github.com/owner/repo`, and `owner/repo`. Shoggoth downloads the repository tarball from `codeload.github.com` (40 MB cap, 3,000 files, regular files only, no path escapes), copies every directory containing a `SKILL.md`, and records the source. Nothing is executed at install time.
- **Entry skills.** Superpowers requires the model to read `using-superpowers` before answering, so `shoggoth/pi/zest-skills.js` injects that skill at the start of every conversation and again after compaction. This follows the approach of the extension in obra/superpowers (MIT). Only bundled sets may do this.
- **Folderless chats.** Older chats without tools cannot open skill files. Bundled skills marked `inline` in `set.json` (diagrams, markdown-writing) are placed in the prompt instead. New chats always have the full toolset.
- **Diagrams.** A fenced `mermaid` block is rendered by Mermaid in the page (`securityLevel: strict`), loaded from `/ui/vendor/mermaid.js`. A parse failure shows the error and an *Ask the model to fix it* action. An `svg` block is displayed as an image, which cannot execute scripts.
- **Selection rationale.** Of the 52 skill projects listed on Hermes Atlas, only Superpowers has skills short enough for small models to follow that also work without additional tools. It is bundled without its subagent, git-worktree, code-review, and plan-execution skills, which depend on tooling Shoggoth does not provide. The diagram and writing skills are Shoggoth's own so their output matches what the page can render. Larger third-party sets (for example avoid-ai-writing, drawio-skill, conorbronsdon/agent-skills) can be added from the Skills page; individual skills run up to 33 KB and suit larger models.

### Skills written by your models

Pi has a `save_skill` tool (`shoggoth/pi/zest-skillsmith.js`) that proposes a reusable skill: a name, one or two sentences on when to use it, numbered steps, and up to five small extra files. The tool always asks, in every mode including *Do everything* and *Look only*, with a card that shows the whole `SKILL.md` and the content of every file. The choices are Save, Save and turn on, Don't save, or Replace when the name exists. Nothing is saved without that yes.

- Accepted skills go in `~/.local/share/shoggoth/skills/sets/own/skills/<name>/`, with a `source.json` saying which chat and model wrote it. They appear on the Skills page as **Written by your models** and can be switched on or off like any other skill.
- `GET /ui/skills/own` returns each one with its source and its whole text. `POST /ui/skills/own/save {name, text}` edits one under the same limits `save_skill` uses (a 40-character slug name, 300-character description, 8 KB of steps, 16 KB for the whole file, no control or direction-changing characters). It edits, never creates, so a skill still appears only after `save_skill`. `POST /ui/skills/own/delete {name}` removes it and its enablement keys.
- `POST /ui/chat/make-skill {chat}` sends that chat's Pi session a fixed prompt: distil what worked into a skill and call `save_skill`. The page supplies no text of its own, and a job is refused.
- Every change calls the same notification the Skills page uses, so idle tasks restart with the new list.

### Pi packages

A Pi package is an npm package that adds an extension, skill or prompt to Pi. Packages run code on this computer, so Shoggoth installs only the pinned list in `shoggoth/recommendations/pi-packages.yaml` (`pipkgs.py`). Each entry pins an exact version and the sha512 npm publishes. Shoggoth downloads the tarball, checks the checksum, and unpacks it under `~/.local/share/shoggoth/pi-packages/<name>@<version>/` with its dependencies. It never runs npm or a lifecycle script, and it refuses a dependency from anywhere but the npm registry. Every installed package must be turned on in the list; an entry with `enabled: false` (all of them in 5.0) cannot be installed.

- `GET /ui/packages` lists the pinned entries with installed, enabled, the review note, and "Runs code on your computer".
- `POST /ui/packages/install {name}` installs one in a background thread with progress in the same reply. `POST /ui/packages/remove {name}` deletes every installed version of it.
- After a real change, idle tasks restart (`agent.restart_idle()`), so Pi loads the package's files on the next message. Installed packages are handed to Pi as local folders through `packages` in its `settings.json`, and any skills they ship are listed like other skill sets.

### Attachments

The composer's paperclip and drag-and-drop post the file's bytes to `POST /ui/attach?chat=<id>&name=<file>` (`filetypes.py`). The route returns `{path, handler, label}` and saves the file under `attachments/` in the chat's folder, or in the chat's scratch folder when it has none.

- The handler is chosen from the extension: text and code read directly; images go to a vision model; a PDF is read with `pdftotext -layout` when poppler is installed, or its pages are rendered to images with `pdftoppm`; spreadsheets and Word documents say they need a tool that is not installed yet.
- The name is cleaned: folder parts, control characters and direction-changing characters are refused rather than trimmed, and a name already in use gets `-1`, `-2`. One file is capped at 50 MB.
- The bytes are written as they came. Nothing in a file is read, parsed or run when it is attached.

## Updating Pi

`piup.py` (shown as **The agent helper** under Advanced > Engines) works like the llama.cpp update:

1. Query the npm registry for the latest release.
2. Download the tarball and the exact `jiti` version that release depends on, and verify both against npm's sha512 integrity values.
3. Unpack to `~/.local/share/shoggoth/pi/<version>/` and start the new Pi once in RPC mode with Shoggoth's extensions loaded. Only a Pi that passes is offered; otherwise nothing is installed.

The copy bundled with the AppImage is never modified. The two newest downloads are retained. *Go back to the version that came with Shoggoth* pins the bundled copy. A new major version is not offered, because Shoggoth has not been validated against it. A release that requires a newer Node.js than the bundled one is likewise not offered. Idle tasks restart on their next message with the new Pi; a working task finishes on the old one.

## Packaging

The AppImage includes Node 22 (binary only), Pi's self-contained bundle, and `jiti`, all pinned by checksum. A source checkout uses the `SHOGGOTH_PI` variable, or `pi` on `PATH`.

## Known limitations

- A live (in-place) job's changes can be inspected but not reverted from the UI.
- Live jobs in one folder run serially. Copy jobs can run concurrently, but their changes are applied one at a time.
- Prompt templates and MCP servers cannot be configured from Shoggoth. Pi reads its own agent directory. Shoggoth rewrites only `models.json` and `settings.json` there.
- Skills can be installed only from GitHub. Other sources (npm, plain URLs, local folders) are unsupported. Updating an installed set requires adding its link again, which replaces it.
