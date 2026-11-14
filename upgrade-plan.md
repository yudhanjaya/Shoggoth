# Shoggoth 5: the rest of the upgrade

Written 14 November 2026, after Waves 0 and 1 merged into the `shoggoth-5` branch (591 Python tests pass). This file is the work list for the rest of 5.0. Its background is the master plan, `docs/plans/2026-11-14-shoggoth-5-overhaul.md` ("the master plan" below); read the section each task names before starting it.

**Who runs it.** DeepSeek V4.1 Flash through Command Code, one task per run, in the worktree `/data/Sideprojects/shoggoth-wt/deepseek-rest` on branch `deepseek-rest`. A driver script (`packaging/dev/run-upgrade.sh`) works through the task ids below in order and logs each result to `upgrade-progress.md`. Claude (Opus) reviews the branch afterwards and merges it into `shoggoth-5`.

---

## Decisions already made (don't revisit)

- **llama.cpp: official builds only.** Shoggoth downloads ggml-org's own release builds. They need glibc 2.38, which means Ubuntu 24.04, Linux Mint 22, Debian 13, Fedora 39 or newer. Older systems (Ubuntu 22.04, Mint 21, Debian 12, RHEL 9) get "This llama.cpp build needs a newer Linux". Building our own for older systems comes later, not in 5.0.
- **The network side is HTTPS only.** It's done (`gateway.py`): plain HTTP from another device gets a 400 that says to use `https://`.
- **Lemonade is gone.** People who run their own Lemonade connect to it like any other engine (`engines.d/lemonade.yaml`).
- **Pi packages stay curated and off by default.** Never add a free-form "install any npm package".
- **The user writes the unslop skill.** Leave `shoggoth/skills/zest/skills/unslop/` as a placeholder.
- **Engine definitions are data** (`shoggoth/engines.d/*.yaml`), never code. Never let a definition run a command.

## Rules for every task

1. Work only in your worktree (`/data/Sideprojects/shoggoth-wt/deepseek-rest`). Never touch `/data/Sideprojects/Shoggoth` itself, and never push, merge into another branch, rebase or reset someone else's branch.
2. **Never run `git stash`.** It is shared by every worktree of this repository. Commit work in progress instead.
3. Python is **standard library only** (the vendored PyYAML in `shoggoth/_vendor` is the one exception). No pip installs into the project.
4. Tests: `python3 -W ignore -m unittest discover tests` must pass before you commit, and so must each `node tests/ui/<file>.test.mjs` you touched. `tests/ui/creature.test.mjs` is already broken on `main` (task U8 fixes it). `tests/test_router.py` uses fixed ports 18980–18989; if only it fails, run it alone once before believing it.
5. Every change gets a test. A UI change gets a `tests/ui/*.test.mjs` test where the logic can be tested without a browser.
6. **GPUs.** Card 1, the RTX 4080 SUPER, holds another program (Voicebox). Never load anything on it and never stop that program. Real runs use `CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=0`, which is the RTX 3080. Without `PCI_BUS_ID`, index 0 is the 4080.
7. Scratch profiles only: `XDG_DATA_HOME=/tmp/claude-1000/llt/data`. A llama.cpp b12375 CUDA 13 build is under `/tmp/claude-1000/llt/data/shoggoth/llamacpp/b12375/` and a small test model (Qwen3-0.6B-Q4_0) under `/tmp/claude-1000/llt/models`. Never read or write `~/.local/share/shoggoth` (the user's real profile). Stop every process you start.
8. `shoggoth/index.html` (one file, about 6,500 lines) has one writer: the U tasks, one at a time, in order. S tasks never edit it.
9. User-facing text: short, plain English. No exclamation marks, no "seamless", "effortless", "powerful", no emoji. A label says what a thing is; a hint only when the label can't.
10. Commit each task as one or more commits whose message starts with the task id ("S3: ..."), ending with `Co-Authored-By: DeepSeek V4.1 Flash <noreply@commandcode.ai>`.
11. When a task can't be finished, commit what works, and write what's missing in the final message of the run. The driver copies it into `upgrade-progress.md`. Don't fake a result, and don't delete or skip a failing test to make the suite pass.

---

## Part S: server side (no `index.html`)

### S1. Ubuntu 24.04 as the minimum
- `README.md` "You need" line: say Ubuntu 24.04 / Debian 13 / Fedora 39 or newer (glibc 2.38), and drop "runs on older systems such as Ubuntu 22.04".
- `.github/workflows/release.yml`: build the AppImage on `ubuntu-24.04`, and fix the comment.
- `packaging/build-appimage.sh`: fix the comments to match. Keep the pinned CPU build.
- `llamacpp.py`: check that the "needs a newer Linux" message names glibc 2.38 and the distros above. Add a test that a fake `GLIBC_2.38 not found` error produces it.
- `docs/` pages that state a minimum: same change (`grep -rn "22.04" docs README.md packaging .github`).

### S2. Record real memory after a load (master plan Phase 3, last paragraph)
Today `library.record_measured()` is never called with real data, because the router's log at the default level has no buffer sizes.
- After `llamacpp.load()` succeeds in `providers/llamacpp/provider.py`, find the model's own llama-server child process (the router starts one per model; `GET /models` or the process tree under the router's pid gives it).
- Measure its GPU memory with `nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits`, and its RAM from `/proc/<pid>/status` VmRSS. Pass GPU memory, plus RAM when the model runs on the CPU or offloads experts, to `library.record_measured(model_id, gb, ctx)`.
- No nvidia-smi (AMD or CPU only): use RSS. Unknown: record nothing.
- Don't raise the log level.
- Tests with a fake `nvidia-smi` script and a fake `/proc`, then one real run on the 3080 with the test model. Write the measured number in the commit message.

