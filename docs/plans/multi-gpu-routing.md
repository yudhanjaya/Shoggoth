# Multi-GPU placement and routing plan

> **Superseded** by [three-engine-rework.md](three-engine-rework.md), which keeps per-GPU assignment, the dispatcher idea and the decision layer, and covers Maya and engine control. Kept for the original investigation.

Covers issue #40 (pick-and-place, one port per model) and the decision-model idea in #18. Goal: a small fast model on one GPU, a large slow model on another, and a decision layer that sends each request to the right one (compaction and summaries to the small model).

## What the code does today

- **The provider is the unit of placement, not the GPU.** `router.resolve(ref)` maps a model name to one provider; `router.activate` starts it after `make_room` stops anything in the same `memory.group`. Nothing in the router knows which device a model sits on.
- **Lemonade is a singleton.** `engine.py` keeps one `_state` and `lem.py` one `_t` (host, port, key). The port range `13307-13326` is already searched, but only one `lemond` is ever started. GPU choice is the global `llamacpp.device` setting, so every model lands on the same card.
- **`make_room` is group-wide.** `lemonade` is `gpu, exclusive: false, max_loaded: 1`; `strata` and the `llama-server` example are `exclusive`. Two models on two cards is not expressible.
- **Strata takes the machine.** It is large-tier and exclusive. Issue #40 already says it can't take part, so it stays out of v1.
- **One choke point for traffic.** The chat page, Pi (via `/api/v1`) and external clients (gateway :13308) all pass through `gateway._route` / `relay`. That is where a decision layer belongs. Pi's compaction goes through it too, as an ordinary chat request to the chat's model; the proxy sees only the request body.
- **Per-device data exists.** `hardware.py` already reports llama.cpp device indices bound to live NVIDIA/AMD telemetry, which the placement UI and fit checks can reuse.

## Design

### 1. Placements

A placement binds a model to a set of GPUs and gives it a role:

```
placements: [
  {id: "fast", role: "fast", model: "<small>", gpus: [1], ctx: 32768},
  {id: "deep", role: "deep", model: "<large>", gpus: [0], ctx: 131072}
]
```

Each placement is served by its own engine **instance** on its own port. That answers #40's "each model exposes its own port" and keeps the two cards from interfering. Stored in Shoggoth's settings; GPU indices use llama.cpp's numbering (`hardware.list_devices`), not `nvidia-smi`'s.

### 2. Instances (the engine change)

Three ways to pin a model to a card, in order of preference. A one-day spike decides which (see Phase 0); none of this has been tried against Lemonade yet.

1. **One `lemond` per placement**, each with its own port, API key, data/log folder and device pinning (`llamacpp.device` set per instance, or `CUDA_VISIBLE_DEVICES` / `HIP_VISIBLE_DEVICES` in the child environment). Keeps Lemonade's downloads, recipes and per-model options. Needs `engine.py` and `lem.py` to take an instance id instead of using module-level state.
2. **Per-load device on one `lemond`**, through `llamacpp_args` or per-model options. Cheapest if Lemonade honours it, but `max_loaded: 1` and the global device setting suggest it won't.
3. **The `llama-server` provider** (already YAML-only, one port per model, takes extra args). Works today for pinning, loses Lemonade's model management. Good as the fallback and as the proof of concept.

The registry gains an *instance* notion: `lemonade@fast`, `lemonade@deep`, each with its own endpoint, `is_running`, `owned_instance`, idle clock and `stop`. `router.resolve` returns (provider, instance, model). The change touches `registry.py`, `provbase.py`, `router.py`, `engine.py`, `lem.py` and `providers/lemonade/provider.py`.

### 3. Per-device arbitration

`make_room` conflicts on **device overlap** instead of group membership: two placements conflict only if their GPU sets intersect. A model with no placement behaves exactly as today. Fit is checked against the target GPU's free memory (`best_gpu_gb` becomes per-device), with KV cache at the placement's `ctx`.

Startup order follows #18: **start the fast placement first**, then the deep one, so the small model is resident before a big load stresses the system. Idle stop and `stop_started` work per instance; the fast placement may opt out of idle stop.

