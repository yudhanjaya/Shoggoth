<h1 align="center"><img src="docs/readme/banner.png" alt="Shoggoth: run AI models on your own computer. Put them to work" width="100%"></h1>

<p align="center"><img src="docs/readme/badge.png" alt="v 4.2: Hitchhiker’s Edition" width="380"></p>

[![Release](https://img.shields.io/github/v/release/yudhanjaya/Shoggoth)](https://github.com/yudhanjaya/Shoggoth/releases)
[![Downloads](https://img.shields.io/github/downloads/yudhanjaya/Shoggoth/total.svg)](https://github.com/yudhanjaya/Shoggoth/releases)
[![License: MIT](https://img.shields.io/github/license/yudhanjaya/Shoggoth)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Linux%20x86__64-blue)
[![Stars](https://img.shields.io/github/stars/yudhanjaya/Shoggoth?style=social)](https://github.com/yudhanjaya/Shoggoth)

# Run AI on your own computer. Put it to work.

**Shoggoth is one download that runs open-weights language models on your own hardware.** Chat with them, draw diagrams, write documents, or let a model read and change the files in a folder while you watch. Shoggoth gives you the complete setup - backend, agent, harness, monitoring - without needing much technical know-how.

**[⬇ Download for Linux](../../releases/latest)** · [Install in three steps](#install) · [Read the docs](shoggoth/docs.md)

![Shoggoth working in a folder: the model's answer after it fixed a bug, with the model in its tube at the top](docs/screenshots/chat.png)

*Working in a folder: the model found why two tests failed, fixed it with a one-line change (shown as a diff) and ran the tests again.*

****

## Why?

The goal of Shoggoth is to be an easy on-ramp to running models locally for new users. I built this to help a friend get local AI models running on their own hardware without having to deal with a million different options, command-line configurations and Reddit posts.

The local AI space can be hard to decipher sometimes - llama, lemonade, VLLM, sglang, the millions of *what agent? what harness?* questions . . . a lot of people want to tinker aren't used to the various terminals, programs and jargon that we use in the local AI space. 

Shoggoth makes that task easier. It also bundles in help docs so that you understand how this stuff works over and under the hood. 

Shoggoth is not meant to be a replacement for various backends or front-ends. I hope that users will graduate to being comfortable setting up Strata, Maya, Ollama or whatever complex VLLM setup they want . . . connecting it to Shoggoth if they like, and running OpenCode or DeepSeek Harness or Hermes Agent to their heart's content. Shoggoth is meant to make it easier for new users and to help them "graduate". 


---

## What you get

| | |
| --- | --- |
| **Private by default** | Chats run on your computer. The one exception is web search. The model asks before every search. |
| **It works on your files** | Choose a folder. The model reads, searches, edits and runs things there while you watch. It asks before it changes anything. |
| **Bigger jobs, in the background** | Describe the work and how to tell it is done, such as a passing test. The job runs round after round. You review its changes before they touch your folder. |
| **Diagrams and documents** | Ask for a picture or a README. It appears in the chat. |
| **Skills** | A skill is a short instruction sheet that makes small models far more dependable. Add more by pasting a GitHub link. |
| **Models that fit your computer** | Shoggoth tells you which models will run well on your hardware. It downloads them for you. |
| **Your own server** | Shoggoth runs [Lemonade](https://lemonade-server.ai). Under Advanced you can connect a server you run yourself (Ollama, LM Studio, llama.cpp, Strata, Maya, Unsloth) and use its models in chat. Shoggoth doesn't install or start those. |
| **Use your models in other apps** | Share them with Hermes, or anything that talks to an OpenAI-style server. |

## Install

1. **Download** `Shoggoth-<version>-x86_64.AppImage` from the [latest release](../../releases/latest).
2. **Run it:**

   ```bash
   chmod +x Shoggoth-*-x86_64.AppImage
   ./Shoggoth-*-x86_64.AppImage
   ```

3. **Follow the Getting ready screen.** It opens in your browser, asks where your models should live, sets up the engine for your graphics card (CUDA, Vulkan or CPU), and suggests a first model per size. Press **Get it**, then start chatting.

![The Getting ready screen: the checklist is done and Shoggoth suggests a first model per size](docs/screenshots/welcome.png)

**You need:** Linux on x86_64, a few common libraries (OpenSSL 3, zstd, libdrm), your graphics driver if you want the GPU, and internet for the first-run downloads. The AppImage runs on older systems such as Ubuntu 22.04. No FUSE? Run it with `--appimage-extract-and-run`.

Closing the browser tab quits Shoggoth after about three minutes. Already have models, or your own Lemonade? Shoggoth finds them.

## Picking a model

Shoggoth suggests one model per size on first run. It keeps a tested list on the Models page.

| Size | Pick | Why |
| --- | --- | --- |
| **Small** | LFM2.5 8B A1B | Very fast, calls tools well, keeps answers short, runs on almost anything. Start here. |
| **Medium** | Qwen3.6 35B A3B MTP, or Gemma 4 26B A4B | The general pick for everyday tasks, research and agent work. |
| **Large** | Qwen3.8 Flash-Next (IQ3_S) | For computers with a GPU and about 64 GB of RAM. Runs models far bigger than your graphics card. Slower. Takes a couple of minutes to start. |

![Models: the recommended picks, each with its creature, and a button to set up and get it](docs/screenshots/models.png)

## Models you can see

Shoggoth draws every model as a many-eyed creature in a glass tube, wired to a bench of lab equipment. A model's name decides its shape and colour. The bigger the model, the bigger the creature, with more eyes and more hands. You can turn them off.

![One family at three sizes: Qwen3.6 at 9B, 35B and 100B. The bigger the model, the bigger the creature, with more camera eyes and more hands holding pens, papers and keyboards](docs/readme/ladder.png)

The **Hardware** page puts your graphics cards on the bench. The **Server** page shows which apps are asking your models for what.

| | |
| --- | --- |
| ![Advanced > Hardware: two graphics cards as machines at the back of a lab bench](docs/screenshots/hardware.png) | ![Server: the loaded model in its jar, a flask for the request, and a card with how long it took](docs/screenshots/server.png) |
| **Hardware**: memory, load and heat for every card. | **Server**: every request from every app, live. |

## You stay in control

- **Four levels of care in every chat:** Ask first, Edit freely, Do everything, or Look only.
- **Jobs work on a copy** of your folder by default. The **Changes** tab shows what they did. You apply what you want.
- **Tool cards** show each step. Edits show a diff.

![Shoggoth asking before it runs a command](docs/screenshots/work-approve.png)

## Make it yours

Fifteen themes, from the calm cream default to Braun T3, Teenage Engineering, Monokai Pro, Dracula and Catppuccin.

| | |
| --- | --- |
| ![Braun T3](docs/screenshots/theme-braun-t3.png) | ![Teenage Engineering](docs/screenshots/theme-teenage-engineering.png) |
| **Braun T3**: matte warm plastic, a glowing lamp and a speaker grille. | **Teenage Engineering**: hard shadows, thick outlines and an orange bar. |
| ![Monokai Pro](docs/screenshots/theme-monokai-pro.png) | ![GitHub Light](docs/screenshots/theme-github-light.png) |
| **Monokai Pro**: dark and warm, with raised keys and a recessed chat. | **GitHub Light**: clean and bright, in GitHub's blue. |

## Why the name?

Because the large language models we run are, effectively, shoggoths:
<img width="1357" height="758" alt="image" src="https://github.com/user-attachments/assets/9d30246f-b926-4e1a-a035-3e43bfb1b242" />
<sub>Source: [Know Your Meme](https://knowyourmeme.com/sensitive/memes/shoggoth-with-smiley-face-artificial-intelligence)</sub>

indescribable alien intelligences that deal in madness and inhumane logics. See also: (https://en.wikipedia.org/wiki/Shoggoth) and (https://www.stilldrinking.org/programming-sucks) for why what we're doing is insane.

And because I love it. (Shoggoth used to be called Zest.)

## Under the hood

Shoggoth drives two open-source programs, so you don't have to:

- [Lemonade](https://lemonade-server.ai) runs your models.
- [Pi](https://pi.dev) lets a model read files, edit them and run commands.

The AppImage includes Lemonade and Pi. Everything updates from **Advanced > Engines** with one button, checked against published checksums.

## Your data and privacy

Everything stays on your computer. Chats, jobs, skills and settings live in `~/.local/share/shoggoth/` and `~/.config/shoggoth/`. Shoggoth goes online only to download models and updates you ask for, to fetch skills when you paste a link, and for web search, which asks first and shows the exact words. Full details: [docs/PRIVACY.md](docs/PRIVACY.md).

## Learn more

| | |
| --- | --- |
| [User guide](shoggoth/docs.md) | The in-app guide: chat, jobs, models, tuning, skills, troubleshooting. |
| [Architecture](docs/ARCHITECTURE.md) | Components, request flow, engine integration, platform support. |
| [Agent runtime](docs/AGENT-RUNTIME.md) | Pi integration, approval gate, jobs, work copies, skills. |
| [Engines](docs/ENGINES.md) | Add an engine with a `provider.yaml`. |
| [Security model](docs/SECURITY.md) | Authentication, network exposure, and the limits of the approval gate. |
| [Privacy and data](docs/PRIVACY.md) | On-disk layout and every network destination. |
| [Development](docs/DEVELOPMENT.md) | Run from source, test, build, release. |

## Tested on:

- Dikkenek: an Intel Xeon CPU E5-2697 v2, 128 GB of DDR 3 RAM, two AMD Radeon VII GPUs for 32 GB VRAM
- Leviathan: An AMD Ryzen 5800X3D, 64 GB of DDR4 VRAM, an Nvidia RTX 4080 Super and an RTX 3080 for a combined 26 GB VRAM
- Minarchist: A 6th generation Intel i5 chip I pulled from an old HP office PC, 24 GB of DDR4 RAM, a GTX 1080 GPU for 10 GB of VRAM

## Shoggoth is built on

- **[Pi](https://github.com/earendil-works/pi)** (MIT), bundled unmodified in the AppImage. Its licence is included alongside it.
- **[Lemonade](https://github.com/lemonade-sdk/lemonade)** (Apache-2.0), bundled unmodified in the AppImage. Its licence is included alongside it.
- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** (MIT) and the other backends, which Lemonade downloads when you install them.
- **[Chakra Petch](https://github.com/m4rc1e/Chakra-Petch), [Doto](https://github.com/oliverlalan/Doto), [Inter](https://rsms.me/inter/) and [JetBrains Mono](https://www.jetbrains.com/lp/mono/)** (SIL Open Font License), embedded in the page. Their licences are in `docs/plans/assets/fonts` and ship in the AppImage.
- **[Python](https://www.python.org)** (PSF licence), via a relocatable build from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), bundled so the AppImage needs no system Python.

## License

Shoggoth is [MIT](LICENSE) licensed.