### S3. Old tuning records
`tuning.json` entries written by the 4.x Lemonade tuner (`applied` holds `llamacpp_args`) must not be rolled back as preset keys. Either convert them with `llamaargs` (the same way `migrate5.args_to_preset` does) or mark them "made by Lemonade, can't be undone here". Add a test.

### S4. Skills written by models: edit, delete, make from chat (master plan 6.3)
- Routes in `server.py`: `GET /ui/skills/own` (list with `source.json`), `POST /ui/skills/own/save {name, text}` (edit, validated like `save_skill`: size limits, no control or bidi characters), `POST /ui/skills/own/delete {name}`.
- Each change calls `skills.on_changed` so idle tasks restart.
- `POST /ui/chat/make-skill {chat}` sends the chat's Pi session the fixed prompt from master plan 6.3 ("distil what worked into a skill and call save_skill"). The approval card still comes from skillsmith.
- Tests for each.

### S5. Pi package install and remove (master plan 6.1)
- `POST /ui/packages/install {name}` and `POST /ui/packages/remove {name}` call `pipkgs`. Install runs in a thread with progress, and only for entries in `recommendations/pi-packages.yaml`.
- `GET /ui/packages` lists entries with installed, enabled, "runs code on your computer" and the review note.
- After a change, call `agent.restart_idle()`.
- Tests with the fake registry the pipkgs tests already use.

### S6. Speech set-up routes (master plan 7a)
- `GET /ui/speech/status` exists. Add `POST /ui/speech/setup`: `speech.install_cpu()`, then `speech.ensure_model()`, in a thread; progress in `speech.UPD`, read through the status route.
- If a GPU whisper build is ever published at a known URL, it goes here too. For now, CPU only, said plainly in the status ("Runs on the processor").
- Test with the fakes in `tests/test_speech.py`.

### S7. LAN client follow-ups (master plan 5.2)
- Add the server's `fit` per model to its `/v1/models` items as an extension field (`"shoggoth": {"fit": ..., "loaded": ...}`) so a client can say "Loads on desk-pc".
- Client-only set-up: `/ui/setup/mode` with mode "connected", when the connected server is a paired Shoggoth, skips the llama.cpp GPU download. Record this so Getting ready doesn't offer it again, until "Also run models on this computer".
- When a paired Shoggoth stops answering, `/ui/sources` reports `state: "offline"` with the peer's name. The beacon listener re-finds it by fingerprint.
- `docs/PRIVACY.md` and `SECURITY.md`: describe LAN sharing (what the beacon sends, HTTPS only, pairing, revoking) and list the five hosted services as destinations (master plan 4.6, last bullets).
- Tests for each.

