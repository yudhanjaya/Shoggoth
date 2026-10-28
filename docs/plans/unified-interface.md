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

Engine names stay available in: Advanced > Engines, a model's "Under the hood" disclosure, logs, error details, docs.

## 3. The new interface

### Navigation

`Chat · Library · Skills · Docs · Help · Settings`, with `Advanced` below as today.

- **Library** replaces "Models". **Settings** (new, short) replaces the scatter of engine pages for ordinary people:
  where models live, how much memory Zest may use, how much it remembers by default, speed (tuning), updates.
- **Advanced** keeps: Server (other apps), Hardware, Engines (generic provider list), All settings, Themes, Logs.
  The Setup guide, the Strata box and the separate Tuning page are retired into the flows below.

### First run: "Zest is getting ready"

One screen, no choices to make. Zest scans the hardware, decides which providers it needs (always the fast one; the
big-model one when the computer can use it), installs and checks them with plain progress, then shows the recommended
model per tier and a **Start chatting** button. "Use models I already have" and "Use my own engine" are links, not steps.
Existing installs of either engine are adopted silently ("Found models you already have").

### The model picker (the centre of the product)

One list, in the chat bar and in Library, grouped by what the person wants rather than by engine:

- **Quick**: answers fast, runs anywhere (small models).
- **Balanced**: the everyday pick.
- **Thorough**: slower, smarter (large models).
- **Very large**: needs a big computer; starts slowly (the Strata class).

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
<library root>/                 chosen once (default: today's location; the Settings screen moves it)
  hub/                          Hugging Face layout; Lemonade's models_dir
  strata/                       Strata's data dir: models/, mtp/, packs/ (strata launch --data-dir)
  .views/<provider>/            symlinks Zest maintains: only the files that provider can run
```

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

A model with more than one capable provider is routed by policy (5.4): fits graphics memory comfortably, take the fast
provider; otherwise the big-model provider. The person can override per model in "Under the hood", rarely needed.

### Making both read "the same folder"

- Lemonade: `models_dir` → `<root>/hub`; `extra_models_dir` → `<root>/.views/lemonade` (links to any other file it can run).
- Strata: `--data-dir <root>/strata` for new downloads; `--model-path` pointing into `hub/` or a view for standard GGUF it
  can use.
- Zest keeps the `.views` folders in sync whenever the catalog changes. No file is stored twice.
- **Verify early (spike):** that Lemonade accepts an arbitrary `models_dir` with the HF layout; that Strata accepts
  `--model-path` for a standard GGUF shard set; and what Lemonade does with a file it can't run in `extra_models_dir`
  (hide it from the views so it never sees one).

### Downloads

Providers declare how they download. Lemonade pulls by name; Strata downloads and prepares inside `strata launch` (not
separable). Zest shows both in one queue; for Strata it measures progress by the growth of `<root>/strata` against the plan's
`download.gb`. A later option is a Zest-side Hugging Face downloader for plain GGUF, so one resumable downloader serves all.

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
  data_dir: "{library}/hub"
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
- `route(model)`: chooses the provider. Order: the person's override → the only provider that can run the file → fits
  graphics memory comfortably → fast provider, otherwise big-model provider → refuse with a reason.
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

### 5.8 Trust

Bundled providers are trusted. A provider from the user folder shows a consent card the first time ("This engine will run
`<command>` and download from `<host>`"), commands run without a shell, templating is limited to a fixed set of variables,
installs need a checksum unless the author marks `unsafe_install: true`, which shows a warning. Providers cannot read other
providers' keys.

## 6. Build order

Each phase leaves Zest working. Estimates: S under a week, M one to two, L two to four.

**A. The seam (M).** Define `Provider`/`GenericProvider`, the registry and the YAML schema. Wrap today's Lemonade and
Strata code as the first two providers *without changing behaviour or UI*. Contract tests (the recorded-JSON and fake-engine
harnesses already used for Strata) run against both. Exit: every existing flow works through `providers.*`.

**B. The library (L).** GGUF header reader, the catalog, `.views`, the library root, adoption of existing folders, the
early spikes in section 4 ("verify"). Exit: one catalog lists every model both engines can see, with correct "who can run it".

**C. One router and one endpoint (L).** `route`/`activate` and the generalised arbiter; Pi's config and the gateway move
onto them. Exit: asking for any model by name, from chat or another app, starts the right thing and frees the rest.

**D. The interface, in steps (L, shippable as it goes).**
1. The model picker and the one-line status chip (chat bar), with progress cards.
2. Library page replacing Models; unified downloads.
3. First run: "Zest is getting ready"; Setup guide retired.
4. Settings: library folder, memory, conversation memory, speed (tuning), updates.
5. Engine-specific screens move to Advanced > Engines (generic, driven by provider data); remove the Strata box and the
   Tuning page's engine headings.
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

## 8. Decisions needed

1. **Library root default.** Keep today's location (Hugging Face cache) as the default root, or ask once during
   "getting ready" where big models should live (they are hundreds of GB)?
2. **Routing policy for models both engines can run.** Prefer the fast provider when it fits (suggested), or always prefer
   the person's last choice?
3. **Tier names.** Quick / Balanced / Thorough / Very large (suggested), or other words?
4. **How much "Under the hood".** Show engine names in a model's details (suggested), or only under Advanced > Engines?
5. **Own downloader.** Build a Zest-side resumable Hugging Face downloader for plain GGUF in Phase B, or keep each engine's?
6. **Provider trust for user-added folders.** Consent card on first use (suggested), or require a signed/known-good list?
