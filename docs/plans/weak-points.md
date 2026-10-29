# Weak points: what to fix, what is fixed, what we accept

The failure modes found by reading the code, running it on real hardware and a review round (2026-10-28), as a checklist.
`[x]` is fixed on `strata-support` and tested; `[ ]` is open and within our reach; `[~]` is reduced but not gone; `[-]` is accepted, or
needs something we don't have (hardware, a signing key, an upstream change), with the reason.

## 1. Hurting the machine or its data

- [x] Anything on the computer could drive Zest's page. A secret in `~/.config/zest/ui-token`, a one-time code to a cookie, a bearer token for scripts (`uiauth.py`).
- [x] A chat id or skill-set name became a path that was deleted (`..`). Both validated.
- [x] A bad request dropped the connection. Unexpected errors now answer a plain 500.
- [x] The disk check looked at the home disk, not the disk Strata keeps its models on.
- [x] The approval of an engine you added covered only `provider.yaml` and `provider.py`. It now covers every file in its folder and the program it runs (a different one asks again).
- [x] A model file whose name starts with `-` could reach a program's argument list as a flag through `{model}`. Such files are not offered, and a start with one is refused in words.
- [x] A Lemonade download had no free-disk check. The page asks `/ui/disk` (the library folder, or Lemonade's own) and refuses in words when it won't fit with 5 GB to spare. (An imported folder copies nothing, so it needs none.)
- [x] A library folder on a disk that isn't mounted: the Library says so, downloads are refused with the reason, and Lemonade is not re-pointed at a folder that isn't there.
- [~] The model is untrusted input (prompt injection through files, web results, skills). The Allow card, look-only folders and the never-allow list are the defence. The card now also says in words when a command can use the internet, delete or overwrite files, install software, use administrator rights or change the system (a reading of the text, tested on 16 commands). Click fatigue and a command that hides what it does in a script remain.
- [-] Downloads are checked against checksums published beside them (integrity, not authenticity). Needs signed releases from upstream. Strata signs its own engine updates.
- [-] Someone with your login can read your files anyway; the secret protects against other users and other programs, not against you.

## 2. Memory and processes

- [x] "Fits" ignored the cost of the conversation (key/value cache at 128k).
- [x] An engine Zest started was forgotten after a restart or an engine reload (and so never idle-stopped).
- [x] A model file was given the name of another quant of the same repo in Lemonade.
- [x] Closing Zest left the engines it started running, holding memory with nothing to stop them. `router.stop_started()` runs when Zest quits and stops only what Zest started.
- [x] A second start request while a big model was loading waited silently. It now says "Waiting for X to finish starting…" without hiding the first one's progress.
- [~] Idle stopping can't see apps that talk to an engine directly, bypassing Zest. They should use the Server page's address. Zest's own chats and the gateway are counted; the engine's own activity is used where it can report it.
- [~] Detection treats anything on the engine's ports that answers its health check as the engine. Real engines are matched more tightly (Strata checks its service name); a bare `llama-server` can't be told from another one.
- [~] Port races: a port can be taken between the check and the start. The engine then exits at once and Zest says so; it doesn't retry on the next port.
- [-] AMD, Intel and unified-memory machines are untested (no hardware here). The README says so.
- [-] Models Lemonade lists without files have no known shape, so the conversation cost isn't counted for them.

## 3. Races and stale state

- [x] Two threads saving settings undid each other (now a three-way merge).
- [x] The progress card could not be read while a start held the lock.
- [-] Caches of 4 to 300 seconds (router index 15 s, installed models 10 s, recommendation 300 s). Every start, stop and download invalidates them; the worst case left is a few seconds of stale text.

## 4. Depending on other people's programs

- [x] Nothing checked that the Strata and Lemonade output Zest reads still has the fields it expects. `providers doctor` runs the real calls against the installed engines (nothing is started) and names what no longer matches; use it before a release.
- [x] The tests used fakes. Real captured Strata output (status, saved setups, installed models, update check, a plan) is in `tests/fixtures/real` and the parsers and field checks run on it (`tests/test_contracts.py`). Still missing: a capture of a running server's status.
- [-] The list of large models, sizes and architectures lives in `strata.py`. It moves to data when Strata publishes a model list command.
- [-] The Strata 0.1.52 engine failed its own start test on this machine. That is upstream's to fix; Zest reports it in words.

## 5. Hiding the engines

- [~] Failures cross the abstraction (a two-minute start, a refused update, low memory). Messages are plain where they were checked; keep sweeping as they turn up.
- [-] Routing is a rule (tier, then first engine that can run it, `prefer_architectures`), not a trial load.

## 6. Tests and process

- [x] The page had no automated test. `tests/ui/smoke.js` loads `index.html` in jsdom against recorded answers, opens every page and fails on any script error or empty page (it fails on a deliberately broken copy). Needs `cd tests/ui && npm install`; skipped without it.
- [x] Settings, the router, the library, providers, the door, the safety fixes and the SDK have tests (122 and counting).
- [-] Never run live: a fresh Strata install (skipped on purpose), a Get it of a Large model that fits (no model fits the free disk here), AMD/Intel.
- [-] Linux x86_64 only; glibc 2.38 or newer; needs FUSE for the AppImage and `nvidia-smi` for NVIDIA numbers.
- [-] The README screenshots are from before the new Library and Settings pages; retaking them needs a screenshot tool that saves files.
- [-] `strata-support` is not merged into main yet (your call).