### S8. File types and attachments, server side (master plan 6.2)
- New `shoggoth/filetypes.py`: extension → handler (`text`, `image`, `pdf-text` with `pdftotext -layout` if installed, `pdf-scan` with `pdftoppm` to images for vision models, `sheet` and `docx` marked "needs a tool that isn't installed" until a package provides them).
- `POST /ui/attach` saves the file to the chat's folder (or the scratch folder for folderless chats) under `attachments/`. The name is cleaned, collisions get a suffix, and there's a size limit (50 MB). The route returns `{path, handler, label}` ("PDF, read as text").
- Never run anything a file contains.
- Tests: path traversal in names refused, each handler picked correctly, missing `pdftotext` reported.

### S9. Rename the bundled skill set `zest` → `shoggoth` (master plan 6.4)
- Move `shoggoth/skills/zest/` to `shoggoth/skills/shoggoth/`.
- `skills.py` still reads the `zest` set id from `state.json`, so enablement survives.
- Fix every reference: `grep -rn "skills/zest\|\"zest\"" shoggoth tests`. Leave internal names like `zest-gate.js` and `/zest-mode` alone.
- Test: a `state.json` with `zest/...` entries still enables the same skills.

### S10. Model catalog, server side (master plan 2.1, 2.3–2.5)
- `recs.py clean_models` validates `quants`, requires `download` on every entry, and warns when a repo isn't from an allowed uploader (`unsloth`, `bartowski`, `ggml-org`, `lmstudio-community`) unless `trusted: true`.
- Search ranking (2.3) on top of `hf.search`: allowed uploaders first, then downloads; hide repos with no `.gguf` files.
- 2.4 stale models: removing a server drops its models from the chat list and from Pi's `models.json` at once. Test: add → appears → remove → gone without restart.
- 2.5 names: send `title` (from `models.yaml`, else GGUF `general.name`, else the id) in every model list the page reads.
- "Qwen3.6-Next" from #51 doesn't exist on Hugging Face. Don't invent an entry for it. Leave a note in the progress log.

### S11. Engine definition gaps (from `docs/plans/engines-research.md`)
- Hosted `key_url` values point at home pages. Find each service's real key page from its own docs and fix the five files. If one can't be confirmed, leave it and say so.
- TabbyAPI and text-generation-webui run as `python main.py` / `python server.py`. Add a `match` on a command-line argument or working-folder name if the schema allows it, or extend `engines.py` with a `cwd` match. No commands, ever.
- NInfer's only unique signature is a metric on `/metrics`. Extend `identify` with a `contains` check on a plain-text body, capped at 64 KB. Test it.

### S12. Leftover Lemonade wording
`grep -rn -i lemonade shoggoth docs README.md` and reword everything that isn't about migration or connecting someone's own Lemonade. Example: `llamaargs.py` says "the way Lemonade does".

### S13. Read aloud (master plan 7b, optional, last of the S tasks)
- Look at whether `bark.cpp` builds and runs on CPU here, using a scratch build folder outside the repo. If it does, write `shoggoth/tts.py` shaped like `speech.py` (start on demand, stop when idle, `speak(text) -> wav bytes`), off by default.
- If bark.cpp isn't usable, try Piper (its own Linux binaries and voices, with checksums) and say Bark isn't available.
- Routes `POST /ui/speech/speak` and the gateway's `/v1/audio/speech`. Tests with a fake engine.

---

## Part U: the UI (`shoggoth/index.html`), one task at a time, in this order

General: the page talks to the server only through `/ui/*` and `/api/*`. Read the route code before using it. Keep the existing look (colours, type, spacing) unless the task says otherwise. Test logic in `tests/ui/`.

### U1. Remove Lemonade from the page
Everything the 1.1 worker listed:
1. Remove `lemReady()`, `backendsInstalled()`, the old `/ui/config` `{config, defaults}` reading, `/ui/backends` recipes other than llamacpp, the `lemonade` target in `/ui/info`, and the `--lemonade` "forced" mode.
2. `/ui/setup` now returns `{setup_done, mode: "builtin"|"connected", mode_saved, engine, connected, models, gpus_found}`.
   - `engine` is `llamacpp.summary()`: `installed, running, port, tag, backend, backend_name, bundled, pinned, builds[], backends[], recommended, max_loaded, device, error, update, log`.
   - The old `existing`, `lemonade` and `arch_supported` fields are gone. `/ui/setup/mode` takes "builtin" or "connected".
