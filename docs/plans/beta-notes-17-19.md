# Plan: beta notes (#17) and fonts (#19)

Issues: [#17 Beta test notes](https://github.com/yudhanjaya/Shoggoth/issues/17), [#19 Fonts](https://github.com/yudhanjaya/Shoggoth/issues/19).

## Decisions so far

- **Lemonade stays.** It remains a supported provider (it may matter for future plans, such as NPU use). Nothing is removed. The "strip out Lemonade" item in #17 is closed as won't-do for now.
- **Out of scope here:** Maya, SGLang, vLLM and the "full engines list". Maya becomes its own PR later.
- **Model recommendations:** the maintainer supplies the tested model set and the descriptive text, built from their own tests. Do not write placeholder entries. Ask when step 6 starts.
- Most UI work lands in `shoggoth/index.html` (one file, about 6,400 lines).

## Order of work

1. Fonts (#19)
2. Bug fixes
3. Navigation and naming
4. One model picker
5. Speed and per-model tuning
6. Models and recommendations
7. New features

Steps 1 and 2 are independent of everything else. Steps 3 and 4 touch the same part of `index.html`, so they go in sequence. Step 5 must land before step 6, because recommendations and the "tuned" tag use its data. Each step is one or more PRs, and `python3 -m unittest discover tests` must pass for every one.

---

## 1. Fonts (#19)

- Embed Chakra Petch (base64 woff2, like Doto, Inter and JetBrains Mono, so nothing loads from the internet). Add its OFL license text next to `docs/plans/assets/fonts/OFL-doto.txt`.
- Use it for the Shoggoth title text: `.brand` (line 178) and `.brandbig` (line 690), which use `var(--serif)` today.
- Keep Doto only for the hardware interface visuals (`.rig .appar text`, `#v-server.pics .reqlist`).
- Try Doto on the model thinking block (`.think`, line 263) and keep it only if readable. The body is 13.5px multi-line prose, so expect it may not be. Fallback: Doto on the "Thinking…" summary label only, with the body in the existing font.

## 2. Bug fixes

Each is a small commit.

- The chat resets the moment a model finishes loading.
- The top bar says "Not running" while a model is running (`#sname`, line 1804).
- Hardware: "No devices found" while Strata is running (line 5105). Check how GPU data is read when the engine is Strata.
- Engine settings page is empty for Strata.
- Job text overflows the right sidebar.
- Chat text overflows its grey-green box in the Teenage Engineering skin.
- The Library no longer shows the shoggoths.
- The Library won't let you switch away from the model folder with the most room.
- Tokens/s is missing from replies (restored in step 5).

## 3. Navigation and naming

- Merge the two left sidebars into one: scrollable chats, with jobs as collapsible sections under them. The Shoggoth system menu stops taking so much space.
- Rename "Library" to "Models".
- Dissolve Settings:
  - where models live → Models
  - memory → Hardware
  - how much a chat remembers → chat window
  - speed → Tuning
  - model pictures → chat right sidebar
  - Update everything → Engines
- Advanced pages use the same font and size as the rest: "Advanced > Tuning", not a different typeface.
- Standardize the folder prompt ("Use the folder", "Choose another folder", "Not now"): proper spacing, and the choice is saved to the model library immediately, not only from Settings.

## 4. One model picker

- Merge the model tube, the top-bar dropdown and the loader sidebar into one control at the top of the chat. It holds the model select, memory, running state and load/unload. Either a dropdown or a sidebar, not both.
- It lists models from every provider, from the same source as Models. Today the dropdown lists only the loaded model.
- Remove the "slots" wording. It is just load and unload.
- The tube is empty by default. The shoggoth drops in on load, and the liquid level shows VRAM use. Make it obvious that the tube is clickable.
- Models shows the same visualizations as the picker.

## 5. Speed and per-model tuning

### What exists

- `lemtune.py` tunes Lemonade's llama.cpp models: it measures the baseline several times, tries one change at a time, keeps only changes that beat the noise (and at least 3%), and can roll back. Results are in `tuning.json`, keyed by model id.
- Strata tunes with `strata tune auto`, and keeps its own results, keyed by preset and profile, under `~/.config/strata/`.
- `tuning.targets()` already puts both in one shape (`has`, `gain_pct`, `decode`, `baseline`, `outcome`, `when`, `can_undo`).

### What was checked on a real install (Strata 0.2.7)

- Strata tunings belong to a **preset**, not a model. One model can have several presets, and `presets/active.json` records the active one per slot.
- **`tuned_preset` from `strata launch --list-presets` is the reliable "tuned" signal.** `tuned_at` is not: for IST-DAS-2.00bpw it is set, but no tuning exists (no stored preset, no tuner history, no `.tune.json`). IST-DAS was never tuned, so "not tuned" was correct there. Our profile-name derivation (`profile_of()`) matches Strata's files.
- IQ3_S dual is tuned with outcome `default_confirmed`: 79.1 tok/s, 0.0% gain. Showing "0%" would read as "tuning did nothing".
- `presets/history.jsonl` records decode and prefill tok/s and peak memory for every trial, which can feed speed ratings for Strata models.

### Design

1. **One model-level function**, `tuning_for(model)`, returning `{engine, source, state, gain_pct, decode_tok_s, baseline_tok_s, when, can_undo, details}`. The UI never branches on engine.
   - Strata: read `tuned_preset` from `--list-presets` (one call, no per-preset subprocess), prefer the active preset, then the Shoggoth-made one, then the most recent. Call `presets status` only for detail on tuned ones. Strata stays the source of truth, read-only.
   - Lemonade: read `lemtune.results()`.
2. **States:** Tuned (with tok/s and gain); Tuned, defaults were already best; Stale; Not tuned (with a "Tune" button).
3. **Shoggoth's own record, separate** (`model-meta.json`, keyed by model id): measured speed rating, GPU headroom, notes. It never holds tuning flags. Those stay in Strata or in `lemtune`'s results, so nothing is duplicated.
4. **Lemonade staleness:** save backend, llama.cpp version and Lemonade version with each result, and mark it stale when they change (the way Strata does). Today `stale` is hard-coded false.
5. **Actions:** "Test speed" (the baseline run on its own, no search) and "Tune" (the full search), the same everywhere. Strata → `strata tune auto`, Lemonade → `lemtune.run()`. Undo works as it does now. Reuse `tuning.blockers()`, since a test needs the GPU.
6. **Live speed:** use llama.cpp `timings` from the response for tokens/s on each reply, with the current `chars / 4 / seconds` estimate (`gateway.py:167`) only as a fallback.
7. **Headroom:** a per-model "leave N GB free for the system" setting, applied on load as a cap on GPU offload.
8. **Tests:** extend `tests/test_strata_presets.py` and add a contract test that both engines return the same record shape, covering no preset, several presets for one model, a stale tune and `default_confirmed`.
9. **Before building:** with the owner's go-ahead, run the Lemonade tuner once on a small model to confirm the measurements work on this machine (it loads a model).

## 6. Models and recommendations

- "Recommended for this computer" shows our picks even when Lemonade is not installed. Choosing one runs the Lemonade install and setup, restarts the server, then downloads through it.
- Rename "Add models" to "Add models from other sources". Work out what "Everything" does, then fix or remove it.
- Card format: model name, minimum requirements, provider, description, speed rating, tools availability. Speed ratings come from step 5 data and the owner's tests, in the style of Handy.computer: not bleeding-edge, but tested.
- A tuned/not-tuned tag on each model in Models (from step 5).
- A model that fails to load is handled gracefully, with a plain explanation.
- **Owner supplies the model set and text.** Do not invent entries.

## 7. New features

- "Start a job" gets a visible button. Better: an agent skill, so the agent asks and fills the jobs bar itself. Right now jobs are little different from chatting.
- Image upload in chat for models that support vision.

## Later, separate PR

- Maya engine (with its own integration testing).
