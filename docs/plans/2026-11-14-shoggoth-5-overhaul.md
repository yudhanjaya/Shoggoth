# Shoggoth 5: the overhaul plan

**Motto:** *Heavy on the cosmos. Light on your machine.*

This plan merges the three open issues (#37, #51, #52) with the new feature list from 14 November 2026. Each item says what changes, which files it touches, and what "done" means. It is ordered so that each phase ships as its own PR and leaves `main` working.

Issue items are tagged **[#51]**, **[#52]**, **[#37]**. New items are tagged **[new]**.

---

## Goals for 5.0

- **Runs on llama.cpp, set up for you.** No Lemonade. Shoggoth downloads the right llama.cpp build for your graphics card and keeps it updated.
- **A curated model list that always shows.** Hand-picked unsloth and bartowski quants for every machine, with an honest fit badge, plus Hugging Face search.
- **An honest memory estimate,** within 10% of what llama.cpp actually uses.
- **Hooks up what you already run.** Finds 20+ local engines on any port (Ollama, vLLM, SGLang, LM Studio, Strata, Maya and the rest) and connects in one click. Anything it misses, you set up by hand and teach it to recognise.
- **Hosted services when you want them:** OpenRouter, Nous Portal, OpenCode, Command Code, Google Gemini, clearly marked as leaving your computer.
- **Switch models anywhere,** from the chat bar or the Models page, across every source.
- **Shoggoths share across the network.** One machine serves; laptops find it, pair with a code, and use its models over an encrypted link.
- **Pi grows.** Curated Pi packages (documents, spreadsheets, subagents, Ponytail), common file types in chat, and skills the model writes itself with your approval.
- **Speech:** talk to it (whisper.cpp), and optionally have it read aloud (Bark).
- **A calmer UI:** a vertical tube on wide screens, real model names, less copy.
- **Clean packaging:** one name everywhere, AppImage update info, catalog warnings gone.
- **A home on the web:** a site with the motto, *Heavy on the cosmos. Light on your machine.*

---

## 0. Decisions to settle before coding

These change the shape of the work. Recommendations are given; the plan below assumes them.

| # | Question | Recommendation |
| --- | --- | --- |
| D1 | **"Bark for speech to text."** Suno's Bark is a *text-to-speech* model (it makes audio from text). It can't transcribe. | Speech-to-text with **whisper.cpp** (`whisper-server`), which has the same shape as llama.cpp: one binary, GGUF-style models, an HTTP server. Add Bark later as an optional *read aloud* voice through **bark.cpp** (GGML port), because the original Bark needs PyTorch and about 5 GB. If you meant TTS all along, swap the order of 7a and 7b. |
| D2 | **"Stata"** in "select your own inference server (Stata, Maya, others)". | Read as **Strata**. Phase 4 also covers PolyStrata, Maya, Ninfer and the others. |
| D3 | What the AppImage bundles once Lemonade is gone. | Bundle the **CPU build** of llama.cpp (about 30 MB) so the app works offline on first run. Download the GPU build (CUDA 12.8 / CUDA 13.5 / Vulkan / ROCm 10.0) on the Getting ready screen. |
| D4 | Where discovery traffic goes in client/server mode. | A plain **UDP beacon** on the local network (stdlib only, no Avahi needed). Optionally also publish `_shoggoth._tcp` through `avahi-publish` when it exists. |
| D5 | Pi packages (npm) run code. | **Curated list only**, pinned by version and sha512, installed the way `piup.py` installs Pi. No free-form `pi install` from the UI in 5.0. |
| D6 | Version and name. | **Shoggoth 5.0**. Drop the `Zest-*` release copy in this release (see Phase 9). |

---

## Phase 1. Replace Lemonade with llama.cpp **[#51] [new]**

The biggest change, so it goes first. Everything else (catalog, estimator, tuning) gets simpler once Shoggoth runs `llama-server` itself.

### 1.1 New engine module: `shoggoth/llamacpp.py`

Replaces `shoggoth/engine.py` (551 lines) and `shoggoth/lem.py` (92 lines). It keeps the same job (start, stop, update, report state) but drives `llama-server` directly.

- **Release discovery.** Query `GET /repos/ggml-org/llama.cpp/releases?per_page=10` and take the newest tag matching `^b\d+$`. **Don't use `/releases/latest`.** As of 13 Nov 2026 it returns `v1.0.0`, a tag holding only `nightly-tag.txt`, so a naive check finds nothing to download.
- **Asset choice.** Map the backend to an asset name:
  - `cpu` → `llama-<tag>-bin-ubuntu-x64.tar.gz`
  - `vulkan` → `llama-<tag>-bin-ubuntu-vulkan-x64.tar.gz`
  - `cuda12` → `llama-<tag>-bin-ubuntu-cuda-12.8-x64.tar.gz` plus `cudart-llama-<tag>-bin-ubuntu-cuda-12.8-x64.tar.gz`
  - `cuda13` → the `13.5` pair
  - `rocm` → `llama-<tag>-bin-ubuntu-rocm-10.0-x64.tar.gz`

  Pick CUDA 12.8 vs 13.5 from the driver version (`nvidia-smi --query-gpu=driver_version`). CUDA 13.x needs driver ≥ 580.
- **Integrity.** Verify each asset against the `digest` field (`sha256:…`) the GitHub releases API returns per asset. Refuse an asset that has no digest. Reuse the download-and-verify code from `engine.py` (`hashlib`, resume, progress in `UPD`).
- **Layout.** `~/.local/share/shoggoth/llamacpp/<tag>/<backend>/`. Keep the two newest tags, as `piup.py` does. A "go back to the previous version" button works the same way as for Pi.
- **glibc check.** The ubuntu builds are linked against the runner's glibc. After unpacking, run `llama-server --version` once. If it fails with a `GLIBC_2.xx not found` error, retry through the bundled loader (`_glibc_dir()` from `engine.py`, kept). If both fail, show "This llama.cpp build needs a newer Linux" with the glibc version found.
- **Start in router mode**, one process for all models:

  ```
  llama-server --host 127.0.0.1 --port {port} --api-key {key}
               --models-dir {library}/view --models-preset {data}/presets.ini
               --models-max 1 --no-webui --metrics --slots
  ```

  `--models-max 1` keeps today's one-model-at-a-time behaviour on the GPU memory group. Make it a setting ("Keep up to N models loaded"), default 1.
- **Per-model options** live in `presets.ini`, which Shoggoth writes (one `[model-id]` section each): `ctx-size`, `n-gpu-layers`, `flash-attn on`, `cache-type-k`/`cache-type-v`, `n-cpu-moe`, `mmproj`, `jinja`, `chat-template-file`, plus the tuned flags. `llamaargs.py` already knows how to validate llama-server flags against `--help`; route every preset line through it.
- **Load/unload** through router endpoints (`POST /models/load`, `POST /models/unload`, `GET /models`). Check the exact JSON shape against the pinned build at start-up and record it in `provcheck.py` so `providers doctor` catches drift, as it does now for Lemonade.
- **Adopting a running server.** Today a `lemond` left running is reused. Do the same: write `{pid, port, key, tag}` to `llamacpp/run.json`; on start, if that pid is alive and `/health` answers with the key, adopt it.

### 1.2 Provider wiring

- New `shoggoth/providers/llamacpp/provider.yaml` + `provider.py`. Start from `providers/_examples/llama-server/provider.yaml`, which already describes llama-server as one YAML file. Change `start` to router mode, `memory.exclusive: false`, `max_loaded` from the setting, `capabilities.multi_model: true`, `tuning: {builtin: llama-bench}`.
- Delete `shoggoth/providers/lemonade/`. Move "Lemonade" into `recommendations/servers.yaml` as a **connected-server preset** (`http://127.0.0.1:13305/api/v1`), so people who run their own Lemonade keep it.
- `registry.py`: the default engine id becomes `llamacpp`. Remove the special cases that name `lemonade`.

### 1.3 Every Lemonade call site (from `grep lem\.|engine\.`)

| File | What changes |
| --- | --- |
| `server.py` (25 calls) | `/api/*` passthrough to Lemonade goes. `/internal/config` routes (lines ~282, 430–482) become `llamacpp` settings routes: backend, device list, version. `effective_mode`/`_existing_target` collapse to "Shoggoth's llama.cpp" or "a server you connected". The startup print at line ~1089 names llama.cpp. |
| `gateway.py:307–366` | `_route` stops defaulting to `lem.target()`. Every model goes through `registry` to its provider's endpoint. The "Lemonade" error label becomes the provider name. |
| `agent.py:117, 960` | Replace `lem.get("/v1/health")` with `registry.loaded()` (router `GET /models` filtered to `status: loaded`). |
| `hardware.py:80, 315–364` | Device list from `llama-server --list-devices` (already the documented approach: "Device lists come from the llama.cpp binary itself"). Drop `lem.system_info()` and `lem.flat_config()`. |
| `library.py:334–354`, `modelscan.py:205–214` | No more `models_dir`/`extra_models_dir` pushes. The library's view folder **is** `--models-dir`. Reload with `POST /models/reload` (or restart the router when the build has no reload). |
| `tuning.py`, `lemtune.py` (350 lines) | Rename `lemtune.py` → `llamatune.py`. Measure with the bundled `llama-bench` against the model file instead of loading it through Lemonade. Store the winning flags in `presets.ini`, and the gain and rollback in `tuning.json` as now. `headroom_args` stays, emitted as `--fit-target`/`-ngl` adjustments. |
| `modelmeta.py` | `DEFAULT_HEADROOM_GB = {"llamacpp": 0.0}`. |
| `index.html` | `lemReady()`, `backendsInstalled()`, `recFetch`, the engine cards on Advanced > Engines, `friendlyProblem` text ("the llama.cpp inside your Lemonade") all change. Search the file for `lemonade`, `Lemonade`, `lem` (about 20 hits for `zest` as well; leave those, they're internal). |

### 1.4 Downloads without Lemonade

Today a download registers a Lemonade custom model (`user.<name>-<quant>`, `checkpoint org/repo:QUANT`). Replace that with a direct download in `hf.py`:

- `GET https://huggingface.co/api/models/{repo}/tree/main` for file sizes and the LFS `oid` (sha256). Download from `/resolve/main/{file}` with `Range` resume, verify sha256, write to `{library}/models/{repo}/{file}`.
- Split GGUFs (`-00001-of-00003.gguf`): download every shard; `gguf.shard_of` already parses the names.
- Vision models: fetch the `mmproj-*.gguf` next to the weights and write it into the preset.
- Model id = `<repo-name>-<QUANT>` with no `user.` prefix.

### 1.5 Migration from 4.x

Run once on first start of 5.0, in a new `shoggoth/migrate5.py`, with a log line per step:

1. Find GGUFs Lemonade downloaded (its HF cache under `lemonade-data` and `{library}/lemonade`). Link them into the library view (hard links where possible, symlinks otherwise). **Never move or delete them.**
2. Build an alias map `old id → new id` (`user.MiMo-…-Q4_K_M` → `MiMo-…-Q4_K_M`; Lemonade catalog names like `Qwen3.6-35B-A3B-MTP-GGUF` → the file's repo + quant). Save to `model-aliases.json`. `agent.py` model lookup and the chat bar check aliases, so old chats keep their model.
3. Carry over per-model options (Lemonade's saved `ctx_size`, `llamacpp_args`) into `presets.ini`.
4. Stop a `lemond` that Shoggoth started. Leave one the person runs.
5. Offer (don't do) "Delete Lemonade's own files (N GB)" on Advanced > Engines.

### 1.6 Packaging

`packaging/build-appimage.sh`: drop the Lemonade download and the `lemond` copy. Add the pinned CPU llama.cpp build (URL + sha256, like `PI_URL`). Keep the glibc loader until 1.1's check shows the ubuntu builds run without it on 22.04.

### 1.7 Tests

Update the 11 tests that touch the engine (`test_chat_loading`, `test_servers`, `test_creatures`, `test_tuning_for`, `test_library`, `test_ownership`, `test_router`, `test_versions`, `test_providers`, `test_recommendations`, `test_rename`). Add:

- `test_llamacpp_release.py`: picks `b12375` over `v1.0.0`; picks the CUDA 13.5 asset only with driver ≥ 580; refuses an asset with no digest.
- `test_presets.py`: `presets.ini` round-trip; an invalid flag is rejected by `llamaargs`.
- `test_migrate5.py`: alias map from a fixture Lemonade data dir; never deletes source files.

**Done when:** a clean profile goes Getting ready → GPU backend downloaded → model fetched → chat works, with no Lemonade on the machine; and a 4.3 profile opens with its old chats and models intact.

---

## Phase 2. Our own model catalog **[#51]**

### 2.1 The list is ours

- `recommendations/models.yaml` becomes the full source of truth. Every entry gets a `download.checkpoint` (an unsloth or bartowski repo and file) because there is no Lemonade catalog to fall back on. Remove the `also:` aliasing for `user.` ids; the alias map from 1.5 covers old names.
- Add the missing picks, starting with **Qwen3.6-Next** (named in #51 as missing from search). Give each entry per-quant sizes: `quants: {Q4_K_M: 5.2, Q6_K: 6.9, Q8_0: 8.6}` so the card can offer a smaller quant when the default doesn't fit.
- `recs.py clean_models`: add validation for `quants`, require `download` on every entry, warn if a checkpoint's repo isn't from an allowed uploader (`unsloth`, `bartowski`, `ggml-org`, `lmstudio-community`) unless `trusted: true`.

### 2.2 Always show recommendations

- `libRecommended()` (`index.html:2509`): show every card regardless of fit. Fit becomes a **badge** on the card ("Runs well" / "Tight" / "Too big for this computer, needs about N GB") instead of a filter.
- Remove the "Shoggoth is still looking at your computer…" gate (`index.html:2524` and `3189`). With no hardware data yet, cards show without a badge.
- **Bug: returning to Recommended after a search shows only that message.** `libRecommended` filters by `LIB.q`, and nothing clears `LIB.q` when the tab changes. A search with no hits in the recommended list leaves every section empty, and the empty-state message is the "still looking" one. Fix: clear `LIB.q` and the search box on tab change; when the filter matches nothing, say "No recommended model matches *X*. Search Hugging Face instead?" with a button.

### 2.3 Our own search

Replace the Lemonade model list behind Models > Search with Hugging Face directly, in `hf.py`:

- `GET https://huggingface.co/api/models?search={q}&filter=gguf&sort=downloads&limit=40`.
- Rank: allowed uploaders first, then by downloads. Hide repos with no `.gguf` files.
- Expanding a result lists its quants from the tree API with sizes and fit badges.
- Cache results 10 minutes in memory. Web access is already disclosed for downloads in `PRIVACY.md`; add the search requests.

### 2.4 Stale models in the picker **[#51]**

"Qwen-code still appears despite Strata being removed." There is no `qwen-code` string in the repo, so it comes from data: a model list cached from a connected server (or Lemonade's own catalog). Fix in `servers.py remove()` and the picker cache: removing a server drops its models from the chat picker and from `models.json` for Pi immediately. Add a test: add server → models appear → remove → gone without restart.

### 2.5 Model names **[#51]**

"With LFM, the model name looked like the model ID or hash." `friendly()` (`index.html:2245`) only rewrites the id. Fix the name chain, in order: the `title` from `models.yaml` → GGUF `general.name` (already read by `gguf.read_header`) → `friendly(id)`. Send `title` from the server in the model list so the chat bar, the Server page, and the tube label all use it. Reproduce with LFM2.5-8B-A1B first and add the id seen to a test fixture.

---

## Phase 3. Fix the memory estimate **[#51]**

"I'm sure it's exaggerating memory needed for context." Confirmed: there are four separate over-counts, and the page uses a second estimator that disagrees with the server.

1. **Hybrid models count every layer as attention.** `gguf._attention` takes `max(head_count_kv)` when it's a per-layer list and multiplies by all layers. LFM2 (conv + attention) and similar hybrids store `0` for layers with no KV cache. Fix: when `head_count_kv` is a list, sum `kv_heads × (dk + dv)` over the layers with a non-zero count, and drop `full_every` for those (it's for models that don't publish a list).
2. **The KV cache is priced at 16 bits.** `kv_cache_gb(bytes_per=2)`. Phase 1 launches with `flash-attn on` and `q8_0` K/V caches by default, which is about 1.06 bytes per element. Pass the cache type from the model's preset.
3. **Every model is priced at 128k context.** `library.py:266` uses `chat.ctx` (default 131072) for every model on every machine. Fix: the default context becomes **auto**: the largest of 16k / 32k / 64k / 128k / the model's maximum that fits, chosen per model, and stored in the preset. The chat bar still lets people pick.
4. **MoE models ignore expert offload.** A 35B-A3B model with `--n-cpu-moe` keeps the experts in RAM and needs a fraction of the VRAM. `provbase.fit` should treat VRAM + (RAM − 8 GB) as available for MoE models when the preset offloads experts, and say "Runs partly on the processor" rather than "Too big".
5. **Two estimators.** `fitInfo()` in `index.html:2287` uses `size + 0.8` GB with no KV term; the server uses `size × 1.1 + kv_gb`. Delete the page one and use the server's `need_gb` and `fit` everywhere.

**Check it against reality.** llama-server logs `llama_kv_cache: size = N MiB` and the model buffer sizes at load. Add `tests/fixtures/kvlogs/` with real logs from the development machine (RTX 4080 SUPER 16 GB + RTX 3080 10 GB, CUDA) for LFM2.5-8B, Gemma-4-12B, Qwen3.6-35B-A3B at 32k and 128k. `test_headroom.py` asserts the estimate is within 10% of the logged total. After each load, record the measured size in `model-meta.json` and prefer it over the estimate next time.

---

## Phase 4. Hook up whatever you run, and switch between it anywhere **[new]**

The model for 5.0: Shoggoth **sets up llama.cpp for you** and offers a curated set of models for it. It also **finds the engines you already run** (Strata, PolyStrata, Maya, Ninfer, Basalt, vLLM, SGLang, Ollama, LM Studio, Lemonade, a llama-server of your own, another Shoggoth) and connects to them in one click. Every model from every source shows up in **one list**, which you can switch from in the chat bar and manage on the Models page.

Shoggoth never installs, starts, stops, updates or tunes an engine it didn't set up. That rule from `servers.yaml` stays.

### 4.1 Sources: one idea for everything that serves models

A **source** is anything that serves models: Shoggoth's own llama.cpp, a connected engine, another Shoggoth (Phase 5), or a paid service (4.6). Today that's split between providers (`registry.py`) and connected servers (`servers.py` → `ConnectedServer`). Keep that code, and give the page one shape for all of it:

`GET /ui/sources` returns `[{id, kind: "local"|"engine"|"shoggoth"|"cloud", name, engine, url, state: "ready"|"starting"|"offline"|"needs_key", models: n, loads: "router"|"ollama"|"fixed", leaves_computer: bool}]`

- `loads` says what Shoggoth can do with the source's models:
  - `router`: it can load and unload (Shoggoth's llama.cpp, a llama-server in router mode, Lemonade).
  - `ollama`: it loads on first request (Ollama, LM Studio).
  - `fixed`: it serves whatever it was started with (most specialised engines: Strata, Maya, vLLM, SGLang). The UI shows no Load or Unload buttons for these.
- `GET /ui/models` becomes the merged list across sources: `[{id, source, title, state: "loaded"|"available"|"offline", ctx, tools, vision, fit?}]`. `fit` exists only for local files Shoggoth can read.
- Model ids are `source/model` internally (`llamacpp/Qwen3.6-35B-A3B-Q4_K_M`, `strata-8080/qwen4exp-100b`). Two sources serving a model with the same name never collide. Pi's `models.json` (rewritten by `agent.py` at each start) gets one provider entry per source, as it does now per engine.

### 4.2 Finding running engines

Today `servers.discover()` probes each preset's usual address. Strata, Maya and llama-server all default to `127.0.0.1:8080`, so a server there is shown as "Server at 127.0.0.1:8080", and an engine on any other port is never found. Replace it with three steps, in a new `shoggoth/scan.py`:

1. **List what's listening.** Read `/proc/net/tcp` and `/proc/net/tcp6` for sockets in LISTEN state (state `0A`) on loopback or any address. Map each socket inode to a process through `/proc/<pid>/fd/*` → `socket:[inode]`, for processes owned by the current user (others' fds aren't readable; those ports are still probed, just unnamed). Take the process name and the first two words of `/proc/<pid>/cmdline`. This takes milliseconds, finds engines on **any** port, and doesn't guess.
2. **Ask each port what it is.** For each listening port not owned by Shoggoth, run `GET /v1/models` with a 1.5 s timeout (8 at a time, as now). Skip ports that don't speak HTTP. Then identify the engine, first match wins:
   - process name or cmdline matches a preset's `match` (e.g. `strata`, `polystrata`, `maya`, `ninfer`, `basalt`, `vllm`, `sglang`, `ollama`, `lms`, `lemond`, `llama-server`);
   - a response signature: `GET /api/version` answers (Ollama); `GET /props` has `default_generation_settings` (llama.cpp); `/v1/models` items with `owned_by: "vllm"` (vLLM) or `"sglang"`; a `Server:` header;
   - otherwise "OpenAI-style server on port N".
3. **Read each model's details** so the picker isn't guessing either: context length from llama.cpp `GET /props` (`n_ctx`), Ollama `POST /api/show`, vLLM's `max_model_len` in `/v1/models`. When nothing says, use 32k and show "context unknown, set it" on the model.

Presets gain `match` (process names), `identify` (replies that name the engine) and `category`. They move into the definition files described below. Still no commands. Add a definition for every engine in **Appendix A**, written from each project's repository and docs. Where a default port isn't known, leave `url` empty and rely on `match`; the process scan finds them wherever they listen.

**Engines that can't be told apart: "Specialized inference engine".** Strata, PolyStrata, Maya, Ninfer and Basalt are built for running very large models on home hardware. Several of them default to port 8080, just like llama-server, and some don't publish anything that names them in their replies. When the process name doesn't settle it (the usual case is a Docker container, or a process owned by another user), Shoggoth doesn't guess. It files the server under one category:

> **Specialized inference engine** on port 8080
> One of the engines built for running very large models on home hardware, such as Strata, PolyStrata, Maya, Ninfer or Basalt. They all serve an OpenAI-style API, so Shoggoth can use this one without knowing which it is. [This is Strata ▾]

- The card works exactly like a named engine: Connect, models, chat.
- The "This is…" menu lets the person name it. Shoggoth remembers the choice for that port and process (4.7 step 2).
- A preset joins the bucket with `category: specialized` in its definition file. A process-name match still names the engine directly. On this machine, `~/.local/bin/strata` listening on 8080 is named "Strata" by its process; the bucket is for when that isn't visible.
- Same idea for any other port shared by engines that answer identically: the definitions say which category they fall into, and the card names the category, lists the candidates, and explains why it can't tell.

**Engine definitions are data, not code.** Every engine Shoggoth knows (local engines, specialized ones, hosted services) is one entry in a definition file. Shoggoth reads them at start-up and again whenever a file changes, the way `recs.py` already reloads `models.yaml`. Adding, changing or retiring an engine needs no new release.

- **Where they live.** Bundled: `shoggoth/engines.d/*.yaml` (shipped, read-only in the AppImage). Yours: `~/.local/share/shoggoth/engines.d/*.yaml` or `*.json`. A user file with the same `id` replaces the bundled entry; `enabled: false` hides one. This replaces `recommendations/servers.yaml` (moved into `engines.d/` at build time, so nothing is lost).
- **One file can hold one engine or a list.** JSON and YAML are equivalent; the loader takes whichever extension it finds.
- **What a definition holds, and nothing else:**

  ```yaml
  id: strata
  name: Strata
  category: specialized          # local | specialized | hosted
  summary: Runs very large models on home hardware by splitting them across your cards and memory.
  url: http://127.0.0.1:8080     # usual address; empty if unknown
  ports: [8080]                  # where the scan looks first
  match: [strata]                # process names
  identify:                      # replies that name it (any one is enough)
    - {path: /v1/models, json: "$.data[*].owned_by", equals: strata}
  formats:                       # API format per model, first match wins
    - {api: openai-completions}
  context: {path: /props, json: "$.default_generation_settings.n_ctx"}
  loads: fixed                   # router | ollama | fixed
  key: optional                  # none | optional | required
  key_url: ""                    # where to get a key (hosted)
  docs: https://github.com/Niko1221/Strata
  hint: Use the address Strata prints when it starts, and its API key if it has one.
  ```

- **No commands, ever.** A definition can't start, install or run anything. The validator rejects any key outside the schema, any `url` scheme other than `http`/`https`, and `identify` paths that aren't plain GET paths. `tests/test_servers.py::test_presets_have_no_launch_fields` becomes a schema test over every bundled file.
- **Bad files don't break Shoggoth.** A file that fails validation is skipped, and the Models page shows "engines.d/foo.yaml: line 4, unknown key `start`".
- **Adding one from a link.** Like skills: paste a URL to a raw YAML/JSON file (GitHub or a gist). Shoggoth fetches it, validates it, shows what it says, and saves it to your `engines.d/` only after you press Add.
- `python3 shoggoth/server.py engines check <file>` validates a file without running Shoggoth.

**Docker.** An engine in a container (vLLM, SGLang, TGI, LocalAI usually are) listens through `docker-proxy`, which is owned by root, so the process scan sees the port but not the engine's name. The response signature in step 2 is what identifies these. That's why every preset in Appendix A has one.

**Several engines share a default port.** llama-server, LocalAI, llamafile, RamaLama and TGI use 8080; TabbyAPI and text-generation-webui use 5000. The order of checks in step 2 (process name, then signature) tells them apart, and the tests in 4.8 cover each collision.

**When scanning runs:** at start-up, on opening the Models page, on opening the chat bar's model picker (rate-limited to once per 20 s), and from a **Look again** button. It never runs in the background on a timer, and it never scans other machines (Phase 5's beacon covers the network).

**What the person sees.** A found, unconnected engine appears as a card: "**Strata** is running on port 8080, serving 1 model. [Connect]". One click connects it, no form. If it wants a key, the card grows a key field, plus a hint from the preset ("Strata prints its key when it starts"). An engine that stops shows as *offline* and keeps its place; Shoggoth reconnects when the port answers again.

### 4.3 Switching in the chat bar

The chat bar's model picker becomes the main place to switch:

- Models grouped by source, in this order: **This computer** (Shoggoth's llama.cpp), then each connected engine by name, then **Other Shoggoths**, then **Paid services** under a heading that says they leave your computer.
- Each row: the model's title (the name chain from 2.5), a state dot (loaded / ready / offline), context size, and tool and vision icons. Offline rows are greyed but stay, so a chat's model doesn't silently vanish.
- At the bottom: **Found running: Ollama on 11434 [Connect]** for anything the scan found that isn't connected, and **Hook up something else…** which opens the Models page Sources section.
- Switching is per chat and takes effect on the next message. The chat stores `{source, model}`. Pi restarts with the new provider the way it does today when the model changes. A short note goes into the thread: "Switched to qwen4exp-100b on Strata."
- **When a source goes away mid-chat:** the composer shows "Strata isn't answering. [Retry] [Switch model]". It doesn't fall back by itself: a different model is a different answer.
- **Default model:** the last one picked, unless it's offline. Then the first ready model from This computer.
- Keyboard: `Ctrl+M` opens the picker, typing filters it.

### 4.4 The Models page

Restructure into two parts:

**Sources** (top, always visible). One card per source: name, engine, address, state, model count, and actions (Look again, Disconnect, Key, Open its own page if it has a web UI). After it, the found-but-not-connected cards from 4.2 and a **Hook up something else** card (type an address and a key, or pick a preset). This replaces the Connect a server form under Advanced > Engines. Advanced keeps only Shoggoth's own llama.cpp settings (backend, version, devices).

**Models**, with tabs:
- **Recommended**: the curated llama.cpp list from Phase 2, always shown, with fit badges and Get buttons.
- **On this computer**: downloaded files, load and unload, tuning, context size, delete.
- **From your engines**: everything connected sources serve, filterable by source. Per model: use in a new chat, set as default, set the context size Pi should assume, and Load/Unload where `loads` allows it.
- **Search**: Hugging Face (2.3), for llama.cpp.

Getting ready (first run) asks one question: "Set up llama.cpp on this computer" or "Use an engine I already run". The second runs the scan and shows the cards. Choosing it skips the GPU download, which stays available later under Sources.

### 4.5 Gateway and other apps

The gateway (port 13308) already forwards to connected servers. With source ids it can expose **every** source's models to Hermes and other apps under one address and key. Strata's model becomes reachable through Shoggoth with no Strata key in the other app. The Server page shows which source served each request.

### 4.6 Hosted services: OpenRouter, Nous Portal, OpenCode, Command Code, Google Gemini

All five are in scope for 5.0. OpenRouter and Nous Portal speak plain OpenAI chat completions. OpenCode and Command Code need Shoggoth to speak more than one API format. Gemini has an OpenAI-style endpoint, with one catch around tool calls.

| Service | Base URL | Formats | Key from | Notes |
| --- | --- | --- | --- | --- |
| **OpenRouter** | `https://openrouter.ai/api/v1` | OpenAI chat completions | openrouter.ai/keys | 300+ models. `/models` gives `supported_parameters` and `pricing`. |
| **Nous Portal** | `https://inference-api.nousresearch.com/v1` | OpenAI chat and completions | Portal dashboard, after subscribing | Hermes models. Base URL comes from secondary sources and Nous's Hermes docs; confirm against portal.nousresearch.com/docs when building. |
| **OpenCode Zen** | `https://opencode.ai/zen/v1` (pay as you go), `https://opencode.ai/zen/go/v1` (Go plan, open models only) | Chat completions for GLM, Kimi, Qwen; **Anthropic Messages** for Claude; **OpenAI Responses** for GPT | opencode.ai account | `/v1/models` lists models but not which format each uses. |
| **Command Code** | `https://api.commandcode.ai/provider/v1` | Chat completions for OpenAI and open models; **Anthropic Messages** for Anthropic models; Responses also offered | commandcode.ai | Sending a model to the wrong endpoint returns an error, so the format must be right per model. |
| **Google Gemini** | `https://generativelanguage.googleapis.com/v1beta/openai/` (OpenAI-style); native `https://generativelanguage.googleapis.com/v1beta` | OpenAI chat completions on the first; Gemini's own `generateContent` on the second | aistudio.google.com/apikey | The key goes in `Authorization: Bearer` on the OpenAI-style endpoint and in `x-goog-api-key` on the native one. Free tier with rate limits, so show 429s as "Gemini's free limit is used up for now". |

**What has to change for OpenCode and Command Code:**

- **Per-model API format.** `agent.py:286` writes every provider into Pi's `models.json` with `"api": "openai-completions"`. Pi's model layer also supports `anthropic-messages` and `openai-responses`. Give each model in the merged list (4.1) an `api` field. Pick it from the preset's rules: `formats: [{match: "^(anthropic/|claude)", api: anthropic-messages}, {match: "^(openai/)?gpt-", api: openai-responses}, {api: openai-completions}]`. Write it per model, not per provider.
- **The proxy must pass the other formats.** Pi talks to models through Shoggoth's `/api/v1` proxy so requests are recorded. `gateway._route` and `relay` need to forward `/v1/messages` and `/v1/responses` as well, with `x-api-key`/`anthropic-version` headers for Messages.
- **The stream classifier** (`gateway.py` `_think`, `_write`, `_obj`) understands OpenAI chunks only. Add Anthropic SSE (`content_block_delta` with `thinking_delta` vs `text_delta`) and Responses events (`response.reasoning_text.delta` vs `response.output_text.delta`). Without this, the Server page and the speed figure show nothing for those models. The forwarded bytes stay unmodified either way.
- **Folderless chat** in the page also calls chat completions directly. Route it through Pi for non-OpenAI models, or add the same two formats to the page's request code. Recommendation: route through Pi, so there's one code path.

**Gemini and tool calls.** Gemini's thinking models attach a *thought signature* to each function call, and the next request must send it back with the tool result, or a multi-step tool run fails or loses its reasoning. On the OpenAI-style endpoint the signature travels in an extra field on the tool call. Pi has to keep that field when it builds the next message.

- Step 1: test Pi's `openai-completions` against the OpenAI-style endpoint with a three-step tool task (read a file, edit it, run a command). If the signatures survive, that's the whole integration.
- Step 2, if they don't: use Pi's native `google-generative-ai` format for Gemini models (`api` per model, as above). The proxy then forwards `/v1beta/models/{model}:streamGenerateContent?alt=sse`, and the stream classifier learns Gemini's chunks (`parts[].thought: true` is thinking, other `parts[].text` is writing).
- Either way, list models from `GET /v1beta/models` and keep only those whose `supportedGenerationMethods` include `generateContent`, so embedding and image-only models stay out of the chat picker.

**Everything else applies to all five:**

- They're `kind: cloud` presets: pick the service, paste a key, done. Never found by scanning.
- Large catalogues (OpenRouter, Zen): only models that support tool calls are shown (OpenRouter's `supported_parameters` includes `tools`; for Zen and Command Code, a per-preset allow-list until their model lists say). People star the ones they want; only starred ones appear in the chat picker. Show prices where the service publishes them.
- A chat on a hosted model shows a persistent "Leaves your computer: OpenRouter" chip, and the picker puts them under **Paid services**.
- Keys stored with mode 0600 (check `servers.py`'s storage and fix it if the file isn't 0600). The page sees only the last four characters, as now.
- **Subscriptions vs API keys.** Command Code and OpenCode's Go plan are subscriptions aimed at their own coding tools. Use only the API keys each service issues for third-party clients, through the documented provider endpoints above, and link to each service's terms from its card. Don't proxy a subscription login.
- `docs/PRIVACY.md` and `SECURITY.md` list all five destinations.

### 4.7 Set up your own provider

For anything the scan doesn't find or identify, and for hosted services that aren't presets.

**Step 1: Connect by hand** (the **Hook up something else** card):

- Address, and an optional key.
- **API format**: OpenAI chat completions (default), Anthropic Messages, OpenAI Responses, or "Ollama native" (`/api/chat`, for old Ollama builds without `/v1`).
- **Models**: read from `/v1/models` when the server has it. Otherwise, type model names.
- **Context size**: read from the server where possible (4.2 step 3), otherwise typed. Default 32k.
- **Is it on this computer?** Inferred from the address (loopback or a private range vs anything else) and used for the "Leaves your computer" chip. The person can override it.
- **Test** button: sends one short chat request and a one-tool request, and reports "Chat works · Tool calls work" or what failed, in plain words. A model that can't do tool calls can chat but is marked as unable to work in a folder.

**Step 2: Teach Shoggoth to recognise it.** After a manual connection to a local address, Shoggoth offers to remember it as a preset: "Recognise this next time? It's `ninfer-server` on port 9100." Yes writes `~/.local/share/shoggoth/servers.d/<id>.yaml` with `name`, `url`, `match` (the process name the scan saw on that port), `identify` (a signature Shoggoth found, if any), `formats` and `hint`. User presets load after the bundled `servers.yaml`, and the same validator runs (`recs.py`, no commands allowed). The scan then names it on any port.

**Step 3: Share it.** The preset card has **Copy as YAML**, so someone can post it in an issue and it can be added to the bundled list. `python3 shoggoth/server.py recommendations check` validates a pasted preset.

**Engines Shoggoth runs** (as opposed to connects to) still use `provider.yaml` and `providers new --from llama-server` (`docs/ENGINES.md`). That stays an advanced route; the Hook-up flow is the one the Models page offers.

### 4.8 Tests

- `test_scan.py`: parse fixture `/proc/net/tcp` text (LISTEN rows, v4 and v6, `0A` state, hex ports); inode → pid mapping from a fake `/proc`; identification order (process name beats signature beats fallback); two engines on 8080 and 8081 are told apart by process name.
- `test_sources.py`: merged list with colliding model names from two sources; offline source keeps its models greyed; removing a source drops its models (this is also 2.4's stale-model fix).
- `test_custom_provider.py`: the Test button's chat and tool probes against a fake server; a saved user preset round-trips through the validator; a preset with a command in it is refused; a remembered `match` makes the scan name the engine on a different port.
- `test_formats.py`: per-model `api` selection for Zen and Command Code model names; Gemini model filtering by `supportedGenerationMethods`; a Gemini thought signature survives a round trip through the proxy; Anthropic SSE and Responses streams classified as thinking or writing; the proxy forwards `/v1/messages` with the right headers.
- `test_chat_switch.py`: switching writes `{source, model}`, restarts Pi with the right provider, adds the note; an offline source blocks sending with the retry/switch prompt.

## Phase 5. Server mode and client mode on the local network **[new]**

Shoggoth can act as a **server** (shares its models with other Shoggoths on the network) or a **client** (finds a Shoggoth server and uses its models). The model runs on the server; the chat, the agent, and the files stay on the client. A remote Shoggoth is a connected server that finds itself.

### 5.1 Server mode

- A switch on the Server page: "Share my models with other Shoggoths on this network". It turns on the existing LAN listener (`gateway.py:480`, `0.0.0.0`) and starts the beacon.
- **Beacon** (new `shoggoth/lan.py`): every 2 s, a UDP broadcast to `255.255.255.255:13309` (and each interface's broadcast address) with
  `{"shoggoth": 1, "name": "<hostname or chosen name>", "profile": "<profile>", "version": "5.0.0", "port": 13308, "fp": "<sha256 of TLS cert>", "models": <count>}`.
  No model names or user data in the beacon. Include `profile` because of the recent per-profile port change (f5d393b): two profiles on one box are two servers.
- **TLS.** Generate a self-signed cert once with `openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256` (OpenSSL 3 is already a requirement) and serve the LAN listener over HTTPS with `ssl.SSLContext`. Loopback stays plain HTTP. This fixes "Traffic on a home network isn't encrypted" from the current copy.
- **Pairing**, so the API key never has to be typed: the client asks to pair; the server shows a 6-digit code and the client's name ("Asha's laptop wants to use your models"). The person types the code on the client. The client sends `HMAC(code, client_nonce‖server_nonce)`, the server returns a per-client key. The client pins the cert fingerprint from the beacon (trust on first use). Paired clients are listed on the Server page with "Remove", which revokes that key.
- The Server page already shows requests per app and IP; label paired clients by their name.

### 5.2 Client mode

- Shoggoths found by the beacon listener appear as source cards (4.2, 4.4) under **Other Shoggoths**, on the Models page and in the chat picker.
- Connecting one runs pairing, then saves it as a connected server with preset `shoggoth` (`servers.yaml`), URL `https://<ip>:13308/v1`, the per-client key, and the pinned fingerprint. `provbase.http_json` gets a `pin` option that checks the peer cert hash.
- **Client-only install.** If the person picks another Shoggoth on first run ("Use an engine I already run"), skip the llama.cpp GPU download. The Engines page offers "Also run models on this computer" later.
- Remote model loading: the gateway already exposes load/unload. Add the server's `fit` per model to its `/v1/models` extension field so the client's picker can say "Loads on desk-pc".
- If the server disappears, the chat shows "desk-pc isn't answering" with a retry, and the beacon listener re-finds it if its IP changed (match on fingerprint, not address).

### 5.3 Tests

`test_lan.py`: beacon encode/decode, ignores malformed packets and packets over 1 KB, pairing HMAC round-trip, a wrong code fails and is rate-limited (5 tries then 60 s lockout), pinning rejects a different cert.

---

## Phase 6. Pi: packages, file types, and skills it writes itself **[#52] [new]**

### 6.1 Curated Pi packages (D5)

Pi packages are npm packages declaring extensions, skills, prompts, themes. They run code, so Shoggoth installs only a pinned list.

- New `shoggoth/pipkgs.py`, reusing `piup.py`'s npm fetch + sha512 check. Unpack to `~/.local/share/shoggoth/pi-packages/<name>@<version>/`. **Never run npm lifecycle scripts.** Resolve dependencies from the package's `dependencies` with exact versions from the registry and verify each.
- `agent.py` regenerates Pi's `settings.json` at each start. Add a `packages` list there pointing to the unpacked folders of enabled packages. Keep `--no-skills`, but add each package's skills to `skills-live/` so the Skills page still shows the full list.
- Skills page gets a "Tools" section: each curated package with on/off, what it adds, and "Runs code on your computer" stated plainly.
- **Test each package from #52 before adding it**, and record the result in `recommendations/tools.yaml`:

Reviewed from published source on 14 Nov 2026 (`docs/plans/pi-packages-review.md`). None of these run install scripts.

| Package | Verdict | Why |
| --- | --- | --- |
| `@dietrichgebert/ponytail` 5.3.3 | **Bundle, with conditions** | Six skills (1.7–5.3 KB), commands, a system-prompt hook; no processes, no network. Offer it for larger models; ship a short Shoggoth `good-code` skill for small ones (see `skills-review.md`). |
| `@sttronn/pi-sheets` 0.1.2 | **Bundle, with conditions** | One skill (22.7 KB, too long for small models) that runs Python with `openpyxl`; needs a venv and PyPI on first use. Only for larger models, and the venv setup has to be visible to the person. |
| `bun-docx` 0.29.0 | **Don't bundle the npm package** | It needs Bun, or downloads an unverified ~100 MB binary at run time. Word support goes through a pinned binary instead, or waits. |
| `@tintinweb/pi-subagents` 0.25.0 | **Don't bundle in 5.0** | Child sessions run inside the same process with their own loader, so Shoggoth's `-e` approval gate isn't applied to them, and they turn skill discovery back on. That would let a subagent edit and run commands without asking. |
| `billion-context` 0.1.213 | **Don't bundle** | Adds its own proxy and ungated subagents, cancels Pi's compaction, and updates itself at run time, which defeats pinning. Its licence adds an attribution term on top of MIT. |

### 6.2 Common file types **[#52] [new]**

- **Attach in chat.** Drag and drop or a paperclip on the composer. The file goes to the chat's folder (or the scratch dir for folderless chats) under `attachments/`.
- **Images:** passed to vision models as image content (mmproj from 1.4). For a model without vision, say so on the attachment chip instead of sending it.
- **PDF:** `pdftotext -layout` when poppler is installed; otherwise a curated Pi package for PDFs (evaluate in 6.1). Scanned PDFs: render pages to images with `pdftoppm` and send to a vision model.
- **Word / spreadsheets:** through `bun-docx` and `pi-sheets` (6.1).
- **Text, code, CSV, JSON, Markdown, HTML:** read directly.
- A shared `shoggoth/filetypes.py` maps extension → handler, and the composer shows which handler will be used ("PDF, read as text").

### 6.3 Skills Pi writes itself **[new]**

Today Pi's skill discovery is off on purpose so a folder can't add skills. Keep that, and give the model a gated way to *propose* a skill:

- New extension `shoggoth/pi/zest-skillsmith.js` registers one tool, `save_skill({name, description, body, files?})`.
  - Validates: `name` is a slug ≤ 40 chars; `description` ≤ 300 chars and says when to use it; `body` ≤ 8 KB (small-model friendly); `files` ≤ 5 text files, ≤ 16 KB each; no executable bits, no paths with `..`.
  - **Always** asks through the gate, in every mode including *Do everything*, with a preview card showing the full SKILL.md. Choices: *Save*, *Save and turn on*, *Don't save*.
  - Writes to `~/.local/share/shoggoth/skills/sets/own/skills/<name>/` with a `source.json`: chat id, model, date.
- New bundled skill `shoggoth/skills/shoggoth/skills/writing-skills/SKILL.md`: when to make a skill (a procedure that worked and will come up again), how to write one for small models (short, numbered steps, one example, a clear trigger in the description), and to call `save_skill`.
- Skills page: a **Written by your models** set with edit (plain textarea), delete, and "from chat X" link.
- A chat menu item, **Make a skill from this chat**, sends a prompt asking the model to distil what worked into a skill and call `save_skill`.
- `skills.py` restarts idle tasks after a save, as toggling does now.

### 6.4 Bundled skills **[#52] [new]**

- **Diagrams:** evaluate `cathrynlavery/diagram-design`. Check the license first. Shoggoth renders only fenced `mermaid` and `svg`; if diagram-design produces HTML or another format, adapt its guidance into the existing `diagrams` skill rather than bundling it as is.
- **Good code:** Ponytail (6.1). If it's too long for small models, write a short `good-code` skill in `shoggoth/skills/shoggoth/` and enable Ponytail only for larger models.
- **Unslop writing:** **you're writing this one.** Leave a slot: `shoggoth/skills/shoggoth/skills/unslop/SKILL.md` with a placeholder and `inline: true` in `set.json`, so it reaches folderless chats too. The paste you linked is a `clean-copy` skill about interface copy (labels, hints, toasts). It's worth using during the UI pass in Phase 8 as well.
- Rename the bundled set `zest` → `shoggoth` (keep reading `zest` from `state.json` so enablement survives).

---

## Phase 7. Speech **[new]** (see D1)

### 7a. Speech to text

- `whisper-server` from whisper.cpp, managed like llama.cpp in Phase 1 (download, verify, start on demand, stop after 5 minutes idle). Check whether whisper.cpp publishes Linux binaries per backend; if not, build them in CI and attach them to Shoggoth's releases with checksums.
- Default model: `ggml-large-v3-turbo-q5_0` (≈550 MB) on a GPU, `base.en` on CPU-only machines. Also evaluate Parakeet TDT 0.6B v3 if a GGML port is usable by then; 2026 comparisons favour it on speed and accuracy for English.
- Composer: a mic button. Hold to talk or click to toggle. Audio is recorded in the page (`MediaRecorder`, webm/opus), posted to `/ui/speech/transcribe`, converted with `ffmpeg` if present (or record 16 kHz WAV in the page with an `AudioWorklet` to avoid needing ffmpeg), and the text lands in the composer for review. It isn't sent automatically.
- The gateway exposes `/v1/audio/transcriptions` (it already lists audio endpoints), so other apps can use it.

### 7b. Read aloud (Bark, optional)

- `bark.cpp` as an optional engine for a "Read aloud" button on answers. Off by default; it's slow and large. If bark.cpp isn't usable, fall back to a lighter TTS (Kokoro or Piper) and say Bark isn't available.

---

## Phase 8. UI **[#51] [new]**

### 8.1 The tube on wide screens

"On widescreen monitors, the Chat section and the test tube take up a huge amount of space." Fix:

- At ≥ 1280 px wide, move the creature into a **vertical tube** in a right-hand column, 200–240 px wide, full height, with the bench equipment stacked under it. The chat column gets `max-width: 72ch` and centres in what's left.
- 900–1279 px: a short horizontal strip, 72 px tall, with the creature small at its left.
- Under 900 px: the creature hides behind a button in the chat bar.
- The creature SVG needs a portrait variant: same body, tube redrawn tall. The generator around `index.html:4260` scales by `rxv`; add an aspect parameter rather than a second generator.
- Remember a collapsed tube per viewer in `localStorage`.

### 8.2 Copy pass

Run every user-facing string past the clean-copy ladder (does this need to exist; does the label need a caption; is this warning scar tissue). Candidates already visible in the code: the headroom row's two-sentence hint (`index.html:2304`), the Large-model note (`2451`), the LAN switch paragraph (`3763`, which also changes once TLS lands). Use `docs/plans/ai-tells.md` and `unslop-audit.md`, which already exist.

### 8.3 Smaller fixes

- Model name chain (2.5) everywhere a model is named.
- Recommended page (2.2).
- README says closing the tab quits "after about three minutes"; since f453fc8 it's within two seconds in app mode. Fix README and `docs.md`.
- Desktop entry still says "Lemonade Console" (`packaging/shoggoth.desktop`). See Phase 9.

---

## Phase 9. AppImage catalog fixes **[#37]**

1. **One name everywhere.**
   - `packaging/shoggoth.desktop`: `GenericName=Local AI`, `Comment=Run AI models on your own computer`, keywords without `lemonade`.
   - Add `packaging/io.github.yudhanjaya.Shoggoth.appdata.xml` (AppStream metainfo: id, name `Shoggoth`, summary = the motto, screenshots from `docs/screenshots/`, releases). Copy it to `usr/share/metainfo/` in `build-appimage.sh`. Missing metainfo is a likely source of the catalog's name warning.
   - `.github/workflows/release.yml`: delete the "Copy for Zest installs" step (D6). Zest 3.x installs that never updated will need a manual download; say so in the 5.0 release notes.
   - Check `packaging/aur/PKGBUILD` and `packaging/shoggoth.svg` for `Zest`.
   - Check the entry name in `data/` on appimage.github.io matches `Shoggoth`.
2. **Update info.**
   - `build-appimage.sh:113`: `appimagetool -u "gh-releases-zsync|yudhanjaya|Shoggoth|latest|Shoggoth-*x86_64.AppImage.zsync" …`. appimagetool writes the `.zsync` next to the AppImage.
   - `release.yml`: upload `dist/*.zsync` with the AppImage.
   - `zestup.py` (Shoggoth's own updater) keeps working as is; AppImageUpdate becomes a second route.

---

## Phase 10. Brand and website **[new]**

### 10.1 Motto

"Heavy on the cosmos. Light on your machine." goes into: the README hero (replace "Run AI on your own computer. Put it to work." or sit under it), the banner image `docs/readme/banner.png`, the version badge (5.0 gets a new edition name), the AppStream summary, the Getting ready screen, the page `<title>`, and `--version` output.

### 10.2 A page for Shoggoth

- A static site in `site/` (one `index.html`, CSS, images; no build step, matching the app's stdlib-only spirit). Published by a new `.github/workflows/pages.yml` to GitHub Pages at `yudhanjaya.github.io/Shoggoth` (a custom domain later if you want one).
- Sections:
  1. Hero: motto, a creature in its tube, **Download for Linux** (links to the latest release asset), version.
  2. Run llama.cpp with a curated model, or hook up the engine you already run (Phase 4).
  3. What it does: chat, work in a folder, jobs, diagrams, skills it writes itself, speech.
  4. Shoggoths on your network (Phase 5): one server, many laptops.
  5. Privacy: what stays local, what leaves and when.
  6. Screenshots: chat, models, hardware, a few themes.
  7. Footer: GitHub, docs, license.
- Use the app's own creature generator to render hero art (a script in `site/tools/` that runs the SVG generator from `index.html` in Node and saves SVGs), so the site and app match.
- Lighthouse ≥ 95 on mobile, works without JavaScript except the release-version fetch.

---

## Phase 11. Docs

Rewrite for 5.0: `README.md` (engine section, "Under the hood", install), `docs/ARCHITECTURE.md` (diagram, Lemonade sections → llama.cpp router), `docs/ENGINES.md` (the bundled provider is now llama.cpp; Lemonade is a connected server), `docs/AGENT-RUNTIME.md` (packages, `save_skill`, attachments), `docs/PRIVACY.md` (cloud services, HF search, LAN beacon), `docs/SECURITY.md` (pairing, TLS, cloud keys, packages run code), `shoggoth/docs.md` (the in-app guide).

---

## Who does what, in parallel

### Workers

| Worker | How it runs | Good for |
| --- | --- | --- |
| **Opus** (main session) | here | Architecture and anything security-sensitive: the llama.cpp engine core, the sources model, LAN pairing and TLS, the Models page and chat picker design, final integration and merges. |
| **Sonnet 5.5** | Claude Code subagent in its own worktree | Well-specified features with tests: the scan, downloads, the estimator, API formats, Pi packages, skillsmith, speech, the tube layout, the website. |
| **Haiku 5.5** | Claude Code subagent in its own worktree | Mechanical work with a clear spec: packaging fixes, preset and catalog YAML, fixtures, renames, doc drafts. |
| **DeepSeek V4.1 Flash** | Command Code, headless: `cmd -p "<brief>" -m deepseek/deepseek-v4.1-flash -w <name> --permission-mode accept-edits --max-turns 60 --output-format json` | Research and verification that needs lots of reading or trying things: confirming engine ports and signatures, testing Pi packages, checking skill licences, drafting the copy pass. Also **second-opinion reviews** of other workers' PRs. |

Command Code also offers Kimi K3, GLM 5.3 and Qwen 3.8 if DeepSeek struggles with a task. `-w` gives each run its own worktree, so it never touches the main checkout.

### Rules that keep parallel work from colliding

- **`shoggoth/index.html` has one writer at a time.** It's one 6,473-line file, and most features touch it. Server-side work runs in parallel; UI work queues (Wave 2).
- **`server.py`, `agent.py` and `gateway.py` are hotspots too.** A worker that needs one of them waits for the wave that owns it, or adds a new module and leaves the wiring to that wave.
- **Every worker gets a self-contained brief:** files it may touch, files it must not, what done looks like, the tests to run (`python3 -m pytest tests/`).
- **Every PR is reviewed by a different model than the one that wrote it.** DeepSeek reviews Sonnet's work, Sonnet reviews Haiku's, Opus reviews anything touching keys, pairing, the approval gate or packages that run code.
- **Hardware runs happen on the development machine** (RTX 4080 SUPER + RTX 3080, 62 GB RAM, CUDA 13 driver). A Voicebox instance often holds about 10 GB on the 4080; stop it before loading a model there. Vulkan and ROCm builds can't be tested on real AMD cards here; they ship marked untested, as now.
- **Engines aren't tested one by one.** Their ports, process names and replies come from each project's own repository and docs.

### Wave 0: start now, all at once (no dependencies between them)

| # | Task | Worker | Touches | Done when |
| --- | --- | --- | --- | --- |
| 0.1 | PR 1: #37 AppImage name and update info, desktop entry, metainfo, README quit timing | Haiku | `packaging/`, `.github/workflows/release.yml`, `README.md` | Build produces `.AppImage` + `.zsync`; `appstreamcli validate` passes |
| 0.2 | Estimator fixes 1, 2, 5 (hybrid layers, cache type, one estimator) | Sonnet | `gguf.py`, `provbase.py`, `library.py`, `fitInfo` removal in `index.html` (a small, isolated edit) | Tests pass with fixture GGUF headers for LFM2, Gemma 4, Qwen3.6-A3B |
| 0.3 | Scan, sources API, engine definition loader and schema, specialized bucket (4.1, 4.2), server side only | Sonnet | new `scan.py`, new `engines.py`, `shoggoth/engines.d/`, `servers.py`, tests | `test_scan.py`, `test_sources.py`, `test_engines.py` pass; no `index.html` changes |
| 0.4 | Engine definition files for every engine in Appendix A and the five hosted services, from 0.5's findings | Haiku | `shoggoth/engines.d/*.yaml` only | `engines check` passes for every file |
| 0.5 | Fill in Appendix A from each engine's repository and docs: default port, binary name, a reply that names it, whether it loads models. No running engines. | DeepSeek | a report (`docs/plans/engines-research.md`) only | Every **verify** cell confirmed or corrected, with a source link |
| 0.6 | Hugging Face downloads (1.4) and search (2.3) as a standalone module | Sonnet | `hf.py`, tests | Resumable, sha256-checked download of a small GGUF; search ranking test |
| 0.7 | Catalog data: `models.yaml` entries with checkpoints and per-quant sizes, including Qwen3.6-Next | Haiku | `recommendations/models.yaml` | `recommendations check` passes; every checkpoint resolves on Hugging Face |
| 0.8 | Pi packages review from their source: what each needs (Bun?), what it registers, whether child processes would skip the approval gate | DeepSeek | a report (`docs/plans/pi-packages-review.md`) only | The 6.1 table filled in, with file references |
| 0.9 | Skill licences and fit: diagram-design, Ponytail size per skill, the clean-copy paste | DeepSeek | report only | A recommendation per skill |
| 0.10 | `pipkgs.py` and `zest-skillsmith.js` as new files, not yet wired in | Sonnet | new files + tests | Unit tests for install verification and `save_skill` validation |
| 0.11 | The website, first draft | Sonnet (with the design-taste skill) | new `site/`, `.github/workflows/pages.yml` | Builds on Pages from a branch; Lighthouse ≥ 95 mobile |
| 0.12 | **Phase 1 engine core:** `llamacpp.py`, the provider, release picking, router start, `presets.ini` | **Opus** | `llamacpp.py`, `providers/llamacpp/`, `registry.py`, `engine.py`/`lem.py` removal | A clean profile chats on llama.cpp with no Lemonade |

### Wave 1: after the engine core (0.12) merges

| # | Task | Worker | Touches |
| --- | --- | --- | --- |
| 1.1 | Lemonade call-site removal in `server.py`, `hardware.py`, `library.py`, `modelscan.py`, `tuning.py` → `llamatune.py` | Opus, with Sonnet doing `tuning.py`/`llamatune.py` | as listed |
| 1.2 | Migration from 4.x (`migrate5.py`) | Sonnet | new file, tests |
| 1.3 | Estimator fixes 3 and 4 (auto context, MoE offload) | Sonnet | `library.py`, `provbase.py`, `presets.ini` writer |
| 1.4 | API formats: Anthropic Messages, Responses, Gemini in the proxy, stream classifier, per-model `api` in `agent.py` | Sonnet | `gateway.py`, `agent.py:286` |
| 1.5 | Hosted presets (OpenRouter, Nous Portal, OpenCode, Command Code, Gemini) and the Gemini signature test | Haiku writes presets; Sonnet runs the test | `servers.yaml`, tests |
| 1.6 | Wire packages and skillsmith into Pi start-up | Sonnet | `agent.py`, `skills.py` |
| 1.7 | whisper.cpp engine, server side | Sonnet | new `speech.py`, provider |
| 1.8 | LAN server/client: beacon, TLS, pairing, pinning | Opus | new `lan.py`, `gateway.py` listener, `provbase.http_json` |
| 1.9 | Review of every Wave 0 PR not yet reviewed | DeepSeek | none |

1.4 and 1.8 both touch `gateway.py`: 1.4 owns the relay and classifier, 1.8 owns the listener and TLS setup. Merge 1.4 first.

### Wave 2: the UI, one writer on `index.html`, in this order

1. Recommended page, name chain, stale models (2.2, 2.4, 2.5): Sonnet
2. Sources strip, Models page tabs, chat-bar picker, Getting ready question, custom provider form (4.3, 4.4, 4.7): **Opus** (the design-heavy one)
3. Hosted-service cards, "Leaves your computer" chip, LAN pairing screens: Sonnet
4. Attachments and file types (6.2), skills page additions (6.3): Sonnet
5. Vertical tube and portrait creature (8.1): Sonnet
6. Mic button (7a): Haiku, from a written spec

Side work that can run during Wave 2 without touching `index.html`: Bark/read-aloud engine (7b, Sonnet), the copy-pass draft as a list of string changes (8.2, DeepSeek; applied by the UI writer in step 6).

### Wave 3: finish

- Docs rewrite (Phase 11): Haiku drafts, Opus edits.
- Copy pass applied, unslop skill dropped into its slot (you).
- Website content updated to match the shipped features: Sonnet.
- Full test run, an AppImage built on Ubuntu 22.04, release notes: Opus.

### How much runs at once

Wave 0 has 12 independent tasks: 1 Opus, 5 Sonnet, 3 Haiku, 3 DeepSeek. 0.4 waits on 0.3's schema and 0.5's findings. Wave 1 has 9, with one ordering constraint (1.4 before 1.8). Wave 2 is serial on `index.html` by design, with two side tasks alongside.

## Order, and how it ships

| PR | Contents | Depends on |
| --- | --- | --- |
| 1 | Phase 9 (#37), desktop entry, README timing fix | nothing; small, can go out as 4.3.1 |
| 2 | Phase 3 estimator fixes 1, 2 and 5 + kv-log fixtures | nothing (works with Lemonade too) |
| 3 | Phase 1 llama.cpp engine + migration | — |
| 4 | Phase 2 catalog, search, names, stale models | 3 |
| 5 | Phase 3 fixes 3 and 4 (auto context, MoE offload) | 3 |
| 6 | Phase 8.1 vertical tube + 8.3 | nothing; can run alongside 3 |
| 7a | Phase 4.1–4.2: sources API and the process scan | nothing (works with Lemonade too) |
| 7b | Phase 4.3–4.5: chat-bar picker, Models page, gateway | 3, 7a |
| 7c | Phase 4.6–4.7: hosted services (OpenRouter, Nous Portal, OpenCode, Command Code, Gemini; Anthropic Messages, Responses and Gemini formats), set up your own provider | 7b |
| 8 | Phase 6.1 packages + 6.2 file types | nothing |
| 9 | Phase 6.3 self-written skills + 6.4 bundled skills (your unslop skill drops in here) | 8 |
| 10 | Phase 5 server/client mode | 7b |
| 11 | Phase 7 speech | 3 |
| 12 | Phase 8.2 copy pass, Phase 10 brand + site, Phase 11 docs, release 5.0 | all |

PRs 1, 2, 6, 7a and 8 don't depend on the engine swap and can be done in parallel with it.

## Appendix A. Local inference engines on Linux

> **Superseded by `docs/plans/engines-research.md`** (14 Nov 2026), which has sourced values for all 25 engines. Corrections that matter most: llama-server's compiled default port is now **9931** (8080 is only the Docker image and examples); TabbyAPI requires a key by default; LM Studio's current API is `/api/v1/models`; mistral.rs runs as `mistralrs`; Aphrodite is being renamed Sonar. PolyStrata, NInfer and Basalt also document 8080, so eleven engines share that port. The table below is kept for history.

The engines Shoggoth should recognise, roughly by how many people run them. Default ports marked **verify** come from memory or a single source; check each against the engine's docs before writing its preset.

| Engine | Default port | Process name(s) for `match` | Signature for `identify` | Loads models? |
| --- | --- | --- | --- | --- |
| Ollama | 11434 | `ollama` | `GET /api/version` → `{"version"}` | on request (`ollama`) |
| llama.cpp `llama-server` (incl. ik_llama.cpp) | 8080 | `llama-server` | `GET /props` has `default_generation_settings` | router mode: yes; otherwise fixed |
| LM Studio | 1234 | `lms`, `LM Studio`, `lmstudio` | `GET /api/v0/models` answers | on request |
| vLLM | 8000 | `vllm`, cmdline `vllm serve` / `vllm.entrypoints` | `/v1/models` items `owned_by: "vllm"`; `GET /version` | fixed |
| SGLang | 30000 | cmdline `sglang.launch_server` | `GET /get_model_info` | fixed |
| Lemonade | 13305 (newer `lemond`), 8000 (older `lemonade-server`), **verify** | `lemond`, `lemonade-server` | `GET /api/v1/health` with `all_models_loaded` | yes |
| KoboldCpp | 5001 | `koboldcpp` | `GET /api/extra/version` → `{"result": "KoboldCpp"}` | fixed |
| TabbyAPI (ExLlamaV2/V3) | 5000 | cmdline `tabbyAPI`, `main.py` in a `tabbyAPI` folder | `GET /v1/model` (singular) answers | yes (`/v1/model/load`) |
| text-generation-webui | 5000 (API), **verify** | cmdline `server.py --api` in `text-generation-webui` | `GET /v1/internal/model/info` | yes |
| LocalAI | 8080 | `local-ai` | `GET /readyz`; `/v1/models` plus `GET /system` | on request |
| Jan | 1337, **verify** | `jan`, `cortex`, `cortex-server` | `GET /v1/models` with Jan's `engine` field, **verify** | on request |
| llamafile | 8080 | the `.llamafile` name | `GET /props` (llama-server inside) | fixed |
| llama-swap | 8080, **verify** | `llama-swap` | `GET /running` | on request (swaps) |
| RamaLama | 8080 | `ramalama` (spawns `llama-server`) | as llama-server | fixed |
| Docker Model Runner | 12434 | `com.docker.*` / `docker-proxy` | `GET /engines` | on request |
| Hugging Face TGI | 8080 (3000 in some images) | `text-generation-launcher`, `text-generation-router` | `GET /info` has `model_id` | fixed |
| mistral.rs | 1234, **verify** | `mistralrs-server` | `/v1/models` `owned_by` **verify** | fixed |
| Aphrodite Engine | 2242 | cmdline `aphrodite` | as vLLM, `owned_by` **verify** | fixed |
| Xinference | 9997 | `xinference`, `xinference-local` | `GET /v1/cluster/info` | yes |
| exo | 52415 | `exo` | `GET /models` | on request |
| **Strata** | 8080 (confirmed on the dev machine) | `strata` | from its repository | fixed |
| **PolyStrata, Maya, Ninfer, Basalt** | from each repository | from each repository | from each repository | from each repository |

Notes:

- **Nothing on port 8080 can be named by port alone.** Five engines use it. The process scan and signatures do the work.
- **Docker-hosted engines** show up only by signature (see 4.2).
- **The specialized engines** (Strata, PolyStrata, Maya, Ninfer, Basalt) carry `category: specialized`. When the scan can't name one, it shows as **Specialized inference engine** with the explanation in 4.2, and the person names it.
- Values marked **verify** get checked against each project's repository and docs by the Wave 0 research task (0.5), not by running each engine.

## Risks

- **Router-mode API churn.** llama-server's router endpoints are new and still changing. Pin the build, check the shape at start-up (1.1), and update the pin deliberately.
- **glibc on old distros.** The ubuntu builds may need a newer glibc than 22.04 ships. The 1.1 check and the kept loader cover it; test on a 22.04 VM before release.
- **Pi packages running code.** Mitigated by the curated, pinned list (D5). `pi-subagents` is the one most likely to slip past the approval gate; test that first.
- **Cloud models in a privacy-first app.** The chip, the separate picker group and the docs must make it obvious when a chat leaves the machine.
- **LAN pairing.** Rate-limit codes and pin certificates, or a neighbour on the same Wi-Fi can use the models.
