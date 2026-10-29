# Plan: the host and its cartridges

A visual layer that explains what Zest does with models: each model is a procedurally grown creature (a "shoggoth", called **the host** in the app), each model file is a **cartridge**, and loading a model is pushing a cartridge into a picture of your computer. It is a layer **over** today's chat and Server pages. It does not replace them, and nothing changes for anyone who leaves it off.

Status: **a plan for review, with a clickable prototype. Nothing in the app is built.** Section 10 lists what's decided and what's still open.

Prototype: open `docs/plans/prototypes/host.html` in a browser. It uses mock data and doesn't talk to Zest. Its controls (bottom left) switch the layer on and off, move the chat picture, open the Machine as a drawer or a page, change the theme, turn on reduced motion, and set up the situations Zest refuses (a busy engine, a model you started yourself). `creature.js` next to it is the voxel generator with the three changes in section 5.

**Words:** the app is renamed **Shoggoth** (section 11). Inside it, the picture layer still uses plain words: model, slot, Load, Unload. "Host" and "cartridge" stay this plan's working names and never appear in the app.

Sources: the concept plates in `Zest Table.pdf`; `Claude outputs/DESIGN.md` and `zest-table-design.zip` (the earlier full-redesign handoff, with a deterministic generator in `tools/creature-generate.js`); ui-ux-pro-max guidance (reduced motion, motion budget, loading feedback, state not colour alone, accessible names). The skill's design-system output (a dark OLED documentation style) was ignored: it fights Zest's warm light themes and DESIGN.md's light-only rule.

## 1. Why this, and why loading first

Loading is the most confusing moment in Zest today. People wait without knowing why. They don't see that picking a Strata model stops Lemonade's, that one big model takes the whole machine, or that "Free memory" means anything. The router already knows all of this (`router.STATE`, `make_room`, the `zest_loading_step` messages). It just has nowhere visual to say it. A machine with bays and cartridges shows these rules plainly:

| What is true | What you see |
| --- | --- |
| A model file on disk | A cartridge on the shelf, labelled with name, size and quant. Its coloured edge is the host's skin tone. |
| A model in memory | The cartridge seated in a bay, and its host awake at the table. |
| Loading (seconds for Lemonade, minutes for Strata) | The cartridge slides in, the host's eyes stay shut, and the bay's LCD prints the router's real step lines. |
| Making room (`make_room` stops another engine) | The other cartridge ejects first, then the new one goes in. |
| Something is answering, so Zest won't stop it | The busy cartridge's lamp is lit and the new cartridge waits beside the slot, with the router's sentence: "Strata is answering right now…" |
| Graphics memory and RAM | The bay's fill gauge, from `/ui/gpu` and the machine's RAM. |
| Engines | Bays. Lemonade is the small fast bay. Strata is the big bay that spans the graphics card, RAM and SSD (drawn as three stacked trays). |
| An app using a model through the Server | A glass on the bench, fed by a tube from the host, that fills while the app is answered. |
| Context window, quant, reasoning, vision, tool calling | Eyes, skin grain, an exposed brain, a lens, hands holding pens (the "model card, drawn" plate). |

## 2. Principles

