# Zest

**Run AI models on your own computer, with one download.** Zest is a friendly interface for [Lemonade](https://lemonade-server.ai), the open-source engine that runs language models locally. The Zest AppImage **includes Lemonade**.

The idea is to provide an user interface that people who aren't necessarily steeped in technical know-how can still navigate to get open-weights models running on their own hardware. Download Zest, and you're good to go. 

![Zest chat](docs/screenshots/chat.png)

## Getting started

1. **Download** `Zest-<version>-x86_64.AppImage` from the [latest release](../../releases/latest).
2. **Run it:**

   ```bash
   chmod +x Zest-*-x86_64.AppImage
   ./Zest-*-x86_64.AppImage
   ```

3. **Follow the welcome steps.** Zest opens in your browser and walks you through four short steps in plain language, one screen at a time:

   | Step | What happens |
   | --- | --- |
   | **Helper** | Zest starts its own private copy of Lemonade. Nothing to choose. |
   | **Your computer** | Zest looks at your graphics card and downloads the program that suits it (CUDA for NVIDIA, Vulkan for most cards, the processor as a fallback). One button. |
   | **A model** | Three suggestions that fit your computer, labelled Quick, Balanced and Thorough. Quick is LFM2.5 8B A1B (fast, to the point, good with tools, happy on almost any computer), Balanced is Qwen3.6 35B A3B MTP and Thorough is Gemma 4 31B MTP, each only if it fits; otherwise Zest picks by size. Press Download on one. |
   | **Chat** | Press Start chatting. |

   People who already have models, or run their own Lemonade, can skip to **Advanced > Setup guide**, which keeps the full set of choices: where models are stored, models you already have (LM Studio, Ollama, llama.cpp and others are found for you), and Lemonade updates.

The full setup page lives on as **Advanced > Setup guide**. That is where you update Lemonade, add a backend, or change where models live. **Help > Run the welcome steps again** repeats the short tour.

### Requirements

- **Linux, x86_64.** The built-in Lemonade needs a reasonably recent system (glibc 2.38 or newer: Ubuntu 24.04, Debian 13, Fedora 39 or later). On an older system Zest tells you so and you can connect it to a Lemonade you install yourself instead.
- A few common system libraries that almost every desktop Linux already has (OpenSSL 3, zstd, libdrm). If one is missing, Zest names it.
- For GPU use, your graphics driver. Zest and Lemonade download everything else.
- Internet access for the first-run downloads (the backend and your models).

If your system has no FUSE support, run `./Zest-*-x86_64.AppImage --appimage-extract-and-run` instead. Closing the browser tab quits Zest (and its Lemonade) after about three minutes; launching it again while it's running just reopens the page.

## Keeping Lemonade up to date

The AppImage ships with a recent Lemonade, but Lemonade moves quickly, so Zest can update its own copy without you downloading a new Zest.

- Press **Check for Lemonade updates** on the Setup guide page (Advanced > Setup guide). If a newer stable release exists, one button downloads it, **checks it against the checksum GitHub publishes for that file**, installs it and offers to restart Lemonade onto it.
- Zest can also look for an update when it opens and put a dot on **Advanced** if there is one. You can switch that off with the checkbox.
- Updates are kept in your own data folder, because the AppImage itself is read-only. The version that came with Zest is never touched, so **"Go back to the version that came with Zest"** is always one click away.
- Updating Zest itself is just downloading a newer AppImage.

## Models

Lemonade handles models; Zest gives you the choices:

- **Where they live.** The default is the **Hugging Face cache** (`~/.cache/huggingface/hub`, or wherever `HF_HOME` points), so downloads are shared with other AI tools. Or choose **any folder** as the place new downloads go.
- **Models you already have.** **Look on this computer** checks the usual places (LM Studio, llama.cpp's cache, GPT4All, Jan, Ollama, `~/models`, `~/Downloads`) and lists what it finds. Tick what you want, or add any folder yourself. **Nothing is copied**: Zest gives Lemonade a folder of links to your files, so there's no extra disk use and your originals stay where they are.
- **Downloading.** Press **Get it** on any model, or **Get from Hugging Face** to paste a link to a GGUF model and choose a version. It runs in the background with a progress bar, and you can keep using Zest. Zest's suggestions are the models that fit your hardware's free memory.

## Features

Zest is organised around four places in the left-hand menu: **Chat**, **Models**, **Help** and **Advanced**. Everything a newcomer needs is in the first three; everything technical sits behind Advanced.

- **Chat** with streaming replies, markdown and code blocks with copy buttons, a collapsible "how it thought" panel for reasoning models, and everyday starter questions. Conversations are kept in your browser.
- **Speed** While a reply is being written, the chat shows the phase (**Loading the model**, **Reading your message**, **Thinking**, **Writing**), a live tokens-per-second figure, a plain word for it (**Very fast**, **Fast**, **Comfortable reading speed**, **Slow**, **Very slow**; about 5 tokens a second is a comfortable reading pace) and a small graph of the speed through the answer. When it finishes, the footer keeps the speed and graph, the tokens, the time to the first token, how fast the model read your message, which model and backend answered, and how much of the context window the conversation uses (for example "Conversation: 34k of 128k"). The status pill at the top shows the live speed too, including for other apps using the Server (marked with a ~ because it is estimated). Each downloaded model card remembers how fast it has been on this computer ("Fast, about 62 tok/s").
- **Models**: shows you what models you can run and one sentence on whether it will run comfortably on your computer. The fit comes from the memory that is actually *free* on your graphics cards right now, so you can tell before loading whether another program is in the way. The default page, **Our picks**, leads with the models I've tested and recommend. **More models** shows the rest of what Lemonade offers that will run well on your computer. A **Details** fold on each card holds the technical names and **Settings for this model**: context window, backend, devices and the llama-server options (flash attention, cache precision, expert layers in system RAM, GPU splitting, thinking, speculative decoding) as tick boxes and dropdowns with plain explanations, checked against your own `llama-server` before you load.
- **Context window: 128k by default.** The built-in Lemonade starts with a 128k (or rather, a 131,072-token) context window, a good threshold for usefulness in long chats and agentic work. Zest sets it once, so changing it later in **All settings** sticks, and any model can have its own size under **Settings for this model**. A bigger window needs more graphics memory. 
- **How do I choose a model?** A button on Models (and an entry in Help) explains how to pick: will it fit, what is it for, how much it needs to remember. It then links to places to compare and learn about various models models: Luke's Dev Lab, Artificial Analysis, LMArena, Aider, the Berkeley Function-Calling Leaderboard, SWE-bench, LiveBench, BenchLM, LLM Stats, r/LocalLLaMA and the Level1Techs forum. 
- **Load, unload, reload and delete.** Every downloaded model has **Load now** (or **Reload** and **Unload** while it is in use) right on its card, so you can free graphics memory or pick up new settings without leaving the page. **Delete from this computer** is under **Details**; it asks first, says how much disk it frees, unloads the model if it is running, and you can download it again later.
- **Get from Hugging Face** takes a link or `org/name`, lists the GGUF versions in the repository with their sizes and a plain label (for example "A good balance"), marks which ones fit your computer, and hands the download to Lemonade.
- **Advanced > Server** lets other apps use your models (see below), with a live picture of what they ask for. The address and key to paste into those apps are at the top, and the Lemonade **activity log** (filter and follow mode) sits at the bottom of the same page.
- **Advanced > Hardware** shows every device llama.cpp can see as a live VRAM tank with utilisation, temperature, power, a short history, and which processes are holding its memory. Pick which devices to use and put them in order, then apply.
- **Advanced > Engines** explains what an engine is (the program that runs one kind of model) and what each backend means (CUDA, ROCm, Vulkan, CPU), then lists every Lemonade engine (llama.cpp, Whisper.cpp, StableDiffusion.cpp and more) with the backends each supports (CUDA, Vulkan, ROCm, CPU). Choose one per engine, or install and update missing ones.
- **Advanced > All settings** edits everything in Lemonade's own configuration, searchable, with a reset-to-default for every value.


## Use your models in other apps

**Advanced > Server** gives you an address, a key and copy-paste setup for tools such as [opencode](https://opencode.ai) and Hermes, or any chat app, coding assistant or agent that can talk to an OpenAI-compatible server. Paste the address (`http://127.0.0.1:13308/v1` by default), the key and a model name, and the other app uses the models on your computer.

The same page shows what those apps are doing:

- **Right now.** A card for each request that is running, with the app's name, a glass that bubbles while the model thinks and fills with lemon as the answer is written, and a line such as "Thought for 7.2 s, about 415 words". With **Show what models are saying** on, each card also quotes the line the model is on. The count of requests today sits underneath.
- **The last minute.** One lane per app, with a pill for each request split into reading the question (grey), thinking (dotted) and writing the answer (lemon), so you can see who was waiting for whom.
- **Requests.** A list with the app, the model, how long it took and whether it worked, filtered by app. Zest labels each app from the `User-Agent` it sends (for example "Hermes agent" or "opencode"), or from an `X-Zest-App` header if the app sets one.
- **Show what models are saying** is off by default. When on, you can open a request to read the question and answer. The text is held in memory only and is gone when Zest closes.

Other apps never talk to Zest's own page. Zest opens a second, much smaller door on its own port (13308) that serves only the chat-style `/v1` endpoints (models, chat, completions, embeddings, responses, audio, images, reranking, and loading or unloading a model). It **always asks for the key**, and pulling, deleting, installing or changing settings are not reachable through it. It listens on this computer only. **Other devices on my network** turns on listening on your local network; anyone there who has the key can then use your models, and the traffic is not encrypted, so use it on a network you trust. **Make a new key** invalidates the old one at once.

## Screenshots

### Welcome
On first run Zest walks through four short steps. Here it is offering three downloads that fit the computer.

![Welcome: choosing a model](docs/screenshots/setup-welcome.png)

### Chat
Streaming answers with code blocks and copy buttons, speed stats, a model picker, a "how it thought" fold for reasoning models, and per-chat instructions. Chats stay in your browser.

![Chat](docs/screenshots/chat.png)

The footer of each reply shows how fast it was written, how fast the model read your message, which model answered and how much of the context window is used. While a reply is being written the same row updates live, with a graph, and the status pill at the top shows the speed.

![Reply with speed footer](docs/screenshots/chat-speed.png)

### Models: our picks
The Models page opens on **Our picks**, written from our own testing (designing a generation ship, which needs web searches for physics and engineering). Each pick says what it is good at and where it falls short, and the card still shows whether it fits your computer.

- **Start here: LFM2.5 8B A1B.** Very fast, runs on almost anything, calls tools well, and keeps answers short.
- **Our general pick: Qwen3.6 35B A3B MTP.** Fast and competent at everyday tasks, research and agent work.
- **If Qwen 3.6 won't run: GLM 4.7 Flash.** Fast, but it makes things up, so it comes with a warning.
- **Bigger and slower, dense models: Gemma 4 31B MTP and Qwen3.8 27B.** Good answers, but slow. With a GPU and 64 GB of RAM, try Strata instead.
- **For advanced users: Strata and Maya,** separate inference engines built for huge models, with links and a clear warning that they run outside Zest and Lemonade.
- **Make any model better:** OpenChamber as the recommended app, and obra/superpowers for skills.

![Our picks](docs/screenshots/picks-top.png)

![The general pick, installed, with a fit warning](docs/screenshots/picks-general.png)

![Advanced engines and the harness recommendation](docs/screenshots/picks-engines.png)

### Models: more models
**More models** (and the filters beside it) lists everything else Lemonade offers. Models you haven't downloaded yet have a **Get it** button with the size. The meter on each card shows what will fit in the VRAM that's free right now; if another program is using the card, Zest warns you and names it.

![More models, on this computer](docs/screenshots/models.png)

**Get from Hugging Face** is for experts. It lists the GGUF versions in a repository, with a plain label, the size, and whether each fits. When Lemonade or llama.cpp refuses a model (for example because its architecture isn't supported yet, it needs a login, it can't be found, or memory or disk is short), Zest shows a plain-language message with the technical details tucked underneath, and offers to open Engines when an update may help.

![Get a model from Hugging Face](docs/screenshots/huggingface.png)

![When a model isn't supported](docs/screenshots/problem-architecture.png)

**How do I choose a model?** Explains what to look for and links to benchmark sites and communities, with a reminder that benchmarks are only a guide.

![How do I choose a model?](docs/screenshots/choose-model.png)

![Places to compare models](docs/screenshots/choose-model-sites.png)

### Settings for one model
Open **Details > Settings for this model** on any card. Set the context window, backend and devices, and tune `llama-server` with tick boxes and dropdowns instead of flags. Rows you have changed are highlighted, the exact arguments Zest will pass are shown, and anything your `llama-server` does not understand is flagged. **Save and reload** applies it to a model that is already running.

![Model settings](docs/screenshots/model-options.png)

### Server: share your models with other apps
Address, key and copy-paste setup for opencode, Hermes and any OpenAI-compatible app. The key is hidden until you press **Show**, including in the code samples.

![Server](docs/screenshots/server.png)

While an app is working you see a card with a glass that fills as it writes, a pill timeline of the last minute, and a request list. The screenshots below show real requests from three apps at once, so two of them are waiting their turn.

![A request in progress](docs/screenshots/server-live.png)

![Request list](docs/screenshots/server-requests.png)

The Lemonade activity log is at the bottom of the same page.

![Activity log on the Server page](docs/screenshots/server-log.png)

### Hardware
Select GPUs and set their order. Each card shows free VRAM, utilisation, temperature, power and which processes hold memory. Nothing changes until you press **Apply**.

![Hardware](docs/screenshots/hardware.png)

### Engines
Each engine says in plain words what it does, and a short guide explains which backend to pick. Choose one per engine, or install and update the ones you need.

![Engines](docs/screenshots/backends.png)

### All settings
Lemonade's own configuration, searchable and saved as you edit.

![All settings](docs/screenshots/settings.png)

### Setup guide
The full set of choices behind the welcome steps: which Lemonade to use, backends, where models live, and updates.

![Setup guide](docs/screenshots/setup-backend.png)

### Help and Advanced

![Help](docs/screenshots/help.png)

![Advanced](docs/screenshots/advanced.png)

## Using a Lemonade you already run

Already have Lemonade Server installed (for example as a system service)? On Advanced > Setup guide choose **A Lemonade I already run**, enter its address and, if needed, its API key. Zest will use it instead of starting its own, and won't touch the built-in copy. Switching back later is one click. Starting Zest with `--lemonade HOST:PORT` does the same without saving anything.

A few things work differently with a Lemonade you run yourself: Zest can't update it (use your package manager), and if it runs as a system service it may not be allowed to read model files in your home folder.

## Running from source

Python 3, standard library only (developed on 3.12):

```bash
git clone https://github.com/yudhanjaya/Zest.git
cd Zest
python3 zest/server.py
```

Then open <http://127.0.0.1:13306> (it opens automatically unless you pass `--no-browser`). A source checkout doesn't include Lemonade: on the welcome screen or Advanced > Setup guide, **Download Lemonade** fetches it (about 7 MB), or you can connect to one you already run.

| Flag | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `--port` | `ZEST_PORT` | `13306` | Port Zest listens on |
| `--host` | `ZEST_HOST` | `127.0.0.1` | Address Zest listens on |
| `--lemonade HOST:PORT` | `LEMONADE_API_KEY` for its key | the choice saved in Zest's settings | Use this Lemonade and skip the welcome screen |
| `--no-browser` | | | Don't open a browser tab |
| `--app` | | | Quit automatically once the tab is closed (the AppImage does this) |
| (none) | `ZEST_LEMONADE_DIR` | none | A folder containing a `lemond` to use as the built-in Lemonade |

> **Security.** Zest's own page has no login and can change Lemonade's configuration, so it listens on `127.0.0.1` only by default and refuses requests whose `Host` header isn't this computer (a defence against DNS-rebinding). Its built-in Lemonade is private: its own port, protected by a random API key that only Zest knows. Other apps use the separate key-protected door described above, never the page itself. Only use `--host 0.0.0.0` on a network you trust.

## Where Zest keeps things

| What | Where |
| --- | --- |
| Zest's settings, including the built-in Lemonade's API key and the key for other apps (file mode `600`) | `~/.config/zest/settings.json` |
| Lemonade updates you've downloaded | `~/.local/share/zest/lemonade/<version>/` |
| The built-in Lemonade's own config and the backends it installs | `~/.local/share/zest/lemonade-data/` |
| Its log (shown on the Logs page) | `~/.local/share/zest/lemond.log` |
| Links to models you chose to bring in | `~/.local/share/zest/imported-models/` |
| Models you download | the Hugging Face cache, or the folder you chose |
| Chats and display preferences | your browser's local storage |

(`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are respected.) To start from scratch, delete `~/.config/zest` and `~/.local/share/zest`. Downloaded models are not in there unless you chose a folder inside it.

## Privacy

Nothing you type into a chat leaves your computer. Zest and Lemonade do contact the following services:

- **api.github.com and github.com**: looking for and downloading Lemonade updates (the automatic check can be switched off), and Lemonade downloading the backends you install.
- **Hugging Face**: Lemonade downloading the models you choose, and Zest reading a repository's public file list when you use **Get from Hugging Face**. Gated models, which need a login, are not supported yet.
- **Benchmark and community sites**: the links in **How do I choose a model?** open in your browser only when you click them. Zest never contacts those sites itself.

## How it works

Zest is a small Python program with no dependencies (`zest/server.py` plus a few modules) and one HTML page (`zest/index.html`).

- `zest/gateway.py` is the door for other apps and the request recorder. Both it and Zest's own chat forward through one function that streams the answer back untouched while noting whether the model is waiting, thinking (`reasoning_content`, or text inside `<think>` tags) or writing.
- `zest/hf.py` reads a Hugging Face repository's file list and groups GGUF files by quantisation.
- `/api/*` is passed straight through to Lemonade, so the page needs no CORS changes. Chat uses the OpenAI-compatible `/api/v1/chat/completions` stream, and model downloads use Lemonade's background download jobs.
- Everything Zest changes in Lemonade goes through Lemonade's own HTTP API: `/internal/config` and `/internal/set` for settings, `/v1/models/{id}/options` for per-model options, `/v1/install` for backends. Zest does not need the `lemonade` command-line tool.
- The built-in Lemonade is the project's official "embeddable" build, started as `lemond <data folder> --port … --no-broadcast` with a random `LEMONADE_API_KEY`. If a previous Zest left one running, the new Zest reuses it instead of starting a second.
- Device order matters: llama.cpp numbers devices differently from `nvidia-smi` (CUDA puts the fastest card first). Zest asks the llama.cpp binary for its own list so the names and IDs line up.
- Live GPU numbers come from `nvidia-smi`. The log is Lemonade's own output (or `journalctl -u lemond` for a system service).

### Good to know

- **Telemetry is NVIDIA-only for now.** AMD and Intel devices are listed and selectable, but show capacity without live usage, and the fit meter treats them as empty.
- **The Hugging Face download is handed to Lemonade as a custom model** (`user.<name>-<quant>`, with the checkpoint `org/repo:QUANT` and the `llamacpp` recipe). Check it against your Lemonade version, since this part of its API has changed before.
- **Process names are shown on the Hardware page.** They come from `nvidia-smi` (name only, not the full path) and are only displayed in your own browser.
- **Devices appear once a backend is installed**, because Zest asks llama.cpp itself for the list rather than guessing.
- Tested on Linux with NVIDIA GPUs. The Vulkan and ROCm paths follow Lemonade's own backend data but haven't been tried on real hardware yet. Reports welcome.

## Building the AppImage

```bash
./packaging/build-appimage.sh
```

This downloads a relocatable Python, Lemonade's official embeddable build, and `appimagetool` (all pinned by SHA-256), assembles the image and writes `dist/Zest-<version>-x86_64.AppImage` plus a checksum. To bundle a newer Lemonade, change the version and checksum at the top of the script. Pushing a tag that starts with `v` runs the same script in GitHub Actions and attaches the result to the release.

To cut a release: bump `VERSION` in `zest/server.py`, then `git tag vX.Y.Z && git push --tags`.

## Zest is built on:

- **[Lemonade](https://github.com/lemonade-sdk/lemonade)** (Apache-2.0, © AMD and contributors) is bundled unmodified in the AppImage; its licence is included alongside it.
- **llama.cpp** (MIT) and the other backends are downloaded by Lemonade when you install them, not bundled.
- **[Python](https://www.python.org)** (PSF licence), via a relocatable build from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), is bundled so the AppImage needs no system Python.

## License

Zest is [MIT](LICENSE) licensed.
