# One interface, many engines: the overhaul plan

Goal: the person picks a model and talks to it. They never choose, start, stop, update or learn the name of an engine.
Lemonade and Strata become *providers*: modules that plug into Zest through one folder and a YAML file, and that Zest
uses behind a single model library, a single model picker, a single endpoint and a single status line.

This plan covers the interface, the shared model folder, the provider system, and the order to build it in. It builds
on `strata-support` (adoption, install, memory rules, updates and tuning already exist there, wired to Lemonade and
Strata directly; this plan generalises them).

## 1. What we know (checked, not assumed)

- **The two engines do not run the same files.** The big Strata models are GGUF files whose architecture is `qwen4exp`
  (Qwen3.8 Flash-Next) or `deepseek41`, which standard llama.cpp, and so Lemonade, does not know. The Qwen3.6-35B-A3B
  Unsloth packs are ordinary GGUF and should run on both. A shared folder is therefore one *place*, not one *set of
  files both engines can open*; Zest has to know which engine can run which file.
- **Layouts differ.** Lemonade keeps models in a Hugging Face cache layout (`models--org--repo/snapshots/…`); its settings
  `models_dir` (default `auto`) and `extra_models_dir` (a folder it scans for GGUF) exist today. Strata keeps
  `models/<name>/`, `mtp/` and `packs/` under a data dir (`strata launch --data-dir`), and downloads and prepares inside
  `strata launch`. Strata can also start from files you already have (`--model-path`).
- **Zest already builds a "library by symlinks".** `modelscan.py` assembles one folder of links from LM Studio, Ollama
  and Hugging Face caches and points `extra_models_dir` at it. The shared library generalises that idea.
- **The code is coupled to Lemonade.** `lem.`/Lemonade appears in 14 of the Python files (server 30 references, agent 14,
  engine 11, tuning 9, zestup 7, hardware 7, gateway 3, modelscan 3) and 77 times in `index.html`. The gateway and Pi both
  assume one Lemonade. The Strata work sits beside that code, not behind a shared interface.
- **The Models page is a hand-written list.** `PICKS` in `index.html` names Lemonade model ids and carries the opinionated
  copy; `PICK_ENGINES` mentions Strata and Maya as "separate engines". The interface currently teaches people about engines.
- **llamastash's "generic backend" is a good precedent** for YAML-declared engines: a binary, args with `{port}`/`{model}`
  placeholders, a ready path, a stop grace, knobs. Zest's provider YAML is the same idea with more to say (models, memory,
  updates, tuning).

### Spike results (Phase B)

- **Lemonade `models_dir`** is accepted, but only models in Lemonade's own registry appear from it. Any other GGUF is picked up only
  from `extra_models_dir`. **Lemonade does not check architecture:** it lists a `qwen4exp` file as a normal downloaded model, so it must
  only ever be pointed at a view of files it can run.
- **Strata refuses ordinary models** ("model type not supported by the Strata engine") and **uses its own files in place** with no
  copy (`own_files: true`). So Strata needs no view; it reads its folder under the library root.
- **When Lemonade runs as a system service** (as the `lemonade` user, models under `/var/lib/lemonade`, mode 0750), Zest cannot read
  its files at all. The library then takes that provider's model list from its API (name, size, labels, quantization) instead of reading
  headers. Sharing a folder only applies to a Lemonade Zest runs itself.
- **GGUF headers read fast** once arrays are skipped in blocks (about 0.1 s for a model with a 250k-word tokenizer), and the results are
  remembered by size and modified time.
- **Some disks can't hold links** (NTFS, FAT, exFAT). The views therefore live in Zest's own data folder, not on the models disk.

## 2. Principles

