# Shoggoth

[![Release](https://img.shields.io/github/v/release/yudhanjaya/Shoggoth)](https://github.com/yudhanjaya/Shoggoth/releases)
[![Downloads](https://img.shields.io/github/downloads/yudhanjaya/Shoggoth/total.svg)](https://github.com/yudhanjaya/Shoggoth/releases)
[![License: MIT](https://img.shields.io/github/license/yudhanjaya/Shoggoth)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Linux%20x86__64-blue)
![Code size](https://img.shields.io/github/languages/code-size/yudhanjaya/Shoggoth)
[![Last commit](https://img.shields.io/github/last-commit/yudhanjaya/Shoggoth)](https://github.com/yudhanjaya/Shoggoth/commits/main)
[![Open issues](https://img.shields.io/github/issues/yudhanjaya/Shoggoth)](https://github.com/yudhanjaya/Shoggoth/issues)
[![Stars](https://img.shields.io/github/stars/yudhanjaya/Shoggoth?style=social)](https://github.com/yudhanjaya/Shoggoth)

**Run AI models on your own computer, and put them to work, with one download.** Shoggoth is a friendly interface for open-source engines that run language models locally ([Lemonade](https://lemonade-server.ai) for small and medium models, [Strata](https://github.com/Niko1221/Strata) for very large ones) and for [Pi](https://pi.dev), a coding-agent harness. You never have to choose an engine: Shoggoth picks and starts the right one for the model you choose. The Shoggoth AppImage **includes Lemonade and Pi**, and sets up Strata the first time you want a large model. Linux-only for now.

The idea is to provide a user interface that people who aren't necessarily steeped in technical know-how can still navigate to get open-weights models running on their own hardware, and then do real work with them: chat, draw diagrams, write documents, or let a model read and change the files in a folder while they watch. Download Shoggoth, and you're good to go.

Shoggoth used to be called Zest. Every model it runs gets drawn as a many-eyed creature in a glass tube, wired into a bench of lab equipment. The pictures carry real information (more eyes means more context, rougher skin means heavier compression), and you can turn them off.

![Shoggoth working in a folder](docs/screenshots/work-tools.png)

*Chat, working in a folder: it read the code, fixed a bug with a one-line change (shown as a diff) and ran the tests again, step by step.*

**Contents:** [What you can do](#what-you-can-do-with-shoggoth) · [Install and run](#install-and-run) · [Finding your way around](#finding-your-way-around) · [Your first conversation](#your-first-conversation) · [Model pictures](#model-pictures) · [Choosing and managing models](#choosing-and-managing-models) · [Skills](#skills) · [Use your models in other apps](#use-your-models-in-other-apps) · [Keeping everything up to date](#keeping-everything-up-to-date) · [Advanced tools](#advanced-tools) · [Themes](#themes) · [Your data and privacy](#your-data-and-privacy) · [For developers](#for-developers)

## What you can do with Shoggoth

- **Chat with an AI that runs on your own computer.** Nothing you type leaves it, unless you let the model search the web: it asks first, every time.
- **Let it work on your files.** Choose a folder and the model can read, search, edit and run things there while you watch. It asks before it changes anything.
- **Hand it a bigger job.** A job keeps working in the background, round after round, and checks its own progress. You review what it changed before anything touches your folder.
- **Draw diagrams and write documents.** Ask for a picture or a README and get one, shown right in the chat.
- **Teach it new tricks with skills.** Short instruction sheets that make small models far more dependable. Add more by pasting a link.
- **Pick and manage models.** Shoggoth tells you which ones will run comfortably on your computer and downloads them for you.
- **See what your models are.** Each model is drawn as a creature built from its own numbers, and the Hardware and Server pages show your graphics cards and the apps using them as a working lab bench.
- **Make it look the way you like.** Fifteen themes, from soft plastic to Dracula and Nord, under **Advanced > Themes**.
- **Use your models in other apps.** Shoggoth can share them with tools such as opencode or any app that talks to an OpenAI-style server.

Shoggoth runs on Linux. It needs no technical setup: you download one file and follow a few short steps. The **Docs** tab inside the app explains everything in plain language (its text lives in [shoggoth/docs.md](shoggoth/docs.md)).

## Install and run

1. **Download** `Shoggoth-<version>-x86_64.AppImage` from the [latest release](../../releases/latest).
2. **Run it:**

   ```bash
   chmod +x Shoggoth-*-x86_64.AppImage
   ./Shoggoth-*-x86_64.AppImage
   ```

3. **Let Shoggoth get ready.** Shoggoth opens in your browser on one screen called **Getting ready**. It asks one question and does the rest itself:

   - **Where should your models live?** Models are big, so Shoggoth lists your disks with their free space, preselects the one with the most room, and one click accepts it. Shoggoth keeps everything in one folder called Models there. If you already have models or an engine running, Shoggoth finds them and skips the question.
   - **Then it works down a short checklist by itself:** it looks at your computer, gets the part that runs AI (a small download, with progress), and fits it to your graphics card (CUDA for NVIDIA, Vulkan for most cards, the processor as a fallback).
   - **Choose your first model.** One suggestion per size: **Small** (LFM2.5 8B A1B: fast, to the point, good with tools, happy on almost any computer), **Medium** (Qwen3.6 35B A3B MTP or Gemma 4 26B A4B, the general pick and the recommended one when it fits) and **Large** (for big computers; it takes a couple of minutes to start). Press **Get it** on one; the download shows its progress right there. **I already have models** brings in the ones you have.
   - **Start chatting.** The button names the model it will use. The model works in your home folder unless you choose another folder.

   ![The Getting ready screen: the checklist is done and Shoggoth suggests a first model per size](docs/screenshots/welcome.png)

*The first-run screen ends with a button that opens the chat.*

People who already have models, or run their own Lemonade or Strata, can skip the first-run screen: Shoggoth finds what is already there. **Settings** holds the few choices (where models live, how long big models stay in memory, how much a chat remembers, updates), the Library's **Add models** brings in models you already have (LM Studio, Ollama, llama.cpp and others are found for you), and **Settings > My own server** connects a Lemonade you run yourself.

### What you need

- **Linux, x86_64.** The built-in Lemonade needs a reasonably recent system (glibc 2.38 or newer: Ubuntu 24.04, Debian 13, Fedora 39 or later). On an older system Shoggoth tells you so and you can connect it to a Lemonade you install yourself instead.
- A few common system libraries that almost every desktop Linux already has (OpenSSL 3, zstd, libdrm). If one is missing, Shoggoth names it.
- For GPU use, your graphics driver. Shoggoth and Lemonade download everything else.
- Internet access for the first-run downloads (the backend and your models).

If your system has no FUSE support, run `./Shoggoth-*-x86_64.AppImage --appimage-extract-and-run` instead. Closing the browser tab quits Shoggoth (and its Lemonade) after about three minutes; launching it again while it's running just reopens the page.

## Finding your way around

The left-hand menu has seven places. Everything a newcomer needs is in the first six; everything technical sits behind **Advanced**, and so does the choice of theme.

| Menu item | What it is for |
| --- | --- |
| **Chat** | Talk to a model, or choose a folder and let it work on your files. |
| **Library** | Every model on your computer in one list, grouped Small, Medium and Large, with the ones that will run well recommended. Get new ones, add ones you have, change a model's settings. |
| **Skills** | Switch skills on and off, and add more from a GitHub link. |
| **Docs** | A plain-language guide to all of this, inside the app. |
| **Help** | Short answers to common questions, and a button to see the first-run screen again. |
| **Settings** | Where your models live, how long large models stay in memory, how much a chat remembers, a link to tuning, and one button to update everything. |
| **Advanced** | Server, Hardware, Engines, Engine settings, Tuning and Themes. You never need these just to chat. |

![The Docs page, with the menu on the left](docs/screenshots/docs.png)

*The menu on the left, with the Docs tab open.*

## Your first conversation

Open **Chat** and type. The bar under the title shows which model will answer and whether it is in memory. If it isn't, Shoggoth loads it when you send your first message, so that answer takes a little longer.

**Chat** runs on [Pi](https://pi.dev), a coding-agent harness that ships inside Shoggoth. Every chat is a co-working session: the model reads, searches, edits and runs commands (it has a terminal) in a folder while you watch. Pick the folder, or leave it and it works in your home folder, and says so at the top of the chat; there it starts on **Ask first**, and jobs need a project folder. It answers questions like a normal chat too, and asks before it changes anything (tool cards show each step; edits show a diff). Choose how careful it is per chat (**Ask first**, **Edit freely**, **Do everything**, **Look only**), add more folders it can look at or change, switch models, watch how much of the context window is used, and add notes while it works.

For bigger work, press **Start a job…**. Say what should get done and how Shoggoth can tell it is done, such as a test command that passes. The job runs in the background, round after round, and stops when the check passes, when it hits its round or time limit, or when it stops making progress. By default it works on a copy of your folder; the **Changes** tab shows what it changed and you apply what you want. Jobs are listed under their chat, and you can keep chatting while they work. Chats and jobs are saved and pick up where they left off. See [docs/COWORK.md](docs/COWORK.md).

### Chat

Replies stream in as they are written. The bar under the title shows which model answers, how much context it has (128k by default) and whether it is in memory, with **Load**, **Unload** and **Reload**. A reasoning model's thinking appears live above its answer and folds away when it is done. Markdown and code blocks come with copy buttons. A small strip shows what the model is doing (reading, thinking, writing, working, waiting for your OK), and a line in italics under it says the same thing in a more unsettling way, always naming the model that is running.

![Chat: the model's answer after it fixed a bug, with the model in its tube at the top](docs/screenshots/chat.png)

### Working in a folder

Every chat works in a folder (your home folder unless you choose one) and can read, search, edit and run things there. Tool cards show each step, edits show a diff, and in **Ask first** and **Edit freely** modes it stops and asks before it runs a command. Here it was asked to fix a folder whose tests were failing: it read the tests and the code, made a one-line change and ran the tests again to confirm.

![Shoggoth asking before it runs a command](docs/screenshots/work-approve.png)

![Reading the code, a diff of the fix, and the tests run again](docs/screenshots/work-tools.png)

## Model pictures

Shoggoth draws every model as a creature, made from the model itself, so the same model always looks the same:

- **Eyes**: how much it remembers (its context window). More eyes, more memory.
- **Size**: how many parameters it has.
- **Skin**: how tightly it is compressed. Rougher skin is smaller and a little less exact.
- **Hands holding pens**: it can use tools.
- **Shape and colour**: models from the same family share them.

The chat's model sits in a glass tube at the top of the Chat page, wired to the model bar. It stirs while the model loads, thinks and writes. Click the tube (or **Open the machine** in the model menu) to open **the Machine**: a drawer with one slot per engine, showing what is in memory, and a shelf of every model you have. You can load and unload from there.

![The Machine: one slot per engine, with the Large model loaded in Strata's slot](docs/screenshots/machine.png)

The **Hardware** and **Server** pages put the same creatures on a lab bench (see [Advanced tools](#advanced-tools) and [Use your models in other apps](#use-your-models-in-other-apps)). If you'd rather not see them, switch off **Show model pictures** under **Settings › Look**. Nothing else changes.

## Choosing and managing models

The **Library** is one list of every model on your computer, whichever engine runs it, and the place you get new ones. You don't pick an engine: Shoggoth runs small and medium models on [Lemonade](https://lemonade-server.ai) and large ones on [Strata](https://github.com/Niko1221/Strata), starts whichever the model you chose needs, and frees the memory of whatever else was running. The engine's name sits, small, at the foot of each card.

![Models on this computer](docs/screenshots/models.png)

**Installed** lists what you have, grouped by size: **Small** (under 10 GB of graphics memory, ready in seconds), **Medium** (10 to 24 GB) and **Large** (for computers with about 64 GB of RAM; they take a couple of minutes to start). **Recommended for this computer** leads with the models I've tested and recommend, plus any Large model that fits. **Everything** is the full list, with a filter for pictures, speech and sound. Each card shows whether the model fits in the memory that is *free* on your graphics cards right now; if another program is using it, Shoggoth says so and names it.

**Getting models**

- **Get it** downloads a model in the background with a progress bar, and you can keep using Shoggoth. Downloads share one "Getting models" strip. A model that needs something extra, such as the first Large model, sets it up for you as part of the same step.
- **Add models** brings in models from a Hugging Face link (a repository's GGUF versions are listed with their sizes and a plain label such as "A good balance"), from any folder, or from the usual places other programs keep them (LM Studio, llama.cpp's cache, GPT4All, Jan, Ollama, `~/models`, `~/Downloads`). **Nothing is copied.** Shoggoth gives each engine a folder of links to your files, so there's no extra disk use and your originals stay where they are.
- **Where they live.** You choose one folder, once (**Settings**, or the first-run screen). Shoggoth keeps new models there, in a **Models** folder with a subfolder for each engine, and moves nothing you already have.
- When an engine refuses a model (its architecture isn't supported yet, it needs a login, it can't be found, or memory or disk is short), Shoggoth shows a plain-language message with the technical details tucked underneath.

**Choosing the right one**

- **How do I choose a model?** A button in the Library (and an entry in Help) explains how to pick: will it fit, what is it for, how much it needs to remember. It links to places to compare and learn about models: Luke's Dev Lab, Artificial Analysis, LMArena, Aider, the Berkeley Function-Calling Leaderboard, SWE-bench, LiveBench, BenchLM, LLM Stats, r/LocalLLaMA and the Level1Techs forum.
- **Start here: LFM2.5 8B A1B.** Very fast, runs on almost anything, calls tools well, and keeps answers short.
- **My general pick: Qwen3.6 35B A3B MTP.** Fast and competent at everyday tasks, research and agent work.
- **If Qwen 3.6 won't run: GLM 4.7 Flash.** Fast, but it makes things up, so it comes with a warning.
- **Bigger and slower, dense models: Gemma 4 31B MTP and Qwen3.8 27B.** Good answers, but slow.
- **Large:** with a GPU and 64 GB of RAM, the Large group runs models far bigger than your graphics card.
- **Make any model better:** OpenChamber as the app I recommend, and obra/superpowers for skills.

**Loading and unloading**

- **Load, unload and reload live in the chat.** A bar under the chat's title shows the model, the context size and whether the model is in memory, with **Load**, **Unload** and **Reload**. Pick a different model there and Shoggoth gets it ready and frees the previous one's memory. Sending a message to a model that isn't in memory loads it for you.
- **Context window: 128k by default.** Chat loads a model with a 128k (131,072-token) context window, a good threshold for usefulness in long chats and agentic work. The context menu in the chat's model bar changes it, and **Settings** has a default for every chat. A bigger window needs more graphics memory.
- **Delete from this computer** is under **Details** on a card. It asks first, says how much disk it frees, and unloads the model if it is running. You can download it again later.

### Settings for one model

Press **Settings** on any downloaded card. Set the backend and devices, and tune `llama-server` with tick boxes and dropdowns instead of flags. Rows you have changed are highlighted, the exact arguments Shoggoth will pass are shown, and anything your `llama-server` does not understand is flagged. **Save** keeps it; for a model that is in memory, press **Reload** in the chat's model bar to use the new settings.

![Model settings](docs/screenshots/model-options.png)

### Large models (experimental)

Some models are far too big for a graphics card. Shoggoth runs them anyway, through [Strata](https://github.com/Niko1221/Strata), which keeps part of the model in RAM and on the SSD. They are slower than a model that fits, and need a lot of RAM and disk.

- **You use them like any other model.** Shoggoth suggests the best Large model that fits your RAM, graphics memory and free disk. **Get it** downloads it and, the first time, sets up Strata for you: it downloads Strata's installer, checks it against the checksum Strata publishes, runs it and saves a setup for the model with a 128k context window (DeepSeek V4.1 is fixed at 8K). Pick the model in Chat and a card shows it starting.
- **Memory is shared.** Only one big model runs at a time. Picking one frees whatever else was running; picking a small one frees the big one, unless it is answering right now. Before a big model starts, Shoggoth checks there is enough free memory and, if not, says how much is missing and which programs are using the most (with a **Start anyway** button).
- **It frees itself** after 20 idle minutes (change it under **Memory** in **Settings**, or set 0 to keep it ready). The next chat starts it again.
- **Existing Strata installs are found by themselves.** If you already have Strata installed, or a model running (started with `strata launch`, by the Strata app, or by hand on a local port), its models show up in the Library and in Chat with no setup. To use a Strata on another computer, open **Advanced > Engines**, then Strata's card, then **Connect to a Strata on another computer**. Shoggoth never stops, restarts or updates a Strata you started yourself, and has a **Stop** button (with a confirmation) only when you ask for one. While one of yours is running, Shoggoth won't start a second big model beside it.
- **Updates.** Strata's engine and Control update from its own signed feed, and Strata test-starts a new engine before switching to it. **Check and update everything** in Settings includes Strata. If the Strata Shoggoth started is running, Shoggoth stops it, updates, and it starts again when a chat needs it. A bigger jump (a new Control series) is left for you to take with `strata update`.

### Tuning

**Advanced > Tuning** makes a model answer faster on your computer. It measures the model as it is, tries changes one at a time, and keeps only those that clearly help. Answers stay the same: tuning never changes how a model is compressed.

- **Large models** are tuned by Strata's own tuner (`strata tune auto`), quick (about 20 minutes) or thorough (up to 2 hours), for chat or for coding with long prompts. The result is saved and used every time the model starts. **Switch off** keeps it but goes back to the standard settings.
- **Small and medium models** that run on llama.cpp are tuned by Shoggoth: batch sizes, flash attention, threads, which backend and which graphics cards. Each is tried by loading the model with it, and a change is kept only if it beats the measuring noise (and at least 3%). **Undo** puts the old settings back.
- Tuning needs the graphics cards to itself, so Shoggoth shows the plan first (time, and what it will stop) and waits for your go-ahead. It stops what Shoggoth started and waits while a chat is answering. It will not start while a Strata you started yourself is running. You can cancel at any time and nothing is changed.

## Skills

**Skills** are short instruction sheets the model reads when a job calls for them. Shoggoth comes with Superpowers (a trimmed copy of [obra/superpowers](https://github.com/obra/superpowers): ask what you really want, plan, test first, find a bug's cause before fixing it, prove it works before saying so), a diagrams skill (the model writes Mermaid and Shoggoth draws it in the chat, with Copy and Save as image; if it doesn't draw, one button asks the model to fix it) and a markdown-writing skill. **To add more, open Skills, paste a link to a GitHub repository (or to one folder in it) and press Add skills.** Any repository that follows the [Agent Skills](https://agentskills.io) format works; [Hermes Atlas](https://hermesatlas.com/ecosystem/) lists many. Switch skills on and off one by one, or by set, and type `/` in a folder conversation to pick one. Only add skills from people you trust. See [docs/COWORK.md](docs/COWORK.md).

![The Skills page: Superpowers and the other skills that come with Shoggoth](docs/screenshots/skills.png)

## Use your models in other apps

**Advanced > Server** gives you an address, a key and copy-paste setup for tools such as [opencode](https://opencode.ai) and Hermes, or any chat app, coding assistant or agent that can talk to an OpenAI-compatible server. Paste the address (`http://127.0.0.1:13308/v1` by default), the key and a model name, and the other app uses the models on your computer.

![Server](docs/screenshots/server.png)

*The lab bench on the Server page, just after **Send a test message**: the loaded model in its jar, a flask for the request, and the card underneath saying how long it took.*

At the top of the page are the address, the key (hidden until you press **Show**, including in the code samples) and copy-paste setup for opencode, Hermes and any OpenAI-compatible app. Below that, the page shows what those apps are doing:

- **Right now.** A lab bench: the model in its jar, piped to a flask for each request that is running. The flask bubbles while the model thinks and fills as the answer is written. A card for each request gives the app's name and a line such as "Thought for 7.2 s, about 415 words". **Send a test message** sends one to the model that is in memory. With **Show what models are saying** on, each card also quotes the line the model is on. The count of requests today sits underneath.
- **The last minute.** One lane per app, with a pill for each request split into reading the question (grey), thinking (dotted) and writing the answer (yellow), so you can see who was waiting for whom.
- **Requests.** A list with the app, the model, how long it took and whether it worked, filtered by app. Shoggoth labels each app from the `User-Agent` it sends (for example "Hermes agent" or "opencode"), or from an `X-Shoggoth-App` header if the app sets one (`X-Zest-App` still works).
- **Show what models are saying** is off by default. When on, you can open a request to read the question and answer. The text is held in memory only and is gone when Shoggoth closes.

Other apps never talk to Shoggoth's own page. Shoggoth opens a second, much smaller door on its own port (13308) that serves only the chat-style `/v1` endpoints (models, chat, completions, embeddings, responses, audio, images, reranking, and loading or unloading a model). It **always asks for the key**, and pulling, deleting, installing or changing settings are not reachable through it. It listens on this computer only. **Other devices on my network** turns on listening on your local network; anyone there who has the key can then use your models, and the traffic is not encrypted, so use it on a network you trust. **Make a new key** invalidates the old one at once.

## Keeping everything up to date

![Settings](docs/screenshots/setup.png)

*Settings, where updates, the models folder and a few other choices live.*

**Check and update everything** on the Settings page looks for newer versions of Shoggoth, Lemonade, Pi and (if you use it) Strata in one go, verifies each download against its published checksum and installs what it finds. A new Shoggoth is saved as a new AppImage next to the current one (which is kept), and **Restart Shoggoth to finish** switches over to it. The sections below cover Lemonade and Pi on their own, and where each now lives.

### Lemonade

The AppImage ships with a recent Lemonade, but Lemonade moves quickly, so Shoggoth can update its own copy without you downloading a new Shoggoth.

- Press **Check for updates** on Lemonade's card under **Advanced > Engines**. If a newer stable release exists, one button downloads it, **checks it against the checksum GitHub publishes for that file**, installs it and offers to restart Lemonade onto it.
- Shoggoth can also look for an update when it opens and put a dot on **Advanced** if there is one. You can switch that off with the checkbox.
- Updates are kept in your own data folder, because the AppImage itself is read-only. The version that came with Shoggoth is never touched, so **"Go back to the version that came with Shoggoth"** is always one click away.

### Pi, the agent helper

Pi is the part that lets a model read files, make edits and run steps. It is updated separately, from **Advanced > Engines**, under **The agent helper** (and **Check and update everything** includes it).

- Press **Check for Pi updates**. If a newer release exists, one button downloads it from the npm registry, **checks it against the checksum npm publishes**, unpacks it, and starts it once to make sure it works with Shoggoth. Only a Pi that passes is offered; otherwise nothing is installed.
- Your next message uses the new Pi. A task that is working right now finishes on the old one.
- Downloads live in your own data folder. The Pi that came with Shoggoth is never touched, so **"Go back to the version that came with Shoggoth"** is always one click away.
- Shoggoth only offers updates within the same major version of Pi, because that is what it has been tested with. A release that needs a newer Node.js than the one inside Shoggoth waits for a newer Shoggoth.
- The same "Look for updates when Shoggoth opens" checkbox covers both.

## Advanced tools

Everything under **Advanced** is optional.

![The Advanced menu: Server, Hardware, Engines, Engine settings, Tuning and Themes](docs/screenshots/advanced.png)

- **Advanced > Server** lets other apps use your models (see below), with a live picture of what they ask for. The address and key to paste into those apps are at the top, and the Lemonade **activity log** (filter and follow mode) sits at the bottom of the same page.
- **Advanced > Hardware** is a lab bench. Each machine at the back is one graphics card: its lights show how hard it is working, and its screen names the model on it. The beaker shows memory used across all your cards, the gauge shows the hottest card, the coil sparks as the cards work harder, and the radio has a meter for each card. Below the bench, every device llama.cpp can see is listed with its memory, load, temperature and the processes holding it. Pick which devices to use, put them in order, and apply.
- **Advanced > Engines** has one card for each engine Shoggoth can use (Lemonade for small and medium models, Strata for large ones, and any you add): what it is, its version, whether it is running and who started it, **Check for updates** and **Update**, and **Stop it** for one Shoggoth started (never one you started yourself). Inside each card, collapsed, are the details specific to it: for Lemonade, every kind of model it runs (llama.cpp, Whisper.cpp, StableDiffusion.cpp and more) with the hardware each can use (CUDA, Vulkan, ROCm, CPU), which you can choose per kind or install; for Strata, your saved setups, connections to a Strata on another computer, and installing it. Below the cards, **The agent helper** (Pi) and **Add an engine**: put a folder with a `provider.yaml` file in the engines folder shown there and press Reload. Shoggoth asks once before an engine you added runs a program, and lists any folder it couldn't load with the line to fix. [docs/ENGINES.md](docs/ENGINES.md) is the guide to writing one ("Add an engine in ten minutes"), with `Shoggoth.AppImage providers new` and `providers check` to start and test it.
- **Advanced > Engine settings** edits everything in Lemonade's own configuration, searchable, with a reset-to-default for every value.
- **Advanced > Themes** changes how Shoggoth looks (see below).
- **Settings** is where you change where models live, how long large models stay in memory and how much a chat remembers, and update everything. Its **My own server, and the first-run screen** section is where you check Lemonade's version or connect to one you run yourself; **Advanced > Engines** has backends, Strata and the agent helper. **Help > Run the welcome steps again** shows the first-run screen again.

![Advanced > Hardware: two graphics cards as machines at the back of a lab bench, with a memory beaker, a heat gauge, a coil and a radio](docs/screenshots/hardware.png)

*Advanced > Hardware with a Large model spread over two cards: an RTX 3080 and an RTX 4080 SUPER, 94% of their memory in use.*

### Using a Lemonade you already run

Already have Lemonade Server installed (for example as a system service)? Under **Settings > My own server, and the first-run screen** choose **A Lemonade I already run**, enter its address and, if needed, its API key. Shoggoth will use it instead of starting its own, and won't touch the built-in copy. Switching back later is one click. Starting Shoggoth with `--lemonade HOST:PORT` does the same without saving anything.

A few things work differently with a Lemonade you run yourself: Shoggoth can't update it (use your package manager), and if it runs as a system service it may not be allowed to read model files in your home folder.

## Themes

Shoggoth looks the same everywhere you use it, but you can change its finish. Open **Advanced > Themes** and pick one. The choice is remembered on this computer, and nothing moves: only the colours and the surface change, so every button, panel and menu stays where it was.

![The theme picker, with Light Touch in use](docs/screenshots/themes.png)

**Light Touch** is the default: a calm cream page with a little hardware about it (keycap buttons, a pressed-in reply box and a small lamp that shows when a model is loaded). Two themes go further, and the rest dress Shoggoth in colour schemes from popular code editors.

| | |
| --- | --- |
| ![Braun T3](docs/screenshots/theme-braun-t3.png) | ![Teenage Engineering](docs/screenshots/theme-teenage-engineering.png) |
| **Braun T3**: matte warm plastic, a glowing lamp and a speaker grille. | **Teenage Engineering**: hard shadows, thick outlines and an orange bar. |
| ![Monokai Pro](docs/screenshots/theme-monokai-pro.png) | ![GitHub Light](docs/screenshots/theme-github-light.png) |
| **Monokai Pro**: dark and warm, with raised keys and a recessed chat. | **GitHub Light**: clean and bright, in GitHub's blue. |

...and more! One Dark Pro, Dracula, Night Owl, SynthWave '84, Cobalt2, Nord, Gruvbox, GitHub Dark and Catppuccin are in the same picker.

## Your data and privacy

![The Help page: short answers to common questions, as one list](docs/screenshots/help.png)

### Where Shoggoth keeps things

| What | Where |
| --- | --- |
| Shoggoth's settings, including the built-in Lemonade's API key and the key for other apps (file mode `600`) | `~/.config/shoggoth/settings.json` |
| The secret that lets a browser into Shoggoth's own page (file mode `600`) | `~/.config/shoggoth/ui-token` |
| Lemonade updates you've downloaded | `~/.local/share/shoggoth/lemonade/<version>/` |
| The built-in Lemonade's own config and the backends it installs | `~/.local/share/shoggoth/lemonade-data/` |
| Its log (shown at the bottom of Advanced > Server) | `~/.local/share/shoggoth/lemond.log` |
| Links to models you chose to bring in, and each engine's view of your Models folder | `~/.local/share/shoggoth/` (`imported-models/`, `views/`) |
| Models you download | the folder you chose (a **Models** folder with a subfolder for each engine), or the Hugging Face cache if you kept that |
| Skills you added and which are switched on | `~/.local/share/shoggoth/skills/` |
| Newer versions of Pi that you downloaded | `~/.local/share/shoggoth/pi/<version>/` |
| Chat conversations, jobs, their transcripts, the folders Shoggoth has worked in (and the commands you always allow there), work copies of folders awaiting review, and Pi's configuration (rewritten at each start) | `~/.local/share/shoggoth/cowork/` |
| Display preferences | your browser's local storage |

(`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are respected.) **Coming from Zest?** The first time Shoggoth starts, it moves `~/.config/zest` and `~/.local/share/zest` to these names and keeps everything in them: chats, skills, settings and keys. Apps that use a `zest-` key keep working, and old `ZEST_*` environment variables are still read. To start from scratch, delete `~/.config/shoggoth` and `~/.local/share/shoggoth`. Downloaded models are not in there unless you chose a folder inside it.

### Privacy

Nothing you type into a chat leaves your computer, with one exception you control: the model has a **web search** tool, and every search asks first, showing the exact words it wants to search for. Those words, and only those, go to the search provider. You can switch it off for good in the chat's Details panel. The default is DuckDuckGo, which needs no key but has no official search service, so it can refuse automated searches; Shoggoth makes one request per search, never retries, and then tells you to get a free [Brave Search](https://brave.com/search/api/) or [Tavily](https://app.tavily.com/) key, which you paste into the same panel (it is stored in Shoggoth's settings file on this computer). A chat with a folder runs locally apart from that: Pi is started offline, with telemetry and update checks off, and talks only to your Lemonade. If you add your own extensions, MCP servers or web tools to Pi, those can reach the network.

Shoggoth and Lemonade do contact the following services:

- **html.duckduckgo.com, api.search.brave.com or api.tavily.com**: only when you allow the model to search the web, whichever provider you chose, with the words of that search.
- **codeload.github.com**: only when you paste a link under Skills, to download that repository's skills.
- **registry.npmjs.org**: looking for and downloading Pi updates (the same automatic-check switch covers it).
- **api.github.com and github.com**: looking for and downloading Shoggoth and Lemonade updates (the automatic check can be switched off), Lemonade downloading the backends you install, and, if you use large models, Strata's installer and its checksum list. Strata checks for its own engine updates from its own feed.
- **Hugging Face**: your engines downloading the models you choose, and Shoggoth reading a repository's public file list when you add one by link. Gated models, which need a login, are not supported yet.
- **Benchmark and community sites**: the links in **How do I choose a model?** open in your browser only when you click them. Shoggoth never contacts those sites itself.

## For developers

### Running from source

Python 3, standard library only (developed on 3.12):

```bash
git clone https://github.com/yudhanjaya/Shoggoth.git
cd Shoggoth
python3 shoggoth/server.py
```

Diagrams in Chat need Mermaid: run `./packaging/fetch-vendor.sh` once (a 26 MB download, checked against its published checksum); the AppImage already includes it.

Chat's agent features need [Pi](https://pi.dev) (Node 22.19 or newer): `npm install -g @earendil-works/pi-coding-agent`, or point `SHOGGOTH_PI` at a `pi` binary. The AppImage already includes both.

Then open <http://127.0.0.1:13306> (it opens automatically unless you pass `--no-browser`). A source checkout doesn't include Lemonade: on the first-run screen, **Download Lemonade** fetches it (about 7 MB), or you can connect to one you already run.

| Flag | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `--port` | `SHOGGOTH_PORT` | `13306` | Port Shoggoth listens on |
| `--host` | `SHOGGOTH_HOST` | `127.0.0.1` | Address Shoggoth listens on |
| `--lemonade HOST:PORT` | `LEMONADE_API_KEY` for its key | the choice saved in Shoggoth's settings | Use this Lemonade and skip the welcome screen |
| `--no-browser` | | | Don't open a browser tab |
| `--app` | | | Quit automatically once the tab is closed (the AppImage does this) |
| (none) | `SHOGGOTH_LEMONADE_DIR` | none | A folder containing a `lemond` to use as the built-in Lemonade |

> **Security.** Shoggoth's own page can change settings, start engines and run commands in a folder, so it is not open to everything that can reach the port. Shoggoth keeps a secret in `~/.config/shoggoth/ui-token` (mode 600). The window Shoggoth opens carries a one-time code (good for two minutes, once) that the server swaps for an HttpOnly, SameSite=Strict cookie; every other request is refused with 401, and a browser that hasn't been let in sees a page saying to start Shoggoth again. A script can send the secret as `Authorization: Bearer <secret>`; `SHOGGOTH_AUTH=off` switches this off for a machine only you use. Running Shoggoth a second time asks the running one for a fresh code. It also listens on `127.0.0.1` only by default and refuses requests whose `Host` header isn't this computer (a defence against DNS-rebinding). Its built-in Lemonade is private: its own port, protected by a random API key that only Shoggoth knows. Other apps use the separate key-protected door described above, never the page itself. Only use `--host 0.0.0.0` on a network you trust.

### How it works

Shoggoth is a small Python program with no dependencies (`shoggoth/server.py` plus a few modules) and one HTML page (`shoggoth/index.html`).

- `shoggoth/gateway.py` is the door for other apps and the request recorder. Both it and Shoggoth's own chat forward through one function that streams the answer back untouched while noting whether the model is waiting, thinking (`reasoning_content`, or text inside `<think>` tags) or writing.
- `shoggoth/agent.py` runs Pi (`pi --mode rpc`, one process per task) against Lemonade through Shoggoth's own `/api/v1` proxy, and relays its JSON events to the page as server-sent events. `shoggoth/pi/zest-gate.js` (the old name is kept so existing setups find it) is the Pi extension that makes it ask before editing or running commands. Details in [docs/COWORK.md](docs/COWORK.md).
- `shoggoth/hf.py` reads a Hugging Face repository's file list and groups GGUF files by quantisation.
- `/api/*` is passed straight through to Lemonade, so the page needs no CORS changes. Chat uses the OpenAI-compatible `/api/v1/chat/completions` stream, and model downloads use Lemonade's background download jobs.
- Everything Shoggoth changes in Lemonade goes through Lemonade's own HTTP API: `/internal/config` and `/internal/set` for settings, `/v1/models/{id}/options` for per-model options, `/v1/install` for backends. Shoggoth does not need the `lemonade` command-line tool.
- The built-in Lemonade is the project's official "embeddable" build, started as `lemond <data folder> --port … --no-broadcast` with a random `LEMONADE_API_KEY`. If a previous Shoggoth left one running, the new Shoggoth reuses it instead of starting a second.
- Device order matters: llama.cpp numbers devices differently from `nvidia-smi` (CUDA puts the fastest card first). Shoggoth asks the llama.cpp binary for its own list so the names and IDs line up.
- Live GPU numbers come from `nvidia-smi` for NVIDIA cards and from the Linux `amdgpu` driver's sysfs files for AMD cards (VRAM used and total, load, temperature, power, fan, and which processes hold memory). The log is Lemonade's own output (or `journalctl -u lemond` for a system service).

### Good to know

- **Live telemetry covers NVIDIA and AMD (on Linux).** Intel devices are listed and selectable, but show capacity without live usage, and the fit meter treats them as empty. The AMD numbers come from the kernel, so they need no extra tools, but they have so far been tested only against a simulated amdgpu sysfs tree and a screenshot from a two-Radeon VII machine, not on AMD hardware here. Two identical AMD cards are matched to llama.cpp's devices by order, so their usage can appear swapped.
- **The Hugging Face download is handed to Lemonade as a custom model** (`user.<name>-<quant>`, with the checkpoint `org/repo:QUANT` and the `llamacpp` recipe). Check it against your Lemonade version, since this part of its API has changed before.
- **Process names are shown on the Hardware page.** They come from `nvidia-smi` or the amdgpu driver (name only, not the full path) and are only displayed in your own browser.
- **Devices appear once a backend is installed**, because Shoggoth asks llama.cpp itself for the list rather than guessing.
- Tested on Linux with NVIDIA GPUs. The Vulkan and ROCm paths follow Lemonade's own backend data but haven't been tried on real hardware yet. Reports welcome.

### Adding an engine

An engine is one folder with one `provider.yaml` (plus a `provider.py` only when YAML can't say it). `shoggoth/providers/_template/` is the annotated starting point, `shoggoth/providers/_examples/llama-server/` is a complete YAML-only engine around a plain `llama-server` (tested end to end with a real one), and [docs/ENGINES.md](docs/ENGINES.md) explains every setting. `python3 shoggoth/server.py providers check <folder>` validates a folder the way Shoggoth itself will read it, `providers new <id> [--from llama-server]` and `providers list` do what they say, and `providers doctor` asks the installed Lemonade and Strata the questions Shoggoth asks and says if their answers have changed (run it before a release). Nothing in Shoggoth's own code needs to change to add one.

### Building the AppImage

```bash
./packaging/build-appimage.sh
```

This downloads a relocatable Python, Node.js, the Pi agent, Lemonade's official embeddable build, and `appimagetool` (all pinned by SHA-256), assembles the image and writes `dist/Shoggoth-<version>-x86_64.AppImage` plus a checksum. To bundle a newer Lemonade, change the version and checksum at the top of the script. Pushing a tag that starts with `v` runs the same script in GitHub Actions and attaches the result to the release.

To cut a release: bump `VERSION` in `shoggoth/server.py`, then `git tag vX.Y.Z && git push --tags`.

## Shoggoth is built on:
- **[Pi](https://github.com/earendil-works/pi)** (MIT) is bundled unmodified in the AppImage; its licence is included alongside it.
- **[Lemonade](https://github.com/lemonade-sdk/lemonade)** (Apache-2.0) is bundled unmodified in the AppImage; its licence is included alongside it.
- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** (MIT) and the other backends are downloaded by Lemonade when you install them.
- **[Chakra Petch](https://github.com/m4rc1e/Chakra-Petch), [Doto](https://github.com/oliverlalan/Doto), [Inter](https://rsms.me/inter/) and [JetBrains Mono](https://www.jetbrains.com/lp/mono/)** (SIL Open Font License) are embedded in the page; their licences are in `docs/plans/assets/fonts` and ship in the AppImage.
- **[Python](https://www.python.org)** (PSF licence), via a relocatable build from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), is bundled so the AppImage needs no system Python.

## License

Shoggoth is [MIT](LICENSE) licensed.
