# Shoggoth 5.0 release notes

Draft, 15 November 2026. This describes what is in the 5.0 branch, what it removes, and what is still missing. It has not been through the final check.

*Heavy on the cosmos. Light on your machine.*

Shoggoth 5.0 changes the engine under the app, adds paid services and other computers, and brings speech, file attachments and model-written skills into the page. It also raises the minimum Linux version.

## What's new

### llama.cpp runs your models

Shoggoth now runs llama.cpp itself. The bundled Lemonade engine is gone.

- It downloads ggml-org's own llama.cpp release for your hardware: processor only, Vulkan, CUDA 12.8, CUDA 13.5 or ROCm 10.0. It picks CUDA 12 or 13 from your NVIDIA driver, and suggests Vulkan when it finds no NVIDIA card.
- Every download is checked against the SHA-256 digest GitHub publishes for that asset. A build with no digest is refused.
- The AppImage includes the processor-only build, so a new install can start before it downloads anything.
- One llama-server process serves every model. "Keep up to N models loaded" sets how many stay in memory (1 to 8; the default is 1).
- Each model's options live in `presets.ini`, which Shoggoth writes and checks. The two newest downloaded builds are kept, and Go back switches to an older one.
- Advanced > Engines shows the version, backend and device, and has the update check, update, restart and Go back. A settings change says when loaded models have to load again.
- After a model loads, Shoggoth measures the memory its llama-server process holds on the graphics card, and in RAM when the model runs there or offloads experts, and stores that number for later estimates. The log level does not change.
- A build that will not start says "This llama.cpp build needs a newer Linux" and names the glibc version and the supported systems.

### Models and memory

- Recommended models show a fit badge from the server: "Runs well", "Tight", "Runs partly on the processor", or "Too big for this computer, needs about N GB".
- Model names use the catalog title, then the GGUF's own name, then a clean id. The old `user.` prefix is gone.
- Shoggoth picks a context size and shows it with the reason ("Auto (32k)").
- Hugging Face search puts the allowed uploaders first, then orders by downloads, and hides repositories with no GGUF files.
- Removing a connected server removes its models from the chat list and from Pi's model list at once, without a restart.
- The catalog check requires a download link on every entry, validates quants, and warns about a checkpoint from an uploader outside the allowed list.

### Sources: what is already running

- Shoggoth finds more than 20 local engines on any port: Ollama, LM Studio, vLLM, SGLang, KoboldCpp, TabbyAPI, text-generation-webui, LocalAI, Jan, llama-swap, TGI and the rest.
- It also knows the specialized engines (Strata, PolyStrata, Maya, NInfer, Basalt), other Shoggoths, and the five paid services.
- Engine definitions are data in `engines.d`. They can name an engine and say how to recognise it. They cannot carry a command, and they cannot start, install or run anything. [docs/ENGINES.md](ENGINES.md) describes the fields.
- When several engines share a port, the Models page groups them as "Inference engine on port N" and says how to tell them apart.
- The Models page has a sources strip. The chat bar's picker groups models the same way, greys out a source that is offline, and writes both the source and the model when you switch.
- A custom connection takes an address, a key, an API format, a model list, a context size, and whether it runs on this computer. A Test button probes it, and "Recognise this next time" saves it as a definition the server validates.
- `/v1/models` carries `{"shoggoth": {"fit", "loaded"}}` on each model, so another app or Shoggoth can say "Loads on desk-pc".

### Paid services

- Five services are built in: OpenRouter, Nous Portal, OpenCode (Zen and Go endpoints), Command Code and Google Gemini.
- A key is kept on this computer and shown only by its last four characters. The card shows the destination host, the price where the service publishes one, and a link to its terms.
- A chat on a paid model keeps a "Leaves your computer: <service>" chip. Shoggoth does not pick a paid model for you.

### Other Shoggoths on your network

- Turn on "Share my models" and give this computer a name. Another Shoggoth finds it under "Other Shoggoths" and pairs with a six-digit code.
- Both screens show a short fingerprint to compare. Each paired computer gets its own key, and Remove revokes it at once.
- Paired computers speak HTTPS only. A request from another device that uses plain HTTP gets a 400 that says to use `https://`. Plain HTTP still works on this computer.
- When a paired Shoggoth stops answering, its source shows as offline with its name. The beacon finds it again by fingerprint.
- The beacon runs every 2 seconds on UDP port 13309. It carries the computer's name, profile id, version, gateway port, certificate fingerprint and model count. It carries no model names, chats, folders or keys.

### Speech

- The composer has a mic button. Hold it to talk, or click to start and stop.
- The page records 16 kHz mono audio with an AudioWorklet and wraps it in a WAV, so ffmpeg is not needed. The words land in the message box and are not sent until you send them.
- whisper.cpp runs on the processor. The first press installs it and its model and shows the progress in the button.
- Speech runs on this computer. The recording goes to the local engine and is not kept.

### Skills and tools

- A model can propose a skill with `save_skill`. A card shows the whole `SKILL.md`, every extra file and any risks, and asks Save, Save and turn on, Don't save or Replace. It asks in every mode, including Do everything.
- Skills under "Written by your models" can be edited in a plain box or deleted. "Make a skill from this chat" asks the model to distil what worked.
- The bundled set is now named Shoggoth. On and off choices saved under the old name still work.
- A bundled `writing-skills` skill explains when a skill is worth keeping and how to write one for a small model.
- The Skills page has a Tools section for checked Pi packages. Each says "Runs code on your computer". Shoggoth installs only a pinned version from its own list, checked against the publisher's checksum. Installing or removing one restarts idle chats.