1. **Models, not engines.** The only nouns in the main interface are: model, chat, folder, memory, speed. Engine names
   appear in one place (Advanced > Engines, and an "Under the hood" line in a model's details), never in a flow.
2. **Zest decides, and says what it is doing.** Choosing a model routes it to the right provider, frees the memory, starts
   what is needed, and shows plain progress ("Getting Qwen3.8 ready: freeing memory, starting, loading. About 2 minutes").
   No questions about engines, ports, backends or recipes.
3. **One folder.** One library root holds every model. Moving it, freeing space and adding models happen in one place.
4. **One endpoint.** Other apps and Pi see one OpenAI-style address. A request for a model starts whatever serves it.
5. **Providers are data first.** A provider is a folder with a `provider.yaml`; code is an escape hatch, not the default.
6. **Never touch what Zest didn't start** stays true for every provider (adopted engines are "yours").
7. **Honest when it matters.** Slow starts, tight memory, and models that can't run here are said plainly, with a reason.

### Vocabulary rules for the main interface

| Don't say | Say |
| --- | --- |
| Lemonade, Strata, Pi, backend, recipe, llama.cpp, engine (in flows) | "Zest", "a model", "the AI", "your computer" |
| Load / unload a model | Get ready / free memory ("Freeing memory for Qwen3.8") |
| Context size, ctx | "How much it can remember" (a slider; "128k" in small print) |
| Preset, profile, run config | "Setup" |
| Port, endpoint, API key (outside Advanced > Server) | nothing |
| Backend: Vulkan / CUDA / ROCm | "Graphics card: NVIDIA RTX 3080" |
| Quantization, IQ3_S, Q2_0 | "Quality: Best / Great / Balanced / Fastest", the code in small print |

Engine names are shown **discreetly** (decision 4): a small muted line such as "Runs on Lemonade" at the foot of a model's
details and card, never in a heading, button, or step. They also stay in Advanced > Engines, logs, error details and docs.

## 3. The new interface

### Navigation

`Chat · Library · Skills · Docs · Help · Settings`, with `Advanced` below as today.

- **Library** replaces "Models". **Settings** (new, short) replaces the scatter of engine pages for ordinary people:
  where models live, how much memory Zest may use, how much it remembers by default, speed (tuning), updates.
- **Advanced** keeps: Server (other apps), Hardware, Engines (generic provider list), All settings, Themes, Logs.
  The Setup guide, the Strata box and the separate Tuning page are retired into the flows below.

### First run: "Zest is getting ready"

One screen with one question, and everything else automatic. The question is **where models should live** (decision 1):
the drive with the most free space is pre-selected, each option shows its free space, "Choose another folder" is one
click away, and Continue accepts the default, so the common path is a single click. Models are big, and the person knows
which disk has room. The rest needs no choices. Zest scans the hardware, decides which providers it needs (always the fast one; the
big-model one when the computer can use it), installs and checks them with plain progress, then shows the recommended
model per tier and a **Start chatting** button. "Use models I already have" and "Use my own engine" are links, not steps.
Existing installs of either engine are adopted silently ("Found models you already have").

### The model picker (the centre of the product)

One list, in the chat bar and in Library, grouped by size of computer rather than by engine (decision 3):

- **Small models**: under 10 GB of graphics memory. Run almost anywhere.
- **Medium models**: 10 to 24 GB of graphics memory.
- **Large models**: need a big computer (16 GB or more of graphics memory *and* about 64 GB of RAM). These are the ones
  the big-model provider runs; they start slowly.

A model's tier is set by what it needs, not by who serves it. A tier the computer can't run stays visible but greyed, with
the reason ("Needs about 64 GB of RAM; this computer has 16 GB"), so people can see what a better machine would add.
(The RAM figure is a fit check, not a literal 64: this computer reports 62.7 GB and runs the Large models fine, so the
check is "memory the model needs fits", using the same rule as the recommendation today.)

Each row: name, one line of opinion (the existing `take` copy), size, a **fit** chip (Fits / Tight / Won't fit here, with
the reason), and a **start time** hint ("Ready in seconds" / "About 2 minutes to start"). Selecting a model that isn't
running:

1. Zest shows a compact progress card in the chat ("Getting ready: freeing memory → starting → loading").
2. Whatever else holds the memory is stopped first, by rule (section 5.6), unless it is yours and busy, in which case the
   card says why it can't and offers the one honest choice.
3. The first message waits for it; nothing else is asked.

The status chip becomes one line: *Ready · Qwen3.8 Flash-Next*, *Getting ready…*, *Busy*, *Tuning…*. No engine names.

### Library

One grid, one set of cards. Tabs: **Installed**, **Recommended for this computer**, **Everything**, plus search.
A card shows the same facts whatever runs it. **Add models**: paste a link, pick a folder, or "Find models already on this
computer". Downloads share one queue and one progress style. Card details have **Under the hood** (engine, format, files,
location) for the curious.

