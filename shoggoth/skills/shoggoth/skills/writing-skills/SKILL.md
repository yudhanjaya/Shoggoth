---
name: writing-skills
description: Write a new skill for the user to keep. Use when the user asks you to make a skill, or when you just finished a procedure that worked and is likely to come up again.
---

# Writing a skill

A skill is a short set of instructions saved for next time. You propose it with `save_skill`. The user always sees the whole skill and decides whether to keep it.

## When it fits

- The user asked for a skill, or you just finished a multi-step task that worked and will probably come up again.
- Not for one-off answers or facts. If the user said no once, do not offer again.

## Steps

1. Name it with a short slug: lowercase words joined by hyphens, like `release-notes`.
2. Write the description in one or two sentences that say **when** to use it. Start with the trigger: "Use when the user wants...".
3. Write the body as numbered steps, each one action. Keep it under 8 KB; shorter is better.
4. Add one small example.
5. Call `save_skill` with `name`, `description` and `body`. Use `files` only for long templates.
6. Tell the user in one sentence what you proposed. If they decline, drop it.

## Example

```
name: release-notes
description: Use when the user wants release notes written from recent git commits.
body:
1. Run `git log --oneline` since the last tag.
2. Group commits as Added, Fixed, Changed.
3. Write one plain sentence per item.

Example: "Fixed: the export no longer drops empty rows."
```
