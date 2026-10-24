---
name: markdown-writing
description: Write clear Markdown for chat replies and for files such as README, notes, docs, changelogs, meeting notes and plans. Use when the user asks you to write, draft, rewrite, tidy or structure a document or text, or when you are about to save a .md file.
---

# Markdown writing

Good writing first, Markdown second. The reader should find what they need in the first few lines.

## Before you write

1. Know who reads it and what they will do next (install it, decide something, continue the work). Put that first.
2. In a folder, look at nearby `.md` files and copy their tone, heading style and naming. Do not impose a new style on an existing project.
3. If something important is missing (the audience, the facts), ask one short question rather than inventing details. Never invent commands, versions, links or numbers.

## Shape

- One `#` title per file, then `##` sections, `###` only if needed. Never skip a level.
- Lead with a one or two sentence summary under the title.
- Short paragraphs: one idea each, usually 2 to 4 sentences.
- Use a numbered list for steps in order, a bulleted list for items without order, a table for things compared across the same attributes (three or more rows).
- Put every command, path, file name and setting in `code`. Put multi-line code in a fenced block with a language (`bash`, `python`, `json`, `text`).
- Link with `[words that say where it goes](https://example.com)`. Never write "click here". Use relative links between files in the same project.
- Bold only the few words that matter. Do not bold whole sentences or every list item's first words.

## Style

- Plain, specific, active. "Run `make test`", not "Tests can then optionally be run".
- Cut filler: "In order to", "It is important to note", "very", "really", "simply".
- Avoid stock phrases that sound generated ("delve", "tapestry", "game-changer", "seamless") and avoid ending with a summary that repeats the page.
- Keep tense and person consistent. Prefer "you" for instructions.
- Dates as `2026-10-24`. Numbers with units.

## Templates

README: title, one-sentence purpose, what it looks like or does, install, use (smallest working example), configuration, how to contribute or get help, license. Skip sections that have nothing true to say.

Changelog entry: `## 1.2.0 (2026-10-24)` then bullets grouped as Added, Changed, Fixed. One line each, starting with a verb.

Meeting notes: date and attendees, decisions (bullets), actions (`- [ ] Name: what, by when`), open questions.

How-to: what you will have at the end, what you need first, numbered steps each with the expected result, how to undo it or what to do if it fails.

## In Zest's chat

Zest shows headings, bold, italics, lists, tables, links, quotes and code blocks. Keep chat replies short and avoid deep nesting: one level of list is enough. Do not wrap a whole reply in a code block.

## When you save a file

Write it with the write tool, then read it back and check that headings, lists and fences are balanced (every opening fence has a closing one) and that every command you wrote is one you actually saw work or were given. Tell the user the path.