### Settings (new)

- **Where models live** (the library folder): one path, space left, "Move my models" with a copy-check-delete flow.
- **Memory**: how much of RAM and graphics memory Zest may use; "Free memory after N idle minutes" (default 20, applies to
  big models).
- **Memory for conversations**: the 128k default, one slider.
- **Speed**: the tuning flow, per model, same for both engines (today's Tuning page, minus the engine headings).
- **Updates**: one switch ("Keep Zest and its parts up to date"), one button.

### Errors and progress

A single translator (`friendlyProblem`, extended) turns any provider failure into: what happened, why, what to do. Provider
log excerpts go under "Details". Progress always reads in tasks the person understands.

### Other apps (Server page)

One address for everything. It routes by model name: asking for a big model starts it (with the same rules). Stays in
Advanced.

## 4. One model library

### Shape

```
Models/                         the one folder the person chooses (decision 1); Settings can move it later
  lemonade/                     what Lemonade downloads (Hugging Face layout; its models_dir)
  strata/                       what Strata downloads: models/, mtp/, packs/ (strata launch --data-dir)

<Zest data folder>/views/<engine>/   links Zest maintains: only the files that engine can run (never inside Models/)
```

The Models folder holds only models, one subfolder per engine. The views are tiny folders of links kept in Zest's own data folder, so they
work whatever kind of disk the models are on (a disk that can't hold links is no longer a problem) and the Models folder stays clean to
look at, back up or move. A new engine gets its own subfolder the same way (`models.data_dir: "{library}/<engine>"`).

Zest owns a **catalog**: every model file set found under the root and under optional extra sources (LM Studio, Ollama,
other Hugging Face caches, adopted engines' folders). For each it records identity, files, size, and, read from the GGUF
header, **architecture, quantization, native context, vision and MTP flags**. (A small header reader is straightforward and
was prototyped while planning.)

### Who can run what

Each provider declares which `formats` and `architectures` it can run (section 5). The catalog joins the two:

| File | Fast provider (llama.cpp) | Big-model provider |
| --- | --- | --- |
| Standard GGUF (Gemma, Qwen3.6, LFM, GLM…) | yes | where the family is supported |
| `qwen4exp`, `deepseek41` GGUF (Strata's own) | no | yes |

Routing is simple (decision 2): **a Large-tier model that Strata supports goes to Strata; everything else goes to
Lemonade.** The tier comes from what the model needs (above), so a model Strata *can* run but that fits a graphics card
outright (Qwen3.6 35B-A3B, the current "all-rounder" pick, is Medium) stays on Lemonade, which starts in seconds. If that
is not what is wanted, the rule changes in one place: the tier cut-offs in the catalog.

### Making both read "the same folder"

- Lemonade: `models_dir` → `Models/lemonade`; `extra_models_dir` → the Lemonade view in Zest's data folder (links to any other file it can run).
- Strata: `--data-dir Models/strata` for new downloads. Strata uses its own files in place and refuses ordinary models, so it needs no view.
- Zest keeps the views in sync whenever the catalog changes. No file is stored twice.
- **Verify early (spike):** that Lemonade accepts an arbitrary `models_dir` with the HF layout; that Strata accepts
  `--model-path` for a standard GGUF shard set; and what Lemonade does with a file it can't run in `extra_models_dir`
  (hide it from the views so it never sees one).

### Downloads

Providers declare how they download. Lemonade pulls by name; Strata downloads and prepares inside `strata launch` (not
separable). Zest shows both in one queue; for Strata it measures progress by the growth of `<root>/strata` against the plan's
`download.gb`. Plain GGUF downloads go through Lemonade (decision 5): Zest asks Lemonade to pull, so there is no second downloader to
build or maintain. Strata's own models download inside `strata launch`.

### Migration

First run after upgrade: Zest *adopts*, never moves. Existing Lemonade and Strata locations become extra sources in the
catalog and appear in the Library immediately. "Move my models into one folder" is offered later, with a space check, and is
never required.

## 5. Providers: modular and pluggable

### 5.1 What a provider is

```
providers/
  lemonade/
    provider.yaml        declarative description (everything below)
    provider.py          optional: only for logic YAML can't express
    docs.md              optional: shown under Advanced > Engines
  strata/
  _template/             copy this to start a new provider
```

Search order: user folder (`~/.local/share/zest/providers/`) over bundled (`zest/providers/`). A folder that fails
validation is skipped and listed under Advanced > Engines with the exact line that is wrong; it never breaks Zest.

### 5.2 `provider.yaml` (draft schema, with the two real providers)

```yaml
id: strata
name: Strata                       # Advanced > Engines and "Under the hood" only
summary: Runs very large models by splitting them across graphics card, RAM and SSD.
tier: very-large                   # which picker group its own models land in by default
api_version: 1

detect:                            # adopt before install
  binaries: [$ZEST_STRATA, strata, ~/.local/bin/strata]
  ports: [8080-8089]
  health: {path: /health, expect: {service: strata}}
  commands:
    running: ["{bin}", launch, --status, --output, json]     # parsed with `running` below

install:                           # optional; omit to say "install it yourself"
  method: script
  url: https://github.com/Niko1221/Strata/releases/latest/download/install.sh
  checksum: {url: https://github.com/Niko1221/Strata/releases/latest/download/SHA256SUMS.txt, name: install.sh}

update:
  check: {command: ["{bin}", update, --check, --output, json], parse: {available: "$.engine.status", serving: "$.serving[*].profile"}}
  apply: {command: ["{bin}", update, --output, json], needs_stopped: true}
  series_guard: "0.2"              # don't take a new series automatically

run:
  mode: managed                    # managed (Zest starts a process) | service (already running) | external (a remote address)
  start: ["{bin}", launch, --preset, "{preset}", --yes, --no-tui]
  stop:  ["{bin}", launch, --stop]
  ready: {path: /health, expect: {loaded: true}, timeout: 1800}
  port_range: 8080-8089

endpoint:
  base_url: "http://127.0.0.1:{port}/v1"
  key: {from: ["{bin}", launch, --show-key]}      # optional
  models: {path: /v1/models, id: "$.data[*].id", context: "$.data[*].meta.n_ctx", vision: "$.data[*].architecture.input_modalities"}

models:
  formats: [gguf]
  architectures: [qwen4exp, deepseek41]
  data_dir: "{library}/strata"
  download: {mode: provider, via: setup}          # downloads happen inside `start`/`setup`
  setup:
    command: ["{bin}", launch, --family, "{family}", --model, "{model}", --context, "{context}", --save-preset, "{name}", --yes, --no-tui]
    plan: ["{bin}", launch, --plan, --family, "{family}", --model, "{model}", --output, json]
  catalog: catalog.yaml                            # families/models, memory need, default context, quality names

memory:
  group: gpu                       # providers in the same group never run together
  exclusive: true
  need: {from: catalog}            # or an expression over file size
  idle_stop_minutes: 20            # default; the Settings screen overrides
  start_estimate: {seconds: 120}

capabilities:
  context: fixed                   # fixed | per-load
  multi_model: false
  tuning: {plan: [...], run: ["{bin}", tune, auto, "{profile}", ...], status_file: true, undo: [...]}
  logs: ["{bin}", logs]
```

```yaml
# lemonade/provider.yaml (shape only)
id: lemonade
name: Lemonade
tier: quick
detect: {binaries: [lemond], ports: [13305, 13307], health: {path: /api/v1/health}}
run: {mode: managed, start: ["{bin}", "{data}", --port, "{port}", --no-broadcast], ready: {path: /live}}
endpoint: {base_url: "http://127.0.0.1:{port}/api/v1"}
models:
  formats: [gguf]
  architectures: [llama, qwen2, qwen3, qwen3moe, gemma3, gemma4, lfm2, glm4, …]   # from Lemonade's own model data
  data_dir: "{library}/lemonade"
  download: {mode: api, pull: {method: POST, path: /api/v1/pull}}
memory: {group: gpu, exclusive: false, need: {from: file-size}, max_loaded: 1}
capabilities: {context: per-load, multi_model: true, tuning: {builtin: zest-measure}}
options: {file: options.yaml}      # per-load knobs (args, backend, device) rendered as forms; llama.cpp schema stays in Advanced
```

Anything a third party needs (a llama-server wrapper, vLLM in a container, LocalAI) is the same file with a different
`start` and `endpoint`. A provider that only serves an already-running OpenAI-style address is five lines (`run.mode: external`).

### 5.3 The Python escape hatch

`provider.py` may define `class Provider(zest.providers.Base)` overriding any step the YAML does by default:

```
detect() · status() · install(progress) · update_check() · update(progress) · start(model, opts, progress)
stop() · ensure(model) · models() · endpoint() · options_schema() · tune_plan() · tune_run(job)
```

`GenericProvider` implements all of it from YAML (spawn with templated args, probe `ready`, parse JSON by path, run
`update` and `tune` commands). Lemonade and Strata ship as YAML plus a small `provider.py` each, where Lemonade's HTTP
API and Strata's presets need more than templates; today's `lem.py`, `engine.py`, `lemtune.py`, `strata.py` become
those modules' internals.

### 5.4 The registry and the router (`providers.py`)

- Loads and validates provider folders (JSON Schema for the YAML, readable errors), exposes the provider list.
- `catalog()`: the unified model list with fit and start-time hints.
- `route(model)`: chooses the provider: Large tier and supported by the big-model provider → that provider; otherwise
  Lemonade; if no provider can run the file, refuse with a reason.
- `activate(model)`: the **memory arbiter** (5.6) stops what must stop, starts the provider, waits for ready, reports
  progress to the interface, and returns the endpoint to use.
- `endpoints()`: what Pi and the gateway should talk to for a given model.

### 5.5 What the rest of Zest stops knowing

| Today | After |
| --- | --- |
| `agent.py` calls `lem.request("/v1/load")`, `strata.ensure` | `providers.activate(model)` returns an endpoint; Pi's `models.json` is generated from `providers.endpoints()` |
| `gateway.py` relays to one Lemonade | gateway routes by model name through `providers.route/activate` |
| `engine.py` starts Lemonade, `strata.py` starts Strata | each provider's `run` section |
| `zestup.py` checks three things by hand | loops over `provider.update_check()` |
| `tuning.py` has two branches | loops over providers with `capabilities.tuning` |
| `hardware.py`, `server.py` expose Lemonade-specific data | per-provider status under Advanced > Engines, generic |
| `index.html` strings and `PICKS` name engines | the catalog supplies cards; copy follows the vocabulary rules |

### 5.6 Memory arbitration, generalised

Every provider declares a memory `group` and whether it is `exclusive` within it. `activate(model)`:

1. Work out what the model needs (`catalog` figure, or file size) and what is free.
2. Providers in the same group that Zest started, or whose engines are idle, are stopped or unloaded; the idle clock
   (`idle_stop_minutes`) applies to Zest-started processes.
3. A provider the person started themselves is never stopped; if it blocks, the progress card says so and offers the one
   real choice ("Stop it yourself, then try again").
4. Below 60% of the need in free memory, refuse with numbers and the biggest memory users (already built for Strata).
5. Start, wait for ready, hand over the endpoint.

This replaces `strata.before_launch`, `free_for_lemonade`, the tuning "stops" list and the ad-hoc unloads.

### 5.7 Adoption, ownership, updates, tuning, secrets

- **Adoption** is `detect` in YAML: binaries on `PATH`, listening ports, health probes, saved addresses. A found engine is
  "yours"; one Zest started is "Zest's"; ownership is stored per provider.
- **Updates** run through `update.check/apply`, with `needs_stopped`, `series_guard` and "yours is left alone" handled once.
- **Tuning** is a provider capability (`measure` built in, or a command). The Speed screen is the same for every provider.
- **Settings and secrets** live under `providers.<id>` in Zest's settings; keys are read from the command or typed once.

### 5.8 Trust, with the least friction (decision 6)

Ordinary people never add providers, so the rule is: **nothing to decide unless something risky is happening.**

- **Bundled providers** (Lemonade, Strata): trusted, no prompts, ever.
- **Adding a provider is an Advanced action** ("Advanced > Engines > Add an engine": drop a folder in, or paste a folder
  link). It is never part of setup or the picker, so most people never meet it.
- **An engine that only points at an address that is already running** (`run.mode: external`, no commands, no downloads):
  no prompt. It can only talk to where it was pointed.
- **An engine that runs a program or downloads one**: one plain confirmation the first time, saying what it will do in
  words ("This engine will start a program on your computer called `llama-server` and download it from github.com. Add it?"),
  with the exact commands under "Details". One click, remembered.
- Commands run without a shell and the YAML can only use a fixed set of variables, so a provider cannot do more than start
  the program it names; installs still need a checksum unless the author marks them `unsafe_install`, which adds a warning
  to the confirmation. No signing or allow-lists: they add friction without protecting anyone who is already choosing to
  add the folder.

## 6. Build order

Each phase leaves Zest working. Estimates: S under a week, M one to two, L two to four.

**A. The seam (M). First slice built on `strata-support`:** the provider interface (`provbase.py`), the registry and YAML validation (`registry.py`), the YAML-only `GenericProvider`, Lemonade and Strata as bundled providers (YAML plus a thin `provider.py` that delegates to the existing code), a read-only `/ui/providers` page of data, and 15 tests including an engine added with nothing but a YAML file. Not yet: nothing outside the new API calls the providers (agent, gateway, updater and tuning still call Lemonade and Strata directly), and the adapters are only checked against the real machine, not by contract tests against the old behaviour. Moving callers over is Phase C.

**A, as planned.** Define `Provider`/`GenericProvider`, the registry and the YAML schema. Wrap today's Lemonade and
Strata code as the first two providers *without changing behaviour or UI*. Contract tests (the recorded-JSON and fake-engine
harnesses already used for Strata) run against both. Exit: every existing flow works through `providers.*`.

**B. The library (L). Built on `strata-support`:** `gguf.py` (header reader, roles), `library.py` (sources, catalog, tiers, routing, fit, per-engine views, root and places, `lemonade_config()`), provider hooks (`model_folders`, `model_id`, `need_gb`, `fit`, `library_models`), `/ui/library`, `/ui/library/places`, `/ui/library/root`, and 17 tests. Verified on this machine: 11 models in three tiers, each routed and mapped to its engine's name for it. Not yet: nothing calls `lemonade_config()` (applying it is only ever for a Lemonade Zest runs), no UI uses the library, and "Move my models" is not built.

**B, as planned.** GGUF header reader, the catalog, `.views`, the library root, adoption of existing folders, the
early spikes in section 4 ("verify"). Exit: one catalog lists every model both engines can see, with correct "who can run it".

**C. One router and one endpoint (L). Built on `strata-support`:** `router.py` (resolve a model name to an engine, `activate` with the memory arbiter, `deactivate`, a generic idle watcher, `chat_models`), provider hooks (`ref`, `chat_models`, `loaded`, `max_ctx`, `is_running`, `busy`, `activity`, `owned_instance`, `idle_minutes`, `pi_endpoint`), the Lemonade context-window logic moved into its provider, `agent.py` no longer mentions Strata (chat list, loading, Pi's config and `--provider` all come from the router), the gateway and Zest's own `/api/v1` proxy route by the model name in the request (starting and freeing as needed, rewriting the name to the engine's own), and `GET /v1/models` for other apps lists every engine's models. 16 new tests (48 in all). Verified live: Lemonade load, auto-load through the proxy, unload. Not verified live: Strata through the router (it wasn't running), the arbiter with two real engines, and the idle watcher with a real provider. Still to do: the memory-need check is only Strata's own (not yet generic), and `strata.before_launch` still exists for the Models-page Start button, which bypasses the router.

**C, as planned.** `route`/`activate` and the generalised arbiter; Pi's config and the gateway move
onto them. Exit: asking for any model by name, from chat or another app, starts the right thing and frees the rest.

**D. The interface, in steps (L, shippable as it goes).**
1. The model picker and the one-line status chip (chat bar), with progress cards. **Built on `strata-support`:** `/ui/picker` (every engine's models with tier, fit, size or memory need, start-time hint and a discreet engine name); a grouped list (Small, Medium, Large) with keyboard support; choosing a model gets it ready straight away; one status line (Ready, Getting ready…, Answering…, Starts when you send); a progress card with the steps Zest takes (freeing memory, starting, loading) and the time so far; "Free memory" and "Load it again" tucked away. Large models start from any saved Strata setup, not only ones Zest made. Checked in the browser with Lemonade models; the Large-tier start is untested live because it would have started your real Strata.
2. Library page replacing Models; unified downloads. **Built on `strata-support`:** the nav and page are now "Library" with tabs Installed / Recommended for this computer / Everything (a kind filter and search), every group named Small / Medium / Large with the same size words on the cards; Large models from the big-model engine appear beside the rest, installed ones with Chat and Make it faster, new ones with Get it (the whole setup in one step); one "Getting models" strip shows Lemonade downloads and a Strata setup together; the engine is named small at the foot of each card; the library folder is one question (disks listed by free space, most room suggested, "Not now" allowed) and one line afterwards; "Add models" offers a Hugging Face link, a folder, or the usual places. Folder imports now never link a file Lemonade can't run. The Strata box moved to Advanced > Engines, where it stays until step 5. Verified in the browser against this machine's real models; not run live: choosing a folder through the dialog (it changes the real Lemonade's settings), Get it for a Large model, Delete.
3. First run: "Zest is getting ready"; Setup guide retired. **Built on `strata-support`:** one "Getting ready" screen replaces the four-step wizard. It asks the one question (where models live; skipped when models or a Lemonade are already here) and then works a checklist by itself: looked at your computer, got the part that runs AI (downloaded automatically, with progress), fitted it to your graphics card (installed automatically), then "Choose your first model" with one suggestion per size (Small, Medium recommended when it fits, Large when the computer can take one) and "Start chatting with <model>". Links: "I already have models" (opens Add models), "I run my own server", "Skip". Engines are never named on it. Verified in the browser on the adoption path (everything already here) and, with stubbed state, the question-first and downloading states. Not run for real: the automatic helper download and graphics-card install on a truly fresh machine, and Get it for a Large model. The Setup guide itself is still reachable (Advanced > Setup guide) until step 4 moves its pieces (updates, Pi, own server, repeat the first-run screen) into Settings.
4. Settings: library folder, memory, conversation memory, speed (tuning), updates. **Built on `strata-support`:** a new Settings page in the main menu (the old "All settings" is now "Engine settings" under Advanced): where models live (path, free space, change, add models you already have), memory (idle minutes for large models Zest started), how much a chat remembers (one selector, 128k default), speed (a link into Tuning with "N of M models tuned"), and Update everything with the auto-check switch; "My own server, and the first-run screen" is a collapsed section holding the Lemonade choices, version checks and a replay of the first-run screen. The Setup guide is gone: its backend, model-folder and chat steps and its hero were removed, Pi moved to Advanced > Engines, and every old `#setup`, `show("setup")` and "Setup guide" link lands on Settings. Update-list names are friendlier ("The part that runs AI (Lemonade)"). Checked in the browser: every section, the controls (remembers, idle minutes) saving, the own-server section, Pi and the large-model box on Engines, and a clean load with no console errors. Not done: "Move my models" (changing the folder only affects new models); a real memory cap (only the idle time is a setting).
5. Engine-specific screens move to Advanced > Engines (generic, driven by provider data); remove the Strata box and the
   Tuning page's engine headings. **Built on `strata-support`:** Advanced > Engines is now one card per engine built from `/ui/providers` (name, tier, built in or added by you, version, running and who started it, a uniform update check and Update, Stop for what Zest started, a consent block with an Allow button for added engines, load errors with the line to fix, and an Add an engine section with the folder and a Reload). New generic calls: `/ui/providers/check`, `/update`, `/stop`, and `update_status()` on every provider. A Lemonade the person runs says so instead of offering an update that wouldn't touch it. Lemonade's kinds-and-hardware manager and Strata's setups and connections sit inside their own cards as collapsed details (the one place still specific to a bundled engine: a provider can't yet ship its own panel in YAML). The Strata box no longer repeats the update line or the idle setting. The Tuning page's engine headings are not changed yet. Checked in the browser with the real engines and with a user-added engine (consent, Allow) and a broken one (load error).
6. Copy, Docs and Help rewritten to the vocabulary rules; remove `PICK_ENGINES` copy from main flows.

**E. The provider SDK (M).** Loader hardening, validation tool (`zest providers check <folder>`), `_template/`, "Add an
engine in ten minutes" docs, and a third example (a bare llama-server wrapper) as proof the YAML is enough.

**F. Polish and migration (M).** Settings migration, accessibility and phone-width passes, an end-to-end UI test pass in the
browser, performance of the catalog scan on big libraries.

## 7. Risks and open questions

- **Format compatibility is the soft spot.** "Same folder" cannot mean "same files" for `qwen4exp`/`deepseek41`. The spike
  decides how much the library can truly share (and whether `models_dir` can be moved freely).
- **Strata downloads aren't separable**, so unified progress is an estimate there, and a failed download can't be resumed by Zest.
- **Cold start time.** Very large models take minutes; the interface must keep that calm and honest, and a model should be
  warmed when the person selects it, not on their first message.
- **A real gateway rewrite.** Routing by model name for other apps changes a long-standing contract; keep the old path
  working for existing clients until Phase C's tests pass.
- **YAML expressiveness.** Some behaviours will need Python; the line is "YAML for what a template can say, Python for
  protocols". Resist growing YAML into a language.
- **Model identity across engines.** The same model may exist as two file sets (a Lemonade GGUF and a Strata pack); the catalog
  needs a stable identity so the person sees one card.
- **Naming and credit.** Hiding engine names from flows must not hide credit: Advanced > Engines, Docs and the README keep
  "Powered by" with links and licences.

## 8. Decisions (answered)

1. **Library root:** the person chooses, once, at first run (best-free-space default, one click), movable later.
2. **Routing:** Large-tier models Strata supports go to Strata; everything else to Lemonade.
3. **Tiers:** Small (under 10 GB of graphics memory), Medium (10 to 24 GB), Large (16 GB and about 64 GB of RAM, the Strata tier).
4. **Engine names:** shown discreetly, as a small muted line in details and at the foot of cards.
5. **Downloads:** plain GGUF through Lemonade.
6. **Provider trust:** bundled is trusted; adding is an Advanced action; no prompt for address-only engines; one plain
   confirmation for engines that run or download a program.

Consequences to watch: the Large tier's "16 GB and 64 GB" overlaps Medium's 10 to 24 GB range, so tiers are by what a model
needs, not by the machine; and models both engines could run follow the tier rule, so Qwen3.6 35B-A3B (Medium) stays on Lemonade.
