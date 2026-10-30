---
name: start-a-job
description: Turn a bigger request into a background job. Use when the user wants something that takes many rounds with a clear finish, such as making tests pass, getting a build working, or carrying out a plan, rather than a question or a quick edit.
---

# Start a job

A job is bigger work that Shoggoth keeps doing in the background, round after round, until a check passes. The user starts it from a form. You fill the form in with `propose_job`; you never start it yourself.

## When it fits

- Many rounds of work with a clear finish: "make the importer tests pass", "get this to build", "carry out the plan above".
- Not for questions, explanations, or a small edit you can do right now. Just do those.

## Steps

1. Work out what done looks like. If the user already said (tests pass, the build works), do not ask again.
2. If you do not know, ask at most two short questions, one at a time: what should be true when it is finished, and is there a command that shows it.
3. When you have tools and a folder, look at the project (a Makefile, `package.json`, a tests folder) and suggest the real check command. In a plain chat with no folder, ask the user for it instead of guessing.
4. Call `propose_job` once with the goal in the user's words, the check command if there is one (otherwise `done_when`), and a sensible number of rounds.
5. Say in one sentence what you filled in, and that they can change it and press **Start job**.

## Rules

- Never claim a job has started. It starts only when the user presses the button.
- A job needs a project folder. If the chat has none, say so; the form asks the user to choose one.
- Do not call `propose_job` again unless the user asks for a change.
