## What Zest is

Zest lets you use AI on your own computer. You talk to it like a chat app, but nothing leaves your machine: the AI, your conversations and your files all stay here. The one exception is web search, which you control: if the model wants to look something up, it asks first and shows the words it will search for.

You can use it in two ways, and switch between them whenever you like:

- **Just chat.** Ask a question, get an answer, think something through, draw a diagram, draft some writing.
- **Work in a folder.** Pick a folder and the AI can look at the files in it, change them, and run commands, while you watch. It asks your permission before it changes anything, unless you tell it not to.

Everything else in Zest (Models, Skills, Advanced) exists to make those two things work well.

## The parts, in plain words

Four things work together. You only ever see Zest, but it helps to know who does what.

```mermaid
flowchart LR
    you["You"] --> zest["Zest: the window you are looking at"]
    zest --> pi["Pi: the helper that reads files and runs steps"]
    pi --> lem["Lemonade: runs the model"]
    zest --> lem
    lem --> model[("The model: the AI itself")]
```

| Part | What it does |
| --- | --- |
| **Zest** | The window you are using. It shows the conversation, asks your permission, and manages everything else. |
| **Pi** | A helper that lets a model do things: read a file, edit it, run a command, follow a skill. It comes inside Zest. |
| **Lemonade** | The engine that loads a model into your computer's memory and gets answers out of it. It also comes inside Zest. |
| **The model** | The AI. A large file, usually a few gigabytes, that you download once and keep. |

## Your first minutes

The first time you open Zest it walks you through four short steps: start the helper, check your graphics card, download a model, start chatting. Each is one button. If you pick the suggested model (Quick, Balanced or Thorough) you will be chatting within minutes, depending on your connection.

You can repeat the tour any time: **Help**, then **Run the welcome steps again**.

## Chatting

Open **Chat** and type. The bar under the title shows which model will answer and whether it is in memory. Press **Load** to load it ahead of time, or just send a message and Zest loads it first. The first answer takes a little longer for that; after it, answers start quickly. **Unload** frees the graphics memory. While the model works, a small strip under the reply shows what it is doing: 📖 reading your message, 🤔 thinking, ✍️ writing, 🛠️ using a tool (🍋 loading, or 🙋 waiting for your OK, when that is the case). Its thinking appears above the reply and folds away when it is done.

- Your conversations are listed on the left, with any jobs they started nested underneath. Click one to carry on where you left off, even after closing Zest. To delete one, hover over it and press the cross.
- Speed: the pill at the top shows how fast the model is writing, in tokens per second. Anything above about 5 is comfortable to read.
- How it thought: some models think before they answer. A small *How it thought* fold shows that thinking if you are curious.
- Adding a note: if an answer is taking a while, you can type again. Your note is passed along when the AI is ready for it. Press **Stop** to cancel what it is doing.
- The side panel (top right) has three tabs. **Jobs** shows background jobs, **Changes** shows what a job changed, and **Details** shows the folder, how careful it is, which model is answering, how full its memory is, and which skills are on.

A chat with no folder is a plain conversation. The AI cannot see or change anything on your computer.

