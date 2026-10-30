# Unslop audit of Shoggoth's UI

Audit made with the [unslop](https://github.com/heydamir/unslop) skill (commit `4652254`, CC BY 4.0, by Damir Sokolovsky). Shoggoth 4.1.0-beta.1 was run from source in a sandbox with its own data and config folders. Every page was viewed at desktop width (1440×900), and Chat also at phone width (375×812). No code was changed.

## Verdict

**Light slop.** unslop's polish mode applies: fix what the audit found and keep the existing brand. The yellow-and-cream palette, Chakra Petch headings and the hardware rig are real decisions, not defaults. There are no emoji, no exclamation marks and no gradient text in the interface. The Advanced pages read "Advanced > Tuning" in one typeface, as #17 asked. The problems below are about consistency and a few leftover habits.

**Direction (from the product, for approval):**

- **Product:** a private desktop app that runs large language models on your own graphics cards.
- **Character:** tactile, plain-spoken, instrument-like.
- **World:** a bench-top hardware rig.
- **Moments:** the hardware rig, the model tube, the shoggoth.
- **Signature:** the highlighter underline on headlines.

## High priority

1. **Two titles on every Advanced page.** "Advanced > Server" sits above a second large headline ("Share your models with other apps"). Docs does the same ("Docs" above "How Shoggoth works"). Keep the breadcrumb and drop the second large heading, or set it at body size.
2. **Raw browser-blue underlined links** (Engines: "lemonade-server.ai", "Strata on GitHub", "the ten-minute guide"; Models: "Not now"; Chat: "Skills"). Use the ink colour with a hairline underline, and the accent only on hover.
3. **Too many font weights.** Measured: 400, 500, 600, 650, 700, 800, 900. Use 400 and 500, with 600 at most. Remove 650. Keep 800–900 only on the large Chakra Petch headlines.
4. **Contradictory Engines state.** Lemonade shows a "Built in" pill next to "Not installed". Say one true thing. This may occur only on source checkouts, since the sandbox had no bundled Lemonade.
5. **Repeated facts on Models cards.** Size appears three times ("Needs about 5.2 GB", "Small · 5.2 GB", button "· 5.2 GB"), and "Runs on Lemonade" twice (meta line and footer). Show each once.
6. **Card titles wrap badly in Installed.** "DeepSeek-V4.1-Flash-1017 GSQ-RCO IQ2_XXS" breaks over four lines beside the "On this computer" pill, and sibling cards differ in height. Put the pill on its own line above the name, and move the quant suffix to a quiet mono line.
7. **The Models page opens with a large folder chooser** (six paths, three buttons) above any model. After a choice is saved, collapse it to one line ("Models folder: /path · Change"). "Not now" should look like a button.
8. **Dead click on the model picker.** With no models installed, "Choose a model" opened nothing visible. It should open a menu with one row saying so, with a link to Models. Not tested with a model installed.

## Medium priority

9. **Sub-nav under Advanced** uses a thin connector line that nothing else in the sidebar uses. Indent the children and drop the line.
10. **Uneven spacing in the sidebar.** The 2×3 icon grid has uneven gaps. Rows should share one height and one gap on the 4px scale.
11. **The "Advanced" yellow dot** (update badge) is the only badge in the nav. Say what it means on hover, or show it only when there is something to act on.
12. **Chat top strip is crowded.** The rig image, the wire loop at the left edge, "EMPTY · CHOOSE A MODEL", the "Choose a model" select and the "Memory 128k · default" button sit within about 100px. The rig disappears on phone width.
13. **Chat right panel.** The "Model pictures are on" notice repeats "Model pictures" in bold mid-sentence. Selected radio cards use the same yellow as the primary button. Use a neutral surface step for selected rows and keep yellow for actions.
14. **Composer footer** ("Folder: home", "Start a job…", Send) wraps to two lines per label on phone width. Use an icon plus short label, or hide "Folder:" on narrow widths.
15. **Hardware cards** both carry a yellow outline all the time. Reserve it for the active card. The page also fades in over about a second, which reads as flicker on first view.
16. **Engines page:** "More engines worth knowing" shows one half-width card in an otherwise empty row. Fold it into the engines list or into "Add an engine".
17. **Tuning with nothing to tune** is one grey sentence on a blank page. Add the next action (a link to Models).
18. **Engine settings** says "Every setting Lemonade offers" but shows only "Engine: Strata" and an empty "0 settings" search. Hide the search when there is nothing to search.
19. **Themes** gives the 12 editor swatch cards the same layout as the 3 Shoggoth themes, so the page has no hierarchy. Make the Shoggoth themes larger and the editor colours a compact list.
20. **Skills:** Superpowers descriptions are shouted ("You MUST use this…"). That is upstream text. Leave it, or clip it to one line with "Details".

## Motion and effects (from the stylesheet)

21. **30 `infinite` animations and about 28 keyframes.** The mascot (`zcBreathe`, `zcSway`, `zcBlink`) is a deliberate character moment; keep it. Check that `pulse`, `bob`, `hop`, `sig` and `flow` run only on live states (loading, thinking), not at rest.
22. **One `transition: all`.** Replace it with named properties.
23. **About 18 gradients and 17 glow-style shadows.** Most are probably in the rig and the skins, which is fine. Check the ones on chrome (buttons, the active nav pill); unslop wants plain surface steps there.

## Copy

24. **Model descriptions on the Models page use profanity** ("getting shit done", "prolific bullshitter"). It is the owner's tested text and voice, so it is the owner's call. Everything else in the UI is plain and polite, so it stands out.
25. **The first-run error** ("The part that runs AI isn't answering") is clear. Its red "!" badge is the only filled red element on any page.
26. **Trailing periods on short labels** ("Fits this computer.", "This is your home folder."). unslop wants none on labels and buttons.

## Not verified

- Tuning with real presets. The sandbox had none, because the config folder was overridden and Strata's saved setups were not visible.
- The model picker and tube with a model installed.
- The Teenage Engineering and Braun skins, the dark editor themes and keyboard focus states.

## Suggested order

Items 1–8 first (visible and easy to defend), then 9–20, then 21–23 after checking which animations and gradients are on chrome. Item 24 is the owner's decision.

## Status

Items 1–26 were worked on branch `unslop-high`; before and after screenshots are in [unslop-shots/](unslop-shots/README.md).

- **Done:** 1–11, 13–19, 21 (steady green status dot), 22, 25, 26. Also sentence-cased the remaining uppercase section labels (chat side panel, file popup, themes, tool tags).
- **Checked, no change needed:** 15's page fade (it is the 0.32s view transition; the slow part is the rig drawing), 23 (the gradients are keycap-button tone shifts within one hue and the glows are in the Braun skin's lamp, both deliberate).
- **Left on purpose:** 12 (the chat top strip is the rig and picker layout from step 4, so changing it is a design decision), 20 (upstream Superpowers text), 24 (the owner's voice).
