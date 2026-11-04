# Shoggoth: build spec

Zest becomes **Shoggoth**. Each model gets a procedurally grown creature (a voxel "host"), and the app shows what models do through pictures of physical things. The model sits in a glass module on the chat header. The Machine is a drawer of slots. The Hardware and Server pages become lab benches. Chat steps get drawn glyphs and Lovecraftian flavour text, the type changes to Doto, Inter and JetBrains Mono, and everything is restyled in a Braun / Teenage Engineering manner from the theme's own colours.

This document is the spec. Everything in it is decided unless section 14 says otherwise. **Status: built** on the `shoggoth` branch (PR "Shoggoth"); what remains is the docs rewrite with new screenshots, the repository rename, and the AppImage and AUR release.

**How to use it.** Read section 0 first. The clickable reference is `docs/plans/prototypes/host.html`. Open it in a browser; it uses mock data. Its "Prototype controls" panel (bottom left) holds the options considered; each one marked "(chosen)" and every default is what this spec describes. Where the prototype and this text disagree, the text wins. Ready-made assets are in `docs/plans/assets/` (section 13).

---

## 0. Build brief

**Where.** Work on the `shoggoth` branch. It already has the wording cleanup (`0336a9e`) and this plan with the prototype and assets. Commit after each stage below, with a clear message ending in the attribution line the session asks for. **Don't push or open a PR until the user says so.** The user files it as one PR, unless they ask for a split.

**Order of work.** Each stage leaves the app working and the tests green:

1. **Rename** Zest to Shoggoth, with the fallbacks in section 9. This is mechanical and touches nearly every file, so do it first and alone.
2. **Type, glyphs and flavour:** embed the fonts (section 8), swap emoji for glyphs (section 7), and add the flavour loader and chat lines (section 7).
3. **Creature module:** port the generator, add the picker fields and the per-machine creature store, and make a hidden `#specimens` page (section 5).
4. **Chat header module** and the first-run note and Settings switch (sections 3A and 3E).
5. **The Machine drawer** (section 3B).
6. **Hardware bench** (section 3C), including the per-card model data (section 4).
7. **Server bench and log screen** (section 3D).
8. **Packaging:** the icon, desktop file and AppImage name (section 9). Then a reduced-motion, keyboard and theme pass, and README screenshots.

