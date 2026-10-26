# Chat and Cowork

The Chat page is a front end for [Pi](https://pi.dev), the minimal coding-agent harness. One conversation can be plain Q&A or an agent working in a folder; the page, the saved list and the transcript are the same.

## How the pieces fit

```
browser (index.html)
   │  POST /ui/cowork/cmd        commands: prompt, steer, abort, compact, set_thinking_level …
   │  POST /ui/cowork/answer     answers to approval questions
   │  GET  /ui/cowork/stream     server-sent events: everything Pi does
   ▼
zest/agent.py ── one `pi --mode rpc` process per task (cwd = the task's folder) ── zest/pi/zest-gate.js
   ▼
Zest /api/v1 (proxy, so it shows on the Server page) ──► Lemonade ──► your model
```

- **Pi** speaks JSON lines on stdin/stdout. Zest forwards a small whitelist of commands, keeps each task's event log in memory so the page can reconnect, and after every run replaces that log with one snapshot (`get_messages`) so replays stay short. The transcript itself lives in Pi's own session file under `~/.local/share/zest/cowork/sessions/<task>/`; reopening a task starts Pi with `--continue`.
- **Provider:** Zest rewrites Pi's `models.json` and `settings.json` (in `cowork/agent/`, used via `PI_CODING_AGENT_DIR`) at every start from Lemonade's downloaded chat models, with the context window the model is loaded with. Zest always loads chat models itself (`/ui/model/load`, passing `ctx_size`: 128k unless the person picked another size in the chat's model bar, saved as `chat.ctx`), and sending a prompt to a model that isn't loaded, or is loaded with less room, loads it first and tells the page (`zest_loading`, `zest_loaded`, `zest_load_failed`). `maxTokens` per answer is a quarter of the window, up to 32k, because a model that thinks aloud can spend many thousands of tokens before it answers. Pi's `message_update` events carry the whole message so far with every word; Zest drops that before relaying them, so a long think stays small on the wire.
- **A chat without a folder** is Pi with `--no-tools` and a short system prompt, working in a private scratch folder. Choosing a folder later turns tools on for the same conversation.
- **Approval:** Pi does not ask before acting, so `zest-gate.js` hooks `tool_call`. Reading is free; edits, writes and commands raise a `select` dialog that the page draws as an Allow / Allow for this session / Don't allow card. Modes: `ask`, `edits` (edits inside the folder pass), `auto`, `plan` (read-only). The mode changes live through a `/zest-mode` command. Writes outside the folder always ask, except in `auto`.
- **Safety:** project trust is declined (`--no-approve`, `defaultProjectTrust: never`), so a folder cannot load its own Pi extensions. This is a prompt, not a sandbox: an approved command runs with your user's permissions. Run it in a folder you can afford to lose, or inside a container, if the model or its inputs are untrusted ([Pi's notes](https://pi.dev/docs/latest)).
- **Packaging:** the AppImage carries Node 22 (binary only) and Pi's self-contained bundle plus `jiti`, all pinned by checksum. Running from source uses `ZEST_PI` or `pi` on the PATH.
- **Updating Pi:** `zest/piup.py`, shown under Advanced > Setup guide, works like the Lemonade update. It asks the npm registry for the latest release, downloads the tarball and the exact `jiti` version that release depends on, checks both against npm's sha512 integrity, unpacks them to `~/.local/share/zest/pi/<version>/`, and starts the new Pi once in RPC mode with Zest's extensions loaded. Only a Pi that passes is offered; otherwise nothing is installed. The copy that came with the AppImage is never touched, the two newest downloads are kept, and *Go back to the version that came with Zest* pins the bundled one. A new major version is not offered, since Zest has not been checked against it, and neither is a release that needs a newer Node than the bundled one. Idle tasks restart on their next message and pick up the new Pi; a task that is working finishes on the old one.

## Chats, jobs and folders

A **chat** is a conversation, optionally in a folder (plus extra folders that are *Can change* or *Look only*). A **job** is a goal a chat hands off to run in the background. Each job is a task of its own: its own Pi process, its own transcript, parented to the chat that started it. Jobs are listed under their chat in the sidebar and as a card in the thread, anchored where the chat was when it started (`after_msgs`).

- **Rounds:** a small orchestrator thread per job (`_job_loop` in `agent.py`) prompts Pi, waits for `agent_settled`, then runs the check itself. The model never decides when to stop. A check is either a command run in the job's folder (exit 0 means done; a trailing "N failed" count feeds the sparkline) or, for "in my words", the model ending a round with `STATUS: DONE`.
- **Limits:** a job stops after its round limit, its time limit (only time spent working counts), or when the failure count has not fallen for three rounds. Pause finishes nothing: it aborts the round in progress and the same round runs again on resume. "Wait for my OK after each round" pauses with `wait_reason: review`, which the sidebar shows as needing you.
- **Work on a copy** (the default): `workcopy.py` copies the folder (leaving out `.git` unless small, `node_modules`, virtualenvs, build output) to `cowork/copies/<job>/work`, keeps a pristine copy of small files in `base/` and a signature of every file in `manifest.json`. **Changes** compares the copy to that record, so the user's repo is never touched and needs no git. **Apply** copies files back and refuses any file the user edited in the meantime. Folders over 30,000 files or 300 MB are not copied; the job form offers to work in the folder itself.
- **Work in the folder itself:** one such job per folder runs at a time; a second waits (`queued`). Zest records the folder when the job starts (`workcopy.snapshot`, no copy), so **Changes** can still list what the job changed, read-only, since those changes are already in place.
- **Memory of trust:** `folders.py` keeps per-folder trust in `cowork/folders.json`: the approval mode a new chat there starts in and the commands the user chose **Always allow**. `zest-gate.js` reads them from `ZEST_ALLOW` and only honours a remembered command when the whole line is built from allowed commands joined by `&&`, `||`, `;` or `|`. Substitution, redirection, backticks, backslashes, newlines and `&` make a line unparseable, so it asks. Programs that can run anything (`sh`, `curl`, `rm`, `ssh`, `docker`, `env`…) are never offered. `python`, `npm`, `git` and similar are remembered together with their subcommand (`python3 -m pytest`, `npm run build`), not alone.
- **Look only** folders are enforced in the gate in every mode except Do everything: an edit there is refused with a reason the model can relay. Like the rest of the gate it is a prompt-level guard, not a sandbox.
- **Live updates:** `GET /ui/cowork/live` streams the whole sidebar state whenever anything in it changes (no polling). The tab title shows how many things wait for the user.
- **Closing Zest:** the watchdog does not quit while a job is working or a question is unanswered. If Zest is stopped anyway, jobs that were working are marked *interrupted* at the next start, and **Continue** resumes them from the transcript with the time already spent kept.
- **Plan to job:** in Look only mode, a long answer gets a **Run this plan as a job…** button that opens the job form with the plan as the goal.
- **Telling the chat:** when a job ends, a short note is queued and added (hidden, in `<zest-note>` tags) to the chat's next message so the model knows what happened.

Routes added: `GET /ui/cowork/live`, `/ui/cowork/job`, `/ui/cowork/job/changes`, `/ui/cowork/job/diff`, `/ui/cowork/folder/info`; `POST /ui/cowork/job/new`, `/job/ctl` (pause, resume, continue, stop, remove), `/job/apply`, `/job/discard`, `/ui/cowork/folders`, `/ui/cowork/seen`, `/ui/cowork/allow/remove`.

## Skills

`zest/skills.py` owns them. Bundled sets live in `zest/skills/<set>/` (each with a `set.json` and a `skills/` folder); sets installed from a link live in `~/.local/share/zest/skills/sets/`, with the on/off choices in `state.json`.

- **Loading:** at every task start Zest rebuilds `cowork/skills-live/` as links to the enabled skills and starts Pi with `--no-skills --skill <that folder>`. Pi's own discovery (`~/.agents/skills`, project `.agents/skills`) is off on purpose: the model sees exactly the list on the Skills page, and a folder cannot add skills of its own. Toggling a skill stops idle tasks so they restart with the new list.
- **Installing:** paste `github.com/owner/repo`, `.../tree/<branch>/<folder>`, `git:github.com/owner/repo` or `owner/repo`. Zest downloads the repository tarball from `codeload.github.com` (40 MB cap, 3000 files, plain files only, no path escapes), copies every folder holding a `SKILL.md` and records the source. It runs nothing from the download; scripts inside a skill only run if the model runs them, which asks first in *Ask first* mode.
- **Entry skills:** Superpowers only works if the model has read its `using-superpowers` skill before it answers anything, so `zest/pi/zest-skills.js` injects that skill's text at the start of every conversation and again after compaction (the same technique as the extension in obra/superpowers, MIT). Only bundled sets can do this.
- **Plain chats** (older chats only; new chats always work in a folder, or the home folder when none is chosen, and have the full toolset) have no tools, so the model cannot open skill files. The short bundled skills marked `inline` in their `set.json` (diagrams, markdown-writing) are put into the prompt instead.
- **Diagrams:** a fenced `mermaid` block in a reply is drawn by Mermaid in the page (`securityLevel: strict`), loaded from `/ui/vendor/mermaid.js`. A block that fails to parse shows the error and an *Ask the model to fix it* button. An `svg` block is shown as an image, which cannot run scripts.
- **What was chosen and why:** of the 52 skill projects on Hermes Atlas, only Superpowers is short enough per skill for small models to follow and works without extra tools. It is bundled minus the subagent, git-worktree, code-review and plan-execution skills, which depend on tools or scripts Zest doesn't have. Others (avoid-ai-writing, drawio-skill, conorbronsdon/agent-skills) are one click away on the Skills page: they are large (up to 33 KB for one skill) and suit bigger models. The diagram and writing skills are Zest's own so that their output matches what the page can show.

## Why Pi and not Pi Durable (yet)

Pi Durable is an experimental TypeScript library for hosting many crash-recoverable, multi-client conversations in one long-lived process. Zest does not need that yet: Pi already persists sessions as files and resumes them, and Zest restarts the process per task. Adopting Durable means writing a Node host that embeds the harness and replaces `agent.py`'s subprocess handling, and Durable's API may still change. The seam is small (`Session` in `agent.py`: start, send, events, answer), so switching later touches that class and not the page. Reasons to switch: tasks that must survive a Zest restart while running, several windows steering one task, or running work on another machine.

## Not built yet

- Undoing a live job's changes (they can be seen but not reverted from the page).
- Several jobs in one folder running together (live jobs take turns; copy jobs can run side by side but their changes are applied one at a time).
- Images in the composer (Pi accepts them; Zest does not send any).
- Prompt templates and MCP servers from a Zest settings page. Pi reads its own agent directory; Zest rewrites only `models.json` and `settings.json` there.
- Skills from places other than GitHub (npm, plain URLs, a local folder), and updating an installed set in place (adding the link again replaces it).
