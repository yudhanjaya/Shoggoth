# Add an engine in ten minutes

An **engine** is a program that loads a model and serves inference. Shoggoth bundles two: [Lemonade](https://lemonade-server.ai) for small and medium models, and [Strata](https://github.com/Niko1221/Strata) for very large ones. Additional engines are defined by **one folder containing one YAML file**, with no change to Shoggoth's source.

For any registered engine, Shoggoth:

- lists it under **Advanced > Engines** and detects an instance that is already running;
- starts it when one of its models is selected, after freeing memory held by conflicting engines;
- stops it after an idle period; and
- lists its models in the **Library** alongside those of other engines.

## Ten minutes, start to finish

This builds an engine around `llama-server` (the program that comes with [llama.cpp](https://github.com/ggml-org/llama.cpp)). You need `llama-server` on your computer and one `.gguf` model.

**1. Make the folder.** In a terminal (from a source checkout, use `python3 shoggoth/server.py` in place of the AppImage):

```bash
./Shoggoth-*-x86_64.AppImage providers new llama-server --from llama-server
```

That copies a finished example into your engines folder (`~/.local/share/shoggoth/providers/llama-server/provider.yaml`). Leave off `--from llama-server` to start from the blank template, `providers new my-engine`.

**2. Tell Shoggoth where the program is.** The example looks for `llama-server` on your `PATH`, then in `~/.local/bin` and `~/llama.cpp/build/bin`. If yours is somewhere else:

```bash
export SHOGGOTH_LLAMA_SERVER=/opt/llama.cpp/llama-b11273/llama-server
```

**3. Check it.** This never starts anything:

```bash
./Shoggoth-*-x86_64.AppImage providers check ~/.local/share/shoggoth/providers/llama-server
```

```
Checking /home/you/.local/share/shoggoth/providers/llama-server
  ✓ provider.yaml reads, and every setting has the right shape (id llama-server, "llama.cpp server").
  · Found its program at /opt/llama.cpp/llama-b11273/llama-server.
  · Before it runs, Shoggoth will ask the person to allow it to: start a program on your computer (llama-server).
  ·   run.start: /opt/llama.cpp/llama-b11273/llama-server --model {model_file} --alias {model} --host {host} --port {port} …
Shoggoth will load this engine.
```

A `✗` is a problem that stops Shoggoth loading the engine, and says which setting and why. A `!` is something that loads but is probably not what you meant (a misspelt setting comes with "did you mean…?"). A `·` is information. The exit code is 1 when there is a `✗`, so you can use it in a script; `--json` prints the same thing for tools.

**4. Reload.** Open **Advanced > Engines**, press **Reload engines**. The card appears, with a box that says exactly what the engine will run and an **Allow it** button. Shoggoth only asks once per version of the files: change the YAML and it asks again.

**5. Use a model.** `llama-server` is added after Lemonade, so Lemonade gets the models both can run. To send one kind of model to your engine, name it in `prefer_architectures` (see below), or just run Shoggoth without Lemonade. Pick the model in the chat's model picker. Shoggoth starts `llama-server` on a free port, waits for `/health`, and chats through it. It stops it again when you pick a model another engine runs, or after 20 idle minutes.

## The file, setting by setting

Everything except `id`, `name` and `api_version: 1` is optional, but a managed engine needs a way to start, a way to tell it is ready, and an address.

| Setting | What it says |
| --- | --- |
| `id`, `name`, `summary` | A short id (letters, digits, `-`, `_`), the friendly name, and one line. |
| `tier` | `small-medium` (the usual) or `large` (needs lots of RAM, starts slowly). A `large` engine is where Large-tier models are sent, when it can run them. |
| `detect.binaries` | Program names to look for: on `PATH`, or full paths. `~` and `$ENV_VARS` work; an unset variable is skipped. |
| `detect.ports`, `detect.health` | Where an already-running copy might listen, and the path that answers `200` when it is really this engine. Add `expect: {key: value}` to check the reply too. A copy found this way is called **yours**: Shoggoth uses it and never stops it. |
| `run.mode` | `managed` (Shoggoth starts it), `service` (already running, Shoggoth only talks to it) or `external` (an address on another computer; the address is `endpoint.base_url`). |
| `run.start`, `run.stop` | The command to start it, and (optionally) one to stop it. Shoggoth also ends the process it started. |
| `run.ready` | `{path: /health, timeout: 120}`: how Shoggoth knows it is ready. |
| `run.port_range` | Ports Shoggoth may start it on, like `8090-8099`. |
| `endpoint.base_url` | What OpenAI-style programs use, like `http://127.0.0.1:{port}/v1`. |
| `endpoint.models` | `{path, id, context}`: where to read the model list, and JSON paths such as `$.data[*].id` for the names and (optionally) each model's context size. |
| `models.formats`, `architectures`, `excluded_architectures` | What it can run. Leave `architectures` out to mean "any". |
| `models.prefer_architectures` | Models of these architectures go to this engine even when another could run them too. |
| `models.serve` | `file`: Shoggoth hands the engine each library model as `{model_file}`, named after its file. Without it, `{model}` is a name the engine already knows. |
| `models.data_dir` | `{library}/my-engine`: the subfolder of the Models folder that holds models this engine keeps itself. |
| `memory.group`, `memory.exclusive` | Engines in the same group don't hold memory together if `exclusive`. Use `gpu` for anything that fills a graphics card. |
| `memory.idle_stop_minutes` | Frees the memory after this long without work. Only applies to something Shoggoth started. |
| `memory.start_estimate` | `{seconds: 30}`: how long to tell the person to expect. |
| `capabilities.activity` | `{path: /slots, busy: "$[*].is_processing"}`: how Shoggoth can tell the engine is working. **Without it Shoggoth never stops the engine by the clock**, because stopping something that is answering would be worse than leaving it running. |

**Commands are lists of words**, never one shell string, so there is no `|`, `&&`, redirect or `$VAR`. The words you may use in commands and addresses are `{bin}` `{host}` `{port}` `{context}` `{model}` `{model_file}` `{library}` `{data}`, plus `{preset}` `{family}` `{name}` `{profile}` `{key}` for engines that need them. Anything else is an error, and `providers check` says which word.

`{context}` is the context window Shoggoth wants, in tokens (128k by default). A model is started once at that size. To change it, reload the model from the chat bar.

## Rules Shoggoth follows

- **One at a time.** Unless `capabilities.multi_model: true`, starting a second model on your engine stops the first.
- **Memory.** Before it starts a model, Shoggoth stops what it started in any other engine of the same `memory.group` (if either is `exclusive`), unless that engine is answering right now, in which case it says so and waits for you. It never stops something you started yourself: if your engine needs the memory to itself, that is an error with the plain reason. Shoggoth doesn't measure free memory for a YAML-only engine, so one that is too big for the card fails when the engine does.
- **What it never does.** Stop an engine it didn't start, or update it.
- **Trust.** Engines that come with Shoggoth are trusted. One you add must be allowed once, and Shoggoth remembers the allowed files by their fingerprint, so a changed file asks again. The box shows the real commands, with `{bin}` replaced by the real program. An engine that only points at an address that is already running needs no approval. A folder that fails to load is skipped and shown on the Engines page with the line to fix; it never stops Shoggoth or the other engines.

## What a YAML-only engine can't do

Some settings exist in the files of Shoggoth's own engines but are acted on only by an engine with a `provider.py`: `install` (download and run an installer, with a checksum), `update.apply`, `models.download` (the engine downloads for itself), `models.setup`, and `capabilities.tuning`. A YAML-only engine can be found, started, stopped, listed, chatted with, and checked for updates by command, but not installed or tuned by Shoggoth. Plain GGUF downloads are done by Lemonade, whichever engine later runs the file.

## When YAML isn't enough: `provider.py`

Add a `provider.py` next to the YAML with `class Provider(provbase.GenericProvider)` (or `provbase.Base` to do everything yourself) and override only what the YAML can't say. `shoggoth/provbase.py` documents every method; the shortest examples are the two real ones, in `shoggoth/providers/lemonade/provider.py` and `shoggoth/providers/strata/provider.py`. The interface in one line each:

- `detect()`, `status()`: what is there, and what the card shows.
- `models()`, `chat_models()`, `endpoint()`, `ensure(model_id, progress, opts)`, `stop()`: serving.
- `model_id(entry)`, `can_run(entry)`, `need_gb(entry)`, `fit(entry, machine)`, `library_models()`, `model_folders()`: how library models map onto your engine.
- `install(progress)`, `update_check()`, `update(progress)`, `update_status()`: keeping it current.
- `activity()`, `owned_instance()`, `idle_minutes()`: so Shoggoth can stop it when idle, and only if it started it.
- `ui(method, action, arg)`: the page's own calls to this engine, served at `/ui/providers/<id>/<action>`. Return `(status, payload)`, or `None` for an action the engine does not have. Strata and Lemonade use it for their setup, start, stop, update, backend and config actions; `provbase.HOST` lends them the app's setup mode and version.
- `make_way()`: free memory for another engine. Defaults to `stop()`; return `(False, why)` when what holds the memory isn't Shoggoth's.
- `release()`: free memory but keep the process, whoever started it. The router calls it for an engine Shoggoth did not start only when that engine's authority (Advanced, with Expert Mode on) is Allowed, or the person answered an Ask. Default: `stop()` (which only works on what Shoggoth started). Never signal a found process.
- `tune_plan()`, `tune_run()`: Advanced > Tuning.

A step an engine can't do raises `NotImplementedError` and Shoggoth shows it as unavailable. A `provider.py` runs inside Shoggoth with your permissions, so Shoggoth names that in the box you must allow.

## Testing your engine

`providers check` covers the file. To test the behaviour, copy the approach in `tests/test_providers.py`: a YAML-only fake engine that starts `tests/fixtures/openai_server.py` (a small OpenAI-style server), `registry.approve(id)`, then `ensure`, `models`, `stop`. Run all tests with `python3 -m unittest discover tests`.

`providers list` shows every engine Shoggoth has loaded and every folder it could not. `providers doctor` asks the engines that are installed and running the questions Shoggoth asks and checks the answers still have the fields Shoggoth reads. An engine written in Python can answer it by defining `selftest()`.

## Maya

`providers/maya/` is bundled and marked `expert: true`, so it is offered only while Expert Mode is on (any provider YAML may say this). Shoggoth does
not install Maya. It finds a running one (its `/health` names `model_label` or `version` and has no `service` field, which is how it is told from
Strata), swaps its model with `POST /models/load`, frees memory with `POST /models/unload` (409 while it is answering; 403 when its model
management is off, which Shoggoth reports and does not work around), and reads `/slots` for activity. A Maya that is not running is started only from a
launch script the person gives under `POST /ui/providers/maya/launch`; saving it is the approval, and a changed script is not approved. A Maya Shoggoth
did not start is never signalled. The tests run against `tests/fixtures/fake_maya.py`, written from the plan's description of the API: no recording of
a real Maya was available, so the contract against a real one is still to be checked on the two-GPU machine.

## From a terminal

`shoggoth serve` is the server with no browser tab and no quit-when-the-tab-closes; it prints the page and gateway addresses. `shoggoth status [--json]`
reads engines, loaded models, the gateway and requests in flight (exit 1 when the gateway is down, 2 when no Shoggoth answers). `shoggoth connect`
prints the base URL, a masked key (`--reveal` shows it), the model names and a `curl` line. Both use `client.py`, which calls the same `/ui/*` routes
as the page with the secret from the config folder.
