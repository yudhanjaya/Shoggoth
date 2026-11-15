// U8: the copy pass. Every user-facing string in the page follows docs/plans/ai-tells.md and the plan's rule 9:
// plain words, no exclamation marks, no dash substitutes, no fit vocabulary that U2 replaced. The named fixes from
// the pass (the Help answer, the backend help, the motto, the quit timing) are pinned so a regression fails by name.
// Run: node tests/ui/copy.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../../shoggoth/index.html", import.meta.url), "utf8");
const start = html.indexOf('<div class="app">');
assert.ok(start > 0, "the app body was not found");
const body = html.slice(start);

// ---- every visible string, in the markup and in the template literals the page builds
const strings = [];

// markup text between tags, up to the inline script, skipping svg path data, css and single identifiers
const markup = body.slice(0, body.indexOf("<script"));
for (const m of markup.matchAll(/>([^<>{}]+)</g)) {
  const t = m[1].trim();
  if (t && /\s/.test(t) && !/[;{}()]/.test(t)) strings.push(t);
}
// Template literals hold most of the copy. Take only the plain sentences inside them: they start with a capital,
// read as words and spaces, and carry no code punctuation or interpolation, so `if(!b) return;`,
// `!gb ? "medium" : gb` and `el.value.trim()` are not mistaken for prose.
const quote = /(["`'])((?:[^\\"'`\n]|\\.){20,})\1/g;
for (const m of body.matchAll(quote)) {
  const t = m[2];
  if (!/^[A-Z]/.test(t)) continue;
  if (/\$\{|=|=>|[{}]|;|\?|!|<[a-z\/]|\.\w+\(|\bconst |\breturn |\bvar |\blet /.test(t)) continue;
  if (!/^[A-Za-z0-9 ,.:;'"“”‘’()…%/–-]+$/.test(t)) continue;
  if (!/[A-Z][a-z]+ [a-z]+ [a-z]+/.test(t)) continue;
  strings.push(t);
}

// ---- the word lists from ai-tells.md, minus the words that are technical terms here
const BANNED = /\b(seamless|effortless|powerful|delve|tapestry|testament to|realm|embark|underscore[sd]?|pivotal|meticulous|game-chang\w*|nestled|vibrant|bustling|intricate|ever-evolving|holistic|actionable|impactful|synergy|load-bearing|leverage|streamline|empower\w*|foster|elevate|unleash|crucial|nuanced|multifaceted|comprehensive|facilitate|underpin|cornerstone|paramount|transformative|quintessential|overarching|genuinely|it's worth noting|here's the thing|at its core|fundamentally|the truth is|in short[,.]|bottom line|under the bonnet)\b/i;

// ---- the fit words U2 settled on. The old comfortable/snug vocabulary must not creep back.
// "Comfortable reading speed" is a speed label, not a fit badge, so the badge words are what is checked.
const STALE_FIT = /\b(probably too big|fits this computer|won't fit on this computer|runs comfortably|a snug fit)\b/i;

const failures = [];
for (const s of strings) {
  if (BANNED.test(s)) failures.push("AI tell: " + s);
  if (STALE_FIT.test(s)) failures.push("old fit word: " + s);
  if (/!/.test(s.replace(/\$\{[^}]*\}/g, ""))) failures.push("exclamation mark: " + s);
  // an em dash used as punctuation, or the spaced hyphen and double hyphen substitutes
  if (/\u2014/.test(s) || / -- /.test(s) || /\s+-\s+[a-z]/.test(s)) failures.push("dash substitute: " + s);
}

// ---- one named fix from the pass, so a regression shows up as a named failure rather than a diff
assert.match(html, /Open <b>Models<\/b> and press <b>Get it<\/b>/, "the Help answer still reads 'Open the Models and press'");
assert.ok(!/Open the <b>Models<\/b>/.test(html), "the Help answer lost its noun");
assert.match(html, /bits\.push\(esc\(m\.why \|\| "Too big for this computer"\)\)/, "the chat model picker went back to the old fit words");
assert.ok(!/bits\.push\(esc\(m\.why \|\| "Won't fit on this computer"\)\)/.test(html), "the chat model picker has the old fit words again");
assert.match(html, /Right for almost everyone\./, "the backend help lost its shortened line");
assert.ok(!/This is the right answer for almost everyone/.test(html), "the backend help still says 'right answer'");

// ---- the motto lives in the header rail, and it is the agreed sentence (master plan 8.3, 10.1)
assert.match(body, /<p class="railmotto">Heavy on the cosmos\. Light on your machine\.<\/p>/, "the motto is not in the rail");

// ---- where the rail shrinks to icons, the motto and the note go with the rest of the words, or they overrun 84 px
const css = html.slice(html.indexOf("<style>"), html.indexOf("</style>"));
assert.match(css, /@media \(max-width:1100px\)\{[^@]*\.railmotto\{display:none\}/, "the motto is not hidden where the rail shrinks to icons");
assert.match(css, /@media \(max-width:640px\)\{[^@]*\.railmotto,/, "the motto is not hidden in the phone-width rail");

// ---- the rail note must not promise more than the app does now that paid services exist
assert.ok(!/Everything runs on this computer\./.test(body), "the rail still claims everything runs here, which hosted models contradict");
assert.ok(!/hintdev/.test(html), "the composer still has the old 'Private: everything runs on this computer' slot");
assert.ok(!/Everything runs on this computer, so your chats stay private/.test(body), "the Getting ready screen still gives the blanket privacy promise");

// ---- Docs: the heading U8 renamed, since the page no longer says it
const docs = readFileSync(new URL("../../shoggoth/docs.md", import.meta.url), "utf8");
assert.ok(!/Under the bonnet/.test(docs), "the guide still has an 'Under the bonnet' heading");
assert.match(docs, /^## Advanced, page by page$/m, "the renamed guide heading is missing");

// ---- README: closing the tab quits within about two seconds (rule 8.3), and privacy names both exceptions
const readme = readFileSync(new URL("../../README.md", import.meta.url), "utf8");
assert.match(readme, /quits about two seconds later/, "the README quit timing is not about two seconds");
assert.ok(!/three minutes/.test(readme), "the README still says three minutes");
assert.match(readme, /web search.*paid hosted model/, "the README privacy row does not name both ways data leaves");

// ---- the blanket "everything stays here" claim is false now that paid services exist, in the guide and the README
assert.ok(!/^Everything stays on your computer\.$/m.test(docs), "the guide still opens its privacy section with 'Everything stays on your computer.'");
assert.ok(!/^Everything stays on your computer\./.test(readme), "the README still opens its privacy section with 'Everything stays on your computer.'");
assert.match(docs, /only web search and a chat on a paid hosted model send anything out/, "the guide does not say what can leave");
assert.match(docs, /search the web when you allow it, and talk to a hosted service you set up/, "the guide's list of what it goes online for is missing search and hosted services");

assert.deepEqual(failures, [], failures.join("\n"));
console.log("ok: " + strings.length + " user-facing strings, the Help fix, the fit words, the motto and the quit timing");