**Web search.** When a question needs something recent or the AI is unsure, it can search the web. Every search asks first and shows the exact words it wants to search for, so you decide what leaves your computer. Choose **Allow for this session** to stop being asked in that chat. You get only the titles, links and short snippets of the top results. By default it uses DuckDuckGo, which needs no key but has no official search service, so it can refuse. When that happens Zest does not keep retrying: it tells you, and the AI carries on from what it knows. For search that works every time, get a free key from [Brave Search](https://brave.com/search/api/) or [Tavily](https://app.tavily.com/), then open the **Details** panel, find **Web search**, choose the provider, paste the key and press **Test search**. The key is kept on this computer and never shown again. You can also switch web search off there.

## Working in a folder

Press **Work in a folder…** under the message box and choose a folder. From then on, the AI can:

- look at files and search them,
- write new files and edit existing ones (you see exactly what changes),
- run commands, such as running a test or listing what is in a folder.

Try asking: *"Look through this folder and tell me what it's for"*, *"Write a README for this project"*, or *"Find where the settings are saved and explain it"*.

Every step appears as a small card in the conversation: what it read, what it changed, what it ran, and what came back. Click a card to open it. Edits show the old line and the new line, in red and green.

You can give a conversation a folder at any point, or change its folder from the Details tab. To let it see another folder as well, press **+ Folder** at the top. A new folder is **look only** by default: the AI can read it but not change it. Click its label to let it change files there too.

Delete a conversation and the files it changed stay exactly as they are.

## Jobs

A **job** is a bigger piece of work that the AI keeps going on its own while you do something else. Use it when one answer is not enough, such as "make the tests pass" or "tidy up these notes".

Press **Start a job…** under the message box (a chat with a folder only), then fill in:

- **What should it get done?** Describe the goal in plain words.
- **Done when.** Either a command that succeeds, such as `python3 -m pytest -q`, or a description in your own words. Zest runs the command itself after every round, so it does not depend on the AI saying it is finished.
- **Limits.** The most rounds and the most time it may use.
- **Where it works.** The safe choice is **a copy**: your folder stays untouched while the job runs. Choose **the folder itself** only for something you can roll back.
- **Wait for my OK after each round**, if you want to look in between.

The job appears as a card in the conversation and under its chat in the list. The card shows the round, how the check is going, and what the AI is doing right now. You can **Pause**, **Resume** or **Stop** it, or **Open** it to read everything it did and add a note for the next round. A job also stops by itself if the check stops improving, so it does not go round in circles.

When a job on a copy finishes, open the **Changes** tab. Click a file to see exactly what changed, tick the ones you want, and press **Apply**. A file you changed yourself in the meantime is skipped, never overwritten. **Throw away** deletes the copy. For a job that worked in the folder itself, the tab shows what changed, but the changes are already there.

If you close Zest while a job is working, it comes back as **Interrupted** and **Continue** picks up where it stopped. Zest stays open while a job is working, even if you close the tab.

Smaller models make mistakes. They can misread a file, invent details, or fail to use a tool at all. That is why Zest asks before it acts, and why it is worth trying a folder you have a copy of first.

## Staying in control

Under **How careful** in the Details panel, choose how much the AI may do without asking:

| Setting | What happens |
| --- | --- |
| **Ask first** (the usual) | It may look at things freely. Before every edit, new file or command, a card asks you: **Allow**, **Allow for this session**, or **Don't allow**. For a command you can also choose **Always allow**. |
| **Edit freely** | Edits to files inside the folder go ahead without asking. Commands, and anything outside the folder, still ask. |
| **Do everything** | It never asks. Use this only for a folder you can afford to lose. |
| **Look only** | It can read and propose, but cannot change anything. Good for "review this and tell me what you'd do". |

You can change the setting while a task is running. If the AI asks permission outside the folder you chose, the card says so in plain words.

**Always allow** remembers a command for that folder, such as `python3 -m pytest`, so it stops asking. It only counts when the whole line is made of commands you allowed, and it is never offered for commands that can do anything, like `rm`, `curl` or `sh`. To take one back, click the folder name at the top and press **Remove**. A folder set to **look only** can never be changed, in any setting except **Do everything**.

These questions help, but they cannot stop a harmful command that you allow. If you allow a command, it runs with your own permissions, the same as if you had typed it. Read what the card shows before pressing Allow, and keep copies of anything precious.

## Skills

A **skill** is a short instruction sheet the AI reads when a job calls for it, like a recipe card: how to plan before building, how to hunt down a bug, how to draw a diagram, how to write good documents. Small models become much more dependable when they follow one.

Zest comes with:

- **Superpowers**: a working method. Ask what the person really wants, make a plan, test first, find the cause of a bug before fixing it, and prove something works before saying so.
- **Diagrams**: draws pictures in the chat.
- **Markdown writing**: writes clear documents (READMEs, notes, changelogs, how-tos).

Open **Skills** to switch any of them on or off, one at a time or a whole set at once.

**To add more skills, paste a link.** On the Skills page, paste the web address of a GitHub page that has skills in it, for example `https://github.com/owner/repository`, and press **Add skills**. You can also link to one folder inside a repository: open the folder on GitHub and copy the address. Zest finds every skill in it and adds them. A skill is just a folder with a file called `SKILL.md`. [Hermes Atlas](https://hermesatlas.com/ecosystem/) lists many.

Good to know:

- **Only add skills from people you trust.** A skill is instructions the AI follows, and some include scripts. While you are on *Ask first*, every command still asks you.
- Fewer is better for small models. Every skill you switch on adds a line the AI reads before every answer. A handful of well-chosen skills beats a hundred.
- In a conversation with a folder, type `/` to pick a skill by name and make the AI use it. You can see when it used one: a **Skill** card appears in the conversation.
- The diagrams and writing skills also work in a plain chat. The others need a folder.
- Changes apply from your next message.

## Diagrams and documents

Ask for a picture and say what you want shown: *"Draw how someone logs in to a website"*, *"Diagram the steps of this project"*, *"Show how these tables relate"*. Zest turns the AI's answer into a real picture in the chat, with **Copy code** and **Save as image** buttons.

Small models sometimes get the drawing code slightly wrong. If a diagram fails, Zest shows what went wrong and a button, **Ask the model to fix it**. One press and it tries again.

Replies can also contain tables, lists, links, quotes and code blocks, shown neatly. In a conversation with a folder, ask it to write a document to a file and it will save it there.

## Choosing and managing models

Open **Models**. **My picks** are the models I have tried and recommend, with a sentence on whether each will run comfortably on your computer. **More models** shows the rest of what Lemonade offers that will run well. **Get it** downloads a model in the background while you carry on. **Get from Hugging Face** adds one from a link. **Settings** on a downloaded model edits how it runs, and **Chat with it** starts a chat with it. Loading and unloading happen in the chat.

- Will it fit? Zest compares a model's size with the memory that is free on your graphics card right now, and marks it comfortable, snug or too big.
- For working in a folder, pick a model that handles tools well. Look for tool-calling in its description. My picks lean that way. Models that are very small can still chat well but often fumble tools.
- Memory (the context window): this is how much the AI can keep in mind at once. Zest sets a roomy 128k by default. Working in a folder uses a lot of it, because the AI reads files. The Details panel shows how full it is, with a **Make room** button that tidies the older part of the conversation. The context menu in the chat's model bar changes the size.
- **Settings for one model** (the **Settings** button on its card) let you change which devices it uses, backend options and more. The defaults are fine for nearly everyone. Chat always loads a model with the context size in its model bar.

## Using your models in other apps

**Advanced**, then **Server**, gives you an address and a key to paste into other tools (coding assistants, chat apps, anything that talks to an OpenAI-style server). The same page shows live what those apps are asking for. It only listens on this computer unless you turn on sharing, and it always needs the key.

## Under the bonnet

Everything in **Advanced** is optional.

- **Hardware** shows your graphics cards filling up as models load, and lets you choose which to use.
- **Engines** shows the programs that run models, and which one each uses (CUDA, Vulkan, ROCm or the processor).
- **All settings** is every setting Lemonade has, searchable.
- **Setup guide** updates Lemonade, updates Pi (the agent helper), brings in models you already have from other apps, or connects Zest to a Lemonade you run yourself.

## Where things live, and privacy

Everything stays on your computer.

- **Conversations and what the AI did** are saved in Zest's own folder (`~/.local/share/zest/cowork/`). That includes the copies that jobs work on until you apply or throw them away.
- **Skills you added** are in `~/.local/share/zest/skills/`.
- **Newer versions of Pi** that you download are in `~/.local/share/zest/pi/`.
- **Models** are in the Hugging Face cache (`~/.cache/huggingface/hub`), or the folder you chose.
- **Settings and keys** are in `~/.config/zest/`.

Zest goes online only to: download models you ask for, check for Lemonade and Pi updates (you can turn that off), and fetch skills when you paste a link. Pi runs offline and sends nothing anywhere. To start from scratch, delete `~/.config/zest` and `~/.local/share/zest`.

## When something goes wrong

**The answer never starts, or it is very slow.** The model may still be loading, or it may be too big for your graphics card. Close games and other heavy programs, or pick a smaller model under **Models**.

**The model seems to have very little memory.** Look at the bar under the chat's title: it shows how much context the model has. Pick 128k from the context menu there and Zest reloads the model, which takes a moment.

**It printed something that looks like code instead of doing the task.** The model did not manage to use its tools. Try asking again more simply, or switch to a stronger model in the Details panel.

**It keeps asking permission.** That is *Ask first* doing its job. Choose **Allow for this session** for edits, **Always allow** for a command you run often, or switch to **Edit freely** for a folder you trust.

**A diagram did not draw.** Press **Ask the model to fix it**. If it still fails, ask for a simpler diagram with fewer boxes.

**"The Pi agent isn't installed."** This only happens when running Zest from source rather than the AppImage. The README explains how to add it.

**Something else is off.** **Advanced**, then **Server**, shows the activity log at the bottom. **Help**, then **Restart the helper** restarts Lemonade.

## Words explained

- **Model**: the AI itself, a big file on your computer.
- **Lemonade**: the engine that runs the model.
- **Pi**: the helper that lets a model read files, make edits and run steps.
- **Agent**: an AI that can take steps on its own (look, edit, run) instead of only talking.
- **Skill**: a short instruction sheet the AI follows for a particular kind of job.
- **Token**: a small piece of a word. About 4 tokens make 3 words. Speed and memory are measured in them.
- **Context window**: how many tokens the AI can keep in mind at once. Its working memory.
- **Tool**: something the AI can use, such as reading a file or running a command.
- **Backend (or engine)**: the program that runs a model on your hardware (CUDA, Vulkan, ROCm, processor).
- **Folder (task)**: a conversation where the AI works in a folder you chose.
- **Job**: a goal the AI works on in the background, round after round, until a check you chose passes.
