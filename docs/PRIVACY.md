# Privacy and data

Shoggoth runs inference locally. Chat content is not sent to any remote service. The only exception is web search, which requires per-query approval. This document lists what is stored, where, and every network destination Shoggoth or its bundled engines may contact.

## Data locations

| What | Where |
| --- | --- |
| Shoggoth's settings, including the built-in Lemonade's API key and the key for other apps (file mode `600`) | `~/.config/shoggoth/settings.json` |
| The secret that lets a browser into Shoggoth's own page (file mode `600`) | `~/.config/shoggoth/ui-token` |
| Lemonade updates you've downloaded | `~/.local/share/shoggoth/lemonade/<version>/` |
| The built-in Lemonade's own config and the backends it installs | `~/.local/share/shoggoth/lemonade-data/` |
| Its log (shown at the bottom of Advanced > Server) | `~/.local/share/shoggoth/lemond.log` |
| Links to models you chose to bring in, and each engine's view of your Models folder | `~/.local/share/shoggoth/` (`imported-models/`, `views/`) |
| Models you download | the folder you chose (a **Models** folder with a subfolder for each engine), or the Hugging Face cache if you kept that |
| Skills you added, and which are switched on | `~/.local/share/shoggoth/skills/` |
| Newer versions of Pi that you downloaded | `~/.local/share/shoggoth/pi/<version>/` |
| Chat conversations, jobs, their transcripts, the folders Shoggoth has worked in (and the commands you always allow there), work copies of folders awaiting review, and Pi's configuration (rewritten at each start) | `~/.local/share/shoggoth/cowork/` |
| Display preferences | your browser's local storage |

`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are honored.

**Migration from Zest.** On first start, Shoggoth moves `~/.config/zest` and `~/.local/share/zest` to the paths above, preserving chats, skills, settings, and keys. Clients using a `zest-` key continue to work, and legacy `ZEST_*` environment variables are still read.

To reset, delete `~/.config/shoggoth` and `~/.local/share/shoggoth`. Downloaded models are stored elsewhere unless the chosen models folder is inside those paths.

## Network behavior

Chat content stays on the machine. The single exception is web search.

- The model has a web search tool. Every search asks first and shows the exact words it wants to search for. Those words, and only those, go to the search provider.
- You can switch web search off for good in the chat's **Details** panel.
- The default provider is DuckDuckGo. It needs no key, but it has no official search service, so it can refuse automated searches. Shoggoth makes one request per search and never retries. If DuckDuckGo refuses, Shoggoth suggests a free [Brave Search](https://brave.com/search/api/) or [Tavily](https://app.tavily.com/) key. You paste the key into the same panel, and Shoggoth stores it in its settings file on this computer.

Apart from search, a chat with a folder runs locally. Pi starts offline, with telemetry and update checks off, and talks only to your Lemonade. If you add your own extensions, MCP servers or web tools to Pi, those can reach the network.

Destinations contacted by Shoggoth and the bundled engines:

- **html.duckduckgo.com, api.search.brave.com or api.tavily.com:** only when you let the model search the web, and only with the words of that search.
- **codeload.github.com:** only when you paste a link under Skills, to download that repository's skills.
- **registry.npmjs.org:** to look for and download Pi updates. The automatic-check switch covers it.
- **api.github.com and github.com:** to look for and download Shoggoth and Lemonade updates (you can switch off the automatic check), for Lemonade to download the backends you install, and, if you use large models, for Strata's installer and its checksum list. Strata checks for its own engine updates from its own feed.
- **Hugging Face:** your engines download the models you choose from there, and Shoggoth reads a repository's public file list when you add one by link. Gated models, which need a login, aren't supported yet.
- **Benchmark and community sites:** the links in **How do I choose a model?** open in your browser only when you click them. Shoggoth never contacts those sites itself.
