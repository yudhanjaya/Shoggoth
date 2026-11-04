# AI tells: a working list, and the docs audit

A checklist of writing patterns that make text read as machine-written, with the ones most associated with Claude called out. Use it as a signal for editing, not as proof of authorship. Several patterns also appear in ordinary human writing, and the tells are only evidence when they cluster.

The Claude-specific entries come from observed output, not from a measured corpus. Treat them as a checklist to read against, not a verified statistic.

## Word and phrase tells

**Strong (review every use):** delve, tapestry, testament to, landscape (as metaphor), realm, paradigm, embark, beacon, underscores, pivotal, meticulous, seamless, game-changer, nestled, vibrant, bustling, intricate, ever-evolving, holistic, actionable, impactful, synergy, load-bearing (as a metaphor).

**Clusters (flag when two or more share a paragraph):** robust, leverage, streamline, empower, foster, elevate, unleash, navigate, harness, crucial, nuanced, multifaceted, comprehensive, facilitate, underpin, cornerstone, paramount, ecosystem (as metaphor), transformative, quintessential, overarching.

**Density (flag when one word repeats 3% or more of the text):** significant, effective, innovative, dynamic, compelling, remarkable, sophisticated, scalable.

**Clarity, not authorship evidence:** utilize, in order to, due to the fact that, serves as, features (as a verb), boasts, commence, ascertain, endeavor.

## Claude-associated patterns

- **Hollow intensifiers.** "genuinely," "truly," "honestly," "to be clear," "it's worth noting that," and "actually" used only for emphasis.
- **Not-X-but-Y framing.** "It's not a bug, it's a feature," "This isn't about X, it's about Y," the split-sentence form ("The headline isn't the speed. The real story is..."), and the countdown of negations before the reveal.
- **Reader-steering frames.** "Here's the thing," "Here's what that means," "The kicker," "The result?", "Here's what's interesting."
- **Narrated candor.** "I want to be upfront," "To be fully transparent," "I'd rather flag this than let you find it later."
- **Vague endorsement.** "worth reading," "worth a look," "worth your time," "worth maintaining."
- **Lingering-attention and reaction claims.** "I can't stop thinking about this," "What surprised me most," "The most interesting part."
- **Self-labelling significance.** "That last move is the contrarian one," "This is the interesting part."
- **Persuasive-authority tropes.** "The real question is," "At its core," "Fundamentally," "The truth is."
- **Bold lead-ins on bullets and table rows.** "**Performance:** Performance improved..." and bold feature names used as list openers, with the bold repeating the sentence that follows.
- **Em-dash substitutes.** Spaced hyphens (" - ") and double hyphens (" -- ") used as dashes, and em dashes in headings and list items.
- **Rule-of-three rhythm.** Ornamental triads ("clear, concise, and actionable") where the content has two items or one.
- **Stacked hedges.** "could potentially," "may eventually," "might ultimately."
- **Balanced concessions.** "While X is impressive, Y remains a challenge," with nothing specific on either side.
- **Empty closers.** "Overall," "In short," "Bottom line," "The future looks bright," and generic future-narrative endings.
- **Openers that restate the prompt.** "You're asking about...", "Great question," "Let me break this down," "Here's a breakdown."
- **Chat artifacts.** "I hope this helps," "Let me know if you need anything else," "I'd be happy to," "Certainly!"
- **Uniform structure.** Every section the same length, a heading and bullets in every section, and summaries that repeat the body.
- **Fabricated specifics.** Numbers, names, dates or benchmarks that the source does not supply. The most dangerous tell, because it reads as confident.

## Human-writing counterweights

Do not flatten these out in the name of cleanup. They are signs of a real writer:

- Deliberate fragments, and sentences that start with "And" or "But".
- First-person stances, reactions and preferences the author actually holds.
- Idiosyncratic word choices, and irregular paragraph and sentence lengths.
- Specific, checkable detail (a version number, a file path, a measured value).

