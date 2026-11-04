# Three-engine rework: Lemonade, Strata and Maya under one controller

Status: plan only, nothing built. Supersedes [multi-gpu-routing.md](archive/multi-gpu-routing.md). Changes a rule that [strata-integration.md](strata-integration.md) and [weak-points.md](weak-points.md) treat as fixed ("never touch what Shoggoth didn't start"). Sources for the routing ideas: [Switch](https://github.com/osolmaz/switch) (decision models, traces, replay) and [vllm-project/semantic-router](https://github.com/vllm-project/semantic-router) (signals, eligibility, cascade); both read, neither run.

## Decisions taken

1. Shoggoth can free, start and stop Lemonade, Strata and Maya whoever started them, by a per-engine authority setting. **Ask is the default for found engines**; Hands off stays a visible option.
2. **Swapping engines is acceptable.** A fast Lemonade model and a large Maya or Strata model need not coexist.
3. **Maya is a provider**, an expert-level engine.
4. **Per-GPU assignment**: each engine is set to see all GPUs or only chosen ones (section 4).
5. **Idle unload is not enough**: Shoggoth must be able to release, stop and start engines itself.
6. **Targets are named choices**; there is no fixed Fast/Deep pair.
7. **The expert mode is an editable flowchart** with the canvas from day one; the active flow is global; several flows can be saved and selected.
8. **A flow carries its own hardware layout.** Which engines run, on which cards, with what RAM budget, belongs to the flow, so the person can keep several: Lemonade + Strata, Lemonade + Maya, Maya alone on both GPUs, Strata alone. The choice is the user's; Shoggoth shows the consequences and does not decide for them (section 4).
9. **Shoggoth offers a restart with the environment variable** for a Lemonade that is running with the wrong devices (section 4).
10. **We don't yet know whether Maya will run on a GPU.** The design must let the person test that: a device setting that includes "none" (processor, RAM and disk only), and a measured footprint recorded for every start (section 4).
11. **RAM and disk are first-class** for Strata and Maya, which live on them. The RAM guard is the user's to set, and RAM, VRAM and disk are **reported from the engines' own metrics**, not guessed (section 4).
11a. **The measured footprint also feeds the Library's "fits" labels and the model recommender**, so they describe this machine.
11b. **Try it runs a short generation** to measure speed, and a user-made flow can be **tested for real with numbers reported** (section 6, "Testing a flow").
11c. **The Models page asks Strata and Maya what they have**: installed models, what each could download, and downloads in progress, including ones Shoggoth did not start (section 3a).
12. **All of it lives behind one Expert Mode switch** under Advanced (section 0).
13. **Simplify Shoggoth first** (section 6b, Milestone 0): finish the provider layer and remove the per-engine paths, one ownership implementation, one updater, one model list, and smaller cuts. No new features in that step, and the test suite passes unchanged.
14. **Shoggoth also runs in the terminal**: a headless server for other harnesses, and an attachable TUI that is beautiful and easy to use (section 6c).
14a. **The TUI is built with Textual** (and Rich), vendored; it has no chat screen. `shoggoth serve` takes an option to start the engines at boot or wait for the first request (section 6c).
15. **Build in this order**: manual switching, then rules-only flows, then one decider, then true split mode (section 9). Everything cut to keep this small is in "Later" (section 10), not deleted.

## What I found

**Maya** (read-only look at the running copy and its `serve/server.py`):
- Descends from Strata: OpenAI-style `/v1/*` (plus `/v1/messages`, `/v1/responses`), `--api-key`, `--idle-unload`, `/slots` (`is_processing`), `/health` (`model`, `max_context`, `version`, `api_key`, `unloaded`).
- **It frees memory without dying.** `POST /models/unload` returns 200, or **409 while a request runs**; `POST /models/load` loads again (about a minute). Can be disabled by `model_management: false`.
- **`--gpu`** takes nvidia-smi numbers, or several for a layer split ("0,2"). It sets `CUDA_DEVICE_ORDER=PCI_BUS_ID` itself, so its numbers match nvidia-smi.
- Process tree: `maya.py` (launcher) → `serve/server.py --parent-pipe` → `build/maya`. On this machine it holds about 9 GB on the 3080, 13 GB on the 4080 SUPER and about 50 of 62 GB of RAM. Port 8080, no API key, GLM 5.3 Flash, 131072 context, images accepted.
- **`GET /metrics` is rich.** I read one live snapshot: the model's footprint by tier (`engine.vram_gb` 14.0, `ram_gb` 46.2, `disk_gb` 22.4), how expert fetches split between VRAM, RAM and disk, a **per-GPU map** (`gpu_map`: card name, nvidia-smi index, free memory, weights / KV / pool by card, which layers sit on it), live and per-request speeds (decode and prefill tokens per second, reused prompt), machine RAM and disk read rate, and the model folder's free and total space. At that moment it was split across both cards (layers 0 to 26 on the 4080 SUPER, 26 to 45 on the 3080) and the machine's RAM was 64 of 67 GB in use. A plain `free` reading is a poor guide here (page cache and mapped files count), so the engine's own figures lead.

**Strata** has `--gpu LIST` and `--gpu-roles` (main / helper / off per card) on `strata launch`, and `strata launch --stop`. It also has `strata status --output json` (a read-only telemetry snapshot, `schema_version` 1, with `--url` for a running server) and `strata monitor`. It was not running when I looked, so I have not seen its fields.

**This code base:**
- Ownership gates everything: `GenericProvider.stop` ("Shoggoth didn't start it"), `providers/strata/provider.py` `stop` only for `strata.zest_server()`, `idle_tick` only for `owned_instance()`, `make_room` refuses an exclusive request against a found engine. `tests/test_ownership.py` pins this.
- A `service`-mode YAML provider is found and chatted with but never started.
- GPU choice for Lemonade is the global `llamacpp.device` setting (`lem.set_config`); there is no per-engine device assignment anywhere. `hardware.py` has each card's uuid, index, live use and which process holds its memory.
- One Lemonade is assumed (`engine._state`, `lem._t`).

## 0. Expert Mode

One switch, **Advanced > Expert mode**, off by default. Everything in this plan is part of it:

| Feature | Expert mode off | Expert mode on |
| --- | --- | --- |
| Authority over engines (section 1) | Today's behaviour: Shoggoth only touches what it started (the same as Hands off for every engine) | Ask / Allowed / Hands off per engine, Ask by default for found engines |
| Maya provider | Not offered | Detected, listed and controllable |
| Per-GPU device settings, RAM guard, measured footprint, Try it (section 4) | Not shown; engines start as today | Engine cards gain device chips, RAM reserve and guard, measured footprint |
| Flows, layouts, the canvas, shadow mode, `auto` (section 6) | Not shown; requests go to the model named | Flow picker and canvas under Advanced; `auto` appears in the model list |
| Cross-engine switching and the switch confirmation | The model picker still refuses a model another engine holds the memory for, as today | Switching releases the other engine, subject to its authority |

- **Turning it off is a clean return**: the active flow stops applying, device overrides are ignored (engines start with all GPUs next time), authority reads as Hands off, and `auto` leaves the model list. Saved flows and settings are kept, not deleted, for when it is turned on again.
- **Turning it on shows a short explanation** of what changes (Shoggoth may release or restart engines you started, with your consent) and nothing else changes until the person uses a feature.
- **Tested both ways**: the existing test suite must pass unchanged with Expert Mode off, and the new tests run with it on.
- The switch is a setting in the same store as the other Advanced options, so it also appears in the settings export and is readable by the app code through one function (`expert.on()`), which every gate in this plan calls.

## 1. Authority

| Setting (per engine, Advanced > Engines) | Meaning |
| --- | --- |
| **Ask** (default for found engines, when Expert mode is on) | The first time Shoggoth needs the memory it asks: "Maya is using both graphics cards. Free it?" Free / Not now / Always for Maya. |
| **Allowed** | Frees or restarts it by itself to make room. |
| **Hands off** | Today's behaviour; a switch explains why it can't. |

Always true: **never interrupt work** (an engine that reports it is answering, by Maya 409 or `/slots`, Strata's activity probe, or Lemonade's health, is waited for up to a limit, then asked about); an engine with no activity report stays on Ask; every switch says what was freed and how to undo it; the first switch per engine shows the cost (reload time).

## 2. Control interface

Add `release()` to the existing providers and let `stop()` act on found engines; `ensure()` already starts or loads.

| Engine | `release()` (keep the process) | `stop()` | `ensure()` |
| --- | --- | --- | --- |
| **Lemonade** | `POST /v1/unload` | managed process, or leave a service up | `/v1/load`, or start `lemond` |
| **Strata** | `strata launch --stop` (works on any instance it manages) | same | `strata launch --preset …` |
| **Maya** | `POST /models/unload` (409 if busy) | only if Shoggoth started it | `POST /models/load` when it is up but unloaded; else its launch command |

The router calls `release()` first. **A found process is never signalled**: all three engines have a way to free memory without it. If Maya's model management is off, Shoggoth says to turn it on or stop Maya yourself. Killing stays limited to processes Shoggoth started, as today.

Launch command for an engine that isn't running (Maya): a setting the person fills in (a script such as `run-maya-maya-s24.sh`, or a command), **prefilled as a suggestion from the running process's command line** when one is found. It is shown and approved once like an added engine and fingerprinted. Nothing is learned or run silently.

## 3. Maya provider

`providers/maya/` (YAML plus a small `provider.py`):
- Detect by `/health` having `model_label` / `version` and no `service: strata`, on ports 8080 to 8089 and the configured port; Strata's detection stays as is. Prefer a `/proc` match on `serve/server.py --engine maya` where available.
- `tier: large`, `memory.group: gpu`, `exclusive: true`; endpoint `http://127.0.0.1:{port}/v1`; models and `meta.n_ctx` from `/v1/models`; `capabilities.activity` on `/slots`; context fixed at start; vision from `architecture.input_modalities`.
- Not an installer: Maya installs itself (`setup.sh`).
- Offer Maya's `--idle-unload` as an optional extra only.
- Pin a tested version range and add a contract test on recorded `/health` and `/v1/models` output, as for Strata.

Strata and Lemonade providers: drop the "only if Shoggoth started it" gates, subject to authority.

## 3a. Models page: what each engine has

The Models page and Library currently read Strata's models by finding their files (`strata.installed_models()` via `strata models list --installed`) and Lemonade's through its own list. Maya's and Strata's own lists become first-class, because each engine manages models it keeps itself.

**What the engines offer** (read-only probes of the running Maya and the installed Strata):

| Question | Maya | Strata |
| --- | --- | --- |
| What is installed? | `GET /api/tags`: name, size in bytes, quantisation label, `maya.ok` / `missing` / `running` | `strata models list --installed --output json`: id, name, run config, `component_status`, `context_tokens` (3 on this machine: DeepSeek V4.1 Flash, and Qwen 3.8 Flash-Next IQ3_S and IQ3_XXS) |
| What could be downloaded? | `GET /api/catalog`: id, family (GLM, DeepSeek, MiniMax, Qwen), name, `installed`, `size_gb` (null when unknown) | Shoggoth's own catalog in `strata.py`, with `strata launch --plan --output json` for the size and warnings of one entry |
| What is downloading now? | `GET /api/pull/status` (`{"pull": null}` when idle) shows downloads started from Maya's own dashboard. A download started from the terminal (`maya.sh`, which runs `setup.py`) happens **before the server is up**, so this does not see it. | Downloads happen inside `strata launch` in a terminal, with **no server running yet**, so no API shows them. |
| Manage | `POST /api/pull` (with `detach`), `/api/pull/cancel`, `/api/delete`, `POST /models/load` with another installed model's name to swap; without an API key these run only from this computer | `strata launch` downloads, `strata models move` moves files |

**Downloads started from a terminal** (how you start both engines): the engines' HTTP APIs are not up yet, so Shoggoth sees the download from outside:
- **The process.** A scan of `/proc` for `strata launch …` (its arguments name the model and `--data-dir`) and for Maya's `maya.sh` / `maya.py` / `setup.py`.
- **The files.** Maya's resumable downloads write `<file>.part` beside the target (`setup.py`), in Maya's models folder (`Maya-data/models/<name>/` here, which `/metrics` reports as `storage.path` once the server is up, and which the saved launch recipe also holds). Strata's partial-file naming is not known; its folder is its `--data-dir`, or the default data folder, or `strata.models_root()`.
- **The number.** Bytes done are the size of the partial files; the total is the catalog's `download_gb` (Maya's manifest, or `strata launch --plan`). Speed is the growth since the last look. The page marks it as **estimated from files** ("Maya download started from a terminal: about 41 of 95 GB, 38 MB/s").
- **Control.** Shoggoth did not start it and does not signal it: the card says to press Ctrl+C in that terminal. A download Shoggoth starts itself (through `strata launch` or Maya's `/api/pull`) has a real progress figure and a Cancel button.
- **When it finishes**, the model appears in `installed()` (Maya may need its engine restarted to load a new model, and says so). A model downloaded some other way can be adopted with Maya's own `--import-model`.
- **Nothing starts a first-run installer.** `maya.sh` on a fresh checkout compiles the engine and asks questions in the terminal; Shoggoth never runs it. It offers to start Maya only when a finished configuration exists (here `maya-maya-s24.json` and the script `run-maya-maya-s24.sh` that Maya wrote), and uses that script as the launch recipe in section 3.

**Provider methods** (added to the interface in section 2): `installed()`, `catalog()`, `downloads()`, and, where the engine offers them, `download(id)`, `cancel_download()` and `delete(id)`. Each returns normalised records; a method an engine lacks returns "not available" and the page hides the control.

**On the page:**
- **One list of installed models across all engines**, each labelled with its engine, size on disk, context, whether it is loaded, and a measured-footprint line when there is one (section 4). Found engines count: this does not depend on who started them.
- **A Downloading strip** across engines: model, engine, progress and speed where the engine reports them, bytes done and total, and the free space where the files are going (Maya's `storage` in `/metrics`; Strata's models folder through `strata.models_root()` and `strata.disk_problem`). It shows downloads Shoggoth did not start, read-only.
- **Available to download**: each engine's catalog entries not yet installed, with size and a fit label from the footprint store or the estimate. Starting one is the person's action: it shows the size and the disk check first, and is refused with the reason when the files won't fit with room to spare (the same rule as Lemonade downloads, 5 GB; Strata keeps its 10 GB).
- **Cancelling and deleting** a download or model is offered where the engine supports it, with a confirmation naming the files and size. A download Shoggoth did not start is cancelled only after asking, whatever the engine's authority setting.
- **Maya keeps several installed models but serves one.** The others are listed as "installed, not loaded"; choosing one asks Maya to swap (`/models/load`), which restarts its engine (the catalog says `restarts: true`) and takes about a minute, so it is a switch with the usual confirmation. Maya names a model `maya-maya-s24:latest` in `/api/tags` but `glm-maya-s24` in the catalog and `glm-5.3-flash` in `/health`; the provider keeps a mapping and shows the friendliest name.
- **Expert mode**: Maya's models appear only with it on, as the provider does (section 0). Strata's installed models already show; its download strip and catalog extensions follow the same switch.

**Tests:** recorded `/api/tags`, `/api/catalog`, `/api/pull/status` (idle and, once seen, during a download) and `strata models list` output in `tests/fixtures/real`, with contract checks on the fields Shoggoth reads; fakes for the download and cancel paths.

## 4. Per-GPU assignment

Each engine has a **device setting**: *all GPUs* (default), a chosen set, or *none* (processor, RAM and disk only; Maya may turn out to need this, or to run well on a card, and the person finds out by trying). The engine sees only those cards. A flow can override the setting per target (below). This is what makes "Maya on the 4080, Lemonade on the 3080" possible without a ledger.

**How it is applied** (at start, or at the next restart for a running engine):

| Engine | Mechanism |
| --- | --- |
| **Maya** | `--gpu 0,1` (nvidia-smi numbers; it fixes PCI order itself) |
| **Strata** | `--gpu LIST`, and `--gpu-roles` (main / helper / off) where the person wants roles |
| **Lemonade** | `CUDA_VISIBLE_DEVICES` (with `CUDA_DEVICE_ORDER=PCI_BUS_ID`) in the environment of the `lemond`. If a running `lemond` has different devices, **Shoggoth offers to restart it with the variable** (subject to authority, and refused while it is answering). A `lemond` Shoggoth did not start and cannot restart (a system service, say) falls back to the `llamacpp.device` setting through `lem.set_config`, which applies to its next load, and the card says that this is the weaker option |
| **Anything else (YAML engines)** | A `{devices}` placeholder in `run.start`, plus the same environment variables |

Rules:
- **Identify cards by UUID where the engine accepts it** (`CUDA_VISIBLE_DEVICES` takes UUIDs), and show the name, index and memory in the UI. Indices follow llama.cpp's numbering from `hardware.list_devices`, not nvidia-smi's, as the architecture doc already requires. If the two disagree, the UI shows both.
- **AMD and Intel**: `HIP_VISIBLE_DEVICES` / `ROCR_VISIBLE_DEVICES` for AMD; others unsupported at first. Untested hardware, as everywhere in this project.
- **Check, don't trust.** After a start, compare the setting with what `nvidia-smi --query-compute-apps` shows for the engine's process (already read by `hardware.py`). A mismatch is shown plainly ("Maya is also using GPU 0").
- **Found engines** keep the devices they were started with; if that differs from the setting, the card says so and offers a restart, subject to authority.
- **Fit uses the assigned cards**, not the whole machine: `provbase.fit` currently uses `best_gpu_gb` and `vram_gb` for all cards; it takes the assigned set instead.
- **Conflict rule, replacing the group rule for GPU engines:** two engines conflict if their device sets overlap (an unset device setting means all). Two engines on disjoint cards can both stay loaded. This is a set intersection in `make_room`, not a ledger.
- **RAM and disk are not split by card**, and Strata and Maya live on them (Maya holds about 50 of 62 GB here). Each engine has a **RAM reserve** (GB) and, for Strata, a disk note; the figure is the measured one when there is one (below), else the user's, else an estimate from `need_gb`. A **RAM guard** setting, per flow, decides what happens when the resident engines plus the target would exceed free RAM:
  - **strict** (default): release the other engine first, and refuse with the reason if even that is not enough, so the machine is never swapped to death;
  - **warn**: say so and let the person go ahead, with the numbers shown;
  - **off**: no check.
  The guard is the user's to set; the defaults protect the person who never opens the setting.
- **Measured footprint.** Every start records what the engine and model took: VRAM on each card, RAM, disk, load time, and speed. The figures come from the **engine's own metrics** first (next subsection), cross-checked against `nvidia-smi` for VRAM. It is kept per engine, model, device set and context size (`footprints.json`, keyed like `tuning.json` by hardware fingerprint and engine version), shown on the engine card ("Maya on GPU 1: 11 GB VRAM, 38 GB RAM, 20 GB disk, loaded in 74 s, 12 tokens/s") and used by the fit and RAM checks in place of estimates. This is what lets the person try Maya on one card, on both, or on none, and see the result.
- **It also feeds the Library and the recommender.** `provbase.fit` and the recommendation code consult `footprints.json` before their formulas; a label that comes from a measurement says "measured on this computer", the others keep saying "estimated".
- **Try a layout.** A flow's layout can be applied with a *Try it* action that starts the engines as described, waits until ready, **runs a short fixed generation on each target** (a prompt of a few hundred tokens and about 64 new tokens, so prefill and decode speed are measured separately and repeated), shows the measured footprint, speeds and any mismatch, and leaves everything running or rolls back on request.

### Metrics from the engines

Each provider gets a `metrics()` method that returns one normalised record, read from the engine and never computed by Shoggoth where the engine knows:

```
{source, vram_by_gpu: [{index, name, used, parts?, layers?}], ram_gb, disk_gb, disk_read_mb_s,
 storage: {path, free, total}, decode_tok_s, prefill_tok_s, tiers?: {vram, ram, disk shares}, live: {state, queued}}
```

| Engine | Source |
| --- | --- |
| **Maya** | `GET /metrics` (`engine`, `tiers`, `gpu_map`, `hardware`, `requests`, `storage`) |
| **Strata** | `strata status --output json` against the server's URL (and `strata monitor` for a live view) |
| **Lemonade** | `/v1/health` and `/v1/models` for what is loaded, `nvidia-smi` for VRAM |

- **Disk use is reported on the engine card**: how much of the model lives on disk, how fast the disk is being read, and the free space where the model files are, taken from the figures above. A guard (refuse when the disk is nearly full or slow) only if the measurements show a need.
- **The per-card breakdown is the check for device assignment**: Maya's `gpu_map` says which cards it really uses and which layers sit on each, better than inferring from process memory.
- **Numbering**: Maya's own device numbers are not nvidia-smi's (its `dev 0` is nvidia-smi index 1 here); read the `index` field and show names.
- **Fixtures and contract tests**: record real `/metrics` and `strata status` output into `tests/fixtures/real`, as for Strata's other output, and check the fields Shoggoth reads. A missing field degrades to "not reported", never an error.
- **UI:** each engine card gets a row of GPU chips (name, memory, live use) to toggle; a preview line says what it will free and what can stay loaded beside it.

## 5. Swap mode, safely

Two engines on disjoint cards (and enough RAM) coexist; everything else swaps. Before anything is released, the target's memory need is compared with what will be free afterwards, so a failed switch never leaves nothing loaded. The order is: release, **verify the memory came back** (poll `nvidia-smi` and `free`), load, verify ready; if the load fails, offer to reload what was released.

## 6. Routing flow (the expert mode)

Data, not code: a flow is one YAML file, edited on a **canvas**, validated before saving. With no active flow nothing changes: requests go to the model named.

### Targets and the schema

```yaml
version: 1
targets:                      # the layout: which engines run, on which cards
  quick: {engine: lemonade, model: <id>, can: [tools], devices: [0]}
  glm:   {engine: maya, model: glm-5.3-flash, can: [tools, vision], devices: [1], ram_gb: 40}
  # devices: all | none | [indices]; unset means the engine's own setting
flow:
  start: n_compact
  nodes:
    n_compact:
      type: match
      when: {kind: [compaction, title]}
      yes: quick
      no: n_tools
    n_tools:
      type: match
      when: {any: [{tool_results_in_turn: {gte: 3}}, {has_code: true}]}
      yes: glm
      no: quick
  default: glm
policy:
  swap: ask                 # ask | never; "allow" later
  ram_guard: strict         # strict | warn | off
```

- **Nodes:** `match` (a condition with yes and no edges) and targets as terminals. A flow is a tree; `default` catches anything that falls off.
- **Conditions** (closed list, no code): `kind` (compaction, title, chat, embedding: from fingerprints of Pi's real requests), `has_tools`, `tool_results_in_turn`, `has_code` (fences, paths, diffs, shell), `has_images`, `question_count`, `prompt_tokens`, `max_tokens`, `requested_model` (glob), `client` (page, Pi, gateway), `loaded` (is target X resident now), and `all` / `any` / `not`.
- **Targets** carry `can` (tools, vision) and a context limit. **Eligibility comes first**: a target that cannot handle the request (images, tools, prompt too long) is removed before any rule applies.
- **Validator:** a `default`; every path ends at a target; every target resolves (or warns "not installed"); no unreachable nodes. Because a flow is a tree there are no cycles.

### Behaviour in v1

- The flow runs in `gateway._route` before `router.resolve`. Naming a concrete model bypasses it, as today.
- **`auto` routes among targets that are loaded.** Moving to another engine is never silent: the request is answered by the resident target and a card offers "Switch to Maya, about a minute?" (`swap: ask`), or the flow's `swap: never` keeps it where it is. Reason: a swap takes about a minute and the gateway has no progress channel like the chat page's loading events, and Pi may time out.
- **Tool chains stay put**: the flow is evaluated at the start of a user turn; later requests in that turn follow it.
- Every request records its **path** (`n_compact:no → n_tools:no → quick`) on the Server page and in response headers (`X-Shoggoth-Flow`, `X-Shoggoth-Path`, `X-Shoggoth-Target`).
- **Shadow mode**: a flow can run log-only for a while, recording what it would have done next to what actually ran. That costs nothing and gives labelled data for any later decider.

### Flows as hardware layouts

A flow is both a routing graph and a **layout**. Typical saved flows, all the person's choice:

| Flow | Layout |
| --- | --- |
| Lemonade + Strata | `quick` (Lemonade) on one card, `big` (Strata) on the other or on both |
| Lemonade + Maya | `quick` on one card, `glm` (Maya) on the other, or on none |
| Maya alone | one target, both GPUs, no nodes (`default: glm`) |
| Strata alone | one target, both GPUs, no nodes |

- **Activating a flow applies its layout.** Shoggoth first shows a preview against what is running now: "Maya: restart on GPU 1 only. Lemonade: restart on GPU 0 only. Strata: release." Each line is subject to that engine's authority (Ask by default), the overlap rule and the RAM guard, and nothing happens without a yes.
- **Switching flows** between layouts is therefore a visible, confirmed operation, not something `auto` does mid-request.
- A flow whose layout does not fit (overlapping cards, RAM) is saved with warnings, not rejected: the person may know something Shoggoth doesn't.
- The device setting on an engine card is the default used when a flow leaves `devices` unset or no flow is active.

### Testing a flow

A flow can be tested before it is trusted, in three levels:

1. **Explain** (instant, no model): one request in, the path and target out.
2. **Dry run**: a set of requests through the flow with no model calls, reporting how they would be distributed (per target, per node), how many cross-engine switches the active layout would need, and which requests fall to `default`.
3. **Live test**: the set is sent for real through the flow on the real layout, as a job like a tune (the existing coordinator in `tuning.py`: needs consent, shows an estimate, blocks chats while it runs, can be cancelled, holds the cards through `router.holds`). It reports numbers:
   - per target: requests, time to first token (median and worst), prefill and decode tokens per second, tokens produced;
   - per switch: which engines were released and loaded, and how long each took;
   - memory: peak VRAM per card, RAM and disk read, from the engines' metrics;
   - totals and a **baseline**: the same set sent only to the biggest target, so the person sees what the flow saved or cost in time;
   - the routing itself: counts per node, and any request that fell to `default` or hit a `can` / context limit.
   It does not score answer quality. Shadow mode (what the flow would have done beside what actually ran) and the person's own judgement stay in charge of that, and the report says so.

**The request set** can be built from recorded requests on the Server page (the person ticks the ones to include), from a built-in sample set (a long compaction-style prompt, a short chat turn, a code edit, a multi-step tool chain, an image question) so a first test needs nothing recorded, or from a file. The report is saved with the flow version, the layout, the hardware fingerprint and the engine versions, and the flow shows a chip such as "Tested: 24 requests, 3 switches, median first token 1.8 s". A change to the flow, layout or an engine version marks it stale.

### Canvas and saved flows

- Drag-and-drop canvas in `index.html` (vanilla SVG and pointer events, no dependency): palette, edges by dragging between ports, side panel per node, pan and zoom, keyboard operable, every edge also editable from the panel.
- YAML **import and export** (read-only preview), not a live two-way editor.
- **Saved flows:** any number, one active globally, a picker above the canvas; duplicate, rename, import, export. Templates: "Lemonade + Strata", "Lemonade + Maya", "Maya alone", "Strata alone" (layouts with a sensible routing graph), plus "Manual only".
- **Explain:** paste a request or pick a recorded one; the path and target are highlighted. This is also the validation harness.

## 6b. Simplifying Shoggoth first (Milestone 0)

Goal: a common interface and a useful local agent, on a code base small enough to add the rest to. The numbers below are from reading the tree, not tracing every call: `server.py` has 10 `/ui/strata/*` routes, `/ui/engine/*`, `/ui/backend*` and `/ui/tuning/*` routes of its own, about 24 direct Lemonade calls and 44 Strata mentions; `index.html` is one file of 6,565 lines with about 75 Strata mentions; `strata.py` is 1,128 lines under a 149-line provider wrapper. The provider layer exists (`registry`, `provbase`, `router`), but the page and server still special-case engines beside it.

**Rule for the whole step: no new behaviour.** Every change is a move, a merge or a deletion. With Expert Mode off, the existing Python tests and `tests/ui/smoke.js` pass unchanged after each step, and each step is its own commit.

| # | Simplification | What changes | Done when |
| --- | --- | --- | --- |
| S1 | **Finish the provider layer, then delete the per-engine paths** | One route family, `/ui/providers/<id>/<action>` (status, start, stop, install, update, models, tune, connect), replaces `/ui/strata/*`, `/ui/engine/*`, `/ui/backend*` and the Lemonade `/api/*` passthrough the page uses. The Engines page is drawn from provider status for every engine. `strata.py` and `engine.py` shrink to engine-specific commands behind the provider; `server.py` and `index.html` stop naming engines outside the Engines page text and the provider folders. Do it one engine at a time, Strata first. | A search for `strata` and `lem.` in `server.py`, `agent.py`, `gateway.py` and the page script finds only labels and the provider calls |
| S2 | **One implementation of "what did I start, is it still mine"** | `engine.py` (pidfile, adopt, `_is_our_lemond`), `strata.py` (`owned`, `zest_server`) and `provbase` (`_owned`, `_pid`, `_t`, `started`) each keep this today. It lives once, in `GenericProvider`, with engine hooks. The authority model (section 1) is built on it. | One module owns started-by-Shoggoth state; `tests/test_ownership.py` still passes before it is rewritten for authority |
| S3 | **One updater** | The four updaters (`piup.py`, 309 lines; `zestup.py`, 250; `strata.update_*`; the Lemonade provider's update) share one helper (check a release, download, verify the checksum, switch the active version, keep the previous one). Components are Shoggoth, Pi and each engine, each with `check / apply / status`; `/ui/update/all` loops over them. | `piup.py` and `zestup.py` contain only what is specific to Pi and to Shoggoth |
| S4 | **One model list** | A single model record; `router.chat_models()` is the only source for the chat pickers (`/ui/picker`, `/ui/cowork/models`); the Library (`library.py`) is the only place that reads files; recommendations are data only (`recommendations/*.yaml`), including the `PICKS` table now written in `index.html`. | The page has no model ids in its source |
| S5 | **Freeze the Lemonade tuner** | `lemtune.py` (350 lines), its routes and its page have never run for real. No new work on it; it moves behind Expert Mode and is deleted if the Phase 0 spikes do not show a gain. `tuning.py` stays as the general "exclusive job" coordinator for Strata's tuner and for the live flow test (section 6). | Nothing outside Expert Mode shows it |
| S6 | **Library views for Lemonade only** | The symlink views and shared library root exist because Lemonade does not check architectures. Maya and Strata keep and list their own models (section 3a) and never use views. Importing from LM Studio, Ollama and Hugging Face stays. `modelscan.py` (253 lines) keeps only what Lemonade needs. | Maya and Strata add nothing to `modelscan.py` or the views |
| S7 | **Engine knobs behind Expert Mode** | llama.cpp argument editing (`llamaargs.py`, `/ui/args/*`), per-model tuning, device settings and the Engines "advanced" panels appear only with Expert Mode on. The default interface shows models, chat, folders, memory and speed. | With Expert Mode off, no engine knob is visible |
| S8 | **Split `agent.py` (1,504 lines) into three modules** | Pi finding and configuration (`pi_config`: lines 83 to 350), the task list and manager, and jobs. A pure move; `agent.py` keeps the public names. | No file in the agent area is over about 600 lines. **Not a pure move:** `tests/test_job_and_pictures.py` replaces `agent.session`, `agent.get_task`, `agent.ensure_loaded` and `agent._update_meta` directly, and `tests/test_chat_loading.py` patches `agent._sessions` and `agent.stop`. Moved code would no longer see those replacements, so those tests change in the same commit (the only allowed exception to "tests pass unchanged"), or the split waits until S1 and S2 have reduced what `agent.py` holds |
| S9 | **Skills: bundled sets stay; installing from a URL and the search settings and test move behind Expert Mode** | Less surface for a local agent to start with; the bundled Superpowers subset and the Shoggoth skills are the useful part. | Off by default, still reachable |
| S10 | **Split `index.html` into per-page scripts before the canvas lands** | The shell, styles and one script file per page, served by the same server from `shoggoth/ui/`, classic scripts, no framework, no build step. The jsdom smoke test loads the shell and the scripts. The canvas editor (section 6) is a new file instead of 1,000+ more lines in the same one. Update ARCHITECTURE.md ("the entire front end, in one file"). | Largest page file under about 1,500 lines |
| S11 | **Archive the plans that are built or superseded** | Move finished plans to `docs/plans/archive/`; keep `weak-points.md` current. The untracked `model-trial-*` files are not touched. | `docs/plans` holds only live plans. **Started:** `multi-gpu-routing.md` and `shoggoth-cartridges.md` are in `docs/plans/archive/`; the rest (`beta-notes-17-19`, `step6-picks`, `unslop-audit`, `unified-interface`) wait for the owner to say whether they are finished |

**Order:** S2 and S1 (Strata first) before Milestone 1, since the authority model and the Maya provider are built on them; S4 with S1; S3 before any new update UI; S5, S7 and S9 are small gates that can go any time; S8 any time; **S10 before Milestone 2** (the canvas); S11 whenever.

**Keep as they are:** the gateway and the request recorder, the permission gate (`pi/zest-gate.js`), work copies, the `provbase` / `registry` design, and `hardware.py` for machine-level telemetry (per-engine figures come from each engine's `metrics()`, section 4).

**What this step buys the rest of the plan:** one place for engine ownership (authority), one provider route family to add Maya and the layouts to, one model list for the Models page work in 3a, and a page that can take a canvas.

## 6c. Terminal mode: a headless server and a TUI

Goal: Shoggoth can run with no browser, as the local model server other harnesses (Pi, Claude Code-style tools, scripts, editors) point at, and a terminal interface lets the person see and run all of it, over SSH or in tmux, without opening the page.

**What exists.** `--no-browser`, `--host` and `--port` already start the server without a tab, and the gateway (default :13308, API key, loopback unless LAN access is on) is the endpoint other harnesses use. `--app` adds a watchdog that quits shortly after the tab closes, which a server must not have. The page and any script reach `/ui/*` with the secret in `~/.config/zest/ui-token` as `Authorization: Bearer`. The AppImage ships its own Python 3.12.15 with `-I` and no site-packages, so the TUI libraries are vendored (see Technology below).

### Headless serving

- **`shoggoth serve`**: no browser, no idle quit, prints a short banner (version, UI address, gateway address and port, whether LAN access is on, active flow, engines found) and keeps running; logs go to stdout (plain lines, or JSON lines with `--log json`).
- **Start now or wait.** A setting (`serve.engines`, with flags `--engines wait|start`, default **wait**) decides what happens when the server comes up:
  - **wait**: nothing is loaded until the first request names a model or `auto`; that request is held while the model loads (`shoggoth status` and the TUI show the step), so a client with a short timeout may give up on a cold start.
  - **start**: Shoggoth loads what is named at boot: the active flow's layout (every target, small models first, as #18 asks), or `--start <flow>` / `--start <model> ...`. Progress is logged and shown in the TUI.
  - **Boot respects authority.** Nobody is there to answer an Ask card at boot, so `start` never releases an engine Shoggoth did not start unless that engine is set to Allowed, and never exceeds the RAM guard. What it cannot do it logs ("Maya is running something you started; staying out of the way") and leaves as a pending Ask that the page or TUI shows when attached; the request path still works as in wait mode.
  - **Keep loaded.** A target can be marked `keep_loaded`, which exempts it from the idle stop (default 20 minutes for engines Shoggoth started), so a model started at boot is not dropped before anyone asks.
- **`shoggoth connect`**: prints exactly what another harness needs: base URL, a masked key with a copy command, the model names (including `auto` and published flows), and ready-to-paste settings for an OpenAI-style client, Pi and common harness environment variables, and a `curl` line that sends one test request.
- **`shoggoth status [--json]`**: a one-shot read of engines, loaded models, GPUs, active flow and requests in flight, for scripts and health checks (modelled on `strata status`). Exit code non-zero when the gateway is down.
- **Run it as a service**: a documented `systemd --user` unit and the AppImage invocation; one running Shoggoth per user (the existing port and token handling already detects a second launch). Stopping it releases what it started, as today (`router.stop_started`).
- **LAN access** stays the existing gateway setting, off by default, with the key required; the TUI and CLI say plainly when it is on. No TLS in scope; the guidance is a tunnel or a reverse proxy.

### The TUI: one client of the same API

- **`shoggoth tui`** attaches to the running Shoggoth over loopback with the UI secret, or starts one in the same process if none is running. It is a *client* of the same JSON and event-stream API the page uses (the provider routes from S1, `/ui/activity`, `/ui/server`, the logs and the streams), so it needs no engine logic of its own and the page and terminal never disagree. Remote use is `ssh host`, then `shoggoth tui`; the secret never crosses the network.
- A small shared client module (`shoggoth/client.py`, standard-library `http.client`) serves the TUI, `status` and `connect`.
- **Core screens are always available**; screens for Expert Mode features appear only when it is on (section 0).

### Technology: Textual

Decided: the TUI is built with [Textual](https://textual.textualize.io) on Rich.
- **Vendored, pinned, like PyYAML.** The AppImage runs Python with `-I` and no site-packages, so the libraries live in `shoggoth/_vendor/` and are added to the path as `yaml` is. From PyPI today: `textual` 8.2.8 (MIT, Python 3.9+), `rich` 15.0.0 (MIT), `markdown-it-py`, `mdurl`, `mdit-py-plugins`, `linkify-it-py` and `uc-micro-py`, `platformdirs`, `typing-extensions`, and `Pygments` (the largest, 1.2 MB as a wheel). They are all pure Python; the wheels total about 2.6 MB packed, more unpacked. Their licences are MIT, BSD-2-Clause and PSF, and each licence file ships in the bundle.
- **Pin and verify.** The versions and SHA-256 sums are listed in `packaging/fetch-vendor.sh`, which already fetches and checks vendored assets. Updating Textual is a deliberate, tested change, not a drift. Pygments lexers and Textual's optional tree-sitter extras are left out unless a screen needs syntax colouring, to keep the bundle small.
- **Cost accepted:** this is the first large third-party Python code in the project, against the "standard library only" line in ARCHITECTURE.md (PyYAML is the one precedent). The document is updated to say the exception: standard library plus the pinned vendored set, and the server itself imports none of it unless the TUI starts.
- **To confirm early:** that Textual starts under the bundled Python 3.12.15 with `-I`, and over SSH and tmux; and its import time, so `shoggoth serve` is unaffected (the TUI is imported only by `shoggoth tui`).

### Look and feel

Beautiful here means consistent, calm and legible, not decorated.
- **Theme.** One Textual theme (TCSS stylesheet) of semantic colours (accent, ok, warn, error, dim, selection), with a light and a dark variant; truecolor where the terminal supports it, falling back to 256 and 16 colours and monochrome through Rich; `NO_COLOR` honoured. Meaning never depends on colour alone: an icon or word also says ok, warn or error.
- **Glyphs.** Rounded borders, block bars for VRAM, RAM and disk (`ProgressBar`), sparklines for tokens per second (`Sparkline`), with an ASCII fallback when the locale is not UTF-8.
- **Layout.** Responsive: a single column at 80x24, two or three panes from 120x40; resize-safe; truncated cells end with an ellipsis and open in full on Enter.
- **Keys.** Everything works from the keyboard, with the mouse as an extra. Number keys switch screens, arrows or `j` / `k` move, Enter opens, Esc backs out, `?` shows the keys for the current screen, `/` filters, and Textual's command palette (Ctrl+P or `:`) takes commands ("load glm", "switch flow", "free maya"). The footer always shows the keys that work now.
- **Safe actions.** Anything that frees, restarts or downloads is a modal naming what happens and the cost (the same preview text as the page); the authority "Ask" cards from section 1 arrive as modals on every attached front end and are answered once.
- **Never blocks.** Slow calls run in workers; the screen shows a spinner and the step ("Loading GLM: 1 of 3"), never a frozen view.
- **Quiet.** No animation for its own sake; a calm status line; errors in plain words with the next step.

### Screens

| Screen | What it shows and does | Needs Expert Mode |
| --- | --- | --- |
| **Dashboard** | Engines (state, model, who started it, devices, VRAM / RAM / disk, tokens per second), a bar per GPU with the processes using it, system RAM and disk, the serving address and key hint, the active flow, requests in flight | no (flow and authority parts yes) |
| **Models** | Installed across engines, Downloading strip (including terminal-started, "estimated from files"), Available with fit labels; load, unload, download with the disk check, delete (section 3a) | Maya's rows yes |
| **Requests** | Live table of gateway and page requests: client, model asked, target chosen, path, first-token time, tokens per second, status; detail pane for one; filter by client or model | no |
| **Engines** | Per-engine detail and actions: start, release, stop, restart with devices, authority, device chips, measured footprint, tail of its log | actions yes |
| **Flows** | Active flow picker; the flow drawn as a tree with box characters (read-only); Explain a pasted request; run a dry or live test and read the report; edit opens the YAML in `$EDITOR`, validates, and saves (the canvas stays in the page) | yes |
| **Logs** | Shoggoth's own log and each engine's log, followed, with search | no |
| **Settings** | Gateway (address, port, LAN, key: reveal and copy by OSC 52), Expert Mode switch, library folders | partly |

Agent sessions, jobs and skills stay in the page; the TUI is for serving and operating, not for running the agent.

### Testing

- Textual's own test driver (`App.run_test()` with a pilot that presses keys and clicks) runs screens headless inside `unittest` (through `asyncio.run`), against a fake provider server. Tests assert on the widgets and on the rendered text of the screen at 80x24 and 120x40, with and without colour.
- One pty test starts `shoggoth tui` in a real terminal type, checks it paints, switches screens, answers a modal and quits on `q`.
- `status` and `connect` are plain functions with JSON output, tested like the other CLI helpers.
- The server's own tests do not import Textual, so they run without the vendored set.

## 7. Phases

**Milestone 0: simplify first** (section 6b), S1 and S2 before Milestone 1, S10 before Milestone 2, the rest interleaved. Phase 0 can run in parallel.

**Phase 0: spikes (about 2 days; needs the two-GPU machine).** These are experiments the product itself should make easy afterwards (Try it, measured footprint); do them by hand first.
- Maya `POST /models/unload` / `load`: do VRAM and RAM come back, and how long does the load take?
- Does Maya run at all on one card (`--gpu 1`), on both, and with no GPU? What are the RAM, speed and load time in each case? Likewise Strata on one card and on both. Nothing in the design assumes the answer.
- Lemonade pinned to the other card with `CUDA_VISIBLE_DEVICES`, restarted with the variable.
- Capture Pi's real compaction and title requests from the Server page recorder and write the `kind` fingerprints.
- **Downloads from the terminal**: start a small `strata launch` download and a Maya download and record what shows from outside: the process arguments, the partial-file names and their growth (Maya writes `.part`; Strata's is unknown), and whether `strata models list` reports a status for a half-downloaded model. When I looked, no Strata process was running and all three installed models were `present`, so I saw no download. Also: does Maya have a flag to start the server without opening a browser tab (the running copy was started with `--open`)?

**Milestone 1: manual switching.**
- Authority setting (Ask / Allowed / Hands off); `release()`; drop the ownership gates; rewrite `make_room`, `idle_tick`, `stop_started` to use authority; rewrite `tests/test_ownership.py` (keep tests that Hands off touches nothing and that a busy engine is never interrupted).
- Maya provider with a fake Maya in the tests (a copy of `tests/fixtures/openai_server.py` whose `/models/unload` returns 409 while busy).
- Read-only Models page support (section 3a): `installed()`, `catalog()` and `downloads()` for Maya and Strata (downloads seen from outside for terminal-started ones), the Downloading strip and the Available list, with fixtures; then `download`, `cancel_download` and `delete` for Maya.
- Engine `metrics()` for Maya, Strata and Lemonade with recorded fixtures; `footprints.json`; the Library fit and recommender reading it; disk reported on the engine cards.
- Per-GPU device setting including *none* (section 4) with the overlap rule, the RAM guard (strict / warn / off), the fit using assigned cards, the verify-after-start comparison, the restart-with-variable offer for Lemonade, and the measured-footprint record with the *Try it* action.
- Swap steps (section 5) with the existing progress card, and the switch confirmation.

**Milestone 2: rules-only flows, layouts and flow testing.** Explain, dry run and the live test job with its report and baseline (section 6, "Testing a flow"). Targets with `devices` and `ram_gb`, layout preview and apply, schema and validator, the evaluator in `gateway._route`, path logging and headers, shadow mode, the canvas with side panel, saved flows and templates, Explain. Extend `tests/ui/smoke.js` for the new page. Tests on recorded request bodies and a golden-file test for the schema.

**Milestone 3: one decider, only if shadow data shows the rules leave a gap.** Choose one kind from measured accuracy on labelled requests (see "Later"); add `classify` nodes with a confidence cutoff and one `otherwise` edge; add Replay. A decider hosted on the small chat model is only available while that model is loaded, so it can decide whether to escalate from `quick`, not whether to leave `glm`.

**Milestone 4: automatic swaps and true split mode, optional.** `swap: allow` for engines set to Allowed, with a minimum stay between switches; per-GPU memory claims beyond the overlap rule; a Lemonade instance per card.

**Milestone T: terminal mode.** After Milestone 0 (it needs the provider routes from S1): `shoggoth serve` with the start-or-wait option, `connect`, `status` and the shared client first; then the TUI core (Dashboard, Models, Requests, Logs, Settings) on the vendored Textual (after the early checks under Technology). The Engines, authority and Flows screens land with Milestones 1 and 2 as those features do. Milestone T can run beside Milestone 1.

**Expert Mode gate, with Milestone 1:** the switch, `expert.on()`, the gates in the table above, and the tests with it off and on. Every later feature lands behind it from the start.

**Docs, with Milestones 0 and 1:** ARCHITECTURE.md (routes, the page split, ownership), ENGINES.md, weak-points.md, strata-integration.md principle 1.

## 8. Risks

- **A TUI that drifts from the page.** If it grows its own logic it will disagree with the page. It stays a client of the same API, and an API change updates both or neither.
- **Terminal differences.** Colour, Unicode and mouse support vary; the theme falls back, and the pty test covers one common terminal type only.
- **A refactor that changes behaviour.** Milestone 0 is large. One engine and one step at a time, one commit each, the suite and the page smoke test green between steps, and nothing new mixed in.
- **Expert Mode leaking.** A feature that works without the switch breaks the promise that nothing changes for ordinary users. The off-path test suite and one gate function are the defence.
- **Losing someone's work.** The biggest. Busy checks, release-before-stop, Ask by default and "a found process is never signalled" are the defences.
- **Metrics drift.** Maya and Strata change often and `/metrics` is not a contract; fixtures, tolerant parsing and "not reported" are the defence.
- **Memory not coming back** after `unload`: verify, then fall back to the engine's `stop` where Shoggoth owns it, otherwise ask the person.
- **Device numbering.** CUDA, llama.cpp and nvidia-smi can order cards differently. UUIDs and showing both numbers are the defence; the AMD and Intel paths are untested.
- **Wrong routing is silent.** A rule that sends real work to the small model gives a worse answer without an error. Defaults go to the big target, paths are logged, shadow mode comes first.
- **Port and identity collisions** among Maya, Strata and a bare `llama-server` on 8080–8099. Prefer `/proc` identification to a port guess.
- **Maya moves fast** (1.1.50 here): pin a version range with a contract test.
- Linux x86_64 and NVIDIA are the only platform looked at.

## 9. Open questions

- Which tool-chain length counts as "many"? (Start at 3 tool results in a turn; it is a field of the condition.)
- Which Pi request text identifies compaction and titles? Settled by the Phase 0 capture.
- Is `wait` the right default for `serve.engines`, or `start` when the active flow has `keep_loaded` targets?
- Which Textual features are worth using beyond the basics (the command palette, themes switching at run time, a log widget for engine logs)?
- Is the Lemonade tuner (S5) deleted outright if the spikes show no gain, or kept behind Expert Mode?
- Is serving the page as several static scripts (S10) acceptable for the AppImage and the packaging scripts, or must it stay one file in the build output (concatenated at build time)?
- What does `strata status --output json` contain on a running server? (Not seen yet; Strata's `metrics()` waits on a recorded sample.)
- How many requests make a useful built-in sample set, and how long should a live test be allowed to run by default?
- Should the live test also record the person's own rating of each answer, to get a quality number beside the speed numbers? (Out of scope for the first version.)

## 10. Later (cut from the first build, kept here on purpose)

- **Deciders for the ambiguous middle**, to be chosen by data: llama.cpp `/v1/systemone` decision models (Switch's docs list Julia-1, Laya, Kev-4B and lev, all Apache-2.0), vllm-sr models (Decision 2.0 Kai 0.6B, Vega 27B and d3 are Apache-2.0 on Hugging Face; Vela 2.0 carries extra terms and Gemma licence files to read first), the chat-model next-token-probability method, and `examples` (a request scored against labelled easy and hard examples with an embedding model, from semantic-router's complexity signal). Their runtime `vllm-srun` needs PyTorch and would be an external endpoint, not bundled. None of these have been run here; the endpoint and model names come from their docs.
- A CPU decision-model **sidecar engine**, a decider cascade (tiny, then bigger, then the big target), confidence cutoffs, and three-valued logic with `on_unknown` once deciders exist.
- **Label collection** ("should have gone to X" marking) and **Replay of decisions for a decider** (re-evaluating a draft flow on recorded decider answers without calling a model). The flow tests in section 6 do not need them.
- Version history for flows, live two-way text editing, per-key flow selection by header, flows published as extra model names (`auto`, `auto-code`), aliases for clients that pick their own model names, a hardware panel beside the canvas, latency-aware choice among equivalent targets.
- Learning a launch recipe from `/proc` and signalling found processes.
- A flow canvas in the terminal (the page's canvas stays the editor), agent sessions and jobs in the TUI, and remote attach over a network with TLS.
- A small model that drives the desktop through a pick-one-action loop (the last line of #18); a separate project.
