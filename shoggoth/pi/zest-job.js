// Gives the model a propose_job tool. It does not start anything: it hands the page a filled-in "Start a job" form, and the person
// reads it and presses Start themselves. Only offered in chats, never inside a job (SHOGGOTH_JOBS is "1" only there).
export default function (pi) {
  if (process.env.SHOGGOTH_JOBS !== "1") return;
  const clamp = (n, lo, hi, dflt) => { n = Math.round(Number(n)); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt; };
  pi.registerTool({
    name: "propose_job",
    label: "Fill in the job form",
    description: "Open the Start a job form for the person, filled in with what they asked for. A job is bigger work that runs in the background, round after round, until a check passes. This does not start the job: the person reviews the form and presses Start. Use it after you know what done looks like.",
    promptSnippet: "propose_job: open a filled-in Start a job form for the person to review",
    promptGuidelines: [
      "Use propose_job for work that takes many rounds and has a clear finish, such as making tests pass or getting a build to work. Do not use it for questions or small edits.",
      "Fill in `check` with a command that exits 0 when the work is done (for example `python3 -m pytest -q`). Without one, describe what done looks like in `done_when`.",
    ],
    parameters: {
      type: "object",
      properties: {
        goal: { type: "string", description: "What the job should get done, in the person's words, with enough detail to work from" },
        check: { type: "string", description: "A shell command that succeeds when the work is done, such as `python3 -m pytest -q`. Leave out if there is none." },
        done_when: { type: "string", description: "When there is no command: a sentence saying what done looks like" },
        rounds: { type: "number", description: "The most rounds to try (1 to 100, default 10)" },
        work_on_copy: { type: "boolean", description: "true (default) to work on a copy of the folder; false to change the folder itself" },
      },
      required: ["goal"],
    },
    async execute(_id, params) {
      const p = params || {};
      const goal = String(p.goal || "").trim();
      if (!goal) throw new Error("Give propose_job a goal: what the job should get done.");
      const check = String(p.check || "").trim();
      const job = {
        goal,
        check: check || String(p.done_when || "").trim(),
        check_kind: check ? "command" : "words",
        rounds: clamp(p.rounds, 1, 100, 10),
        copy: p.work_on_copy !== false,
      };
      return {
        content: [{ type: "text", text: "The Start a job form is now open in front of the person with these details filled in. They will read it and press Start themselves. In one sentence, tell them what you filled in and that they can change it. Do not call propose_job again unless they ask you to change something." }],
        details: { job },
      };
    },
  });
}
