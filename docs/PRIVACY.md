# Privacy and data

Shoggoth runs inference locally. Chat content is not sent to any remote service. The only exception is web search, which requires per-query approval. This document lists what is stored, where, and every network destination Shoggoth or its bundled engines may contact.

## Data locations

| What | Where |
| --- | --- |
| Shoggoth's settings, including the keys for other apps and servers you connected (file mode `600`) | `~/.config/shoggoth/settings.json` |
| The secret that lets a browser into Shoggoth's own page (file mode `600`) | `~/.config/shoggoth/ui-token` |
| llama.cpp builds you've downloaded | `~/.local/share/shoggoth/llamacpp/<tag>/<backend>/` |
| llama.cpp's own state: presets, the running router, and its view of your Models folder | `~/.local/share/shoggoth/llamacpp/` and `~/.local/share/shoggoth/views/llamacpp/` |
| The engine log (shown at the bottom of Advanced > Server) | `~/.local/share/shoggoth/llamacpp/llama-server.log` |
| Links to models you chose to bring in, and each engine's view of your Models folder | `~/.local/share/shoggoth/` (`imported-models/`, `views/`) |
| Models you download | the folder you chose (a **Models** folder with a subfolder for each engine), or the Hugging Face cache if you kept that |
| Skills you added, and which are switched on | `~/.local/share/shoggoth/skills/` (skills your models wrote are under `sets/own/`) |
| Speech to text: whisper.cpp builds, its model, and its log | `~/.local/share/shoggoth/whisper/` |
| Read aloud: the Piper program, its voices, and its log | `~/.local/share/shoggoth/piper/` |
| Pi packages you installed | `~/.local/share/shoggoth/pi-packages/` |
| Files you attach to a chat | `attachments/` inside the chat's folder, or `~/.local/share/shoggoth/cowork/scratch/<chat>/attachments/` when the chat has no folder |
| LAN sharing: the certificate, its private key and the hashes of paired clients' keys (folder mode `700`, files mode `600`) | `~/.local/share/shoggoth/lan/` |
| Newer versions of Pi that you downloaded | `~/.local/share/shoggoth/pi/<version>/` |
| Chat conversations, jobs, their transcripts, the folders Shoggoth has worked in (and the commands you always allow there), work copies of folders awaiting review, and Pi's configuration (rewritten at each start) | `~/.local/share/shoggoth/cowork/` |
| Display preferences | your browser's local storage |

`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are honored.

**Migration from Zest.** On first start, Shoggoth moves `~/.config/zest` and `~/.local/share/zest` to the paths above, preserving chats, skills, settings, and keys. Clients using a `zest-` key continue to work, and legacy `ZEST_*` environment variables are still read.

To reset, delete `~/.config/shoggoth` and `~/.local/share/shoggoth`. Downloaded models are stored elsewhere unless the chosen models folder is inside those paths.

## Network behavior

Chat content stays on the machine unless you choose otherwise: a model on a server you connected elsewhere sends that chat to that server, and web search sends the words of the search. Speech to text, read aloud and attachments run or stay locally.

- The model has a web search tool. Every search asks first and shows the exact words it wants to search for. Those words, and only those, go to the search provider.
- You can switch web search off for good in the chat's **Details** panel.
- The default provider is DuckDuckGo. It needs no key, but it has no official search service, so it can refuse automated searches. Shoggoth makes one request per search and never retries. If DuckDuckGo refuses, Shoggoth suggests a free [Brave Search](https://brave.com/search/api/) or [Tavily](https://app.tavily.com/) key. You paste the key into the same panel. Shoggoth stores it in its settings file on this computer.

Apart from search, a chat with a folder runs locally. Pi starts offline, with telemetry and update checks off. It talks only to your local engine. If you add your own extensions, MCP servers or web tools to Pi, those can reach the network.

**Speech and attachments.** The mic records in the page and sends the audio to `whisper-server` on this computer; nothing is kept after it is transcribed. Read aloud runs Piper on this computer and keeps the WAV only until the page plays it. A file you attach is written to the chat's folder and read there. None of these three contacts a remote service, apart from downloading the engine or voice you asked for.

**Sharing on your local network.** When "Share my models with other Shoggoths on this network" is on, Shoggoth sends a small UDP beacon to port 13309 every 2 seconds. The beacon carries this computer's name, a profile id, the Shoggoth version, the gateway port, the certificate fingerprint and the number of models. It carries nothing else: no model names, no chats, no folder names and no keys. Other Shoggoths that heard the beacon and were paired with this one connect over HTTPS only. Removing a pair stops that computer's key at once, so it cannot reach the shared models again.

**Servers online.** No online service is built in. A server you connect by address, or describe in your own `engines.d` file, is contacted only after you connect it. Chats sent to a model on it leave this computer and go to that server.

Keys for these servers are stored in Shoggoth's settings file, which is kept at mode 600 so only your user account can read it.

Destinations contacted by Shoggoth and its bundled engine:

- **html.duckduckgo.com, api.search.brave.com or api.tavily.com:** only when you let the model search the web. Only the words of that search are sent.
- **codeload.github.com:** only when you paste a link under Skills, to download that repository's skills.
- **registry.npmjs.org:** to look for and download Pi updates, and to download a Pi package you install from Shoggoth's pinned list. The automatic-check switch covers the updates.
- **api.github.com and github.com:** to look for and download Shoggoth and llama.cpp updates (you can switch off the automatic check), to download the llama.cpp builds you install, and to download whisper.cpp for speech to text or Piper for read aloud when you ask for them. Servers you connect under Advanced > Engines are contacted only at the address you gave.
- **Hugging Face:** your engines download the models you choose from there. Shoggoth also downloads whisper.cpp's speech model from `ggerganov/whisper.cpp` and a Piper voice from `rhasspy/piper-voices` when you set those up. Shoggoth reads a repository's public file list when you add a model by link. Gated models, which need a login, aren't supported yet.
- **Benchmark and community sites:** the links in **How do I choose a model?** open in your browser only when you click them. Shoggoth never contacts those sites itself.
