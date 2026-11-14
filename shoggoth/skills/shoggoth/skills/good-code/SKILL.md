---
name: good-code
description: Use when writing, changing or fixing code in the user's folder. Keeps changes small, safe and checked. Based on the rules in Ponytail (MIT).
---

# Good code

1. Find the scope first. Read the task and the code it touches. List every place the change must reach: callers, tests, config.
2. Take the first option that works: it does not need to exist; the codebase already has it; the standard library has it; an installed library has it; one clear line; otherwise the least code that works.
3. Add nothing nobody asked for: no extra options, wrappers or "for later" code. Follow the layout and style around it.
4. Fixing a bug: search for every caller of the function first, then fix the cause once, in the shared code.
5. Never cut corners on checking input from outside, saving data safely, security, or what the user asked for.
6. New logic with a branch, loop, parser or file write gets one small test or check that fails if it breaks.
7. Comment only the why, in one line. Do not describe what the code does.
8. Finish by saying what you did not do or did not check, in one or two lines.
9. If another skill sets the order of work (test first, debug first), follow that order. This one only limits size and scope.

Example: asked to fix a wrong total, search for who calls `total()`, fix it there once, add one test with the failing numbers, and say "not checked: the CSV export".
