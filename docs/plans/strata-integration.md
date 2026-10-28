# Strata integration plan

Goal: bigger-than-VRAM models (Strata) that feel built in. Install and forget, updates without thought, an existing
Strata picked up the way an existing Lemonade is, and tuning for both engines.

Facts this plan rests on (checked against Strata 0.2.6/0.2.7 and Lemonade 2026.42):

- `strata launch --status --output json` and `--list-presets --output json` are stable JSON (`schema_version: 1`).
  Some commands print a plain line after the JSON, so parse the first object only.
- `strata launch --plan --output json` reports download size, warnings and model memory without changing anything.
- `strata update --check --output json` reports installed and offered engine/control versions from a signed feed. It
  test-starts before switching and refuses to update an engine that is serving (unless `--wait`).
- `strata tune auto <profile> --mode quick|thorough --use chat|coding --goal speed|room --status-file F` saves a
  tuned preset applied at every start; `strata presets` applies, undoes, switches off or rechecks it. Quick takes
  about 20 minutes and needs the GPU to itself.
- A running Strata answers `GET /health` (`{"service": "strata", "model", "max_context", "api_key": bool}`) and
  `GET /v1/models`. The default port is 8080.
- Lemonade has no tuner, but ships `bench_scenarios.json` (verify whether it can be driven).

## Principles

1. **Never touch what Zest did not start.** Zest never stops, restarts, updates or re-tunes a Strata that was running
   before Zest looked, unless the person presses the button for that one.
2. **Adopt before installing.** If Strata is already there, use it.
3. **Memory is shared.** One heavy engine at a time, with a free-RAM check before every start.
4. **Nothing heavy runs without consent** (installs, 100 GB downloads, 20-minute tunes).
5. **Plain words.** No terminal for the common path; the exact command is shown when the terminal is the honest answer.

## Phase 0: harden the spike (`strata-support`)

- Persist which instance Zest started (profile name, pid, port) in Zest's settings, so a restart of Zest keeps it.
- Handle Strata API keys (`launch --show-key`), port clashes with 8080, GPU roles on multi-GPU machines.
- Contract tests on recorded JSON; check `schema_version`, degrade gracefully when it changes.

## Phase 0b (built): detect and adopt an existing Strata (the Lemonade parallel)

Zest handles Lemonade as: its own copy by default, or one you already run ("existing" mode), and a left-over copy from an
earlier Zest is reused rather than duplicated. Strata gets the same three cases.

**Finding it** (all read-only, cached for a few seconds):

1. The `strata` command: `ZEST_STRATA`, then `PATH`, then `~/.local/bin/strata`. Adopt it as is; never reinstall over it.
2. Servers `strata launch` manages: `strata launch --status --output json`.
3. Servers nothing lists (the desktop app, `strata serve`, another user's launch): probe `127.0.0.1` ports 8080 to 8089
   for `/health` with `service == "strata"`.
4. Servers on other machines: the person types an address (and key) once, like Lemonade's "existing" mode. Saved in
   Zest's settings, shown as "Connected", forgettable.

**Using it:**

- Every ready server's models appear in Chat as `strata/<name>`; Pi gets one provider per server, pointing at it directly.
- A server that needs a key and Zest has none shows "needs a key" with one field; keys for launch-managed servers are read
  with `strata launch --show-key`.
- Each server is labelled **yours** (found running, or typed in) or **Zest's** (Zest started it). Only Zest's get idle
  shutdown, update-restarts and auto-tune. Yours get a manual **Stop** with a confirmation, never anything automatic.
- If the person starts Zest while a Strata of theirs is serving, Zest shows it as connected and does not start a second
  big model beside it; asking to start one explains why instead.
- If `strata` is installed but nothing is running, the saved presets are listed with Start.
- The install step is skipped whenever `strata` is found; the Setup guide says "Strata found (version)".

## Phase 1: install and forget (built on `strata-support`; real model download and install not yet run)

- Silent install on first need (installer checked against the release's SHA256SUMS), progress as one line.
- Recommendation from `launch --plan` for a few candidates against the person's RAM, VRAM and free disk: one default
  ("Qwen3.8 Flash-Next, 55 GB, fits") and "See every model".
- Zest-owned preset `zest-<model>` with `--context 128k --access local` and a free port. DeepSeek V4.1 is fixed at 8K; the
  page says so rather than promising 128k.
- Download progress parsed from `strata launch` output (verify `--output jsonl`).
- Picking a Strata model in Chat starts it on demand ("Loading the model"), replacing today's error.

## Phase 2 (built on `strata-support`; tested with fakes): memory arbitration

- Starting Strata unloads Lemonade models first; choosing a Lemonade model while Zest's Strata is up stops it first.
- Zest-owned Strata stops after N idle minutes (default 20, adjustable). Yours never does.
- Free-RAM check before every start; refuse with a clear message rather than swapping the machine to death.

## Phase 3 (built on `strata-support`; tested with fakes and a real read-only check): seamless updates

- Strata (control and engine) joins "Check and update everything" via `strata update --check` / `update`.
- Serving conflict: if Zest owns the instance, stop, update, restart on the next message. If it is yours, defer and say why.
- Tested version range, like Pi: newer-than-tested is shown, and a major jump is not taken automatically.
- After an update, `presets status` / `recheck`; offer a re-tune if the tuning went stale. (Moved to Phase 4, which owns tuning.)

## Phase 4: tuning for both engines

One flow, "Make it faster" per model plus Advanced > Tuning. Each engine implements `plan()`, `run()`, `apply()`, `rollback()`.

- Before a run: estimated time, whether it needs the GPU alone, which models get unloaded. Chats are blocked until it ends;
  cancel is always available. Never starts without consent.
- **Strata:** wrap `strata tune auto` (progress from the status file; chat/coding and speed/room as plain choices); apply,
  undo, recheck from `strata presets`.
- **Lemonade:** a Zest-built tuner using Strata's method. Search only quality-neutral knobs from `llamaargs` (backend, device,
  flash attention, batch/ubatch, threads, KV cache type). Measure time to first token and decode speed on fixed short and long
  prompts, repeating the baseline to learn the noise, and keep a candidate only if it beats the noise. Apply through the existing
  per-model options, with one-click rollback. Spike first: 1-2 days on one model to see whether the knobs matter.
- Results in `tuning.json` keyed by engine, model, hardware fingerprint and engine version; a chip such as "Tuned, +18% decode";
  a GPU or engine change marks it stale. Optional "tune while I'm away", opt-in.

## Phase 5: quality gates before merge

- Contract tests on recorded Strata JSON; a fake-Strata harness (start, stop, update, tune, failures) in CI.
- A test that Zest never stops, restarts or updates a Strata it did not start.
- A hardware pass: real install, real start, real update, real quick tune, a Lemonade tune. Keep "Experimental" until clean.

## Risks

- Strata is 0.x and ships several times a week: pin a tested range, run contract tests nightly against the latest.
- The piped installer: checksum-checked here; stricter policy is a decision for the maintainer.
- RAM exhaustion is the likeliest way to ruin a session, hence Phase 2 before polish.
- The Lemonade tuner is the largest new build; its payoff depends on the spike.

## Decisions

- Idle shutdown default (suggested 20 minutes). Tuning always opt-in (suggested). First model recommended on mid-range
  hardware (suggested Qwen3.8 Flash-Next IQ3_XXS or Q2_0).