**Constraints the codebase already follows. Keep them.**
- **Server:** stdlib Python only. YAML is read through `zest/_vendor` (the folder becomes `shoggoth/_vendor`). No new runtime dependencies.
- **Front end:** one file, `index.html`. No frameworks and no CDN: nothing loads from the internet. Fonts are embedded as base64 `woff2` `@font-face` rules, like today.
- **Colours:** every colour comes from theme tokens (`--surface`, `--surface-2`, `--line`, `--text`, `--muted`, `--faint`, `--lemon`, `--lemon-text`, `--leaf`, `--warn`, `--bad`, `--steel`, `--hi`, `--sh`, `--edge`, `--well`), except the creature's own palette, the icon, and the dark mainframes (section 6).
- **Plain words:** short, crisp sentences in all UI text. Buttons say Load and Unload; an engine is a slot. "Host" and "cartridge" are this spec's working names and never appear in the UI. "Shoggoth" is the app's name.
- **The layer mirrors the app.** Every action is an existing action (Load = the picker's load, Unload = Free memory). Pictures show the router's real state and never run ahead of the server.
- **No new polling.** Reuse `tick()` (every 2.5 s: `pollGpu`, `pollServer`), the Server page's own poll, and the chat stream events.
- **Stream events:** the `zest_loading`, `zest_loading_step` and `zest_loaded` event names are a wire format that other apps can receive, so they keep their names.

**Tests that must pass:**
```bash
python3 -m unittest discover tests
```
```bash
cd tests/ui && node smoke.js && node --test gate.test.mjs
```
Add the tests listed in section 11.

**Done means:**
- Every stage is built and committed, and all tests pass, including the new ones.
- The prototype's chosen look matches the app in Light Touch, Braun T3, Teenage Engineering and one dark theme.
- Reduced motion and keyboard-only use both work.
- An existing Zest install upgrades without losing settings, models or chats.

**File map:**

| Area | Files |
| --- | --- |
| UI, all pages | `zest/index.html` |
| Model picker data | `zest/library.py` (`picker()`) |
| Settings store | `zest/store.py` |
| Loads, unloads, ownership | `zest/router.py`, `zest/providers/*` |
| GPU numbers | `zest/hardware.py` (`all_gpus()`), `/ui/gpu` in `zest/server.py` |
| Requests for the Server page | `zest/gateway.py` (`Recorder`), `/ui/server` |
| Recommendations-style YAML loader | `zest/recs.py`, `zest/recommendations/` |
| Packaging | `packaging/` (AppRun, `build-appimage.sh`, `zest.desktop`, `zest.svg`/`.png`, `aur/`) |
| In-app guide | `zest/docs.md` |
| Reference | `docs/plans/prototypes/host.html`, `creature.js`; `docs/plans/assets/` |

---

## 1. Why

Loading is the most confusing moment in the app. People wait without knowing why. They don't see that a Strata model stops Lemonade's, that a big model takes the whole machine, or that "Free memory" means anything. The router already knows all of this (`router.STATE`, `make_room`, the loading step lines). The pictures give it somewhere to show:

| What is true | What you see |
| --- | --- |
| A model in memory | Its creature in the glass module on the chat header, and seated in its engine's slot in the Machine |
| Loading | The creature's eyes stay shut, and the slot's status line prints the router's real steps (with elapsed time for Strata) |
| Making room | The other model leaves its slot first, then the new one goes in |
| Something is answering, so the app won't stop it | The load waits beside the slot with the router's sentence |
| Graphics memory, heat, compute | The Hardware bench: a memory beaker, a heat gauge, a coil and a radio, with one mainframe per card |
| An app using a model through the Server | A flask on the Server bench that bubbles while thinking and fills while writing |
| The model card | The creature itself: eyes for context, skin grain for quant, a brain for reasoning, a lens for vision, hands for tools, size for parameters |

## 2. Principles

1. **On by default, with an off switch.** Settings › Look › **Show model pictures** is on for new and existing installs. A one-time note in chat says where to turn it off (section 3E). With it off, the app looks as it did, but with the new name, type and glyphs.
2. **A mirror, never a second controller** (section 0).
3. **Plain words** (section 0). Every creature part has a plain caption: "9 eyes: remembers about 256K tokens of a conversation".
4. **Uncanny, never gory.** The creature never moves its mouth, only its eyes. There are no jump scares. The flavour text can be dark, but never gory.
5. **Colours from the theme** (section 0), with three exceptions: the creature, the icon and the mainframes.
6. **Braun and Teenage Engineering, not steampunk.** Matte housings, generous radii, perforated grilles, recessed sockets, small screws, clean round gauges, mono labels with numbered chips (01, 02…). There's no wood, brass or copper. The accent (`--lemon`) is used sparingly, like Braun's yellow second hand.
7. **One light, from the top left.** Housings are lit along the top and shaded along the bottom. Objects cast soft contact shadows on the surface they stand on.

## 3. Surfaces

### A. The chat header module
- **Where:** a glass module on the header row, right of the page title ("Chat"), about 96 px from it. It's an SVG of about 300 × 98 px. The header grows from 68 to **112 px** while pictures are on. The model bar below keeps its height.
- **Look** (the prototype's `tubeSVG`):
  - A rounded glass window holds the creature, tinted with its colour.
  - The window sits between two matte, grooved end caps, on short posts.
  - Under it is a base plate with two screws, "MODEL / BAY 01" in mono, a small accent key, a status LED with a soft glow, a perforated grille, and a recessed socket at the bottom left.
- **Cable:** one round lead in `--text`, from the socket down to a plug sitting on the model picker's top edge, right above the model's name, with an accent collar.
  - It's drawn in an absolutely positioned SVG over the header and model bar, from the real positions of the socket and the name.
  - It's redrawn when the model or window size changes.
- **Moods** come from events the page already receives:

| Mood | When | LED and status word |
| --- | --- | --- |
| `idle` | ready | `--leaf`, READY |
| `thinking` | sent, before the first token, and during reasoning | `--lemon`, THINKING |
| `answering` | answer tokens streaming | `--lemon`, WRITING |
| `loading` | `zest_loading` to `zest_loaded`, or `/ui/router` says a load is running | `--warn`, LOADING |
| `working` | Cowork running a tool | `--lemon-text`, USING A TOOL |
| `none` | no model loaded | `--steel`, NO MODEL |

- **Motion:**
  - **Bubbles:** they rise in the glass while thinking, answering or working.
  - **Cable signal:** a dashed accent line runs along the cable while loading or answering.
  - **Creature:** it breathes, blinks and glances, and shuts its eyes while loading (the generator's CSS).
- **Click** (it's a real `<button>`): opens the Machine drawer. Its accessible name is "Open the machine. Picture of <model>: <parts>."
- **Below 900 px wide:** the module and cable are hidden, and the header goes back to 68 px.
- **Page titles:** Doto at 52 px weight 900 on every page (section 8), so the title balances the module.

### B. The Machine drawer
- **Opening and closing:**
  - A modal `<dialog>` slides in from the right, `min(760px, 100vw)` wide.
  - It opens from the header module, and from a new "Open the machine" link at the bottom of the model picker.
  - Esc and Close shut it, and focus returns to the model picker.
- **This computer:** a matte case listing the graphics card(s), RAM and SSD. It holds **one slot per engine**, from `/ui/providers` and `/ui/router`.
  - **Lemonade:** "Fast. Holds one model at a time." (Or `max_loaded` models, from Lemonade's settings.)
  - **Strata:** "For very large models. Uses the whole machine: graphics card, RAM and SSD." Three trays show how its model is split across graphics card, RAM and SSD.
  - Each slot has a dark status display (the router's lines, newest last, the older ones dimmed), a recessed bay holding the seated model, and a memory gauge with numbers.
- **Seated model:** a strip with its creature (cropped), name, size and quant, a "Chat uses this" tag on the active one, and **Unload**.
  - A model the app didn't load says "Started by you" and has no Unload ("Shoggoth didn't load this, so it won't unload it."). This matches the router's ownership rules.
- **Your models shelf:** cartridge-shaped cards (a notch on top and a coloured strip in the creature's tone), grouped Small, Medium and Large.
  - Each card shows the creature, name, size, quant and engine, a fit note ("Close other big programs first", "Takes about 2 minutes to start"), and **Load**.
  - Recommended models you haven't downloaded (from `models.yaml`) sit greyed out with **Get N GB**, which does the same as the Library's Get.
- **Loading:**
  - Press Load. Dragging a card into its engine's slot does the same, but is never the only way.
  - The card slides into the slot only after the server accepts the request.
  - The status line prints the router's step text. Strata steps add the elapsed seconds.
  - Making room shows "Unloading <model> to make room" and the other slot's "Unloaded <model> to make room for <model>".
  - One `aria-live` line announces "<model> is ready."
- **Refusals stay visible:** a busy engine, a tune running (`router.holds`), or a model you started.
  - A warning box beside the slot gives the router's sentence, with **Try again** and **Dismiss**.
  - Example: "Strata is answering opencode right now, so Shoggoth won't stop it. Try again when it finishes."

### C. The Hardware page: the lab bench
- **Placement:** with pictures on, a bench sits above the page's existing numbers, which stay as they are and remain what screen readers use.
  - The intro reads: "Each machine at the back is one graphics card. Its lights show how hard it's working. The beaker shows memory used across all your cards. The gauge shows the hottest card. The coil sparks as the cards work harder, and the radio has a meter for each one."
  - The bench is one SVG, viewBox about 1000 × 536. It handles **1 to 4 cards**.
- **Back row, one mainframe per card:**
  - **Size and placement:** each cabinet is up to 200 px wide, sharing the row. They stand on a dark raised platform (a vented slab on short feet), which rests on a faint floor line at the back with a soft shadow.
  - **Colour:** the cabinets are **dark in every theme**: near-black matte, dark panels, light lettering. Only lit lamps and the memory bar take the accent. This is what separates back from front on light themes.
  - **Top screen:** "GPU n" and the card's name, then "MODEL ON THIS CARD", the model's name, and "N GB HERE" (from the per-card model data, section 4).
  - **Below it:** a memory bar with "MEM used/total GB · temp °C".
  - **Lamps:** a 5-row bank lit in proportion to the card's use, flickering while it works.
  - **Tape reels:** they spin faster with use and stop when idle.
  - **Cables:** strung between neighbouring cabinets, and a bundle drops from the platform to the floor.
- **Tinted glass:** a pane between the back row and the bench. It's a `--veil` fill (12 % dark on light themes, 42 % on dark ones), with a faint diagonal sheen and a highlight on its top edge.
- **The table:** a top surface and a front edge, from theme tokens. Every object casts a soft contact shadow. The front edge carries the numbered label row.
- **Instruments:**
  - **01 Model:** the active model in a fat glass beaker, drawn large (about 190 px).
    - The beaker is half-filled with liquid in the creature's tint.
    - It has an electrode, wall thickness, a rolled lip, enamel 100–400 ml marks, and a heavier base.
    - It stands on a plinth reading "<MODEL NAME>" and "<size> GB · <ENGINE>", with a socket.
  - **02 Memory:** a graduated beaker for all cards together, with **0–100 % printed on the glass** (ticks every 5 %, numbers every 25 %, a "%" mark).
    - It fills with the accent colour and has a curved surface (meniscus).
    - Bubbles rise faster with compute.
    - Its foot reads "used / total GB".
  - **03 Heat:** a round gauge for the **hottest card**, 20–100 °C, with a red arc from 85 °C.
    - It has a chamfered bezel, a recessed face, a glass dome with a crescent highlight, and an accent needle with a soft shadow.
    - It sits on a stem with a collar, on a foot.
  - **04 Compute:**
    - **The coil:** a perforated base, a shaded winding, a primary ring and a torus top. Arcs grow in number and length with average compute. Above about 80 % an arc jumps to the radio's aerial.
    - **The radio:** a Braun T3-style pocket radio with a recessed hole grille, **one needle meter per card** (numbered, each behind its own glass), a signal lamp that brightens with compute, a knurled knob, and a telescopic aerial that sparks.
  - **Label row:** `01 MODEL <n> cards`, `02 MEMORY <n>%`, `03 HEAT <n>°C`, `04 COMPUTE <n>%`.
- **Wiring, as a hobbyist would:** neat instruments, joined up a little haphazardly, in theme colours (`--bad`, `--text`, `--lemon`, `--lemon-text`, `--leaf`, `--muted`), each with a thin highlight.
  - **Probes:** electrodes stand in both beakers, with crocodile clips.
    - A long red loop runs from the model beaker's probe to the memory beaker's probe.
    - A black lead drops to a breadboard.
  - **Breadboard:** between the memory beaker and the gauge, with a chip, a resistor, jumpers, and an LED that flickers with compute.
  - **Other leads:**
    - A yellow lead is wound around the gauge stem and runs on to the coil base.
    - A blue lead runs from the breadboard to the coil.
    - A red and black pair with banana plugs goes into the radio's side.
    - A grey lead runs back along the table.
  - **Loose ends:** tape strips, and a green lead hanging off the table's front edge with a stripped copper end.
  - **Layering:** table wires are drawn behind the instruments, so they show through the glass. Clips, plugs, tape and the hanging lead are drawn in front.
- **Text on the bench** is Doto weight 900 (section 8).
- **The data:**
  - **Per card:** `/ui/gpu` gives memory, use, temperature and power at the existing 2.5 s tick. Ease toward new values between polls, using CSS transitions or a light `requestAnimationFrame` ease, while the page is visible.
  - **Arcs:** redraw them while the page is visible, at most about every 110 ms.

### D. The Server page: lab bench and log screen
- **Bench strip** above the request list, with pictures on.
  - On a short table, the active model sits in a small beaker on the left.
  - It's cabled to **one Erlenmeyer flask per app** that's being answered now, or was in the last few seconds.
  - **What each flask shows:**
    - It bubbles while the request is `thinking`.
    - It fills, and its cable carries a flowing signal, while the request is `answering`.
    - It stays full briefly after `done`, then drains.
  - A numbered plate on the table's front gives each app's name and state ("01 OPENCODE / WRITING").
  - The data is the gateway snapshot `/ui/server` already returns: per request `app`, `model`, `state` (`waiting`, `thinking`, `answering`, `done`, `error`), `t0`, `t_think`, `t_write`, `t_end`, `tokens_out`.
- **Log screen** (new): a dark recessed panel above the request list, in Doto. The header reads "LOG" with a line count. It shows the newest 9 lines:
  - start-up lines (grey), such as "shoggoth started on 127.0.0.1:13305 · gateway on /v1";
  - "<app> → POST <path> · <model>" when a request arrives (light);
  - "<app> · <model> thinking for N s, first token" on the first token (grey);
  - "<app> ← 200 · N tokens in N s · N tok/s" when done (accent), or the error in `--bad`.

  Derive these on the client from state changes between polls of `/ui/server`. No new endpoint is needed. Start-up lines come from data the page already has (the version, port and engine states).
- **The request list:** stays as it is, set in Doto, and remains the accessible record.

### E. First run and the switch
- **Settings › Look › Show model pictures:** a switch, on by default. The description reads "Draws each model as a creature, beside the chat, in the Machine, and on the Server and Hardware pages. Turn it off for the plain look."
  - It's stored in browser storage as `shoggoth.pictures` (default `true`), like the theme.
- **One-time note:** at the top of the chat, "Model pictures are on. Turn them off in Settings › Look". It has a link, and a × that dismisses it for good (`shoggoth.picturesNoted`).

### F. Specimens (developer page)
- **What it is:** a hidden `#specimens` page, like the prototype's Example pictures.
  - It shows every installed and recommended model's creature, with a live stage, mood buttons, the Q2/Q4/Q8 comparison and the part captions.
  - It's useful for checking the generator, and is not linked from the rail.

**Later, not in this build:** creature portraits on Library cards (lazy, only cards on screen, greyed when not downloaded).

## 4. Data and server work

| Needed | How |
| --- | --- |
| Model card | `/ui/picker` already has `id`, `title`, `ctx`, `vision`, `tools`, `reasoning`, `size_gb`, `engine`, `tier`, `live`, `fit`. |
| **Quant, parameters, lineage** | Add three fields to `library.picker()`. **`quant`:** from the file name or catalog (`Q4_K_M`, `IQ3_S`, `Q8_0`); default `Q4_K_M`. **`params`:** billions, parsed from the name (`35B`, `0.6B`, `8x7B` → 56); otherwise estimated from size and quant, snapped to common sizes. **`lineage`:** the Hugging Face `org/repo` with the quant and `GGUF` stripped; otherwise the model id. Q4 and Q8 of one model share a lineage, so they share a body. |
| **Creature per model** | A per-machine store in `store.py` settings: `creatures: {lineage: {form, tone}}`. On first sight of a lineage, take the hash's form (`hashStr(lineage + "|" + params) % 9`) and tone (`hashStr(lineage) % 5`). If the form is taken by another installed model, step to the next free one (forms 0–8), and do the same for tone (0–4). Never reassign: deleting a model doesn't change the others. Expose it in `/ui/picker` as `form` and `tone`. |
| Loading state and steps | Chat stream events, plus `/ui/router` `state` for loads started elsewhere. |
| Slots | `/ui/providers` (engines, running state, owner) and `/ui/router` (what's loaded where). |
| **Which model is on which card** | Add `models: [{model, engine, mem_mb}]` to each card in `/ui/gpu`. For each process in the card's `procs`, read `/proc/<pid>/cmdline`. If it's a `llama-server`-style process, take its `-m` / `--model` path and match it to a library model by file path. Otherwise match the process to an engine by name (`isLemonadeProc`'s pattern, or Strata's process). If no model matches, report the engine with `model: null`, and the mainframe shows the engine's name. |
| Memory, heat, compute | `/ui/gpu`, as today: `mem_used_mb`, `mem_total_mb`, `util`, `temp`, `power`, `procs`. |
| Apps being answered | The `/ui/server` snapshot (section 3D). |
| Flavour lines | `flavour.yaml` (section 7), served from `/ui/recommendations` or a new `/ui/flavour`. |
| The switch and note | Browser storage (section 3E). |

## 5. The creature

- **Generator:** port `docs/plans/prototypes/creature.js` into `index.html`. It's the voxel generator from the original design handoff, with three changes that must be kept:
  1. **`form`** picks the body form (0–8), from the creature store, so each installed model gets its own. Installed models also get distinct tones.
  2. **No extra row of small eyes.** It's off unless `eyeRow` is set, so the eye count tracks context: `3 + round(log2(ctx_k / 4))`.
  3. **Skin grain uses its own random stream,** so Q4 and Q8 of a model keep the same body and differ only in grain.
- **Inputs:** `{seed: lineage, params, quant, ctx (K tokens), vision, reasoning, tools, tone, form}`.
  - **Mapping:** context sets the eyes, quant the skin grain (Q8 smooth, Q2 rough), reasoning an open brain, vision a lens, tools hands holding pens, and parameters the body size.
- **Rendering:** use the prototype's `pic()`.
  - Paths are batched per colour. Eyes and sparks are decals placed by matrix.
  - Mood classes are `mood-idle`, `-thinking`, `-answering`, `-loading`, `-working` and `-none`.
  - Animation runs only on `.live` pictures.
- **Cropping:** small pictures are cropped to the creature without its shadow (`cropAll()`, using `getBBox` on `.zc-breathe`).
- **Caching:** cache by input, so a re-render never regenerates. At most one live creature per view; the rest are still.
- **Accessibility:** `role="img"` with `aria-label` "Picture of <name>: <parts>", for example "9 eyes: remembers about 256K tokens of a conversation; an open brain: thinks before it answers; hands holding pens: can use tools, such as web search".

## 6. Visual system

- **Paint servers:** shared gradients and filters live once in a hidden `<svg><defs>`. Use the prototype's set: `kGlass`, `kGlassH`, `kMatte`, `kMatteH`, `kAccent`, `kTorus`, `kBezel`, `kBezelIn`, `kRecess`, `kDome`, `kCyl`, `kTable`, `kTableFront`, `kArcGlow`, `kCrt`, `kDark`, `kVeil`, `kShadow`, `kSoft`, `kArc`.
  - Theme colours enter through `style="stop-color:var(--…)"`.
- **Helpers to port:**
  - `housing()`: a matte box with a lit top and a shaded bottom.
  - `holes()` and `holesIn()`: grilles with recessed holes.
  - `contact()`: soft contact shadows.
  - `wire()`, `sag()`, `croc()`, `plug()`, `tape()`: wiring.
  - `bolt()` and `sparks()`: arcs.
- **Printed marks on glass:** `--text` with a 2.4 px `--surface` halo (`paint-order: stroke`), so they read over liquid, glass or a dark cabinet.
- **The three colour exceptions:**
  - **The creature:** its five palettes.
  - **The icon:** fixed colours.
  - **The mainframes:** `#2f3136` → `#1c1d21`, with `#0c0d0f` panels and `#e9e6dc` / `#a7a49a` lettering; lamps and bars use `--lemon`.
- **New token:** `--veil`. It's `color-mix(in srgb, #141820 12%, transparent)` in light themes and `color-mix(in srgb, #05070a 42%, transparent)` in dark ones.

## 7. Glyphs and flavour text

**Glyphs.** Seventeen 24 px line drawings replace every emoji in chat. They're drawn in `currentColor` at stroke width 1.6, so they follow the theme. They're in `docs/plans/assets/glyphs/*.svg` and in the prototype's `GLYPH`. Inline them as `<svg class="gl">`.

| Emoji | Glyph | Where |
| --- | --- | --- |
| 🍋 Loading | `rise`: an eye surfacing from water | Cowork step diagram |
| 📖 Reading | `eye`: a slit-pupilled eye | step diagram, reading a file |
| 🤔 Thinking | `dream`: a closed, lashed eye under a spiral | step diagram, the thinking block |
| 💭 Thought | `thought`: three small eyes | a finished thinking block |
| ✍️ Writing, 📝 | `quill`: a tendril holding a quill | step diagram, writing a file |
| 🛠️ Working | `grasp`: a suckered tendril | step diagram, a tool being prepared |
| 🙋 Your OK | `threshold`: a doorway with a tendril at it | step diagram, approval cards |
| ✏️ Edit | `edit`: lines with a tendril tip | editing a file |
| ⚡ Command | `sigil`: a prompt in a ringed sigil | running a command |
| 🔎 Search | `seek`: a radiant eye | grep and find |
| 📁 Folder | `crypt`: a lidded casket with an eye | listing a folder, "Working in…" |
| 🧰 Skill | `tome`: a book with a sigil | using a skill |
| 🌐 Web | `orb`: an eye inside a globe | web search and its errors |
| 🧹 Tidying | `maw`: a toothed maw | compaction |
| ✂️ Cut short | `severed`: a cut tendril | an answer that ran out of room |
| ⚠️ Warning, ✅ Done | `omen`, `sealed` | the search test and other notes |

The emoji are in `index.html` (`CW_STEPS`, `CW_EMOJI`, the thinking block, the compaction, cut-short and search notes, the approval card, the folder hero) and in `docs.md`. The ⚡ on the Tuning page's Speed step is not chat; swap it too, for `sigil`.

**Flavour text:**
- **Where:**
  - Under each step's plain word in chat: Loading, Reading, Thinking and Writing, inside the reply while it runs.
  - On each Cowork tool card, under the plain line ("Searching files for “basil”").
  - It's one italic line in JetBrains Mono, `--muted`.
- **Changing:** a new line every few seconds while a step lasts (about 2.2 s), never repeating the one just shown.
- **Names:** `{model}` is always the running model's name. Never name a mythos figure. `{target}` is what a tool works on.
- **Screen readers:** only the plain word goes to the live region. The flavour line is `aria-hidden`.
- **The lines:** they live in `docs/plans/assets/flavour.yaml`, which has 17 sets: loading, reading, thinking, writing, the tool sets (`grasp`, `quill`, `seek`, `eye`, `sigil`, `edit`, `crypt`, `tome`, `orb`), `threshold`, `maw`, `severed`, `idle`.
  - Ship it as `shoggoth/recommendations/flavour.yaml` (user copy in `<data>/recommendations/flavour.yaml`).
  - Validate it with the same `recs.py` checks: known sets, non-empty strings, only `{model}` and `{target}` as placeholders.
  - `recommendations check` should cover it.

## 8. Type

- **Fonts:** `docs/plans/assets/fonts/` has the Latin-subset `woff2` files and their licences (`OFL-*.txt`, SIL Open Font License 1.1): Doto (variable 100–900), Inter (variable 300–800, upright and italic), JetBrains Mono (400–700, upright and italic). `fonts.css` there has the `@font-face` rules.
  - Embed them in `index.html` as base64, replacing Young Serif and Nunito Sans. JetBrains Mono upright is already embedded; add its italic.
  - Update the font comment at the top of `index.html`.
- **Roles:**
  - `--serif` (the display role) is Doto, `--sans` is Inter, and `--mono` is JetBrains Mono.
  - Page titles: Doto 900, 52 px, letter-spacing 0. The wordmark and section headings: Doto 800.
  - Flavour text: JetBrains Mono italic, 13 px.
- **Screens are Doto 900:**
  - Everything inside the Hardware bench SVG, the Server bench, the log screen and the request list.
  - Keep Doto at 12 px or more where possible. Small Doto smudges, so check the bench plates and mainframe captions at real size.

## 9. The rename and packaging

Zest appears about 700 times across code, docs and packaging. Everything moves to Shoggoth, and existing installs keep working:

| What | Becomes | Fallback |
| --- | --- | --- |
| The name in the UI, window title, README, `docs.md`, `docs/*.md`, recommendation file comments and text | Shoggoth | — |
| `zest/` package folder, `zest/server.py`, the CLI | `shoggoth/`, `shoggoth/server.py`, a `shoggoth` command | a `zest` command that prints a one-line notice and runs `shoggoth` |
| 19 `ZEST_*` environment variables (`ZEST_APP_GRACE`, `ZEST_APPROVAL`, `ZEST_AUTH`, `ZEST_CONFIG_DIR`, `ZEST_DATA_DIR`, `ZEST_FOLDERS`, `ZEST_HOST`, `ZEST_INJECT`, `ZEST_LEMONADE_DIR`, `ZEST_PI`, `ZEST_PI_TARGET`, `ZEST_PORT`, `ZEST_PRESET`, `ZEST_SEARCH`, `ZEST_SEARCH_KEY`, `ZEST_STRATA`, `ZEST_TOKEN`, and any others found) | `SHOGGOTH_*` | each `ZEST_*` is still read when its `SHOGGOTH_*` is unset |
| Data and config folders `~/.local/share/zest`, `~/.config/zest` | `…/shoggoth` | moved on first run if the new one doesn't exist, with a note in the log. If the move fails, the old folder is used in place. |
| Browser storage keys (`zest.*`) | `shoggoth.*` | read the old key once and write the new one |
| Stored settings such as Lemonade's `loaded_by_zest` | `loaded_by_shoggoth` | read the old name once |
| The `zest` provider in the opencode connect snippet, and the model owner name the gateway reports | `shoggoth` | the gateway still answers requests that name `zest` |
| Stream event names `zest_loading`, `zest_loading_step`, `zest_loaded` | unchanged | — (a wire format other apps may see) |
| AppImage, `zest.desktop`, `zest.svg`/`.png`, the AUR package | `Shoggoth-x86_64.AppImage`, `shoggoth.desktop`, the new icon, an AUR `shoggoth` package with `provides`/`replaces: zest` | — |
| Copy that leans on the lemon ("What shall we squeeze today?", "the lemon fills…") | new copy in the same plain voice; the empty-chat line becomes something with the new voice | — |
| The GitHub repository | renamed by the user; GitHub redirects old URLs, and the docs' links are updated | — |

**Icon:** many eyes on the old lemon tile. It's final in `docs/plans/assets/icon/shoggoth.svg`, with PNGs at 16, 24, 32, 48, 64, 128, 256 and 512.
- **Packaging:** use these for `packaging/shoggoth.svg`, `packaging/shoggoth.png`, the AppImage and the desktop file.
- **Favicon:** the app's favicon and the brand mark beside the name in the rail use the same SVG.
- **Checked:** it reads as a creature, not a skull, at 16 and 32 px.

Tests cover the fallbacks: an old env var, an old data folder that gets moved, an old storage key, and the `zest` command.

## 10. Motion and accessibility

- **Reduced motion:** `prefers-reduced-motion: reduce` stops every ambient animation. That covers breathing, blinking, bubbles, reels, flickering lamps and the cable signal.
  - Lamps then light in order, and arcs are redrawn only when compute changes by about 10 %.
  - The Machine's cards move straight to their place.
- **Motion budget:** one ambient loop per view (the creature breathing). Everything else moves in response to an event.
- **Feedback matches the wait:**
  - A load under about 1 s shows no animation.
  - Seconds get the slide-in.
  - Minutes get step lines with elapsed time.
- **Never colour alone:** every lamp has a word, and every gauge a number.
- **Controls:** all are native `<button>` and `<input>` elements, with 44 px targets, the existing focus ring, and full keyboard use. The drawer keeps focus inside and returns it on close.
- **Pictures:** the decorative SVGs are `aria-hidden` or carry one summary label. The lists and numbers stay the accessible source of truth.

## 11. Tests to add

- **Generator (node):**
  - The same input gives byte-identical output.
  - Q4 and Q8 of one lineage have the same body voxels.
  - Five sample models get five forms through the store.
  - The eye count matches the context formula.
- **Picker and store:**
  - `quant`, `params` and `lineage` parse for the sample names (`Qwen3.6-35B-A3B-MTP-GGUF`, `LFM2.5-8B-A1B`, `Qwen3-0.6B`, `Mixtral-8x7B`).
  - Creature assignment is stable across restarts and after deleting a model.
- **`/ui/gpu` per-card models:** a process whose command line has `-m <path>` maps to the right library model, and unknown processes fall back to the engine name.
- **Flavour YAML:** the shipped file has no problems, a bad placeholder is reported with its location, and a user copy wins.
- **Rename fallbacks:** section 9.
- **UI smoke** (`tests/ui/smoke.js`):
  - Every page opens with pictures on and off, with no script errors.
  - The Hardware page renders with 1 and 4 recorded cards. Add `/ui/gpu` recordings to `tests/ui/api.json`.
  - No emoji are left in chat UI strings.

## 12. Decisions log

All decisions were made 29 October 2026, with the user, from the prototype:

1. **Art:** voxel creatures, from the original generator.
2. **Words:** plain words for controls. The app is named Shoggoth, and "host" and "cartridge" never appear in the UI.
3. **The Machine:** a drawer over the chat, not a page.
4. **Later redesign:** build reusable parts, so this can grow into the full "at the table" redesign later.
5. **Rename:** full, with fallbacks.
6. **Icon:** many eyes, on the lemon tile.
7. **Chat emoji:** custom drawn glyphs replace them all.
8. **Flavour text:** full cosmic horror, always on, with the plain word kept. The running model's name replaces any mythos name.
9. **Picture in chat:** the glass module on the header, cabled to the model picker. The header is 112 px.
10. **First run:** pictures on by default, with a one-time note.
11. **Mainframes:** they show the model's name and the GB of it on each card.
12. **Server bench:** the lab bench, like Hardware.
13. **Type:** Doto for titles and screens, Inter for body, JetBrains Mono for labels and flavour.
14. **Doto screens:** approved as shown in the prototype.
15. **Style:** Braun/TE skeuomorphism from theme tokens, not steampunk. Mainframes are dark in every theme, behind a tinted glass pane.
16. **Hardware details:**
    - The model in a fat beaker.
    - Memory as a beaker with a 0–100 % scale printed on the glass.
    - A heat gauge for the hottest card.
    - A coil and a radio with a meter per card.
    - Hobbyist wiring, a breadboard, and a table with contact shadows.
17. **UI text:** short, plain sentences. The app's existing copy was audited and cleaned (`0336a9e`).

## 13. Assets

| File | What |
| --- | --- |
| `docs/plans/prototypes/host.html` | Clickable prototype of everything, with mock data. Its "(chosen)" options and defaults are the spec. |
| `docs/plans/prototypes/creature.js` | The voxel generator, with the three changes (section 5). |
| `docs/plans/assets/icon/shoggoth.svg`, `shoggoth-{16…512}.png` | Final icon. |
| `docs/plans/assets/glyphs/*.svg` | The 17 chat glyphs. |
| `docs/plans/assets/flavour.yaml` | The flavour lines: 17 sets. |
| `docs/plans/assets/fonts/` | Doto, Inter and JetBrains Mono (Latin `woff2`), their OFL licences, and `fonts.css`. |

Not needed for this build: the original design handoff (`Zest Table.pdf`, `Claude outputs/DESIGN.md`, `zest-table-design.zip`). The generator from it is already in `creature.js`. Those files describe the later "at the table" redesign.

## 14. Still open

- **The GitHub repository rename** is the user's to do, whenever they choose. Code and docs should not depend on the new URL until then.
- Nothing else. If something comes up during the build that this spec doesn't settle, ask the user rather than guess.

## 15. Out of scope

- The full "at the table" redesign (a new rail, the table replacing chat, the whiteboard replacing Cowork).
- Creature portraits on Library cards.
- Cross-machine creature matching.
- A "try any name" generator field.
- Mobile-specific layouts.