3. **Getting ready.** If `engine.installed` is false, or `engine.bundled` is true and `recommended` isn't cpu, offer `POST /ui/backend/install {backend}`. Show progress from `GET /ui/engine/update` (`{state, bytes, total, tag, backend, error}`).
4. **Advanced > Engines, the llama.cpp card:**
   - `GET/POST /ui/config` (`max_loaded` 1–8 as "Keep up to N models loaded", `enabled`).
   - `POST /ui/device`, `POST /ui/backend`.
   - Updates: `POST /ui/engine/check` and `/ui/engine/update`.
   - "Go back": `POST /ui/engine/use {tag, backend}`.
   - `POST /ui/engine/restart`.
   - `restarted: true` means loaded models need loading again.
5. `/ui/backends` has one recipe `llamacpp` with backends cpu, vulkan, cuda12, cuda13 and rocm. `/ui/info` has `llama_tag`.
6. `/ui/sources`' first entry is `{id:"llamacpp", kind:"builtin", own:true, state, models, loaded[], version, backend, error, url}`. Show it as "llama.cpp (on this computer)".
7. Model ids have no `user.` prefix. Old ids map through `model-aliases.json`; the server handles that.
8. `/ui/logs` is llama.cpp's log. Remove the journalctl/lemond wording.
9. `friendlyProblem`: drop "the llama.cpp inside your Lemonade". The new errors include "llama.cpp isn't set up yet." and "This llama.cpp build needs a newer Linux (glibc 2.38 or later…)".
10. "Update everything" items are `zest`, `llamacpp`, `pi`.
11. `/ui/models/imports` returns `listed_before`/`listed_after`. Ollama blobs aren't imported; suggest connecting Ollama.
12. `migrate5`'s summary (`lemonade_gb`, `depend_on_lemonade`): on Advanced > Engines, offer "Delete Lemonade's own files (N GB)".
    - It needs a server route that deletes only the paths `migrate5.lemonade_bytes` counted. Write that route in this task, with a test.
    - Warn when `depend_on_lemonade` > 0.
    - Ask before deleting, saying it can't be undone.

### U2. Recommended page, names, stale models (master plan 2.2, 2.4, 2.5, and Phase 3 fix 5)
- Every recommended card shows, with a fit badge ("Runs well" / "Tight" / "Runs partly on the processor" / "Too big for this computer, needs about N GB").
- Remove the "still looking at your computer" gate. Fix the search-filter bug: clear `LIB.q` on tab change, and show a "No recommended model matches X. Search Hugging Face instead?" button.
- Delete `fitInfo()` and use the server's `need_gb`/`fit` everywhere. Show `ctx` and `ctx_why` (auto context); `fit == "partial"` means "Runs partly on the processor".
- The name chain: `title` → GGUF name → `friendly(id)`.

### U3. Sources, Models page, chat-bar picker, custom provider form (master plan 4.3, 4.4, 4.7)
The design-heavy one. Read 4.3–4.7 fully.
- **Models page:** a sources strip showing llama.cpp, found engines (with the shared-port "Inference engine on port N" bucket and its explanation), connected servers, hosted services and other Shoggoths.
- **Chat-bar picker:** groups by source, offline sources greyed out. Switching writes `{source, model}`.
- **"Hook up something else" form:** address, key, API format, models, context, "on this computer?", a Test button, and "Recognise this next time?". The save writes a user definition through the server and needs a route; write it, validated by `engines.py`.
- Chat context shows "Auto (32k)" etc. `POST /ui/model/ctx` accepts `"auto"`.

