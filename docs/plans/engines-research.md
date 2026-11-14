# Engine research for Appendix A

Sources are each project's own repository or official documentation, read on 2026-11-14. No engine was run or installed.

How to read the citations: a docs URL is the official page that states the value. A repository file is named by path and,
where useful, the symbol that holds the value (`--port` default, a route handler, a response struct). Raw `raw.githubusercontent.com`
fetches do not carry line numbers, so exact line numbers are given only where a docs page or the source itself states a fixed
default; nothing here is presented with a line number I did not read. Values I could not confirm from a primary source are
marked **UNCONFIRMED**.

## Corrections to Appendix A (read this first)

| Appendix A said | What the sources say |
| --- | --- |
| llama.cpp `llama-server` default port 8080 | **9931**. The compiled default changed; the Docker image and most examples still map/publish 8080. |
| TabbyAPI needs no key | **Wrong.** TabbyAPI generates `api_tokens.yml` on first start and authenticates every endpoint unless `disable_auth: true`. |
| Jan `/v1/models` exposes an `engine` field | **Not in the official API docs.** Jan returns only `{"id", "object"}` entries. Treat Jan as unidentifiable by signature; match the `jan` process. |
| LocalAI signature `/readyz` + `/v1/models` + `/system` | Better: `GET /.well-known/localai.json`, which is unauthenticated and LocalAI-specific. |
| LM Studio signature `/api/v0/models` | Current LM Studio (0.4.0+) documents `GET /api/v1/models` (native REST). `/api/v0` is legacy. |
| mistral.rs process `mistralrs-server` | The installed command is `mistralrs` (`mistralrs serve`); `mistralrs-server` is the old/binary name. |
| Aphrodite port 2242, process `aphrodite` | Still 2242 and `aphrodite run`, but the package has been **renamed to Sonar** (PyPI migration notice). |
| llama-swap 8080 **verify** | Confirmed: 8080 is the documented listen address; the container also listens on 8080. |
| Lemonade 8000 (older) | Not confirmed from any current source. Current Lemonade Server is 13305 only. |

---

## 1. Ollama

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `11434` (all examples use `http://localhost:11434`) | https://github.com/ollama/ollama/blob/main/docs/api.md |
| Process name(s) | `ollama` (server binary / service) — name itself **UNCONFIRMED** from this pass, every operation is through the `ollama` command | docs above; install docs |
| Identifying GET (no auth) | `GET /api/version` → `{"version": "<semver>"}`. No other engine in this list answers that path with that shape. | docs/api.md, "Version" |
| Loads models? | On request (Ollama-style). Explicit load = `/api/generate` with empty prompt; unload = same with `"keep_alive": 0`. | docs/api.md, "Load a model" / "Unload a model" |
| Context length | `POST /api/show` → `model_info["<architecture>.context_length"]` (example: `"llama.context_length": 8192`). Loaded context is not reported; `/api/ps` gives size/VRAM only. | docs/api.md, "Show Model Information" and "List Running Models" |
| API key by default | No. Server binds loopback; no auth in the documented API. | docs/api.md |

## 2. llama.cpp `llama-server` (incl. ik_llama.cpp)

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `9931` (`--port PORT ... default: 9931`). Docker examples publish 8080 explicitly. | https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md ("Server-specific params"; "Quick Start": "by default listens on 127.0.0.1:9931") |
| Process name(s) | `llama-server` (`ik_llama-server` for the ik fork) | same README, "Build" (`./build/bin/llama-server`) |
| Identifying GET (no auth) | `GET /v1/models` → `data[0].owned_by == "llamacpp"`. The README's example response states it explicitly. | same README, "GET /v1/models" example |
| Loads models? | Fixed model per process. Router mode (`--models-dir` / `--models-preset`) loads on request, up to `--models-max`. | same README, `--models-*` flags; "router mode" notes |
| Context length | `GET /props` → `default_generation_settings.n_ctx` (the loaded per-slot context). `/v1/models` also has `meta.n_ctx_train` (training context, not the loaded window). | same README, "GET /props" response example |
| API key by default | No (`--api-key` default: none). | same README, `--api-key` |

