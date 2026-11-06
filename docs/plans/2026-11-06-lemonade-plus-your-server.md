# Lemonade, plus a server you wire in: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shoggoth ships one engine, Lemonade. Advanced lets a person connect a server they run themselves (Maya, Strata, llama.cpp, Unsloth, LM Studio, Ollama). Shoggoth ships no Strata or Maya setup, install, update, tuning or launch code.

**Architecture:** Start from `main`, not from PR 42. PR 42 (three-engine control, authority, GPU assignment, routing flows, terminal mode) is dropped whole. `main` already has the YAML engine layer, and `run.mode: external` there means "an address, Shoggoth only talks to it". A connected server is one saved entry (preset, address, key) that the registry turns into an external-mode engine. Strata is then deleted from every layer.

**Tech Stack:** Python 3 standard library plus the vendored YAML, one `index.html`, `unittest`, jsdom smoke test (`tests/ui/smoke.js`).

**Spec:** The user's request of 2026-11-06 (no separate document): "default shoggoth runs lemonade. Advanced Mode lets you wire in a server — Maya, Strata, pure llama.cpp, unsloth, LMStudio, Ollama, are going to be popularly supported options. That's it. We don't ship Maya or Strata setup."

## Decisions taken (change any of these before work starts)

1. **Connect only.** Shoggoth never installs, starts, stops, updates, tunes or frees memory for a connected server. The person runs it; Shoggoth uses it and never signals it (this is what `main`'s ownership rule already does for "yours"). The only engine Shoggoth manages is Lemonade.
2. **"Advanced Mode" is the existing Advanced section** (Advanced > Engines). No Expert Mode switch is added; `expert.py` and the rest of PR 42 are not carried over. If a switch is wanted, it is a later 17-line change, not part of this plan.
3. **Presets are labels and default addresses, not support.** Each preset gives a name, a default address and a one-line hint. Any OpenAI-style server works with a typed address.
4. **Existing Strata users keep working.** Their saved Strata addresses and keys become connected servers with id `strata`, so chats that name `strata/<model>` still resolve. Their models folder and Strata install are never touched.
5. **PR 42 is closed, not merged, by you.** The remote branch `advanced-user-mode` stays as the archive. Nothing here pushes or closes anything.

Unverified, check before shipping the preset table: the default ports of **Maya** and **Unsloth** (Studio). The others are the documented defaults: Ollama 11434, LM Studio 1234, llama.cpp `llama-server` 8080, Strata 8080.

## Global Constraints

- With nothing connected, behaviour is exactly Lemonade alone. No page, route or setting mentions an engine the person did not connect.
- A connected server's key is never returned by any `/ui/*` route (masked to its last four characters) and never written to a log or the activity list.
- Addresses must be `http://` or `https://`. No user-info (`user:pass@`), no other scheme.
- An unreachable connected server never delays startup or blocks chat with other engines (probe timeout 1.5 s, as `provbase.http_json` already uses).
- Every task ends with `python3 -m unittest discover tests` and `node tests/ui/smoke.js` passing, and is its own commit with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Address typed with a trailing `/v1`, a trailing slash, or `localhost` vs `127.0.0.1`: stored once, normalised the same way, so two spellings are not two servers.
2. A server that answers 401/403: the card says it needs a key, not "unreachable".
3. A server that lists zero models (Ollama with nothing pulled, LM Studio with none loaded): connected, shown as such, not an error.
4. Two connected servers whose model names collide (both have `llama3`): each is reached as `<server id>/<model>`, never ambiguous.
5. A saved server whose id collides with `lemonade` or a folder engine: refused with a message that says which id.
6. Migration run twice, or with a half-written `strata` settings block: the second run changes nothing, and no key is lost.

## File Structure

- Create `shoggoth/servers.py`: saved connected servers, presets, address checks, the probe. One responsibility: what the person connected.
- Create `shoggoth/servers/presets.yaml`: the six presets (data, not code).
- Modify `shoggoth/registry.py`: also build one external-mode provider per saved server.
- Modify `shoggoth/server.py`, `shoggoth/index.html`: `/ui/servers` routes and the Advanced > Engines "Connect a server" card; later, delete the Strata routes and UI.
- Delete `shoggoth/strata.py`, `shoggoth/providers/strata/`, `tests/test_strata_presets.py`, `tests/fixtures/real/strata_*`.
- Modify (remove Strata): `tuning.py`, `zestup.py`, `recs.py`, `recommendations/models.yaml`, `recommendations/tools.yaml`, `library.py`, `modelmeta.py`, `store.py`, `agent.py`, `docs/ENGINES.md`, `docs/ARCHITECTURE.md`, `shoggoth/docs.md`, `README.md`, and the Strata cases in `tests/test_ownership.py`, `test_contracts.py`, `test_headroom.py`, `test_recommendations.py`, `test_tuning_for.py`, `test_library.py`, `test_creatures.py`, `test_providers.py`, `test_store.py`, `test_router.py`, `test_rename.py`, `test_chat_loading.py`, `tests/ui/api.json`.

---

### Task 1: Connected servers (data, presets, probe)

**Files:**
- Create: `shoggoth/servers.py`, `shoggoth/servers/presets.yaml`
- Modify: `shoggoth/store.py` (DEFAULTS gains `"servers": []`)
- Test: `tests/test_servers.py`

**Interfaces:**
- Produces:
  - `servers.presets() -> list[dict]` each `{id, name, url, hint}` from `presets.yaml`.
  - `servers.normalise(url: str) -> str` returns `scheme://host:port` with no trailing `/` or `/v1`; raises `ValueError(plain-words message)` for a bad scheme, user-info, or empty host.
  - `servers.add(preset: str, url: str, key: str = "", name: str = "") -> dict` saves `{id, preset, name, url, key}` and returns it; `id` is the preset id (`ollama`, `ollama-2`, …) and never `lemonade`.
  - `servers.remove(sid: str) -> None`, `servers.all() -> list[dict]` (keys included, internal use), `servers.public() -> list[dict]` (key replaced by `key_hint`, last four characters or `""`).
  - `servers.probe(url: str, key: str = "") -> {"reachable": bool, "needs_key": bool, "models": list[str]}`; never raises, 1.5 s timeout.

- [ ] **Step 1: Write the failing tests** in `tests/test_servers.py` (temp `SHOGGOTH_CONFIG_DIR`, as `tests/test_store.py` does):
  - `test_normalise_spellings_are_one_address`: `normalise("http://localhost:11434/v1/")` equals `normalise("http://localhost:11434")` equals `"http://localhost:11434"`.
  - `test_normalise_refuses`: `ValueError` for `"file:///etc/passwd"`, `"ftp://x"`, `"http://u:p@host"`, `""`.
  - `test_add_twice_gives_distinct_ids`: two `add("ollama", …)` give `ollama` and `ollama-2`.
  - `test_same_address_twice_is_refused`: second `add` of the same normalised address raises `ValueError`.
  - `test_public_never_returns_the_key`: after `add("strata", "http://h:1", key="sk-abcd1234")`, `json.dumps(servers.public())` does not contain `sk-abcd1234` and has `key_hint == "1234"`.
  - `test_probe_reads_models_and_401`: a `ThreadingHTTPServer` fixture answering `/v1/models` with `{"data":[{"id":"m1"}]}` gives `models == ["m1"]`; one answering 401 gives `needs_key is True, reachable is True`; a closed port gives `reachable is False`.
  - `test_presets_have_no_launch_fields`: every preset's keys are a subset of `{id, name, url, hint}` (this is what keeps Maya and Strata setup out).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_servers -v`. Expected: ImportError.
- [ ] **Step 3: Implement** `servers.py` over `store.load()/store.update()` and `provbase.http_json`. Use `urllib.parse.urlsplit` for `normalise`. Write `presets.yaml` with six entries: ollama 11434, lmstudio 1234, llama-server 8080, strata 8080, maya and unsloth (ports per the "Unverified" note above, hint says "Check the address your server prints on start").
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `Connected servers: saved addresses, presets and a probe`.

### Task 2: The registry serves connected servers

**Files:**
- Modify: `shoggoth/registry.py` (`Registry.load` appends servers after folder engines)
- Test: `tests/test_providers.py` (new class `ConnectedServers`)

**Interfaces:**
- Consumes: `servers.all()`.
- Produces: each saved server is a `provbase.GenericProvider` with spec `{id, name, api_version: 1, run: {mode: external}, endpoint: {base_url: "<url>/v1", models: {path: /v1/models, id: "$.data[*].id"}}, memory: {group: "none"}}`, `trusted=True`, `approved=True` (no program runs). Its key is read from `ctx.settings(id)["key"]`, so `servers.add` must also `remember` it there.

- [ ] **Step 1: Write the failing tests**:
  - `test_a_saved_server_becomes_an_engine`: after `servers.add("ollama", fixture_url)`, `Registry().load()` includes id `ollama`, `mode == "external"`, and its `models()` lists the fixture's model ids.
  - `test_models_are_named_by_server`: two servers with the same model id `m1` resolve as `ollama/m1` and `ollama-2/m1`.
  - `test_a_connected_server_is_never_stopped_or_made_room_for`: activating its model calls no `stop` on Lemonade and `make_room` does nothing (`memory.group "none"`); `stop()` on it returns `(False, …)` and sends no signal.
  - `test_id_collision_with_a_folder_engine_is_reported`: a saved server with id `lemonade` is skipped and appears in `Registry.problems` with the id in the message.
  - `test_unreachable_server_does_not_slow_load`: with a closed port saved, `Registry().load()` returns in under 1 s.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_providers.ConnectedServers -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the append in `Registry.load`, building the spec in memory (no folder, no `provider.py`). Do not probe at load; models are read lazily as for any engine.
- [ ] **Step 4: Run** the class, then the full suite and the smoke test. Expected: PASS.
- [ ] **Step 5: Commit** `The registry turns each connected server into an address-only engine`.

### Task 3: Advanced > Engines, "Connect a server"

**Files:**
- Modify: `shoggoth/server.py` (routes), `shoggoth/index.html` (the Engines view), `tests/ui/api.json`
- Test: `tests/test_servers.py` (route class), `tests/ui/smoke.js` replays the new entries

**Interfaces:**
- Produces: `GET /ui/servers -> {"presets": [...], "servers": servers.public() + reachability}`; `POST /ui/servers {action: "add", preset, url, key?, name?}` → the saved server or `400 {error}` in plain words; `{action: "remove", id}`; `{action: "test", url, key?}` → `servers.probe`. All behind the existing UI token.

- [ ] **Step 1: Write the failing tests**: with a real `server.Handler` thread (pattern of `tests/test_expert.py` in PR 42's branch, or `tests/test_auth.py` on `main`): `test_add_then_list_then_remove`; `test_bad_address_is_a_400_with_words` (`file:///x` returns 400 and a message that does not contain a traceback); `test_the_key_is_not_in_any_response` (the add and list bodies do not contain the key); `test_requires_the_ui_token` (401 without it).
- [ ] **Step 2: Run** to confirm failure.
- [ ] **Step 3: Implement** the routes next to the other `/ui/providers/*` entries, and the card: a preset dropdown that fills the address, an address field, an optional key field, a **Test** button that shows reachable / needs a key / models found, **Connect**, and per-server **Remove**. Copy follows the existing Advanced style; the line under the card says "Shoggoth uses this server but does not start, stop or update it." Add `/ui/servers` entries (one server, one empty) to `tests/ui/api.json`.
- [ ] **Step 4: Run** the route tests, full suite, and `node tests/ui/smoke.js`. Expected: PASS, no script errors. Then open the Engines view in the browser pane once to look at it.
- [ ] **Step 5: Commit** `Advanced > Engines: connect a server of your own`.

### Task 4: Existing Strata users keep their connection

**Files:**
- Modify: `shoggoth/servers.py` (`migrate_strata()`), called once from `server.main()` after `store.load()`
- Test: `tests/test_servers.py`

**Interfaces:**
- Consumes: the old settings block `strata: {remote: [{url}], keys: {url: key}, known, owned, idle_minutes}`.
- Produces: `servers.migrate_strata() -> int` (servers added). Each old `remote` address becomes a server with id `strata` (then `strata-2`, …) and its key; adds a `migrated: "strata"` marker so a second call returns 0. Leaves the old block in place (a later release may drop it).

- [ ] **Step 1: Write the failing tests**: `test_remote_addresses_and_keys_carry_over`; `test_a_second_run_changes_nothing` (settings file bytes equal); `test_half_written_block_is_skipped_not_fatal` (`remote: [{}]`, `keys: null`); `test_the_first_one_keeps_the_id_strata` (so `strata/<model>` in an old chat still resolves).
- [ ] **Step 2: Run** to confirm failure.
- [ ] **Step 3: Implement** with `store.update` so it is one atomic write.
- [ ] **Step 4: Run** the tests and the full suite.
- [ ] **Step 5: Commit** `Carry saved Strata addresses over as connected servers`.

### Task 5: Remove Strata from the server and the page

**Files:**
- Delete: `shoggoth/strata.py`, `shoggoth/providers/strata/`
- Modify: `shoggoth/server.py` (the `/ui/strata*` routes and handlers, `_strata_make_room`, the `strata.on_change` and `before_launch` hooks, the `engine not in ("strata", "lemonade")` check), `shoggoth/index.html` (Strata card and form, "Make it faster" for Strata, Strata rows in Models and Hardware, `STR` state), `shoggoth/zestup.py` (Strata update lines and the `"strata"` name in the update-all list), `tests/ui/api.json`
- Test: `tests/test_providers.py`, `tests/test_ownership.py`, `tests/test_contracts.py`

**Interfaces:**
- Consumes: Tasks 2–4 (connected `strata` servers still appear as ordinary engines).
- Produces: `grep -rniE "strata" shoggoth/server.py shoggoth/index.html shoggoth/zestup.py` matches only the preset name and the migration; the bundled engines are exactly `{"lemonade"}`.

- [ ] **Step 1: Rewrite the tests that name Strata** rather than deleting coverage: `test_bundled_providers_load` asserts the bundled ids equal `{"lemonade"}`; `test_strata_reports_its_update_in_the_common_shape` is removed; in `test_ownership.py` the cases that proved "an engine started elsewhere is never stopped" are re-pointed at the connected-server provider from Task 2, keeping each assertion.
- [ ] **Step 2: Run** the suite. Expected: the edited tests FAIL (Strata still bundled).
- [ ] **Step 3: Remove** the code listed above. Delete `STR`-related functions in `index.html` and re-record or hand-trim `tests/ui/api.json` so the smoke test no longer replays `/ui/strata*`.
- [ ] **Step 4: Run** full suite and `node tests/ui/smoke.js`. Expected: PASS. Start the app (`python3 shoggoth/server.py`), open Chat, Models, Advanced > Engines, Hardware, and confirm no dead Strata control and no console errors.
- [ ] **Step 5: Commit** `Remove Strata: the bundled engine, its routes and its page`.

### Task 6: Remove Strata from the data layers

**Files:**
- Modify: `shoggoth/tuning.py` (`_strata_presets_for`, the `strata/` branch, the `"source": "strata presets"` record), `shoggoth/recs.py` (`strata` key in `models()`, `strata_catalog`, the `engine: strata` rule, counts line), `shoggoth/recommendations/models.yaml` (the `strata:` section and every `engine: strata` entry), `shoggoth/recommendations/tools.yaml` (4 mentions), `shoggoth/library.py` (`SUBDIRS` becomes `("lemonade",)`; the Strata projector comment), `shoggoth/modelmeta.py` (`DEFAULT_HEADROOM_GB` becomes `{}`-free: Lemonade 0.0 only), `shoggoth/store.py` (drop `strata` from DEFAULTS; migration in Task 4 reads the raw saved file instead), `shoggoth/agent.py` (the `pi_url` comment and any Strata-only branch; keep `pi_url` itself, connected servers use it), `shoggoth/hardware.py` if it names Strata
- Delete: `tests/test_strata_presets.py`, `tests/fixtures/real/strata_*`
- Test: `tests/test_recommendations.py`, `test_tuning_for.py`, `test_headroom.py`, `test_library.py`, `test_store.py`, `test_creatures.py`, `test_router.py`, `test_rename.py`, `test_chat_loading.py`

**Interfaces:**
- Produces: `recs.models()` returns `{recommended, first_run, coming_soon}` (no `strata`); `tuning.status(model)` has only the Lemonade path.

- [ ] **Step 1: Edit the tests first**: remove Strata-only cases, and change shared ones to Lemonade. Add `test_recommendations_carry_no_engine_but_lemonade`: every entry in `models.yaml` that names an `engine` names `lemonade`. Add `test_old_settings_with_a_strata_block_still_load` (a settings file containing the old block loads, and nothing reads it except `migrate_strata`).
- [ ] **Step 2: Run** the suite. Expected: FAIL where tests now expect no Strata.
- [ ] **Step 3: Remove** the code and data listed. Keep the user's `~/<models>/strata` folder out of `library` scanning only; do not delete it.
- [ ] **Step 4: Run** full suite and smoke test. Run `python3 shoggoth/recs.py` (its self-check, as `recs.py` line 431 prints counts) and confirm it reports no Strata count.
- [ ] **Step 5: Commit** `Remove Strata from tuning, recommendations, library and settings`.

### Task 7: Docs, and a guard against drift

**Files:**
- Modify: `docs/ENGINES.md`, `docs/ARCHITECTURE.md`, `shoggoth/docs.md`, `README.md`, `docs/plans/strata-integration.md` (move to `docs/plans/archive/`), `docs/plans/three-engine-rework.md` (add a top note: "Superseded by 2026-11-06-lemonade-plus-your-server.md; kept as history")
- Test: `tests/test_servers.py`

- [ ] **Step 1: Write the failing test** `test_shipped_code_has_no_strata_or_maya_setup`: reads every `.py`, `.yaml` and `.html` under `shoggoth/` and fails if any file other than `servers/presets.yaml` and `servers.py` (the migration) contains `strata launch`, `strata tune`, `install.sh`, or the word `maya` (case-insensitive).
- [ ] **Step 2: Run** it. Expected: PASS only if Tasks 5 and 6 were complete; if it fails, it names the file, so fix that file.
- [ ] **Step 3: Rewrite the docs**: ENGINES.md opens with "Shoggoth bundles Lemonade. Connect any other OpenAI-style server under Advanced > Engines", then the connect steps and the preset table (name, default address); the YAML-folder section stays, since `providers new llama-server` is still the way to have Shoggoth start a program. README: remove Strata as a product feature and the "very large models" claim; keep one sentence that a Strata or Maya server can be connected. State plainly that Shoggoth does not install or update them.
- [ ] **Step 4: Run** full suite and smoke test.
- [ ] **Step 5: Commit** `Docs: Lemonade is the engine; other servers are connected, not shipped`.

### Task 8: Check it on a real machine, then hand over

- [ ] **Step 1:** Fresh profile (`SHOGGOTH_CONFIG_DIR` set to an empty temp dir): Shoggoth starts, Lemonade is the only engine, nothing mentions another.
- [ ] **Step 2:** Connect a real Ollama or `llama-server` at its preset address: Test shows its models, a chat answers through it, and Lemonade is not unloaded.
- [ ] **Step 3:** Copy a settings file that has a saved Strata address and key: after start it appears as the connected server `strata`, and an old chat naming `strata/<model>` still opens.
- [ ] **Step 4:** Tell the owner what was checked on this machine and what was not (Maya and Unsloth are unchecked unless one is available).
- [ ] **Step 5:** Open a PR from `lemonade-plus-your-server` into `main` only when asked. Closing PR 42 is the owner's call.

---

## Self-review

- **Spec coverage:** default Lemonade (Tasks 5–6, 8.1); wire in a server with the six names (Tasks 1–3); no Maya or Strata setup (Tasks 5–7, guard test); roll back PR 42 (branch from `main`, Decision 5).
- **Not covered, on purpose:** PR 42's authority, GPU assignment, flows, terminal mode, Maya provider. They are not carried over. Anything the owner wants back from them is a separate plan.
- **Types:** `servers.add/public/probe/normalise/migrate_strata` are used with the same names in Tasks 2–4.
- **Risk:** Task 6 touches many files; each test edit keeps its assertion where the case still makes sense. The migration (Task 4) is the one place old Strata settings are read.
