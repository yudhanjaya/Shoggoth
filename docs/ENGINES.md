# Engines

An **engine** is a program that loads a model and serves inference. Shoggoth bundles one: [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`, which it runs in router mode. You can connect a server you run yourself, or add an engine Shoggoth starts for you.

Two kinds of file describe engines, and they do different jobs:

- An **engine definition file** (`shoggoth/engines.d/*.yaml`) names an engine and says where to find it and how to recognise it. It is data. It can start, install or run nothing.
- A **provider** (a folder with `provider.yaml`, and `provider.py` only when YAML is not enough) tells Shoggoth how to start, stop, install or update an engine. It is described in the second half of this page.

## Engine definition files

The bundled definitions live in `shoggoth/engines.d/`. A definition of your own goes in `engines.d/` inside Shoggoth's data folder (`~/.local/share/shoggoth/engines.d/`) as `.yaml`, `.yml` or `.json`. One file holds one definition or a list of them. A definition of yours with the same `id` replaces the bundled one; `enabled: false` hides it. Files are read again when any of them changes.

Shoggoth uses definitions in three places:

- **The sources list** (`/ui/sources`) names the servers it found on this computer and the servers you connected.
- **The scan** (`scan.py`) reads `/proc/net/tcp`, asks each listening port `GET /v1/models`, and uses the definitions to say what answered. It only reads and sends GETs; it never starts, stops or looks at another computer.
- **Connect a server** uses the definition's `hint`, `key`, `formats` and `key_url` to show the right form for a service.

A definition cannot carry a command. `engines.py` refuses any key outside its schema, and a `url` that is not `http(s)` or a probe path that is not a plain GET path. A file with mistakes still loads what it can; each bad definition is skipped and reported.

Here is a local engine, `ollama.yaml`:

```yaml
id: ollama
name: Ollama
category: local
summary: Runs models on your computer and loads each one the first time it is asked for.
url: http://127.0.0.1:11434
ports: [11434]
match: [ollama]
identify:
  - {path: /api/version, json: "$.version"}
formats:
  - {api: openai-completions}
loads: ollama
key: none
docs: https://github.com/ollama/ollama
hint: "Ollama's usual address. Start Ollama first, then pull a model with ollama pull."
```

| Setting | What it says |
| --- | --- |
| `id`, `name`, `summary` | A short id (lowercase letters, digits, `-`, `_`, up to 48), the friendly name, and one line. `id` and `name` are needed for an enabled definition. |
| `category` | `local`, `specialized` (built for very large models) or `hosted` (a service online). |
| `url` | The service's usual base address. `http://` or `https://`, with no user name or password in it. |
| `ports` | Ports the engine usually listens on. If `url` names a loopback port and `ports` is missing, the port is taken from the address. |
| `match` | Process names to look for in `/proc`: the program name, or any word of its command line. Case-insensitive. |
| `cwd` | Folder names to look for in the process's working folder. Use this for an engine that only shows `python main.py`, such as TabbyAPI or text-generation-webui. |
| `identify` | Signatures in a reply, checked in order. `path` is a GET path with no query. Add `json` (a path such as `$.data[*].owned_by`) with `equals`, or `equals`/`contains` on a plain-text body (the first 64 KB), or `header` plus `equals`/`contains`. |
| `formats` | The API formats the service speaks, and a regular expression to choose one for a model: `api` is `openai-completions`, `openai-responses`, `anthropic-messages` or `google-generative-ai`; `match` is optional and is tried against the model name. |
| `context` | `{path, json}`: where to read a model's context size, for example `{path: /props, json: "$.default_generation_settings.n_ctx"}`. |
| `loads` | `router` (loads and unloads models on request), `ollama` (loads the first time a model is asked for) or `fixed` (the model was chosen when it started). |
| `key` | `none`, `optional` or `required`. |
| `key_url`, `docs` | Web addresses for where to get a key and the project's documentation. |
| `hint` | One line shown under the address field. |
| `enabled` | `false` hides the definition. |

**No online services are bundled.** Their prices and terms change faster than Shoggoth is released. Put a `category: hosted` definition in your own `engines.d` to have Shoggoth name one, pick its API format per model, and show it in **Connect a server**. Shoggoth reads the folder again when a file changes; nothing has to be rebuilt.

**Check a file before you reload.** This reads the file and starts nothing:

```bash
./Shoggoth-*-x86_64.AppImage engines check ~/.local/share/shoggoth/engines.d/my-engine.yaml
```

It prints `✓` or `✗` for each file, the line number of a mistake, and a close match for a misspelt key. `--json` prints the same report for tools. A bad file does not stop Shoggoth or the other definitions: it is skipped and listed in the Models page's sources strip, with the line to fix.

## Connect a server you already run

Under **Advanced > Engines**, **Connect a server** takes the address of a server you run yourself. Pick a name for the usual address (Ollama `http://127.0.0.1:11434`, LM Studio `http://127.0.0.1:1234`, llama-server `http://127.0.0.1:9931`, Strata, Maya, Unsloth, or **Another server**), add the API key if it has one, press **Test**, then **Connect**. **Probe and connect** asks each preset's usual address on this computer for its model list (`/v1/models`, nothing else) and connects what answers, skipping servers that want a key and ones already connected. An address several presets share (8080) is connected as "Server at 127.0.0.1:8080".

- A connected server is an engine that is only an address (`shoggoth/servers.py`). Its models are named `<server id>/<model>`, so two servers that both have `llama3` stay apart. Saved servers live in the `servers` setting.
- Shoggoth never installs, starts, stops, updates, tunes or frees memory for it. If it shares a graphics card with Shoggoth's llama.cpp, you manage that.
- The address must start with `http://` or `https://` and carry no user name or password. A trailing `/v1` is dropped. `localhost` and `127.0.0.1` count as the same address.
- The key is kept in Shoggoth's settings file and is never sent back to the page; the page sees only its last four characters.
- Shoggoth does not ship setup for Strata or Maya. The names are labels and default addresses (`shoggoth/engines.d/`), nothing more.

## An engine Shoggoth starts: the provider folder

A **provider** is one folder containing `provider.yaml`. It tells Shoggoth how to start the engine, how to tell it is ready, and where it serves. For any provider engine, Shoggoth:

- lists it under **Advanced > Engines** and detects an instance that is already running;
- starts it when one of its models is selected, after freeing memory held by conflicting engines;
- stops it after an idle period; and
- lists its models in the **Library** alongside those of other engines.

### Ten minutes, start to finish

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
  ✓ provider.yaml reads. Every setting has the right shape (id llama-server, "llama.cpp server").
  · Found its program at /opt/llama.cpp/llama-b11273/llama-server.
  · Before it runs, Shoggoth will ask the person to allow it to: start a program on your computer (llama-server).
  ·   run.start: /opt/llama.cpp/llama-b11273/llama-server --model {model_file} --alias {model} --host {host} --port {port} …
Shoggoth will load this engine.
```

A `✗` is a problem that stops Shoggoth loading the engine. It says which setting and why. A `!` is something that loads but is probably not what you meant (a misspelt setting comes with "did you mean…?"). A `·` is information. The exit code is 1 when there is a `✗`, so you can use it in a script; `--json` prints the same thing for tools.

**4. Reload.** Open **Advanced > Engines**, press **Reload engines**. The card appears, with a box that says exactly what the engine will run and an **Allow it** button. Shoggoth only asks once per version of the files: change the YAML and it asks again.

**5. Use a model.** Your engine is added after Shoggoth's own llama.cpp, so llama.cpp gets the models both can run. To send one kind of model to your engine, name it in `prefer_architectures` (see below). Pick the model in the chat's model picker. Shoggoth starts `llama-server` on a free port, waits for `/health`, and chats through it. It stops it again when you pick a model another engine runs, or after 20 idle minutes.

### The file, setting by setting

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

**Commands are lists of words**, never one shell string, so there is no `|`, `&&`, redirect or `$VAR`. The words you may use in commands and addresses are `{bin}` `{host}` `{port}` `{context}` `{model}` `{model_file}` `{library}` `{data}`, plus `{preset}` `{family}` `{name}` `{profile}` `{key}` for engines that need them. Anything else is an error. `providers check` says which word.

`{context}` is the context window Shoggoth wants, in tokens (128k by default). A model is started once at that size. To change it, reload the model from the chat bar.

### Rules Shoggoth follows

- **One at a time.** Unless `capabilities.multi_model: true`, starting a second model on your engine stops the first.
- **Memory.** Before it starts a model, Shoggoth stops what it started in any other engine of the same `memory.group` (if either is `exclusive`), unless that engine is answering right now, in which case it says so and waits for you. It never stops something you started yourself: if your engine needs the memory to itself, that is an error with the plain reason. Shoggoth doesn't measure free memory for a YAML-only engine, so one that is too big for the card fails when the engine does.
- **What it never does.** Stop an engine it didn't start, or update it.
- **Trust.** Engines that come with Shoggoth are trusted. One you add must be allowed once. Shoggoth remembers the allowed files by their fingerprint, so a changed file asks again. The box shows the real commands, with `{bin}` replaced by the real program. An engine that only points at an address that is already running needs no approval. A folder that fails to load is skipped and shown on the Engines page with the line to fix; it never stops Shoggoth or the other engines.

### What a YAML-only engine can't do

Some settings exist in the files of Shoggoth's own engines but are acted on only by an engine with a `provider.py`: `install` (download and run an installer, with a checksum), `update.apply`, `models.download` (the engine downloads for itself), `models.setup`, and `capabilities.tuning`. A YAML-only engine can be found, started, stopped, listed, chatted with, and checked for updates by command, but not installed or tuned by Shoggoth. Plain GGUF downloads are done by Shoggoth itself, whichever engine later runs the file.

### When YAML isn't enough: `provider.py`

Add a `provider.py` next to the YAML with `class Provider(provbase.GenericProvider)` (or `provbase.Base` to do everything yourself) and override only what the YAML can't say. `shoggoth/provbase.py` documents every method; the shortest real example is `shoggoth/providers/llamacpp/provider.py`. The interface in one line each:

- `detect()`, `status()`: what is there, and what the card shows.
- `models()`, `chat_models()`, `endpoint()`, `ensure(model_id, progress, opts)`, `stop()`: serving.
- `model_id(entry)`, `can_run(entry)`, `need_gb(entry)`, `fit(entry, machine)`, `library_models()`, `model_folders()`: how library models map onto your engine.
- `install(progress)`, `update_check()`, `update(progress)`, `update_status()`: keeping it current.
- `activity()`, `owned_instance()`, `idle_minutes()`: so Shoggoth can stop it when idle. It does so only if it started it.
- `make_way()`: free memory for another engine. Defaults to `stop()`; return `(False, why)` when what holds the memory isn't Shoggoth's.
- `tune_plan()`, `tune_run()`: Advanced > Tuning.

A step an engine can't do raises `NotImplementedError` and Shoggoth shows it as unavailable. A `provider.py` runs inside Shoggoth with your permissions, so Shoggoth names that in the box you must allow.

### Testing your engine

`providers check` covers the file. To test the behaviour, copy the approach in `tests/test_providers.py`: a YAML-only fake engine that starts `tests/fixtures/openai_server.py` (a small OpenAI-style server), `registry.approve(id)`, then `ensure`, `models`, `stop`. Run all tests with `python3 -m unittest discover tests`.

`providers list` shows every engine Shoggoth has loaded and every folder it could not. `providers doctor` asks the engines that are installed and running the questions Shoggoth asks and checks the answers still have the fields Shoggoth reads. An engine written in Python can answer it by defining `selftest()`.