### Files in a chat

- The paperclip and drag-and-drop save a file under `attachments/` in the chat's folder, or its scratch folder when the chat has none.
- The name is cleaned, a collision gets `-1`, `-2`, and one file is capped at 50 MB. A file is written as it came and is never run.
- A chip says how the chat will read it: text, an image, "PDF, read as text", "PDF, pages as images", or a spreadsheet or Word file that needs a tool.
- An image goes to a model that can see pictures as picture content. A model that cannot see it says so on the chip, and the image is not sent.

### The screen

- The Models page has a sources strip, and the chat bar's model picker is grouped by source.
- The creature sits in a vertical tube on screens 1280 px and wider, in a short strip from 900 to 1279 px, and behind a button below 900 px. The tube has a portrait frame, and a collapsed column is remembered for that browser.
- The recommended cards, the fit words, the model names and the empty states were rewritten in plain English. The motto is in the rail: *Heavy on the cosmos. Light on your machine.*

### Packaging and supported systems

- The AppImage is named Shoggoth everywhere. The desktop entry and the AppStream metainfo match, and the release uploads a `.zsync` for AppImageUpdate.
- The release workflow builds on Ubuntu 24.04, because the bundled llama.cpp needs glibc 2.38.
- Updates for Shoggoth, llama.cpp and Pi are in one place, checked against published checksums.

## What's removed

- **Lemonade.** Shoggoth no longer bundles it, installs it or starts it. If you run Lemonade yourself, connect it like any other engine. Shoggoth will not start, stop or update it.
- **Plain HTTP on the network.** Only this computer may use `http://127.0.0.1`. From another device, the gateway answers HTTP with a 400 that says to use `https://`.
- **Ubuntu 22.04 and other old systems.** 5.0 needs glibc 2.38 or newer: Ubuntu 24.04, Debian 13, Fedora 39, Linux Mint 22 or newer. The official llama.cpp builds need it, and the router cannot run through the old loader.
- **The Zest release copy.** The release workflow no longer publishes a second, Zest-named asset. A Zest 3.x install that never updated will not see 5.0 by itself; download the Shoggoth AppImage by hand.
- **Old `user.` model ids.** They are no longer used. `model-aliases.json` maps each old id to its new one, so old chats and saved settings keep working.

## Moving from 4.x

The first start of 5.0 moves a 4.x setup across. It never moves, changes or deletes a model file, and it is safe to run again. Each step is recorded in `migrate5.json`.

- It links the GGUFs 4.x downloaded into the folder llama.cpp reads, with hard links on the same disk and symbolic links otherwise.
- It makes `model-aliases.json`, so an old model id finds its new model.
- It carries each model's saved context size and extra llama-server options into `presets.ini`, and drops options llama.cpp no longer accepts.
- It stops the engine 4.x started. An engine you started yourself is left alone.
- It counts the space 4.x's own files take. Advanced > Engines then offers to delete those files, and never a model.
- Nothing is downloaded during the move, and nothing is deleted without asking.

Tuning records written by 4.x are kept as history. They cannot be undone from the Tuning page, because their options belong to the old engine. Shoggoth says so instead of writing those options into the new presets.

## Known gaps

These are the parts the branch does not finish or has not verified.

### Not finished

- **Model downloads from the page.** The server can download from Hugging Face with resume and a checksum check, but the page's Get it and download controls still call the old `/api/v1/pull` and `/api/v1/downloads` routes. The server does not forward those routes, so the buttons report that downloads are not available in this copy. This is the first thing to fix before release.
- **Read aloud.** Piper is on the server, and `POST /ui/speech/speak` and `/v1/audio/speech` work, but the page has no Read aloud button. It is off by default.
- **PDF text.** The attachment chip chooses and shows a PDF handler, but nothing runs `pdftotext -layout` or `pdftoppm` yet. A PDF is passed to the chat as a path.
- **Spreadsheets and Word files.** They are labelled as needing a tool that is not installed. No package provides that tool in 5.0.
- **Pi packages.** The Tools section and the install and remove routes exist, but every package on the list is disabled until it has been tested. None can be installed in 5.0.
- **Client-only set-up.** The server records that a connected Shoggoth will serve the models, but the page does not use that flag to skip the llama.cpp download in Getting ready.
- **The website.** The `site/` update for 5.0 did not finish.
- **The release itself.** `VERSION` is still `4.3.0`, the AppStream metainfo still lists 4.3.0, and no 5.0 AppImage has been built.

### Not tested

- Vulkan and ROCm llama.cpp builds have not been run.
- A split or vision model has not been run through the router.
- LAN sharing has not been tried on a real second machine. A person on the network who fakes the first beacon can still pair as a man in the middle; comparing the short fingerprint on both screens is the defence. A password-authenticated key exchange would close it.
- No hosted service has been tried with a real key. Gemini thought signatures through Pi are untested.
- The memory estimate is not yet checked within 10%. It needs load logs for LFM2.5-8B, Gemma-4-12B and Qwen3.6-35B-A3B. One real measurement exists: Qwen3-0.6B Q8_0 at 32768 context measured 2.87 GB on the RTX 3080.
- The mic has not been used with a real microphone, browser or whisper model here.
- `make-skill` and the `save_skill` card have not been tried with a live Pi session and model.

### Not included

- **Qwen3.6-Next** from issue #51 is not in the catalog. No such model exists on Hugging Face, so 5.0 does not invent an entry for it.
- 4.x tuning records are not converted to the new preset keys. They are marked as made by the old engine and cannot be undone here.