1. **Additive, with an off switch.** One switch: **Settings › Look › Show model pictures**, on by default since decision 10, with a one-time note in chat saying where to turn it off. When it is off, the page's markup and behaviour are exactly today's. The UI smoke test runs both ways to prove it.
2. **A mirror, never a second controller.** Every action in the layer is an existing action: Load calls the same load as the chat model picker, Unload the same "Free memory", and so on. It shows the router's real state and never makes up its own: no success animation before the server confirms, no eject before the engine reports it stopped.
3. **Plain words only (decided).** The metaphor is visual. Buttons say Load and Unload; an engine is a slot. Every anatomy part has a plain caption ("8 eyes: remembers about 128K tokens of a conversation"). No "host" or "cartridge" anywhere in the app. (The app's own name is now Shoggoth; see section 11.)
4. **Warm, matte, a little uncanny; never cute, never horror.** The host never moves its mouth, only its eyes (the "four moods" plate). No gore, no jump scares.
5. **Works with every theme.** The creature's own palette is fixed. Everything around it (the module, cables, instruments, panels, LCDs and slots) uses the theme's colour tokens (`--surface`, `--surface-2`, `--line`, `--text`, `--muted`, `--hi`), so it reads in Light Touch, Braun T3, Teenage Engineering and the dark palettes. In the Teenage Engineering theme, `--line` is black, which gives its hard outlines for free.
6. **Braun and Teenage Engineering, not steampunk.** Matte housings, generous corner radii, perforated speaker grilles, a recessed socket, small screws, clean round gauges and mono uppercase labels with numbered chips (01, 02…). No wood, brass or copper. The theme's accent (`--lemon`) is used sparingly, as Braun uses its yellow second hand: gauge needles, the memory liquid, plug collars, a key on the module, and the arcs.

## 3. The three surfaces

### A. The host beside the chat (small, always optional)
- **New option (prototype default): a tube on the header.** The picture sits in a glass tube on a circuit board, on the header row next to the page title ("Chat"). A cable runs from the module's socket down into a plug on the model picker, right above the model's name.
  - Drawn as a Braun/TE module: a glass window between matte, grooved end caps, sitting on a base plate with a speaker grille, an LED, a small accent key, "MODEL / BAY 01" in mono, and a recessed socket. The cable is one round lead in the text colour with an accent collar at the plug.
  - The header grows from 68 to 112 px while this placement is on (you approved a taller header), which makes room for a tube about 300 by 98 px.
  - The board's LED and the status word next to the tube show the mood. Bubbles rise in the tube while the model thinks or writes, and a signal runs along the harness while it loads or writes. All of this is off under reduced motion.
  - The harness is drawn from the real positions of the board and the picker, so it follows the name when the model changes. Below 900 px wide the tube and harness are hidden.
- A portrait of about 112 px beside the chat and Cowork model bar. It sits in a slot reserved in the header, so the message column never moves (no layout shift).
- Moods come from the real request lifecycle that the page already receives:
  - `idle`: listening.
  - `thinking`: sent, before the first token, and during reasoning tokens.
  - `answering`: tokens streaming.
  - `loading`: between `zest_loading` and `zest_loaded`.
  - `working`: Cowork running a tool.
- A lamp with a word next to it mirrors the mood (LISTEN, THINK, SPEAK, WORK), so the state never relies on colour or motion alone.
- Clicking the portrait (a real `<button>`) opens the Machine.
- Below 768 px wide it collapses to the lamp and word.

### B. The Machine (where cartridges are swapped)
- A drawer over the chat (a modal `<dialog>` from the right), opened from the picture or from a new "Open the machine" link at the bottom of the model picker. Closing it returns focus to where you were.
- **The cabinet:** one bay per engine, from `/ui/providers` and `/ui/router`, each with a fill gauge for memory and an LCD line.
  - Lemonade's bay can hold `max_loaded` cartridges.
  - Strata's bay is the tall one with three trays.
- **The shelf:** your cartridges, from `/ui/picker`: downloaded models, grouped Small, Medium and Large, each with its fit note ("Close other big programs first").
  - Recommended but not downloaded models (from `models.yaml`) sit on the shelf greyed out, with **Get n GB**.
- **Load:** press the model's Load button, or drag it into a slot (drag is an extra, never the only way, per WCAG 2.5.7).
  - The cartridge animates in only after the load request is accepted.
  - The LCD prints the router's step text, then the host wakes.
  - Strata's wait is long, so the LCD shows the elapsed time and "You can keep chatting with another model meanwhile" where that's true.
- **Unload:** the same as Free memory. A model Zest didn't load (yours) has no Unload. It says "Started by you" instead, matching the router's ownership rules.
- **Refusals** are shown, not hidden: a busy engine, a tune in progress (`router.holds`), not enough memory. Each shows the server's sentence and the cartridge stays on the shelf.

### C. The bench on the Server page (an extra strip, the list stays)
- With the layer on, a strip above the existing requests list shows one glass per app that is being answered now, from the gateway snapshot `srvPaint()` already polls.
  - Pale bubbles while it thinks, lemon fill while it writes, empty when idle.
  - The app's name and state word sit under each glass.
- The connect panel, request list and log are untouched. The strip is decorative with one `aria-label`; the list stays the accessible source of truth.

### D. The Hardware page: the model wired into an apparatus
- With the layer on, a bench sits above the Hardware page's existing numbers, which stay unchanged and remain the accessible record. It is driven only by what `/ui/gpu` already reports per card (memory, utilisation, temperature, power), at the page's existing poll rate. It handles 1 to 4 graphics cards.
  - **Back row, one mainframe per card:**
    - Each shows "GPU n" and the card's name, plus a memory bar with that card's memory and temperature.
    - A bank of lamps lights in proportion to the card's compute, flickering while it works.
    - Two tape reels spin faster as the card works harder and stop when it idles.
    - The cabinets share the width, up to 200 px each. They stand on a dark raised platform, a slab with a vented front on short feet, which rests on a faint floor line at the back of the room with a soft shadow. Each cabinet has a recessed kick plate where it meets the platform.
    - **The cabinets are dark in every theme** (near-black matte housings, dark lamp panels, light lettering). Only the lit lamps and the memory bar take the theme's accent. This is what separates back from front on light themes, where pale cabinets and pale instruments ran together. It is the one exception to principle 5's "everything from the theme".
  - **A pane of tinted glass** sits between the back row and the instruments: a dark veil (`--veil`, 12 % on light themes, 42 % on dark ones), a faint diagonal sheen, and a highlight along its top edge.
  - **01 Model:** the loaded model in a fat glass beaker, drawn large so the creature is easy to see. It sits on a plinth that shows the name, size and engine, with a cable from the plinth's socket to the memory beaker.
  - **02 Graphics memory:** a graduated beaker for all cards together, with a 0–100 % scale printed on the glass (ticks every 5 %, numbers every 25 %). Its foot reads "22.2 / 48 GB". It fills with the accent colour and bubbles harder as compute rises.
  - **03 Heat:** a round gauge on a stem showing the hottest card, 20 to 100 °C, with a thin red arc from 85 °C and an accent needle.
  - **04 Compute:**
    - A coil on a perforated base for overall compute: more and longer arcs as it rises, and above about 80 % an arc jumps to the radio's aerial.
    - A pocket radio with one needle meter per card (numbered 1 to 4), a signal light that brightens with overall compute, and sparks at its aerial.
  - A numbered label row on the table's front edge gives each instrument's live value.
- **Wiring, as a hobbyist would do it.** This is where the mad science comes in: neat, well-made instruments joined up a little haphazardly. Every wire uses a theme colour (`--bad`, `--text`, `--lemon`, `--lemon-text`, `--leaf`, `--muted`), with a thin highlight so it reads as insulated lead.
  - Electrodes stand in both beakers. Crocodile clips on their tops lead off in a long red loop from the model's beaker to the memory beaker's probe, and a black lead drops to a breadboard.
  - **The breadboard:** sits on the table between the memory beaker and the gauge, with a chip, a resistor, jumper wires, and an LED that flickers with compute.
  - **The other leads:** a yellow lead wound round the gauge's stem, a blue lead to the coil's base, a red and black pair with banana plugs into the radio's side, and a grey lead back along the table.
  - **Loose ends:** tape strips holding cables down, and a green lead hanging off the table's front edge with a stripped copper end.
  - **At the back:** cables are strung between neighbouring mainframes, and a bundle drops off their platform to the floor.
  - **Layering:** wires on the table are drawn behind the instruments, so they show through the glass. The clips, plugs, tape and the hanging lead are drawn in front.
- **Skeuomorphic detail.** One light source, from the top left, and still every colour from the theme:
  - **The table:** everything stands on a tabletop with a lit front edge, and each object casts a soft contact shadow. Housings are lit along the top and shaded along the bottom.
  - **The glass:** wall thickness (a second, inner outline), a heavier base, a curved liquid surface with a meniscus, a rolled lip, specular streaks, and scales printed in enamel. Printed marks carry a thin halo in the surface colour, so they read over liquid, glass or a dark cabinet behind.
  - **The gauge:** a chamfered bezel, a recessed face, a glass dome with a crescent highlight, a needle with a soft shadow, and a collar on its stem.
  - **The coil:** a true torus with its hole, a rounded and shaded winding, and a primary ring at its base.
  - **The radio:**
    - Grille holes that are recessed, with a lit lower edge.
    - Meter windows set in, each behind its own glass, with needles that cast a shadow.
    - A signal lamp with a lens and a knurled knob.
    - A telescopic aerial on a swivel.
- Under reduced motion the reels stop and the bubbles stop. The lamps light in order instead of flickering, and the arcs are redrawn only when compute changes by about 10 %.
- In the prototype a model is split across the cards in proportion to their memory, as llama.cpp does by default. The real page reads each card's own numbers.

**Later:** host portraits on Library cards (lazy, only cards on screen, greyed when not downloaded).

## 4. Data: almost all of it exists already

| Needed | From |
| --- | --- |
| Model card (name, ctx, vision, tools, reasoning, size, live, engine, tier, fit) | `/ui/picker` (already has these). |
| Quant and parameter count | **Add two fields to `library.picker()`**: `quant` (the catalog has it) and `params` (parsed from the name: `4B`, `0.6B`, `8x7B`; else estimated from size and quant, snapped to common sizes, as DESIGN.md 8.5 says). |
| Loading state and steps | Chat stream events `zest_loading`, `zest_loading_step`, `zest_loaded`; `/ui/router` `state` for loads started elsewhere. |
| Memory | `/ui/gpu` (per-GPU VRAM), `/ui/library` machine RAM. |
| Apps being answered | The gateway snapshot the Server page polls. |
| Which body each model gets | **Small store addition**: a per-machine archetype assignment, so five installed models show five different body forms and deleting one never changes the others (DESIGN.md 8.5, item 4). |
| The switch | `store` (so the AppImage window remembers it), read once at boot. |

No new polling while the layer is off. While it is on, it reuses the existing polls (`pollServer`, the picker, the Server timer) and adds no timers of its own. Motion is CSS.

## 5. The creature

- **Seed from lineage, not file.** Use the Hugging Face `org/repo` with the quant and "GGUF" stripped, so Q4 and Q8 of one model share a body and differ only in skin grain. Fall back to the model id.
- **The model card, drawn:**
  - context length → eyes (each doubling adds one);
  - quant → skin grain (Q8 smooth, Q2 rough);
  - reasoning → brain;
  - vision → lens;
  - tool calling → hands with pens;
  - parameter count → body size.
- The generator is deterministic and cached by its inputs, so it never regenerates on re-render. At most one animated host per view; shelf and Library portraits are static.
- `role="img"` with a label read off the card, for example: "Picture of Qwen3.6 35B: 9 eyes: remembers about 256K tokens of a conversation; an open brain: thinks before it answers; hands holding pens: can use tools."
- **Art direction: voxels (decided),** from the generator in the zip. The prototype's copy makes three changes the real port should keep:
  1. `form` picks the body form, so each installed model gets its own (the archetype store). Installed models also get distinct colours.
  2. The extra row of small eyes is off. With it, 128K and 256K models both showed 14 eyes, which breaks "more eyes, longer memory".
  3. Skin grain draws from its own random stream. Before this, Q4 and Q8 of one model grew different bodies. Now the body, legs and brain match and only the grain changes; one or two eyes may still shift.

## 6. Interaction and motion rules (from ui-ux-pro-max)

- `prefers-reduced-motion: reduce` turns every ambient animation off and shows static poses. The cartridge moves straight to its place.
- **Motion budget:** one ambient loop per view (the host breathing). Everything else moves only in response to an event: insert, eject, a mood change.
- **Feedback matches the wait.** A load under about one second shows no animation, just the result. Seconds get the insert slide. Minutes get step lines plus elapsed time.
- **Never colour alone:** every lamp has a word, every gauge a number.
- All controls are native `<button>` and `<input>` elements, with 44 px minimum targets, Zest's existing focus ring and full keyboard use. The Machine is a `<dialog>` with focus kept inside and returned when it closes.
- One `aria-live="polite"` line announces "Qwen3.6 35B is ready" once per load, not per animation frame.

## 7. How we know it doesn't disrupt anything

- UI smoke test (`tests/ui/smoke.js`) runs every page with the layer off **and** on. Off must produce the same DOM as today.
- No requests or timers added while off (checked in the smoke run's request log).
- Generator test in node: same input, byte-identical SVG; Q4 and Q8 share the body; five sample models give five forms.
- Manual pass on the real app: a Lemonade load, a Strata start (slow path), a refusal (busy), keyboard-only, reduced motion, one dark theme.

## 8. Phases (each one ships behind the switch)

1. **Decisions:** the open questions below.
2. **Creature module:** generator, model adapter (seed, params, quant), archetype store, the two picker fields, and a hidden specimen sheet (`#specimens`) that shows the sample models. Tests.
3. **Host beside the chat:** portrait, moods, lamp, the Settings switch.
4. **The Machine:** bays, gauges, shelf, insert and eject wired to existing load and unload, refusals, the Strata slow path.
5. **Server bench strip, and the Hardware apparatus.**
6. **Library portraits;** accessibility and reduced-motion pass; screenshots for the README.

Rough size: phase 2 is the largest single piece (the generator port and adapter). Phases 3 and 5 are small; phase 4 is medium.

## 9. Out of scope

The full "Zest at the table" redesign in DESIGN.md (new rail, the table replacing chat, the whiteboard replacing Cowork) is not part of this. This layer could be its first step, but nothing here commits to it. Also out: cross-machine creature matching, a "try any name" field, mobile-specific design.

## 10. Decisions and open questions

Decided (29 October 2026):
1. **Art:** voxels, from the existing generator (section 5).
2. **Words:** plain words for the layer's controls (section 2, principle 3), but the app itself is now named Shoggoth, which replaces the earlier "no shoggoth in the UI" (section 11).
3. **Where the Machine opens:** a drawer over the chat, so your conversation stays in view. The prototype still has the page version for comparison; the app builds only the drawer.
4. **The bigger redesign:** this layer should grow into DESIGN.md's table later. So the picture, the slot and the model card are built as reusable parts, not as chat-only markup.
5. **Rename:** Zest becomes Shoggoth, fully, with fallbacks for existing installs (section 11).
6. **App icon:** many eyes, on Zest's old lemon tile (section 11).
7. **Chat glyphs:** custom drawn glyphs replace every emoji in chat (section 11).
8. **Flavour text:** full cosmic horror, always on, with the plain word kept beside it. Mythos names are never used: the running model's name stands in (section 11).

Decided from mockups (29 October 2026; the prototype's "Mockups to choose from" controls still show every option):
9. **The picture in chat:** the tube on the header, cabled to the model picker (section 3A). The header is 112 px while the layer is on.
10. **First run:** pictures start **on**, with a small dismissible note at the top of the chat: "Model pictures are on. Turn them off in Settings › Look." The switch is in Settings › Look. This replaces principle 1's "off by default".
11. **Mainframes:** each card's cabinet shows a small screen at its top: "MODEL ON THIS CARD", the model's name, and how many GB of it are on that card. The lamps and memory bar move down to make room.
12. **Server bench:** the lab bench, matching the Hardware page. The model sits in a beaker on a table, cabled to one Erlenmeyer flask per app, which bubbles while the app's request is thinking and fills while it is written. A numbered plate on the table's front gives each app's name and state.

13. **Type:** Doto, a dot-matrix face, for titles and for every screen. Inter for body text, JetBrains Mono for small labels (section 12).

Still open:
1. **The Doto screens:** sign-off on how the Hardware apparatus, the Server bench and the log look set in Doto (section 12).

## 11. Shoggoth: the name, the icon, the glyphs and the flavour

Decided 29 October 2026. The prototype's Identity page shows all of it, and its chat and "Cowork uses a tool" button show it in use.

### The rename, in full, with fallbacks
Zest appears about 700 times across the code, docs and packaging. Everything moves to Shoggoth, and existing installs keep working:

| What | Becomes | Fallback |
| --- | --- | --- |
| The name in the UI, window title, README, docs, `docs.md`, the recommendations files' comments and text | Shoggoth | — |
| Python package folder `zest/`, entry `zest/server.py`, the CLI | `shoggoth/`, `shoggoth/server.py`, a `shoggoth` command | a `zest` command that prints a one-line notice and runs `shoggoth` |
| 19 `ZEST_*` environment variables (`ZEST_DATA_DIR`, `ZEST_PORT`, `ZEST_AUTH`…) | `SHOGGOTH_*` | each `ZEST_*` is still read when its `SHOGGOTH_*` is unset |
| Data and config folders `~/.local/share/zest`, `~/.config/zest` | `…/shoggoth` | moved on first run if the new one doesn't exist, with a note in the log; if the move fails, the old folder is used in place |
| Browser storage keys (`zest-*`) and stored settings such as Lemonade's `loaded_by_zest` | `shoggoth-*`, `loaded_by_shoggoth` | read the old key once, write the new one |
| The `zest` provider in the opencode connect snippet, and the model owner name the gateway reports | `shoggoth` | the gateway still answers requests that name `zest` |
| AppImage, `zest.desktop`, `zest.svg`/`.png`, the AUR package | `Shoggoth-x86_64.AppImage`, `shoggoth.desktop`, the new icon, an AUR `shoggoth` package with `provides`/`replaces: zest` | — |
| The GitHub repository | renamed by you; GitHub redirects the old URLs, and the docs' links are updated | — |

Tests cover the fallbacks: an old env var, an old data folder that gets moved, and an old storage key.

### The icon
**Chosen: many eyes.** A dark blob with five eyes of different sizes, on Zest's old lemon tile, so existing users recognise it. It is drawn in fixed colours, because an icon must look the same on every desktop. It is also the brand mark beside the name in the rail, and it becomes `shoggoth.svg` and `.png` in packaging.
- When it is drawn for real, check that it reads as a creature and not a skull at 16 and 32 px. A lumpier outline, an eye breaking the silhouette, or a tendril or two would help.
- The candidates not chosen: the eye (one slit-pupilled eye on a dark tile) and a grown voxel host.

### Chat glyphs
Seventeen 24 px line drawings replace the emoji, drawn in the text colour so they follow the theme, at the same stroke as the app's other icons. Eyes, tendrils, a maw and sigils, sinister without gore:

| Emoji | Glyph | Where |
| --- | --- | --- |
| 🍋 Loading | `rise`: an eye surfacing from water | Cowork's step diagram |
| 📖 Reading | `eye`: a slit-pupilled eye | step diagram, reading a file |
| 🤔 Thinking | `dream`: a closed, lashed eye under a spiral | step diagram, the thinking block |
| 💭 Thought | `thought`: three small eyes | a finished thinking block |
| ✍️ Writing, 📝 | `quill`: a tendril holding a quill | step diagram, writing a file |
| 🛠️ Working | `grasp`: a suckered tendril | step diagram, a tool being prepared |
| 🙋 Your OK | `threshold`: a doorway with a tendril at it | step diagram, approval cards |
| ✏️ Edit | `edit`: lines with a tendril tip | editing a file |
| ⚡ Command | `sigil`: a prompt inside a ringed sigil | running a command |
| 🔎 Search | `seek`: a radiant eye | grep and find |
| 📁 Folder | `crypt`: a lidded casket with an eye | listing a folder, "Working in…" |
| 🧰 Skill | `tome`: a book with a sigil | using a skill |
| 🌐 Web | `orb`: an eye inside a globe | web search, its errors |
| 🧹 Tidying | `maw`: a toothed maw | compaction |
| ✂️ Cut short | `severed`: a cut tendril | an answer that ran out of room |
| ⚠️ Warning, ✅ Done | `omen`, `sealed` | the search test and other notes |

### Flavour text
- **Where:** under each step's plain word in chat (Reading, Thinking, Writing, Loading) and on each tool card ("Searching files for “basil”"), one italic line in the serif. It changes every few seconds while a step lasts, never repeating the line just shown.
- **Tone:** full cosmic horror, never gory. Examples: "Ph’nglui… the many eyes of Qwen3.6 35B open, and it considers you." "Angles that should not meet are meeting." "GLM-4.7 Flash rewrites garden/notes.md. The old text does not resist."
- **Names:** no mythos names, ever. Wherever a line would name an old one, it names the running model: `{model}` is filled in with the model's name, and `{target}` with what a tool is working on.
- **Always on,** for everyone. The plain word stays beside it, so meaning never depends on the flavour. Only the plain word goes to the screen reader's live region; the flavour line changes too often to announce.
- **Editable:** the lines ship in a YAML file (`flavour.yaml`), read and checked by the same loader as the recommendations, so you can add lines without touching code. There are 16 sets: loading, reading, thinking, writing, one per tool, the approval wait, making room, cut short and idle.

## 12. Type: Doto, Inter, JetBrains Mono

Decided 29 October 2026, from four Teenage Engineering-style pairings mocked up in the prototype (its Type control still offers today's fonts for comparison). Doto, Inter and JetBrains Mono are all under the SIL Open Font License, so they can be embedded in `index.html` as base64 `woff2`, the way the app carries its fonts today. Subset them to Latin to keep the page small.

- **Titles:** Doto at weight 900, 52 px for the page title ("Chat", "Server"), and Doto for the wordmark and section headings. This is the "larger, more proportionate" title, sized for the 112 px header.
- **Body:** Inter, for messages, help text and controls.
- **Labels:** JetBrains Mono for small mono labels outside the screens. The flavour text is JetBrains Mono italic.
- **Screens are Doto.** Everything shown inside a visual pane is set in Doto at weight 900, as if on a dot-matrix display:
  - **Hardware:** the mainframe screens, the instrument numbers and printed scales, the plinth and foot labels, and the numbered label row.
  - **Server:** the lab bench plates, a new log screen, and the request list.
  - **The log screen** is a dark recessed panel above the request list, showing the gateway's lines as they happen (request in, first token, done with tokens and speed) in light, grey and accent colours.
  - Small Doto (under about 11 px) smudges, so these screens set it at 12 px or more where they can. The bench plates and mainframe captions are the ones to check at real size.