## Auditing a document

1. Scan for the word lists first, then the dash substitutes, bold lead-ins and "worth" frames.
2. Check each cluster in context. A technical term such as "robust estimator" is not a tell.
3. Ask whether the source actually supports each sentence. Remove or flag claims the source does not support rather than making them more specific.
4. Keep the author's own voice and any reaction they really had.

## Docs audit: shoggoth/docs.md, README.md and docs/

Scope: the in-app guide (`shoggoth/docs.md`), the repository README, and `docs/*.md`. The Help text and FAQ inside `shoggoth/index.html` were not audited in this pass.

### Changes made

- **Opencode recommendation removed** from `README.md` (the "Use your models in other apps" row), `shoggoth/index.html` (the FAQ answer, the Help and Server page copy, and the default Server tab), and `shoggoth/recommendations/tools.yaml` (the opencode connection entry, with its config snippet). Hermes remains.
- **Em-dash substitutes** replaced in `shoggoth/docs.md` (two spaced hyphens, one in the opening paragraph and one in the DuckDuckGo paragraph).
- **"Worth" frame** rewritten in `shoggoth/docs.md`: "it's always worth maintaining a copy" became "keep a copy".
- **Typo** fixed: "it it runs".
- **Trailing spaces** removed from two lines in `shoggoth/docs.md`.
- **Synced with the README** in `shoggoth/docs.md`: the three-minute quit on closing the tab (unless a job is working), and a troubleshooting entry for the AppImage start-up requirements and the `--appimage-extract-and-run` fallback.

### Flagged, not changed

- **README.md, feature table.** Each row opens with a bold lead-in and the table has an empty header row (`| | |`). The lead-ins are a deliberate design choice in a project README, so this is for the author to decide. "No cloud account, no technical know-how, and nothing you type leaves your machine" is a negation chain.
- **Repeated sentence.** "Everything stays on your computer" and "The one exception is web search" appear in the README, `docs/PRIVACY.md` and `shoggoth/docs.md`. Each appearance is accurate; the repetition is the tell.
- **Bold repeats.** `shoggoth/docs.md` bolds many UI labels, which is correct for button names. Bold is used for a few plain-prose terms too, such as "**Ask first** (the usual)". Worth a second look.
- **First person.** `shoggoth/docs.md` has "the models I have tried and recommend" (the Models section). This is the author's stance and is left as it is.
- **Deliberate voice.** "a more unsettling way" in the chat section and "why what we're doing is insane" in the README are voice, not tells. Keep them unless the author wants them out.
- **Not audited in this pass.** The files under `docs/plans/` and `docs/design/`, which are working notes.

### In-app copy: shoggoth/index.html

The visible copy was extracted from the page and its scripts and read in full. It is mostly plain and direct, and the word-list tells are almost absent.

**Changes made**
- Empty chat state: "Your chats stay on this computer and never go online" became "Your chats stay on this computer." Web search can send the search words online, so "never go online" was inaccurate.
- Help hub: dropped "Nothing here needs any technical knowledge." It repeats the Guide card, which already says "No technical knowledge needed."

**Flagged, not changed**
- "Changes apply the next time a model loads" appears three times, and "under the bonnet" appears in the Help copy, Advanced copy and the docs. Repetition, not errors.
- The FAQ says in the first person that Shoggoth explains recommendations "where I explain each recommendation." This is the author's voice, the same as in the guide, so it was left for the author to decide.
- The benchmark note ends "That is the best way to find out what works for you." It is a superlative closer, but the claim is reasonable. Left as written.
- The FAQ says conversations "are never sent anywhere", and the next sentence explains the web-search exception. Accurate enough; left.
- The one em dash in the file (`index.html` near the "Extra args" row) is a placeholder for an empty value, not prose.
- **Test fixture.** `tests/ui/api.json` still includes the opencode entry. It is a captured fixture, so it was not edited.
