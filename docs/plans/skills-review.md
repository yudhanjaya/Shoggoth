# Skills review: diagram-design and Ponytail

**Date:** 14 November 2026
**Scope:** Shoggoth 5 plan §6.4 and [`../AGENT-RUNTIME.md`](../AGENT-RUNTIME.md#skills).
**Method:** read both repositories at their current `main` (not cloned; file lists and contents fetched from the GitHub API and `raw.githubusercontent.com`), then compared them with `shoggoth/skills/`.

Two constraints decide everything below:

1. **The chat renders only fenced `mermaid` and `svg` blocks.** HTML is not rendered. A fenced `svg` block is shown as an image and cannot run scripts. Folderless chats have no tools at all, and bundled skills marked `inline` (including `diagrams`) are pasted into the prompt of every folderless chat.
2. **The working budget for a bundled skill is about 8 KB**, and an `inline` skill should be smaller than that because it is a permanent prompt cost.

| Repository | License | Total skill text | Output | Verdict |
| --- | --- | --- | --- | --- |
| [cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design) | MIT | 1 `SKILL.md` of 31,967 B plus ~820 KB of references and ~364 KB of scripts | Self-contained HTML + inline SVG; SVG/PNG on export | **Adapt 10 rules into the existing `diagrams` skill. Do not bundle.** |
| [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | MIT | 6 `SKILL.md` files, 1,769–5,417 B each (21,177 B total) | Instructions only (plus plugin hooks/commands) | **Write a short Shoggoth `good-code` skill. Keep Ponytail as an optional install for large models.** |

Neither repository needs to be vendored. Both are MIT, so adapting prose is allowed; include a courtesy attribution line in the set's `set.json`.

---

## 1. cathrynlavery/diagram-design

### 1.1 License

- **MIT.** `LICENSE` is 1,071 B; the GitHub API reports SPDX `MIT`.
- The repository also ships third-party assets: Tabler Icons (MIT) and Simple Icons (CC0), and loads Instrument Serif / Geist / Geist Mono from Google Fonts. That matters only if we copy its assets. We should not; we are adapting a handful of design rules, not redistributing icons, fonts or templates.
- MIT requires the copyright and permission notice when substantial portions are copied. A paraphrase of the *rules* is not a substantial copy, but a line such as `Rules adapted from cathrynlavery/diagram-design (MIT)` in `shoggoth/skills/zest/set.json` (later `shoggoth/set.json`) is cheap and accurate.

### 1.2 Size of each skill file

There is **one skill**, `skills/diagram-design/SKILL.md`, with a large support tree. This is not a set of small skills.

| File | Bytes | Note |
| --- | ---: | --- |
| `skills/diagram-design/SKILL.md` | **31,967** | The skill itself; ~4× the 8 KB budget |
| `references/primitives-core.md` | 13,125 | Exact SVG markup and connector rules |
| `references/layout-budget.md` | 5,443 | 4px grid and per-type budgets |
| `references/style-guide.md` | 20,856 | Colours, type ramp, font source |
| `references/semantic-patterns.md` | 16,679 | Behaviour patterns loaded before a type |
| `references/output-spec.md` | 19,200 | Format × size × detail × audience |
| `references/type-*.md` (50 files) | **1,079–43,216** each, ~492 KB total | One type reference is loaded per diagram |
| `references/primitive-icons.md` | 105,941 | Icon library |
| `scripts/*.py` (8 files) | **3,682–82,126** each, ~364 KB total | Import and export; PNG needs Playwright |
| `assets/template*.html` | 4,080–23,154 | Scaffolds |
| `assets/example-*.html` (~150) | 5,079–208,294 each | One per type per variant |

A routine request loads `SKILL.md` plus one type reference plus at most `primitives-core.md` and `layout-budget.md`: **roughly 38–88 KB of instruction before drawing anything**, 5–11× the whole small-model budget. An import adds `output-spec.md` (19 KB) and an import reference (11–14 KB); an export adds `export.md` (31,981 B) and a Python script.

### 1.3 What output it tells the model to produce

- **Default: one self-contained `.html` file** with embedded CSS and **inline SVG**. Its §12 says "Always produce a single self-contained `.html` file"; static by default, JavaScript only for optional animation.
- On request it can **export the `<svg>` node to SVG or PNG** (§12, `references/export.md`). Export is manual ("never produce export files unprompted"). PNG rendering uses Playwright; SVG export uses `scripts/export_svg.py`.
- Its import path accepts draw.io, **Mermaid**, Excalidraw and PlantUML and *redraws* them in its own style. Mermaid is an **input format only**; it never emits a fenced `mermaid` block.
- It is explicit that it is the wrong tool for some jobs: "Diagrams that live in Git and change every sprint → Mermaid. It diffs, and GitHub renders it." That is exactly Shoggoth's default case.

### 1.4 Would it work with a renderer that only shows Mermaid and SVG?

**Not as a bundle.**

- The headline deliverable is HTML, which Shoggoth will display as plain text. Only the SVG export matches, and that is a secondary path that assumes the model can write files and run Python (and, for PNG, install Playwright). Folderless chats cannot do either.
- The 32 KB `SKILL.md` alone breaks the small-model budget before any reference is loaded. Making it `inline` in folderless chats, as the current `diagrams` skill is, is impossible.
- Its workflow depends on a first-run style-guide gate, exact coordinates, a 4px grid, per-type references and (optionally) verification scripts. A small local model will not follow that reliably, and Shoggoth cannot run the checker.
- Its 50-type taxonomy is far larger than the renderer can express with Mermaid, and mostly irrelevant to chat answers.

### 1.5 Recommendation: adapt, do not bundle

Add a compact **editorial rules** section to `shoggoth/skills/zest/skills/diagrams/SKILL.md` (renamed to `shoggoth` in 6.4). Keep Mermaid as the default, keep the existing type table as the single source of truth, and extend the SVG paragraph with the SVG-specific rules. Target **1.2–1.8 KB of new text**, keeping the whole skill at or below roughly 6 KB, because it is `inline: true` and every folderless chat pays for it.

While editing, fix the now-stale example: the flowchart in the current skill still shows `pi --> lem["Lemonade"]`, which Phase 1 removes. Replace it with `llama-server` or a neutral engine box.

### 1.6 Ten rules to carry over

These are the rules with the best value-per-byte for a small model, rewritten so they apply to Mermaid and raw SVG rather than its HTML templates.

1. **Run the deletion test before you draw.** Can a node go? Can two nodes that always travel together become one? Can an arrow go because the layout already implies it? Can a label go because shape or colour already says it? The diagram is finished when nothing can be removed. (Its "highest-quality move is usually deletion".)
2. **If a table, a paragraph or a bullet list would say it better, do not draw.** A one-shape "diagram" is a sentence. This should sit next to the existing "do not describe a diagram in words" rule.
3. **Budget the drawing: at most 9 nodes and 12 arrows; split above that.** Aim for a density of about 4/10. Above 9 nodes, draw an overview and a detail instead of one dense picture.
4. **One focal colour, on 1–2 things.** Use the accent (`classDef` in Mermaid, one stroke/fill in SVG) for the one or two elements the reader should look at first. Accent on everything erases the signal.
5. **State the plan in one line before drawing.** Say the chosen type, the direction, and what you are leaving out. Then draw. This catches the wrong diagram before tokens are spent on it.
6. **Use the reader's words in names, and reserve monospace for technical values.** Names go in normal text; ports, commands, paths and field types may use code formatting. Do not make monospace the blanket "developer" font.
7. **Show hierarchy through shape, not identical boxes.** Decision = diamond, store = cylinder, external = a distinct shape or dashed edge, normal step = box. Identical boxes for every node erase the structure.
8. **Make connectors carry meaning.** Pick one direction (`TD` or `LR`) and keep it; label only edges whose meaning is not obvious; never draw crossing diagonals; in hand-written SVG use right-angle elbows and keep 6–10px of clear space around every label.
9. **For raw SVG only: no shadows; 1px hairlines; corner radius ≤ 10px; geometry on a 4px grid; text no smaller than ~10px.** Give the SVG `role="img"` with a `<title>` and a one-sentence `<desc>`, and keep it static (Shoggoth will not run scripts).
10. **Avoid generated-looking decoration.** No dark-mode cyan/purple glow, gradients, drop shadows, three identical summary cards, or vertical rotated text; do not imitate Mermaid's default look when hand-writing SVG. Plain shapes, clear labels and whitespace.

Deliberately **not** carried over: the 50-type guide, the style-guide onboarding gate, the format/size/detail/audience dials, the import pipelines, the motion contract, the icon library, the geometry scripts, and its "full editorial" card layout. They are either unrenderable, tool-dependent, or too large for the budget.

---

## 2. DietrichGebert/ponytail

### 2.1 License

- **MIT.** `LICENSE` is 1,071 B; the GitHub API reports SPDX `MIT`.

### 2.2 Skill files and sizes

The six skill files are all comfortably under 8 KB individually. The repository is also a plugin, and that part is much larger than the skills.

| File | Bytes | What it is |
| --- | ---: | --- |
| `skills/ponytail/SKILL.md` | **3,496** | The core "lazy senior dev" mode; the only generally useful skill |
| `skills/ponytail-review/SKILL.md` | **5,143** | Review a change, with a fixed four-part finding format |
| `skills/ponytail-audit/SKILL.md` | **5,417** | The same review for a whole repository |
| `skills/ponytail-debt/SKILL.md` | 1,769 | `git grep` for `shortcut:` comments into a ledger |
| `skills/ponytail-gain/SKILL.md` | 2,164 | Prints its published benchmark scoreboard |
| `skills/ponytail-help/SKILL.md` | 3,188 | Reference card for levels and commands |
| `.agents/rules/ponytail.md` | 2,623 | The core rules as a host rule file; copied into `.clinerules`, `.cursor/`, `.kiro/`, `.windsurf/`, `.github/copilot-instructions.md`, etc. |
| `.openclaw/skills/*/SKILL.md` | 1,745–5,250 | Duplicate distribution of the same six skills |
| `pi-extension/index.js` | 9,486 | Pi extension that implements the mode and commands |
| `hooks/*.js` | 3,462–14,078 | Activation, statusline, mode tracking, config; Claude/Cursor/Codex hooks |
| `package.json` | 1,260 | npm package entry (`@dietrichgebert/ponytail`) |

**Important distinction:** the *skills* are text, but "Ponytail" as advertised is a **Pi/Claude plugin**: a persistent mode with `lite`/`full`/`ultra` levels, `/ponytail-*` commands, a statusline "band", session activation and a benchmark. None of that comes from the `SKILL.md` files alone. Shoggoth's skill loader copies directories containing a `SKILL.md`; installing `github.com/DietrichGebert/ponytail` would give the six skills but **not** the extension or hooks, so levels, mode persistence and the slash commands would not exist. That is not a reason to avoid the text; it is a reason not to describe it as "Ponytail" without qualification.

### 2.3 What fits a small model under ~8 KB

**Fits, and is worth it:**

- `skills/ponytail/SKILL.md` at **3,496 B**. It is one page, numbered, and its six-step "first option that fully works" ladder is exactly the shape small models handle well.
- From it: the scope pass (callers, tests, fixtures, config), the six-step ladder, the ban on speculative abstraction/options/config, "deletion beats addition", "keep the structure the codebase already has", the never-cut list, the one-test rule for non-trivial logic, and the closing "what I skipped / what risk remains" line.

**Fits byte-wise but not attention-wise:**

- `ponytail-review` (5,143 B) and `ponytail-audit` (5,417 B) are under 8 KB, but they assume `grep`, git and the ability to read a repo, and they ask for a long structured report. In a folderless chat they cannot run; with a small model they will produce the *shape* of a review without the checking discipline. These are the parts that justify the plan's "models ≥ 20B only" note.

**Does not transfer meaningfully:**

- `ponytail-debt` needs a live `git grep` and the `shortcut:` comment convention, which only pays off if someone runs the ledger.
- `ponytail-gain` is a benchmark display, not guidance.
- `ponytail-help` documents levels and commands Shoggoth will not have.
- The hooks, statusline, config file and npm/Pi extension are code execution and duplicate Shoggoth's own approval-gate and packaged-extension concerns.

### 2.4 Recommendation

**Write a short Shoggoth `good-code` skill** at `shoggoth/skills/shoggoth/skills/good-code/SKILL.md` (or `zest` until the 6.4 rename), target **2.5–3 KB**, in Shoggoth's plain voice, credited to Ponytail. Enable it by default for folder chats; do **not** mark it `inline`, because folderless chats rarely write code and the diagrams/markdown skills already consume that inline budget.

Then treat full Ponytail as an **optional curated package for models ≥ 20B**, not a bundled default:

- If §6.1's testing shows the Pi extension works under Shoggoth's Pi, pin `@dietrichgebert/ponytail` by version and sha512 like any other curated package, and expose its review/audit as buttons in a folder chat. Its own instructions then supply the mode.
- If the extension cannot be pinned and verified, install only the `ponytail` skill directory as a third-party set, with a note on the Skills page that levels and commands are unavailable. Do not copy review/audit/debt/gain/help into the bundled set: they overlap Superpowers and add tooling assumptions.

Do not implement a "≥ 20B" model gate for skills in 5.0. Shoggoth has no per-model skill enablement and adding it for one skill is more machinery than value. Bundling the short skill and leaving Ponytail installable for people who want it gets the same result.

### 2.5 Core rules the short version should keep

1. **Scope first.** Read the task and the code it touches. List every place the change must reach: callers, tests, fixtures, config, exports. Check what it could break for users. That is scope; extra features are not.
2. **Take the first option that fully works.** (a) Does it need to exist at all? (b) Is it already in this codebase — a helper, component, pattern? Use it the way the surrounding code does. (c) Standard library or platform feature? (d) An already-installed dependency? (e) Can it be one clear line? (f) Otherwise, the minimum code that works.
3. **No speculative structure.** No abstraction, wrapper, option, config or "for later" code nobody asked for. Deletion beats addition. Keep values in the form the platform already gives you. Keep the codebase's existing layers and conventions.
4. **The shortest working diff wins — once you know everything it must touch.** A one-liner that needs decoding is not short.
5. **Never cut the things that protect people.** Input validation at trust boundaries, error handling that prevents data loss, security, accessibility, and anything the user explicitly asked for. (Shoggoth's own addition: the approval gate and folder boundaries are not negotiable.)
6. **A bug fix starts with the callers.** Grep every caller of the function you are about to change, then fix the root cause once in the shared code, not in one caller.
7. **Non-trivial new logic gets one small test or self-check.** A branch, loop, parser, money, security, data write or a new script needs a check that fails when it breaks. Trivial changes need none.
8. **Comment only the why that the code cannot show,** in one line. Do not narrate what the code does.
9. **End with what you skipped and what risk remains,** one or two lines. If you did not check something, say so.
10. **Defer to the process skills.** If `test-driven-development` is active, its test-first order wins; if `systematic-debugging` is active, its investigation order wins. `good-code` constrains *size and scope*, not the order of work.

Drop from the short version: levels, mode persistence, slash commands, the statusline, `ponytail:`/`shortcut:` ledger machinery, the benchmark scoreboard, and the repo-wide audit. Also drop the "lazy" persona framing; keep the rules, because a small model may take "be lazy" literally. Its own benchmark notes that local small models were tested (llama3.2), but the published benchmarks are for large hosted models, not for the 2–8B models Shoggoth targets.

---

## 3. Overlap with Shoggoth's existing bundled skills

Current bundled sets:

- **`zest`** (to be renamed `shoggoth` in 6.4): `diagrams`, `markdown-writing`, `start-a-job`, all `inline: true`.
- **`superpowers`** (MIT, trimmed to six): `using-superpowers` (bootstrap), `brainstorming`, `writing-plans`, `test-driven-development`, `systematic-debugging`, `verification-before-completion`.

| Proposed content | Existing skill | Overlap | What to do |
| --- | --- | --- | --- |
| diagram-design guidance | `zest/diagrams` | **High in purpose, low in substance.** Both answer "which diagram type"; the existing table already covers flowchart, sequence, ER, state, class, gantt, mindmap, timeline. The existing skill has no editorial rules (deletion, density, focal colour, connector hygiene, SVG accessibility). | **Extend the existing skill; do not add a second diagrams skill.** Keep the existing type table as the only type table. Add the ten rules in §1.6. |
| diagram-design "a table may be better" | `zest/markdown-writing` | Small. Markdown-writing already says to use a table for comparison. | One sentence in `diagrams` is enough; do not cross-reference. |
| Ponytail core | `superpowers/test-driven-development` | **Partial.** Ponytail's "one small test for non-trivial logic" is weaker than TDD's "no production code without a failing test". Loaded together, TDD wins on test order, Ponytail on scope. | State the deferral explicitly in `good-code` (rule 10). Do not restate TDD. |
| Ponytail core | `superpowers/systematic-debugging` | **Partial.** Both say find the root cause; Ponytail adds "grep every caller first" and "fix once in the shared code". Systematic-debugging has the fuller investigation procedure. | Keep one line in `good-code` and point at the debugging skill for the method. |
| Ponytail core | `superpowers/verification-before-completion` | **Partial.** Both require evidence. Verification-before-completion owns "run the command and read the output"; Ponytail owns "new logic leaves a test or self-check". | Keep the test rule; do not duplicate the completion-claim gate. |
| Ponytail review/audit | `superpowers/systematic-debugging` + `verification-before-completion` | **Moderate.** Its "must fix / should fix / nice to have" report format is new, but its checks repeat the debugging and verification skills. | Do not bundle. Optional package only. |
| Ponytail core | `superpowers/writing-plans`, `brainstorming` | Low. Only the "enumerate the scope" habit overlaps; Ponytail is deliberately anti-planning. | No action. |
| Ponytail core | `zest/markdown-writing` | Low. "Short sentences, plain English, one-line risk note" is adjacent. | Do not duplicate style rules. |
| anything above | `zest/start-a-job` | None. | No action. |

Net effect if both recommendations are implemented:

```
shoggoth/skills/shoggoth/          (renamed from zest)
  set.json                         + good-code, writing-skills, unslop slots
  skills/diagrams/                 extended with 10 editorial rules, Lemonade example fixed
  skills/markdown-writing/         unchanged
  skills/start-a-job/              unchanged
  skills/good-code/                new, ~3 KB, adapted from Ponytail
  skills/writing-skills/           new (6.3)
  skills/unslop/                   new (6.4)

shoggoth/skills/superpowers/       unchanged
```

No new set is added, no existing skill is replaced, and the total instruction weight of the bundled sets grows by roughly 3–4 KB.

---

## 4. Decisions and follow-up

**Decisions**

1. **diagram-design: adapt, do not bundle.** Its MIT license permits it, but its output (self-contained HTML) is invisible in Shoggoth's chat, its 32 KB `SKILL.md` is 4× the budget, and its per-diagram load is 38–88 KB. Carry the ten rules in §1.6 into the existing `diagrams` skill. Credit the source in `set.json`.
2. **Ponytail: write the short skill, do not bundle the plugin.** The core 3,496 B skill fits the budget, but the value that survives in Shoggoth is the scope/anti-overengineering discipline, not the mode, levels or commands. A ~3 KB `good-code` skill, credited, covers small models. Leave the full package as an optional, pinned install for large models (or as an opt-in third-party set with a note that its commands are unavailable).
3. **No overlap-driven additions.** `good-code` explicitly defers test order and debugging method to Superpowers. `diagrams` stays the only diagram skill.

**Follow-up when 6.4 is implemented**

- Add the rules to `shoggoth/skills/zest/skills/diagrams/SKILL.md`; keep the file at or below ~6 KB because it is `inline: true`.
- Fix the `Lemonade` node in the diagrams example as part of the Phase 1 rename.
- Add `shoggoth/skills/zest/skills/good-code/SKILL.md` at ~2.5–3 KB, with the ten rules in §2.5, and no `inline` entry in `set.json`.
- Add attribution to `set.json`: `"note": "Editorial diagram rules adapted from cathrynlavery/diagram-design (MIT). good-code adapted from DietrichGebert/ponytail (MIT)."`
- Optionally add a size warning to `skills.py` for any bundled `SKILL.md` over 8 KB, matching the `save_skill` limit already planned in 6.3. This is a warning, not a rejection, because third-party sets can legitimately be larger.

**Sources**

- <https://github.com/cathrynlavery/diagram-design> — `skills/diagram-design/SKILL.md`, `references/`, `scripts/`, `LICENSE`, `README.md`
- <https://github.com/DietrichGebert/ponytail> — `skills/*/SKILL.md`, `.agents/rules/ponytail.md`, `LICENSE`, `pi-extension/`
- Shoggoth: [`docs/AGENT-RUNTIME.md`](../AGENT-RUNTIME.md#skills), `shoggoth/skills/zest/`, `shoggoth/skills/superpowers/`