### U4. Hosted services and LAN screens (master plan 4.6, 5.1, 5.2)
- **Hosted-service cards:** paste a key and see only the last four characters. Show the destination host (`host` on hosted definitions) next to the key field, the price where published, and a link to the service's terms.
- A chat on a hosted model shows a persistent "Leaves your computer: OpenRouter" chip.
- **LAN sharing on the Server page,** using `/ui/lan/*` (route table in `shoggoth/lan.py`'s `handle()`):
  - the sharing switch, with a name;
  - waiting requests ("Asha's laptop wants to use your models", the code, Turn down);
  - paired clients with Remove;
  - the short fingerprint shown on both screens with "Check this matches on the other computer";
  - "Other Shoggoths" found on the network, with the pairing flow (enter the code).
  - The old LAN paragraph about unencrypted traffic goes. The network side is now HTTPS only.

### U5. Skills and attachments (master plan 6.2, 6.3)
- **Skills page:**
  - a "Written by your models" set with edit (plain textarea), delete and a "from chat" link (S4);
  - a "Tools" section for Pi packages (S5) that says plainly "Runs code on your computer";
  - the `save_skill` approval card (`{tool:"save_skill", what, preview, files:[{path,bytes,content}], exists, folder, risks}`, options Save / Save and turn on / Don't save, or Replace…), showing the full SKILL.md and every file's content;
  - a chat menu item, "Make a skill from this chat".
- **Composer:**
  - a paperclip and drag-and-drop, calling `/ui/attach` (S8);
  - an attachment chip with the handler label;
  - for an image on a model without vision, the chip says so instead of sending.

### U6. The tube on wide screens (master plan 8.1)
As specified there: a vertical tube at ≥ 1280 px, a strip at 900–1279 px, a button below that. Add a portrait variant through an aspect parameter in the existing creature generator. A collapsed tube is remembered in `localStorage` (wrapped in try/catch).

### U7. Mic button and read aloud (master plan 7a, 7b)
- **Mic button in the composer:**
  - Hold to talk, or click to toggle.
  - Record 16 kHz mono WAV with an AudioWorklet so ffmpeg isn't needed, and post to `/ui/speech/transcribe`.
  - The text lands in the composer, never sent automatically.
  - First use runs `/ui/speech/setup` with progress.
- **Read aloud** on answers, only if S13 shipped an engine.

### U8. Copy pass and small fixes (master plan 8.2, 8.3)
- Fix `tests/ui/creature.test.mjs` (`CR_FORMS is not defined`; it fails on `main` too: find what the test expects from `index.html`).
- Do the copy pass over every user-facing string, with `docs/plans/ai-tells.md` and `docs/plans/unslop-audit.md`. Keep a list of changed strings, before and after, in the commit message.
- The README and `docs.md` quit timing: within about two seconds in app mode.
- The motto in the header or About: *Heavy on the cosmos. Light on your machine.*

---

## Part W: wrap-up

### W1. Docs (master plan Phase 11)
- `docs/` pages and `README.md` for 5.0: llama.cpp instead of Lemonade, sources, hosted services, LAN sharing, speech, skills, packages.
- `docs/ENGINES.md`: how to write an engine definition file.
- A "Moving from 4.x" section describing what `migrate5` does.

### W2. Website (master plan 10.2)
Update `site/` to the shipped features and the motto. Don't invent features that aren't built.

### W3. Release notes draft
`docs/RELEASE-5.0.md`:
- what's new;
- what's removed (Lemonade, plain HTTP on the network, Ubuntu 22.04);
- what moving from 4.x does;
- known gaps (from `upgrade-progress.md`).

### W4. Final check
Run the full suite and every UI test, then start the app on the scratch profile (3080 only) and check:
- Getting ready → model → chat;
- the sources list;
- the speech status route.

Write the result in `upgrade-progress.md`. Don't build the AppImage.

---

## Task order for the driver

`S1 S2 S3 S4 S5 S6 S7 S8 S9 S10 S11 S12 U1 U2 U3 U4 U5 U6 U7 U8 S13 W1 W2 W3 W4`

S tasks come first so the routes exist before the UI uses them. S13 (read aloud) is optional and runs late so it can't hold anything up.

## Still not verified anywhere (for the reviewer)

- **Never run:**
  - the AppImage build;
  - Vulkan and ROCm llama.cpp builds;
  - a split or vision model through the router;
  - a real second machine for LAN sharing;
  - any hosted service with a real key;
  - Gemini thought signatures through Pi.
- **Memory fixtures:** the 10% check still needs LFM2.5-8B, Gemma-4-12B and Qwen3.6-35B-A3B load logs (master plan Phase 3).
- **Pairing man-in-the-middle:** someone on the network who fakes the first beacon can pair as a man in the middle. The defence is comparing the short fingerprint on both screens. A password-authenticated key exchange would close it properly; consider it for 5.1.