### 4. The decision layer (dispatcher)

Lives in `gateway._route`, before `router.resolve`. The person (or Pi) asks for the virtual model `auto`; the dispatcher rewrites it to a placement. Naming a concrete model bypasses it, so nothing changes for anyone who doesn't opt in.

**Tier 1: rules (v1, no model call, deterministic).** First match wins:

| Signal | Goes to |
| --- | --- |
| Compaction / summarisation prompt (fingerprint of Pi's prompt, to be captured in the spike) | fast |
| Title / rename-chat / short-label requests | fast |
| No tools in the request, small `max_tokens`, short prompt | fast |
| Prompt would not fit the fast model's context | deep |
| Tool-calling, agent turns, long reasoning requests | deep |
| Images in the request | the placement whose model has vision |
| Anything unmatched | deep |

**Tier 2: a decision model (later, opt-in).** For the unmatched middle, ask the fast model for a one-token verdict (`fast` or `deep`) with a hard timeout (~1 s) and a constrained output. Any timeout, error or malformed answer means deep. This is the #18 idea, using the model that is already resident, so no extra load.

**Safety rules, always on:**
- Never block on a cold load for the fast path: if the fast placement isn't up, send to deep (and say so).
- If the fast model errors or truncates, retry once on deep.
- Every decision is recorded on the Server page ("→ fast: compaction"), so a wrong rule is visible and fixable.

### 5. Pi

Pi's `models.json` is regenerated at each start (`agent.py`), so it gets one extra model, `auto`, which the dispatcher resolves. Whether Pi lets compaction use a *different* model than the chat is unknown; if it does, that is simpler and exact, and the rule for compaction becomes a fallback. Checked in the spike.

### 6. UI

Advanced > Placement: one card per GPU (name, memory bar, live use, from `hardware.py`), a model picker per card with a fit warning, a role toggle (fast / deep), the port shown for each, and an on/off switch for `auto`. The Server page marks which placement answered.

## Phases

**Phase 0: spike (1 to 2 days).** Needs the two-GPU machine.
- Does a second `lemond` pinned to GPU 1 load and serve independently of one on GPU 0? Does per-load device work on a single `lemond`?
- Capture the real request bodies Pi sends for compaction and for chat titles (the Server page recorder already stores them) and decide the fingerprints.
- Does Pi accept a separate compaction model?
- Decide route 1, 2 or 3 above.

**Phase 1: instances and per-device arbitration.** Instance keying through registry, router and Lemonade provider; device-overlap `make_room`; tests with the fake OpenAI server (two instances, no overlap, overlap, one stopped without touching the other, restart reuse).

**Phase 2: placements and UI.** Settings schema, fit per GPU, ordered startup, Placement page, ports on the Engines page.

**Phase 3: rule dispatcher.** `auto` model, rules table, decision log, fallbacks, Pi wiring, tests on recorded request bodies.

**Phase 4: decision model and hardware pass.** Tier-2 classifier behind a switch; real two-GPU run (load order, VRAM headroom, failure of each card, compaction really lands on the small model). Keep "Experimental" until clean.

## Risks

- Lemonade may not allow per-instance devices cleanly; the fallback (llama-server provider) is real but less polished.
- Misrouting a hard request to the small model gives a worse answer silently. Mitigated by "unmatched means deep", the decision log, and the one-retry escalation.
- Compaction fingerprints depend on Pi's prompt text and can drift with Pi updates; add a contract test on a recorded body, as for Strata.
- AMD, Intel and mixed-vendor numbering is untested (see ARCHITECTURE.md); placement indices must come from llama.cpp's device list.
- Unified-memory and single-GPU machines must see no change: with no placements the code path is today's.

## Decisions for the maintainer

- Roles: fixed `fast` / `deep`, or arbitrary named placements with user-defined rules? (Suggested: fixed two roles for v1.)
- Should `auto` be the default chat model once placements exist, or opt-in per chat?
- Is Strata ever a `deep` placement (it would need GPU roles from Phase 0 of the Strata plan), or does it stay mutually exclusive with placements?