## 3. LM Studio

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `1234` (`http://localhost:1234/api/v1/...`) | https://lmstudio.ai/docs/developer/rest/list |
| Process name(s) | `lms` (CLI), `LM Studio` (desktop), `llmster` (documented headless service). Engine process names are **UNCONFIRMED**. | https://lmstudio.ai/docs/developer/core/server ("lms server start"); headless docs |
| Identifying GET (no auth unless enabled) | `GET /api/v1/models` → top-level `"models"` array (not OpenAI's `"data"`), entries carry `"publisher"`, `"key"`, `"type"`. The path+shape is LM Studio 0.4.0+ native REST only. | https://lmstudio.ai/docs/developer/rest/list |
| Loads models? | On request, with explicit control: `POST /api/v1/models/load`, `POST /api/v1/models/unload`; idle TTL/auto-evict optional. | https://lmstudio.ai/docs/developer/rest (Supported endpoints) |
| Context length | `GET /api/v1/models` → `models[].loaded_instances[].config.context_length` (loaded) and `models[].max_context_length` (model maximum). | https://lmstudio.ai/docs/developer/rest/list (Response fields / example) |
| API key by default | No: "By default, LM Studio does not require authentication for API requests." Optional API tokens. | https://lmstudio.ai/docs/developer/core/authentication |

## 4. vLLM

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8000` (all examples: `http://localhost:8000/v1`) | https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/ |
| Process name(s) | `vllm` (console script / `vllm serve`); Python process, engine core child processes. Exact engine-core process names **UNCONFIRMED**. | same docs page ("vllm serve ..."); README |
| Identifying GET (no auth) | `GET /version` → JSON version information. (`/v1/models` `owned_by: "vllm"` is in Appendix A but was **not confirmed** in the current `models/serving.py`; treat owned_by as UNCONFIRMED.) | https://docs.vllm.ai/en/latest/serving/online_serving/ ("Basic APIs": `/version`); https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/models/serving.py (`ModelCard(... max_model_len ...)`) |
| Loads models? | Fixed: one base model per server launch. Only LoRA adapters load/unload at runtime (`/v1/load_lora_adapter`, `/v1/unload_lora_adapter`). | docs, "LoRA dynamic loading" |
| Context length | `GET /v1/models` → `data[].max_model_len` (set from `model_config.max_model_len` in `show_available_models`). | `vllm/entrypoints/openai/models/serving.py`, `OpenAIModelRegistry.show_available_models` |
| API key by default | No (`--api-key` / `VLLM_API_KEY` optional; without it `/v1` is open). | docs page, "API key authentication" |

## 5. SGLang

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `30000` (`--port` default `30000`; host default `127.0.0.1`) | https://docs.sglang.io/docs/advanced_features/server_arguments ("HTTP server") |
| Process name(s) | `python3 -m sglang.launch_server` (so the process is `python`/`python3`; no `sglang` daemon binary). A router binary `sglang_router.launch_server` also exists. | same page, "Common launch commands" |
| Identifying GET (no auth) | `GET /get_model_info` → includes `model_path`, `tokenizer_path`, `is_generation`, `model_type`, `architectures`. Path is SGLang-native. | https://docs.sglang.io/docs/basic_usage/native_api ("Get Model Info", exact key set asserted in the example) |
| Loads models? | Fixed: one model per server launch. Weights can be updated from disk (`/update_weights_from_disk`) but not swapped to another model. | native_api, "Update Weights From Disk"; server args |
| Context length | `GET /server_info` → includes "token limits and memory pool sizes"; the exact context key (`context_length`/`max_total_num_tokens`) is **UNCONFIRMED**. | native_api, "Get Server Info" |
| API key by default | No (`--api-key` default `None`; `--admin-api-key` optional). | https://docs.sglang.io/docs/advanced_features/server_arguments ("API related") |

## 6. Lemonade

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `13305` (`http://localhost:13305/v1`). The older 8000 `lemonade-server` port is **UNCONFIRMED**. | https://github.com/lemonade-sdk/lemonade/blob/main/README.md ("Connect Lemonade Server") |
| Process name(s) | `lemond` (server). `lemonade-server` was the older package name; current docs refer to `lemond` and `lemonade` CLI. Exact executable names on Linux **UNCONFIRMED**. | https://github.com/lemonade-sdk/lemonade/blob/main/docs/api/lemonade.md ("`lemond` keeps running") |
| Identifying GET (no auth unless key set) | `GET /v1/models` → `data[].owned_by == "lemonade"` (documented in the model shape). Also `GET /v1/health` reports `"all_models_loaded"` and a Lemonade `version`. | https://github.com/lemonade-sdk/lemonade/blob/main/docs/api/lemonade.md (models register response; `GET /v1/health`) |
| Loads models? | Yes: `POST /v1/load`, `POST /v1/unload`, `POST /v1/pull`; models also load on first use. | lemonade.md, "POST /v1/load" / "POST /v1/unload" |
| Context length | `GET /v1/models` → `data[].context_length` (loaded `ctx_size` else configured else max) and `max_context_window`. | lemonade.md, model object fields under "Response format" |
| API key by default | No; `LEMONADE_API_KEY` optional. When set it is required (Bearer). | lemonade.md, endpoints: "Requires `LEMONADE_API_KEY` when it is set" |

## 7. KoboldCpp

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `5001` (README: web UI and all APIs at `localhost:5001`) | https://github.com/LostRuins/koboldcpp/blob/concedo/README.md ("APIs and integrations") |
| Process name(s) | `koboldcpp` (Linux binary `koboldcpp-linux-x64`; source: `python3 koboldcpp.py`) | same README, "Linux" and "Compiling on Linux" |
| Identifying GET (no auth) | `GET /api/extra/version` → `{"result": "KoboldCpp", "version": "..."}` | KoboldCpp API reference: https://lite.koboldai.net/koboldcpp_api (linked from README) |
| Loads models? | Fixed: one model per process launch (`--model`). | README |
| Context length | `GET /api/extra/true_max_context_length` → number of tokens. (`/api/v1/model` returns the KoboldAI model/version block.) | KoboldCpp API reference, "true_max_context_length" |
| API key by default | No documented key; the server is open on its port. | README / API reference |

## 8. TabbyAPI

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `5000` (`config_sample.yml`: "The port to host on (default: 5000)") | https://github.com/theroyallab/tabbyAPI/blob/main/config_sample.yml |
| Process name(s) | `python main.py` inside the TabbyAPI folder; Docker entrypoint runs `main.py`. No `tabbyAPI` binary. | README (Docker) and wiki 03 Usage |
| Identifying GET (auth required) | `GET /v1/model` (singular) returns the loaded model's card; `GET /props` returns llama.cpp-style properties including `model_path`. Not a uniquely-named engine field, but `/v1/model` is TabbyAPI's own route. | https://github.com/theroyallab/tabbyAPI/wiki/03.-Usage |
| Loads models? | Yes: `/v1/model/load`, `/v1/model/unload`, plus inline loading on a request whose `model` names another folder. | wiki 03 Usage, "Model loading" / "Inline loading" |
| Context length | `GET /props` → `default_generation_settings.n_ctx`; `GET /v1/models` → `data[].max_model_len` (documented as the vLLM/SGLang-compatible field); load request uses `max_seq_len`. | wiki 03 Usage, "API Server" bullets |
| API key by default | **Yes.** "Every call to a TabbyAPI endpoint requires some form of authentication"; `api_tokens.yml` is generated on first start, API vs admin keys. `disable_auth: true` bypasses it. | wiki 03 Usage, "Authentication" |

## 9. text-generation-webui (now TextGen)

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `5000` for the API (`--api-port`; the Gradio UI is `7860`). The 5000 default is **UNCONFIRMED** from a primary default value; every example uses 5000. | https://github.com/oobabooga/text-generation-webui/blob/main/README.md (CLI flags: `--api`, `--api-port`); wiki OpenAI API examples |
| Process name(s) | `python server.py --api` (process `python`); `--nowebui` for API-only. Repo is now `oobabooga/textgen`. | same README |
| Identifying GET (auth unless `--api-key` unset) | `GET /v1/internal/model/info` (model card + `model_name`). Path is text-generation-webui's internal API. | wiki "OpenAI API" / internal endpoints (Appendix A value; **UNCONFIRMED** in this pass) |
| Loads models? | Yes: model load/unload endpoints and `--idle-timeout`; the UI can switch models without restart. | README ("Switch between backends and models without restarting"; `--idle-timeout`) |
| Context length | `GET /v1/internal/model/info` → `truncation_length` / model settings. **UNCONFIRMED** exact key. | wiki |
| API key by default | No (`--api-key` optional; `--admin-key` for load/unload). | README CLI flags |

## 10. LocalAI

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` | https://github.com/mudler/LocalAI/blob/master/README.md (all Docker examples `-p 8080:8080`) |
| Process name(s) | `local-ai` (CLI and server); backends run as child processes/containers named per backend (`llama.cpp`, `vllm`, …). | README (`local-ai run`, `docker start local-ai`) |
| Identifying GET (no auth) | `GET /.well-known/localai.json` → `{"version": "v2.28.0", "endpoints": {...}, "capabilities": {...}}`. Unauthenticated even with database auth/API keys. | https://localai.io/docs/features/api-discovery/ |
| Loads models? | On request: models are configured/installed and load on first use; model management and backend install are API-driven. | README; api-discovery "model-management", "backend-install" |
| Context length | `GET /v1/models/capabilities` → `data[].context_size` (positive model-level value, else default, else 4096). | https://localai.io/docs/features/api-discovery/, "Model Capabilities" |
| API key by default | No for a single-user local instance; authentication is optional (LocalAI 4.9 added deny-by-default modes for multi-user). Discovery endpoints are always keyless. | api-discovery, "Authentication and discovery"; README "Multi-user ready" |

## 11. Jan

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `1337` (`http://127.0.0.1:1337/v1`) | https://docs.jan.ai/desktop/api-preference |
| Process name(s) | `jan` (deb/AppImage desktop app). Local inference runs in the bundled `llama.cpp` engine / `mlx` on Apple Silicon; the engine child process name is **UNCONFIRMED**. | https://github.com/janhq/jan/blob/main/README.md |
| Identifying GET | **No confirmed unique signature.** `GET /v1/models` returns only `{"id", "object"}` — no `engine`, `owned_by`, or version field. | https://docs.jan.ai/desktop/api-preference (`GET /v1/models` response example) |
| Loads models? | On request (models are loaded by the local engine when used; Jan picks the active local model). | https://docs.jan.ai/desktop/api-server |
| Context length | **UNCONFIRMED.** No documented endpoint reports the loaded context window. | docs.jan.ai (no such field in API reference) |
| API key by default | **UNCONFIRMED.** Every documented example sends `Authorization: Bearer YOUR_API_KEY`; the server settings page controls whether a key is required. | api-preference; api-server |

## 12. llamafile

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` (server mode; combined mode serves `http://localhost:8080`) | https://docs.mozilla.ai/llamafile/using-llamafile/running_llamafile |
| Process name(s) | The `.llamafile` file's name (`./Qwen3.5-0.8B-Q8_0.llamafile`), or `llamafile` when run with external weights. | same page |
| Identifying GET (no auth) | `GET /props` (llama-server inside) — returns `model_path`, `build_info`, `default_generation_settings`. **Not unique** to llamafile; process-name matching is the real discriminator. | same page; llama.cpp `/props` docs |
| Loads models? | Fixed: one bundled model per executable. | same page |
| Context length | `GET /props` → `default_generation_settings.n_ctx` (llama-server). | llama.cpp server docs, `/props` |
| API key by default | No (`--api-key` pass-through optional). | docs (CLI flags) |

## 13. llama-swap

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` (`llama-swap --config ... --listen localhost:8080`; container `LLAMA_SWAP_LISTEN=0.0.0.0:8080`) | https://github.com/mostlygeek/llama-swap/blob/main/README.md ("Homebrew Install", "Configuring startup") |
| Process name(s) | `llama-swap` (single Go binary); it spawns whatever `cmd` names, usually `llama-server`. | README, "Building from source" / config |
| Identifying GET (no auth) | `GET /running` → the list of currently running models. llama-swap-specific API. | README, "llama-swap API" (`/running`) |
| Loads models? | On request: swaps the upstream server per `model` value; also `POST /api/models/unload`, `/unload/:model_id`, `ttl` auto-unload. | README, "How does llama-swap work?" and API list |
| Context length | `GET /props?model=<model_id>` → proxied llama-server `default_generation_settings.n_ctx`. (`?model=` is required in llama-swap.) | README, "llama-server supported endpoints" (`/props` requires `?model=`) |
| API key by default | No; API keys are optional config. | README, "API Key support" |

## 14. RamaLama

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` if free, else a random port 8081–8090 (`ramalama serve`) | https://github.com/containers/ramalama/blob/main/README.md (`ramalama-serve`: "The default serving port will be 8080 if available, otherwise a free random port") |
| Process name(s) | `ramalama` (CLI inside the container: `/usr/bin/ramalama`); the actual server is `llama-server` (or vLLM/MLX) inside the rootless container. | README (`podman ps` output shows `/usr/bin/ramalama...`) |
| Identifying GET (no auth) | As `llama-server`: `GET /v1/models` → `data[0].owned_by == "llamacpp"`. No RamaLama-specific signature; process/container name is the discriminator. | llama.cpp server docs; README |
| Loads models? | Fixed: one model per `ramalama serve` invocation. | README `ramalama serve` |
| Context length | `GET /props` → `default_generation_settings.n_ctx` (llama-server backend). | llama.cpp server docs |
| API key by default | No documented key; models run rootless with `--network=none` for `run`, `serve` publishes the port. | README, "Security" |

## 15. Docker Model Runner

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `12434` (`dmr serve` default; Docker Desktop Model Runner also 12434). The dev container example uses 8080/13434 explicitly, not the default. | https://github.com/docker/model-runner/blob/main/README.md ("Standalone (dmr)": "listens on TCP port 12434 by default") |
| Process name(s) | `model-runner` (server), `dmr` (standalone daemon+CLI), `docker-proxy`, and the backend child `com.docker.llama-server`. | README, `dmr serve`, "llama.cpp integration" (`/app/bin/com.docker.llama-server`) |
| Identifying GET (no auth) | `GET /engines` → list of inference engines; DMR-specific route. Exact JSON field (**UNCONFIRMED**) but the path/route is DMR's. | Docker Model Runner API (docs.docker.com/ai/model-runner/); README uses `/engines/llama.cpp/v1/...` |
| Loads models? | On request: `docker model run` / `dmr run` pulls and loads; models are managed (`/models`, `/models/create`, DELETE). | README, "API Examples" |
| Context length | **UNCONFIRMED.** No documented endpoint reports the loaded context window (`/models/{id}` shows model metadata; backend is llama.cpp). | README, docs.docker.com/ai/model-runner/ |
| API key by default | No. | README (no auth in examples) |

## 16. Hugging Face TGI

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` on the host in the official Docker command (`-p 8080:80`; the container serves 80). A native `text-generation-launcher` default (3000 in some images) is **UNCONFIRMED**. | https://github.com/huggingface/text-generation-inference/blob/main/README.md ("Docker") |
| Process name(s) | `text-generation-launcher` (launcher), `text-generation-router` (Rust router), Python `text_generation_server` shards. | README ("text-generation-launcher --help"; architecture) |
| Identifying GET (no auth) | `GET /info` → `{"model_id": "...", "model_sha": ..., "version": ...}`. `model_id` is documented as the served model's id. | TGI OpenAPI/Swagger: https://huggingface.github.io/text-generation-inference (`/info`); Appendix A value |
| Loads models? | Fixed: one model per launcher invocation. | README, `--model-id` |
| Context length | `GET /info` → `max_total_tokens` (and `max_input_length`). | TGI `/info` schema (Swagger) |
| API key by default | No (`--api-key` optional; `HF_TOKEN` only for gated model downloads). | README |

## 17. mistral.rs

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `1234` (`http://localhost:1234/ui`; `mistralrs serve -m ...`) | https://github.com/EricLBuehler/mistral.rs/blob/master/README.md ("Chat in Your Browser") |
| Process name(s) | `mistralrs` (installer command, `mistralrs serve`); old binary name `mistralrs-server`. | README; https://docs.mistralrs.dev/reference/cli/ |
| Identifying GET (no auth) | `GET /v1/models` → entries include `"status"` (`loaded`/`unloaded`/`reloading`), `"max_model_len"`, `"tools_available"`, `"mcp_tools_count"`, `"mcp_servers_connected"`. The `tools_available`/`mcp_*` fields are mistral.rs-specific; `GET /` is `/health`. | https://docs.mistralrs.dev/reference/http-api/ ("Model routing") |
| Loads models? | Fixed model(s) per launch; multiple models can be loaded/unloaded at runtime per docs, but no HTTP load/unload route was documented in this pass — treat as fixed + documented runtime model management. | README ("Multiple models: Load/unload at runtime") |
| Context length | `GET /v1/models` → `data[].max_model_len` ("the most tokens one request can use; loaded models only"). | https://docs.mistralrs.dev/reference/http-api/ |
| API key by default | No: "There is none. The server accepts and ignores `Authorization`." | same page, "Authentication" |

## 18. Aphrodite Engine

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `2242` | https://github.com/lfopensource/aphrodite-engine/blob/main/README.md ("accessed at port 2242"; Docker `-p 2242:2242`) |
| Process name(s) | `aphrodite` (console script, `aphrodite run <model>`) | same README |
| Identifying GET (no auth unless `--api-keys`) | `GET /v1/models` → `owned_by: "aphrodite"` is the expected value but is **UNCONFIRMED**; vLLM-derived code may emit `"vllm"`. Prefer the `aphrodite` process name. | README (vLLM-derived); Appendix A |
| Loads models? | Fixed: one model per `aphrodite run`. | README |
| Context length | `GET /v1/models` → `data[].max_model_len` (inherited vLLM field) — **UNCONFIRMED** for this fork. | vLLM `serving.py`; Aphrodite is built on vLLM |
| API key by default | No (`--api-keys` optional; Docker example passes `sk-empty`). | README |
| Note | PyPI: "Aphrodite has been renamed to Sonar"; the `aphrodite` command now prints migration instructions in new packages. | https://pypi.org/project/aphrodite-engine/ |

## 19. Xinference

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `9997` (`http://127.0.0.1:9997`) | https://github.com/xorbitsai/inference/blob/main/README.md ("Start the server") |
| Process name(s) | `xinference-local` (local server), `xinference` (CLI/worker) | README (Quick start) |
| Identifying GET (auth may be required) | `GET /v1/cluster/info` (Appendix A value). The README documents `GET /v1/models` and the Web UI; the cluster endpoint is **UNCONFIRMED** in current docs. | README; inference.readthedocs.io |
| Loads models? | Yes: models are launched/deployed and can be terminated through the API/SDK (`launch_model`, `terminate_model`). | README ("Launch Model", "Running Models") |
| Context length | **UNCONFIRMED.** Model metadata in the Web UI shows context; no documented OpenAI-compatible field for the loaded window. | README; docs |
| API key by default | Authentication system exists; the README's example creates an API key and sends `Authorization: Bearer`. Whether a fresh local install enforces it by default is **UNCONFIRMED**. | README ("Create a key under API Key Management") |

## 20. exo

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `52415` (`http://localhost:52415`) | https://github.com/exo-explore/exo/blob/main/README.md ("Quick Start", "Run from Source") |
| Process name(s) | `exo` (`uv run exo`; macOS app runs it in the menu bar) | README |
| Identifying GET (no auth) | `GET /models` → model list (entries with `model_id`); `GET /state` gives instance/deployment state. `/models` is exo's native route (Ollama uses `/api/tags`). | README, "Other useful API endpoints" |
| Loads models? | On request: create/delete instances (`POST /instance`, `DELETE /instance/{id}`), `/instance/previews`, `/instance/await`. | README, "Using the API" |
| Context length | **UNCONFIRMED.** No documented endpoint reports the loaded context window (`/state` reports instances, not context). | README |
| API key by default | No. | README |

## 21. Strata (github.com/Niko1221/Strata)

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` (`http://127.0.0.1:8080`) | https://github.com/Niko1221/Strata/blob/main/docs/DETAILS.md ("Quick start", "Using it") |
| Process name(s) | `strata` (Terminal: `strata launch`); foreground `serve/server.py`; engine child `strata` (Qwen engine) and per-family engines (`strata` for Qwen3.8, other engines for DeepSeek/Qwen3.6). | AGENTS.md ("The engine and the server", `serve/`); DETAILS.md |
| Identifying GET (no auth by default) | `GET /props` → exposes chat template, context limit, generation defaults, model path and **engine version** (llama.cpp's `/props` has `build_info`, not `engine_version`). Exact JSON key for the engine version is **UNCONFIRMED**; the docs state the field is present. | DETAILS.md, "Using it" / "Metadata" |
| Loads models? | Fixed one model per server; `POST /load`, `POST /unload` (and `/v1/load`, `/v1/unload`) load/unload that model, with `--lazy`, `idle-unload`, `min-free-vram-mib`. | DETAILS.md, "Sharing the GPU" |
| Context length | `GET /props` → the context limit (llama.cpp-style `default_generation_settings.n_ctx` per Appendix A; exact key **UNCONFIRMED**). `/v1/models` also lists the model "with its context limit". | DETAILS.md, "Using it" (Metadata) |
| API key by default | No. Local loopback by default; `--api-key` / `"api_key"` recommended beyond localhost, `--no-api-key` explicitly disables. | DETAILS.md, "Network access" |

## 22. PolyStrata (github.com/VecSzn/PolyStrata; mirror simplexai-labs/PolyStrata)

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` (app at `http://127.0.0.1:8080`; base URL `/v1`) | https://github.com/VecSzn/PolyStrata/blob/main/README.md ("Using it") |
| Process name(s) | `polystrata.py` (CLI/launcher); engines `strata` (Qwen3.8, unchanged Strata engine) and `strata-poly` (the other eleven models). Vision child `strata-vision`. | README; https://github.com/VecSzn/PolyStrata/blob/main/docs/BACKENDS.md ("What runs", "How a backend works") |
| Identifying GET (no auth by default) | `GET /api/models/current` → the model in use and whether it is loaded. PolyStrata's own registry route. `GET /api/models` → every installed model with `backend`, `context`, `status`. | docs/BACKENDS.md, "Changing the model" (HTTP table) |
| Loads models? | Yes: `POST /api/models/load` (`{"model": "<name>"}`), `POST /api/models/unload`; switching stops one engine before starting the next. | docs/BACKENDS.md, "Changing the model" |
| Context length | `GET /api/models` → `[].context` (the run config's `--max-context`). | docs/BACKENDS.md, "The registry" (field table: `context`) |
| API key by default | No on localhost; `--api-key <secret>` (and host `0.0.0.0`) for other devices. | README ("From your phone or another PC") |

## 23. Maya (github.com/mw00/project-maya)

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` (app and OpenAI base URL `http://127.0.0.1:8080/v1`) | https://github.com/mw00/project-maya/blob/main/README.md ("Quick start", "Using it") |
| Process name(s) | `maya.sh` (launcher), `maya-<model>` engine processes (per-model engine; DeepSeek uses `maya-ds4`), `serve/web` is served by the server. Exact engine binary names are per model. | README ("Each model has an engine built for it", `maya-<model>.json`, `maya-ds4`); docs/SETTINGS.md |
| Identifying GET (no auth by default) | `GET /api/tags` → each model entry carries a `"maya"` object (`title`, `ok`, `missing`, `running`). No other Ollama-compatible engine in this list adds that key. | `serve/model_api.py`, `tags_entry()` |
| Loads models? | Yes: on request a model that is not loaded swaps to it; `/models/load`, `/models/unload`, `/api/pull`, `/api/delete`; `keep_alive` unloads when idle. | README, "Model API"; `serve/model_api.py` |
| Context length | `GET /api/show` → `model_info.context_length` (the config's `context`). | `serve/model_api.py`, `show_entry()` (`"context_length": s.get("context")`) |
| API key by default | No on localhost; `./maya.sh --setup --host 0.0.0.0 --api-key <secret>` for remote. | README ("From another device") |

## 24. NInfer (github.com/Neroued/ninfer)

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` (`http://127.0.0.1:8080/v1/...`; `--port 8080`) | https://github.com/Neroued/ninfer/blob/master/docs/serving.md ("Start the server", endpoints examples) |
| Process name(s) | `ninfer-serve` (server), `ninfer` (CLI), `ninfer-worker` (distributed worker), `ninfer-perplexity`. | https://github.com/Neroued/ninfer/blob/master/README.md ("Product binaries") |
| Identifying GET (no auth by default) | `GET /metrics` → `ninfer_model_info` (also `ninfer_max_context_tokens`, etc.). The `ninfer_*` metric prefix is unique. `GET /health` is unauthenticated and returns `{"status":"ok"}`. | docs/serving.md, "Live metrics" table |
| Loads models? | Fixed: one Engine serves one model; the artifact is chosen at startup. | README ("One Engine serves one model with up to eight active requests") |
| Context length | `GET /v1/models` → `data[].max_model_len` ("configured OpenAI model alias and effective max_model_len"). | docs/serving.md, "Endpoints" |
| API key by default | No (`--api-key` optional; `GET /health` stays unauthenticated). | docs/serving.md, "Authentication and CORS" |

## 25. Basalt (github.com/jesdga95/basalt)

| Item | Value | Source |
| --- | --- | --- |
| Default HTTP port | `8080` (`server.port` default 8080) | https://github.com/jesdga95/basalt/blob/main/docs/configuration/index.md ("Server": `port` default `8080`) |
| Process name(s) | `basalt` (`bin/basalt serve`), plus `basalt-vision`, `basalt-bench`, `basalt-probe`; engine failure log per model. | https://github.com/jesdga95/basalt/blob/main/README.md (Quick start, CMake output) |
| Identifying GET (no auth by default) | `GET /api/logs` → the server's console/engine log feed used by its stats page; Basalt's own route. Exact JSON shape **UNCONFIRMED**, but the path is Basalt-specific (also `/slots`, `/metrics`). | docs/running.md, "The stats page" (`/metrics`, `/slots` and `/api/logs`) |
| Loads models? | Yes: multiple models share one server, one loaded at a time; a request's `model` swaps; `POST /v1/load` loads ahead; `--lazy`, `idle-unload`, `idle-sleep`. | https://github.com/jesdga95/basalt/blob/main/docs/configuration/models.md ("Requests and turns", "What a swap keeps") |
| Context length | **UNCONFIRMED.** `max-context` is a start/config value (default 8192, shared across slots), but no documented GET returns the loaded window. | docs/configuration/index.md ("Context and KV cache") |
| API key by default | No (`api-key` default none; CLI-only; required only when binding `0.0.0.0`). | docs/configuration/index.md ("Server") |

---

## Notes for the presets

- **Port 8080 is now even more crowded**: llama.cpp's *default* is 9931, but llamafile, llama-swap, RamaLama, LocalAI, Docker Model Runner (dev/container), TGI (host), Strata, PolyStrata, Maya, NInfer and Basalt all document 8080. Port alone identifies none of them.
- **Three engines want an API key on a fresh localhost install**: TabbyAPI (generated admin/API keys), and — in multi-user or remote modes — Lemonade (`LEMONADE_API_KEY`), LocalAI and Xinference. For a process scan, probe the unauthenticated discovery route first where one exists (`LocalAI /.well-known/localai.json`, NInfer `/health`, Strata `/health`, Basalt `/health`).
- **Signatures that are genuinely unique** (safe to key `identify` on): Ollama `/api/version`; llama.cpp `/v1/models` `owned_by: "llamacpp"`; LM Studio `/api/v1/models` top-level `models`; Lemonade `owned_by: "lemonade"`; KoboldCpp `/api/extra/version` `result: "KoboldCpp"`; LocalAI `/.well-known/localai.json`; llama-swap `/running`; PolyStrata `/api/models/current`; Maya `/api/tags` `maya` key; NInfer `/metrics` `ninfer_*`; Basalt `/api/logs`.
- **Signatures that are not unique** and should be paired with the process name: llamafile, RamaLama (both are llama-server inside), text-generation-webui, Jan, vLLM/Aphrodite (vLLM-derived shape), SGLang, TGI, mistral.rs, exo.
- **Context reporting is the weakest field.** Confirmed machine-readable paths exist for Ollama (`/api/show`), llama.cpp/llamafile/RamaLama/llama-swap (`/props`), LM Studio (`/api/v1/models`), vLLM (`/v1/models` `max_model_len`), Lemonade (`/v1/models` `context_length`), LocalAI (`/v1/models/capabilities` `context_size`), TabbyAPI (`/props`/`max_model_len`), PolyStrata (`/api/models` `context`), Maya (`/api/show` `context_length`), NInfer (`/v1/models` `max_model_len`). TGI's `/info` reports `max_total_tokens` (documented schema). SGLang, Jan, DMR, Xinference, exo, Strata (key unconfirmed), Basalt and Aphrodite remain partially or wholly unconfirmed.
