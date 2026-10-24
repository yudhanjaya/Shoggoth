# Chat and Cowork

From version 3 the Chat page is a front end for [Pi](https://pi.dev), the minimal coding-agent harness. One conversation can be plain Q&A or an agent working in a folder; the page, the saved list and the transcript are the same.

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
- **Provider:** Zest rewrites Pi's `models.json` and `settings.json` (in `cowork/agent/`, used via `PI_CODING_AGENT_DIR`) at every start from Lemonade's downloaded chat models, with the context window Lemonade really loaded the model with. If that is under 32k the page says so and offers to reload the model with 64k or 128k, since an agent's prompt alone is about 4k tokens.
- **A chat without a folder** is Pi with `--no-tools` and a short system prompt, working in a private scratch folder. Choosing a folder later turns tools on for the same conversation.
- **Approval:** Pi does not ask before acting, so `zest-gate.js` hooks `tool_call`. Reading is free; edits, writes and commands raise a `select` dialog that the page draws as an Allow / Allow for this session / Don't allow card. Modes: `ask`, `edits` (edits inside the folder pass), `auto`, `plan` (read-only). The mode changes live through a `/zest-mode` command. Writes outside the folder always ask, except in `auto`.
- **Safety:** project trust is declined (`--no-approve`, `defaultProjectTrust: never`), so a folder cannot load its own Pi extensions. This is a prompt, not a sandbox: an approved command runs with your user's permissions. Run it in a folder you can afford to lose, or inside a container, if the model or its inputs are untrusted ([Pi's notes](https://pi.dev/docs/latest)).
- **Packaging:** the AppImage carries Node 22 (binary only) and Pi's self-contained bundle plus `jiti`, all pinned by checksum. Running from source uses `ZEST_PI` or `pi` on the PATH.

## Skills

`zest/skills.py` owns them. Bundled sets live in `zest/skills/<set>/` (each with a `set.json` and a `skills/` folder); sets installed from a link live in `~/.local/share/zest/skills/sets/`, with the on/off choices in `state.json`.

- **Loading:** at every task start Zest rebuilds `cowork/skills-live/` as links to the enabled skills and starts Pi with `--no-skills --skill <that folder>`. Pi's own discovery (`~/.agents/skills`, project `.agents/skills`) is off on purpose: the model sees exactly the list on the Skills page, and a folder cannot add skills of its own. Toggling a skill stops idle tasks so they restart with the new list.
- **Installing:** paste `github.com/owner/repo`, `.../tree/<branch>/<folder>`, `git:github.com/owner/repo` or `owner/repo`. Zest downloads the repository tarball from `codeload.github.com` (40 MB cap, 3000 files, plain files only, no path escapes), copies every folder holding a `SKILL.md` and records the source. It runs nothing from the download; scripts inside a skill only run if the model runs them, which asks first in *Ask first* mode.
- **Entry skills:** Superpowers only works if the model has read its `using-superpowers` skill before it answers anything, so `zest/pi/zest-skills.js` injects that skill's text at the start of every conversation and again after compaction (the same technique as the extension in obra/superpowers, MIT). Only bundled sets can do this.
- **Plain chats** have no tools, so the model cannot open skill files. The short bundled skills marked `inline` in their `set.json` (diagrams, markdown-writing) are put into the prompt instead.
- **Diagrams:** a fenced `mermaid` block in a reply is drawn by Mermaid in the page (`securityLevel: strict`), loaded from `/ui/vendor/mermaid.js`. A block that fails to parse shows the error and an *Ask the model to fix it* button. An `svg` block is shown as an image, which cannot run scripts.
- **What was chosen and why:** of the 52 skill projects on Hermes Atlas, only Superpowers is short enough per skill for small models to follow and works without extra tools. It is bundled minus the subagent, git-worktree, code-review and plan-execution skills, which depend on tools or scripts Zest doesn't have. Others (avoid-ai-writing, drawio-skill, conorbronsdon/agent-skills) are one click away on the Skills page: they are large (up to 33 KB for one skill) and suit bigger models. The diagram and writing skills are Zest's own so that their output matches what the page can show.

## Why Pi and not Pi Durable (yet)

Pi Durable is an experimental TypeScript library for hosting many crash-recoverable, multi-client conversations in one long-lived process. Zest does not need that yet: Pi already persists sessions as files and resumes them, and Zest restarts the process per task. Adopting Durable means writing a Node host that embeds the harness and replaces `agent.py`'s subprocess handling, and Durable's API may still change. The seam is small (`Session` in `agent.py`: start, send, events, answer), so switching later touches that class and not the page. Reasons to switch: tasks that must survive a Zest restart while running, several windows steering one task, or running work on another machine.

## Not built yet

- Opening a changed file or showing a task-wide diff (the side panel lists files; diffs appear in each Edit card).
- Images in the composer (Pi accepts them; Zest does not send any).
- Prompt templates and MCP servers from a Zest settings page. Pi reads its own agent directory; Zest rewrites only `models.json` and `settings.json` there.
- Skills from places other than GitHub (npm, plain URLs, a local folder), and updating an installed set in place (adding the link again replaces it).
- Importing the old browser-stored chats into Pi sessions.
