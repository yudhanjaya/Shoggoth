# Add an engine in ten minutes

An **engine** is a program that loads a model and answers questions with it. Zest ships with two ([Lemonade](https://lemonade-server.ai) for small and medium models, [Strata](https://github.com/Niko1221/Strata) for very large ones), and you can add your own. An engine is **one folder with one YAML file**. You do not touch Zest's code.

Zest does the rest for any engine you add: it shows it under **Advanced > Engines**, finds it if it is already running, starts it when you pick one of its models, frees the memory of whatever else was running first, stops it after it has been idle, and lists its models in the **Library** next to everyone else's.

## Ten minutes, start to finish

This builds an engine around `llama-server` (the program that comes with [llama.cpp](https://github.com/ggml-org/llama.cpp)). You need `llama-server` on your computer and one `.gguf` model.

**1. Make the folder.** In a terminal (from a source checkout, use `python3 zest/server.py` in place of the AppImage):

```bash
./Zest-*-x86_64.AppImage providers new llama-server --from llama-server
```

That copies a finished example into your engines folder (`~/.local/share/zest/providers/llama-server/provider.yaml`). Leave off `--from llama-server` to start from the blank template, `providers new my-engine`.

**2. Tell Zest where the program is.** The example looks for `llama-server` on your `PATH`, then in `~/.local/bin` and `~/llama.cpp/build/bin`. If yours is somewhere else:

```bash
export ZEST_LLAMA_SERVER=/opt/llama.cpp/llama-b11273/llama-server
```

**3. Check it.** This never starts anything:

```bash
./Zest-*-x86_64.AppImage providers check ~/.local/share/zest/providers/llama-server
```

```
Checking /home/you/.local/share/zest/providers/llama-server
  ✓ provider.yaml reads, and every setting has the right shape (id llama-server, "llama.cpp server").
  · Found its program at /opt/llama.cpp/llama-b11273/llama-server.
  · Before it runs, Zest will ask the person to allow it to: start a program on your computer (llama-server).
  ·   run.start: /opt/llama.cpp/llama-b11273/llama-server --model {model_file} --alias {model} --host {host} --port {port} …
Zest will load this engine.
```

A `✗` is a problem that stops Zest loading the engine, and says which setting and why. A `!` is something that loads but is probably not what you meant (a misspelt setting comes with "did you mean…?"). A `·` is information. The exit code is 1 when there is a `✗`, so you can use it in a script; `--json` prints the same thing for tools.

**4. Reload.** Open **Advanced > Engines**, press **Reload engines**. The card appears, with a box that says exactly what the engine will run and an **Allow it** button. Zest only asks once per version of the files: change the YAML and it asks again.

**5. Use a model.** `llama-server` is added after Lemonade, so Lemonade gets the models both can run. To send one kind of model to your engine, name it in `prefer_architectures` (see below), or just run Zest without Lemonade. Pick the model in the chat's model picker. Zest starts `llama-server` on a free port, waits for `/health`, and chats through it. It stops it again when you pick a model another engine runs, or after 20 idle minutes.

## The file, setting by setting

Everything except `id`, `name` and `api_version: 1` is optional, but a managed engine needs a way to start, a way to tell it is ready, and an address.

| Setting | What it says |
| --- | --- |
| `id`, `name`, `summary` | A short id (letters, digits, `-`, `_`), the friendly name, and one line. |
| `tier` | `small-medium` (the usual) or `large` (needs lots of RAM, starts slowly). A `large` engine is where Large-tier models are sent, when it can run them. |
| `detect.binaries` | Program names to look for: on `PATH`, or full paths. `~` and `$ENV_VARS` work; an unset variable is skipped. |
| `detect.ports`, `detect.health` | Where an already-running copy might listen, and the path that answers `200` when it is really this engine. Add `expect: {key: value}` to check the reply too. A copy found this way is called **yours**: Zest uses it and never stops it. |
| `run.mode` | `managed` (Zest starts it), `service` (already running, Zest only talks to it) or `external` (an address on another computer; the address is `endpoint.base_url`). |
| `run.start`, `run.stop` | The command to start it, and (optionally) one to stop it. Zest also ends the process it started. |
| `run.ready` | `{path: /health, timeout: 120}`: how Zest knows it is ready. |
| `run.port_range` | Ports Zest may start it on, like `8090-8099`. |
| `endpoint.base_url` | What OpenAI-style programs use, like `http://127.0.0.1:{port}/v1`. |
| `endpoint.models` | `{path, id, context}`: where to read the model list, and JSON paths such as `$.data[*].id` for the names and (optionally) each model's context size. |
| `models.formats`, `architectures`, `excluded_architectures` | What it can run. Leave `architectures` out to mean "any". |
| `models.prefer_architectures` | Models of these architectures go to this engine even when another could run them too. |
| `models.serve` | `file`: Zest hands the engine each library model as `{model_file}`, named after its file. Without it, `{model}` is a name the engine already knows. |
| `models.data_dir` | `{library}/my-engine`: the subfolder of the Models folder that holds models this engine keeps itself. |
| `memory.group`, `memory.exclusive` | Engines in the same group don't hold memory together if `exclusive`. Use `gpu` for anything that fills a graphics card. |
| `memory.idle_stop_minutes` | Frees the memory after this long without work. Only applies to something Zest started. |
| `memory.start_estimate` | `{seconds: 30}`: how long to tell the person to expect. |
| `capabilities.activity` | `{path: /slots, busy: "$[*].is_processing"}`: how Zest can tell the engine is working. **Without it Zest never stops the engine by the clock**, because stopping something that is answering would be worse than leaving it running. |

**Commands are lists of words**, never one shell string, so there is no `|`, `&&`, redirect or `$VAR`. The words you may use in commands and addresses are `{bin}` `{host}` `{port}` `{context}` `{model}` `{model_file}` `{library}` `{data}`, plus `{preset}` `{family}` `{name}` `{profile}` `{key}` for engines that need them. Anything else is an error, and `providers check` says which word.

`{context}` is the context window Zest wants, in tokens (128k by default). A model is started once at that size. To change it, reload the model from the chat bar.

## Rules Zest follows

- **One at a time.** Unless `capabilities.multi_model: true`, starting a second model on your engine stops the first.
- **Memory.** Before it starts a model, Zest stops what it started in any other engine of the same `memory.group` (if either is `exclusive`), unless that engine is answering right now, in which case it says so and waits for you. It never stops something you started yourself: if your engine needs the memory to itself, that is an error with the plain reason. Zest doesn't measure free memory for a YAML-only engine, so one that is too big for the card fails when the engine does.
- **What it never does.** Stop an engine it didn't start, or update it.
- **Trust.** Engines that come with Zest are trusted. One you add must be allowed once, and Zest remembers the allowed files by their fingerprint, so a changed file asks again. The box shows the real commands, with `{bin}` replaced by the real program. An engine that only points at an address that is already running needs no approval. A folder that fails to load is skipped and shown on the Engines page with the line to fix; it never stops Zest or the other engines.

## What a YAML-only engine can't do

Some settings exist in the files of Zest's own engines but are acted on only by an engine with a `provider.py`: `install` (download and run an installer, with a checksum), `update.apply`, `models.download` (the engine downloads for itself), `models.setup`, and `capabilities.tuning`. A YAML-only engine can be found, started, stopped, listed, chatted with, and checked for updates by command, but not installed or tuned by Zest. Plain GGUF downloads are done by Lemonade, whichever engine later runs the file.

## When YAML isn't enough: `provider.py`

Add a `provider.py` next to the YAML with `class Provider(provbase.GenericProvider)` (or `provbase.Base` to do everything yourself) and override only what the YAML can't say. `zest/provbase.py` documents every method; the shortest examples are the two real ones, in `zest/providers/lemonade/provider.py` and `zest/providers/strata/provider.py`. The interface in one line each:

- `detect()`, `status()`: what is there, and what the card shows.
- `models()`, `chat_models()`, `endpoint()`, `ensure(model_id, progress, opts)`, `stop()`: serving.
- `model_id(entry)`, `can_run(entry)`, `need_gb(entry)`, `fit(entry, machine)`, `library_models()`, `model_folders()`: how library models map onto your engine.
- `install(progress)`, `update_check()`, `update(progress)`, `update_status()`: keeping it current.
- `activity()`, `owned_instance()`, `idle_minutes()`: so Zest can stop it when idle, and only if it started it.
- `make_way()`: free memory for another engine. Defaults to `stop()`; return `(False, why)` when what holds the memory isn't Zest's.
- `tune_plan()`, `tune_run()`: Advanced > Tuning.

A step an engine can't do raises `NotImplementedError` and Zest shows it as unavailable. A `provider.py` runs inside Zest with your permissions, so Zest names that in the box you must allow.

## Testing your engine

`providers check` covers the file. To test the behaviour, copy the approach in `tests/test_providers.py`: a YAML-only fake engine that starts `tests/fixtures/openai_server.py` (a small OpenAI-style server), `registry.approve(id)`, then `ensure`, `models`, `stop`. Run all tests with `python3 -m unittest discover tests`.

`providers list` shows every engine Zest has loaded and every folder it could not. `providers doctor` asks the engines that are installed and running the questions Zest asks and checks the answers still have the fields Zest reads. An engine written in Python can answer it by defining `selftest()`.
