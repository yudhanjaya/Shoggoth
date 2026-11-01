# Developing Shoggoth

Moved from the README. For using Shoggoth, see the [README](../README.md) or the Docs tab in the app.

## Running from source

You need Python 3 and its standard library only. Shoggoth is developed on 3.12.

```bash
git clone https://github.com/yudhanjaya/Shoggoth.git
cd Shoggoth
python3 shoggoth/server.py
```

Diagrams in Chat need Mermaid. Run `./packaging/fetch-vendor.sh` once. It downloads 26 MB and checks it against its published checksum. The AppImage already includes Mermaid.

Chat's agent features need [Pi](https://pi.dev), which needs Node 22.19 or newer. Install it with `npm install -g @earendil-works/pi-coding-agent`, or point `SHOGGOTH_PI` at a `pi` binary. The AppImage already includes Pi and Node.

Then open <http://127.0.0.1:13306>. It opens by itself unless you pass `--no-browser`. A source checkout doesn't include Lemonade. On the first-run screen, **Download Lemonade** fetches it (about 7 MB), or you can connect to one you already run.

| Flag | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `--port` | `SHOGGOTH_PORT` | `13306` | Port Shoggoth listens on |
| `--host` | `SHOGGOTH_HOST` | `127.0.0.1` | Address Shoggoth listens on |
| `--lemonade HOST:PORT` | `LEMONADE_API_KEY` for its key | the choice saved in Shoggoth's settings | Use this Lemonade and skip the welcome screen |
| `--no-browser` | | | Don't open a browser tab |
| `--app` | | | Quit automatically once the tab is closed (the AppImage does this) |
| (none) | `SHOGGOTH_LEMONADE_DIR` | none | A folder containing a `lemond` to use as the built-in Lemonade |

> **Security.** Shoggoth's own page can change settings, start engines and run commands in a folder, so it doesn't accept everything that can reach its port.
>
> - Shoggoth keeps a secret in `~/.config/shoggoth/ui-token` (mode 600).
> - The window Shoggoth opens carries a one-time code. The code is good for two minutes, once. The server swaps it for an HttpOnly, SameSite=Strict cookie.
> - Every other request is refused with 401. A browser that hasn't been let in sees a page saying to start Shoggoth again.
> - A script can send the secret as `Authorization: Bearer <secret>`. `SHOGGOTH_AUTH=off` switches all this off, for a machine only you use.
> - Running Shoggoth a second time asks the running one for a fresh code.
> - By default it listens on `127.0.0.1` only, and refuses requests whose `Host` header isn't this computer. This defends against DNS rebinding.
> - The built-in Lemonade is private. It has its own port and a random API key that only Shoggoth knows.
> - Other apps use the separate key-protected door described under "Using your models in other apps" in the [docs](../shoggoth/docs.md), never the page itself.
>
> Only use `--host 0.0.0.0` on a network you trust.

## How it works

Shoggoth is a small Python program with no dependencies (`shoggoth/server.py` plus a few modules) and one HTML page (`shoggoth/index.html`).

- `shoggoth/gateway.py` is the door for other apps and the request recorder. Both it and Shoggoth's own chat forward requests through one function. That function streams the answer back untouched, and notes whether the model is waiting, thinking (`reasoning_content`, or text inside `<think>` tags) or writing.
- `shoggoth/agent.py` runs Pi (`pi --mode rpc`, one process per task) against Lemonade through Shoggoth's own `/api/v1` proxy. It relays Pi's JSON events to the page as server-sent events. `shoggoth/pi/zest-gate.js` is the Pi extension that makes Pi ask before it edits or runs commands. It keeps its old name so existing setups find it. Details are in [COWORK.md](COWORK.md).
- `shoggoth/hf.py` reads a Hugging Face repository's file list and groups the GGUF files by quantisation.
- `/api/*` passes straight through to Lemonade, so the page needs no CORS changes. Chat uses the OpenAI-compatible `/api/v1/chat/completions` stream. Model downloads use Lemonade's background download jobs.
- Everything Shoggoth changes in Lemonade goes through Lemonade's own HTTP API: `/internal/config` and `/internal/set` for settings, `/v1/models/{id}/options` for per-model options, and `/v1/install` for backends. Shoggoth doesn't need the `lemonade` command-line tool.
- The built-in Lemonade is the project's official "embeddable" build. Shoggoth starts it as `lemond <data folder> --port … --no-broadcast`, with a random `LEMONADE_API_KEY`. If a previous Shoggoth left one running, the new Shoggoth reuses it instead of starting a second.
- Device order matters. llama.cpp numbers devices differently from `nvidia-smi`: CUDA puts the fastest card first. Shoggoth asks the llama.cpp binary for its own list, so the names and IDs line up.
- Live GPU numbers come from `nvidia-smi` for NVIDIA cards, and from the Linux `amdgpu` driver's sysfs files for AMD cards: VRAM used and total, load, temperature, power, fan, and which processes hold memory. The log is Lemonade's own output, or `journalctl -u lemond` for a system service.

## Good to know

- **Live telemetry covers NVIDIA and AMD, on Linux.** Intel devices are listed and selectable, but show capacity without live usage, and the fit meter treats them as empty. The AMD numbers come from the kernel, so they need no extra tools. They have only been tested against a simulated amdgpu sysfs tree and a screenshot from a two-Radeon VII machine, not on AMD hardware here. Two identical AMD cards are matched to llama.cpp's devices by order, so their usage can appear swapped.
- **Shoggoth hands a Hugging Face download to Lemonade as a custom model:** `user.<name>-<quant>`, with the checkpoint `org/repo:QUANT` and the `llamacpp` recipe. Check this against your Lemonade version, because this part of its API has changed before.
- **The Hardware page shows process names.** They come from `nvidia-smi` or the amdgpu driver (the name only, not the full path), and only your own browser displays them.
- **Devices appear once a backend is installed,** because Shoggoth asks llama.cpp itself for the list rather than guessing.
- **Testing so far:** Linux with NVIDIA GPUs. The Vulkan and ROCm paths follow Lemonade's own backend data, but haven't run on real hardware yet. Reports are welcome.

## Adding an engine

An engine is one folder with one `provider.yaml`, plus a `provider.py` only when YAML can't describe it. Nothing in Shoggoth's own code needs to change to add one.

- `shoggoth/providers/_template/` is the annotated starting point.
- `shoggoth/providers/_examples/llama-server/` is a complete YAML-only engine around a plain `llama-server`, tested end to end with a real one.
- [ENGINES.md](ENGINES.md) explains every setting.
- `python3 shoggoth/server.py providers check <folder>` validates a folder the way Shoggoth itself reads it.
- `providers new <id> [--from llama-server]` creates a new engine folder, and `providers list` lists them.
- `providers doctor` asks the installed Lemonade and Strata the questions Shoggoth asks, and says if their answers have changed. Run it before a release.

## Building the AppImage

```bash
./packaging/build-appimage.sh
```

The script downloads a relocatable Python, Node.js, the Pi agent, Lemonade's official embeddable build and `appimagetool`, all pinned by SHA-256. It assembles the image and writes `dist/Shoggoth-<version>-x86_64.AppImage` plus a checksum. To bundle a newer Lemonade, change the version and checksum at the top of the script.

Pushing a tag that starts with `v` runs the same script in GitHub Actions and attaches the result to the release, along with a `Zest-` named copy so Zest 3.x installs can update themselves.

To cut a release, bump `VERSION` in `shoggoth/server.py`, then run `git tag vX.Y.Z && git push --tags`.
