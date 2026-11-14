# Wave 0 review

Scope: the Wave 0 work on branch `cc-wave0-review` (Shoggoth 5): `shoggoth/llamacpp.py`, `hf.py`, `pipkgs.py`, `pi/zest-skillsmith.js`, `scan.py`, `engines.py`, their tests, and the packaging that ships them. Read-only review; nothing was changed and no test suite was run. Line numbers are the current checkout.

No high-severity issue was found. Everything below is medium unless marked otherwise. Two things bound the threat model: the new modules are not wired into `server.py` yet (they are imported only by each other and the tests), and a local attacker who can write Shoggoth's data directory is outside most of what the sandbox can protect.

---

## 1. `shoggoth/llamacpp.py`

### L1. The router's API key is passed on the command line
**File:** `shoggoth/llamacpp.py:419-423`, spawned at `:449`
**Severity:** medium
**What:** `command()` puts `--api-key <key>` in `argv`. `/proc/<pid>/cmdline` is world-readable on a default Linux, so every local user can read the key for the life of the process (`ps aux` shows it too). `run.json` is chmod 0600, but that protection is undone by the argv.
**Failure:** another local account reads `--api-key` from `/proc`, then sends its own requests to `127.0.0.1:1334x/v1` without Shoggoth's knowledge, or uses the key to dump prompts through `/slots` (enabled by `--slots`).
**Fix:** write the key to a 0600 file and use `--api-key-file` (already listed as a known llama-server flag at `llamaargs.py:82`), or keep the key in the environment if the pinned build supports it. Remove it from `command()`.

### L2. The install-time smoke test does not use the environment the server needs
**File:** `shoggoth/llamacpp.py:247-255` (`version_of`) vs `:446-450` (`start`)
**Severity:** medium (partly unsure)
**What:** `start()` adds `LD_LIBRARY_PATH=<build dir>` before launching, but `install()` runs `version_of(exe)` with the caller's environment. The CUDA assets keep `libggml-cuda.so`, `libllama.so` and the separately downloaded `libcudart` in the build directory.
**Failure:** if the official binaries do not carry an `$ORIGIN` rpath, the CUDA build fails `--version`, `version_of` returns `None`, and `install()` refuses every CUDA install with "The new llama.cpp won't start on this computer." If they do carry one, the inconsistency is harmless. This needs one real install on the development machine to settle; the test should mirror `start()`'s env either way.
**Fix:** pass `env=dict(os.environ, LD_LIBRARY_PATH=tmp + ...)` to `version_of`, or extend `version_of` to take an env like `util.run` already does.

### L3. Adoption trusts `run.json` more than it can verify
**File:** `shoggoth/llamacpp.py:402-409`
**Severity:** low-medium
**What:** adoption checks `_start_time(pid) == info.get("t")` and then `healthy(port, info.get("key"))`. Two holes: (a) if `t` is missing, `_start_time(pid)` and `info["t"]` can both be `None`, so the identity check passes for a dead pid; (b) `healthy()` only checks `/health`, which llama-server serves **without checking the key** (llama.cpp's own notes say only `/health` and `/models` are always public), so the "answers with its key" claim in the docstring is not what the code tests.
**Failure:** a stale or hand-edited `run.json` (or a truncated write) that names a live pid and a port where some process answers `/health` is adopted. Shoggoth then routes chat and prompts to that process and hands its `run.json` key to it. Note the limit: `request()` is hardcoded to `127.0.0.1`, so a forged `run.json` cannot make Shoggoth send the key to a **remote** host, and the key it sends is the one in that same file, not a secret from elsewhere. The real risk is silently substituting the model server.
**Fix:** require `isinstance(info.get("t"), str)`; verify with a key-protected endpoint before adopting (e.g. `/props`); record the adopted pid/start time in `_state` so `stop()` can re-verify it; log a line when adopting.

### L4. The key-bearing temp file is created with default permissions
**File:** `shoggoth/llamacpp.py:361-366`
**Severity:** low-medium
**What:** `_save_run` opens `run.json.tmp` with plain `open(..., "w")` (0666 & umask, normally 0644), replaces it over `run.json`, and only then `chmod 0600`. The data directory itself is created without a mode (`store.py:64`), normally 0755.
**Failure:** `SIGKILL` between the write and the replace leaves `run.json.tmp` holding the current key at 0644; while the write is in progress, another local user can read it. `stop()` removes `run.json` but never the `.tmp`.
**Fix:** create the file with `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, 0o600)` as `store.save()` does (`store.py:138`), chmod the temp file before `os.replace`, and unlink any stale `.tmp` in `stop()`.

### L5. Downloads cannot resume and have no size cap
**File:** `shoggoth/llamacpp.py:212-226`
**Severity:** low
**What:** `_download` always opens the destination `wb` and hashes in one pass. Unlike `hf.py`, there is no `Range` resume, and the plan explicitly asked to reuse "hashlib, resume" from the old code (`docs/plans/2026-11-14-shoggoth-5-overhaul.md:60`). The CUDA pair is the largest download in the app.
**Failure:** a dropped connection 90% through a 1 GB asset throws the bytes away and restarts from zero on the next try (the file is inside the `.part` directory, which is removed at the start of `install`).
**Fix:** download to `.part`, resume with `Range` exactly as `hf._stream` does, verify the length against the API's `size` and then the sha256.

### L6. Concurrent installs share one temp directory
**File:** `shoggoth/llamacpp.py:229-245`; `_lock` defined at `:29` but unused here
**Severity:** low
**What:** `tmp = final + ".part"` is fixed, and `install()` never takes `_lock`. Two entry points (two UI clicks, or an update while setup runs) can run at once.
**Failure:** install B's initial `shutil.rmtree(tmp)` deletes install A's extracted files, A then fails with "The download doesn't contain llama-server", or both write into the same `.dl-` paths and one publishes a mixed build.
**Fix:** hold `_lock` for the whole install, or use `tempfile.mkdtemp(prefix=".install-", dir=root())` per run and discard it in `finally`.

### L7. Pruning can delete the build that is currently running
**File:** `shoggoth/llamacpp.py:269-276`
**Severity:** low
**What:** `_prune` protects only `_state["dir"]`, which is not populated until `running()`/`start()` runs in *this* process (`_state` starts without a `dir` key, `:30`).
**Failure:** restart Shoggoth, do not chat, update llama.cpp while the previous router (adopted from `run.json`) is still serving: the new tag becomes the third and the running tag is removed. The live process keeps working from deleted inodes, but the next restart cannot find its build and `choose()` silently falls back to another backend.
**Fix:** also consider `_load_run()["dir"]` when it is still alive, or never prune the tag recorded in `run.json`.

### L8. Hardlinks and special files are dropped without a word
**File:** `shoggoth/llamacpp.py:192-205`
**Severity:** low
**What:** only `isdir()`, `issym()` and `isfile()` members are handled. Hardlinks (`islnk()`), FIFOs and device nodes fall through every branch and are silently skipped. This is safe — no traversal, no device nodes written — but it is not what the docstring implies.
**Failure:** an asset that uses hardlinks (e.g. `libggml.so.0` linked to `libggml.so.0.0.0`) extracts without those files and `install()` fails later with the misleading "The download doesn't contain llama-server." There is no test for hardlinks.
**Fix:** handle `m.islnk()` by `os.link`ing to an already-extracted member that resolves inside `dest`, and reject special files with an explicit message; add a hardlink case to `tests/test_llamacpp.py`.

### L9. `presets.ini` is newline-safe but not parser-safe
**File:** `shoggoth/llamacpp.py:284-317`
**Severity:** low
**What:** newline injection is correctly blocked (keys via `_KEY`, values via `val`, section names via `_SECTION`), and the file cannot gain a section from a value. But llama.cpp's INI parser (`common/preset.cpp`, fetched at master) treats whitespace followed by `;` or `#` as a comment start and requires keys to match `[A-Za-z_][A-Za-z0-9_.-]*`. `_KEY` allows keys starting with a digit, and `val()` allows `;`/`#`.
**Failure:** a model path such as `/models/Old #2/Weird-Q4.gguf` is silently truncated to `/models/Old`, so the model fails to load with no useful error. A caller that passes an option name beginning with a digit produces a presets file llama-server refuses to parse.
**Fix:** reject values containing whitespace followed by `;`/`#` (or quote/escape them), require option keys to start with a letter, and route keys through `llamaargs` as the plan requires. Also reject a model id of `*` or `default` (`:308-311`): `[*]` is llama.cpp's global section, so a file whose stem is `*` would overwrite the defaults for every model.

---

## 2. `shoggoth/hf.py`

### H1. An existing destination is trusted by size alone, and non-LFS files are never verified
**File:** `shoggoth/hf.py:268-270`, hash check at `:293-295`
**Severity:** low
**What:** `_fetch_one` returns immediately when `dest` exists and `os.path.getsize(dest) == size` (or when `size` is unknown), without hashing it. And the final check is `if want and ...`, so an entry with no `lfs.oid` (a GGUF stored as a normal git file) is accepted with no integrity check at all. The test fake gives every GGUF an LFS oid, so neither path is covered.
**Failure:** a shard corrupted or swapped for one of the same length is accepted forever, including on a later retry after a checksum failure elsewhere; a repo serving a non-LFS GGUF gets no verification, contrary to the module docstring.
**Fix:** hash an existing destination before trusting it (cheap compared with the model), or keep a `{size, sha256}` sidecar from the successful download; refuse files the tree API gives no sha256 for, or at least record that they are unverified.

### H2. The branch is hardcoded to `main`
**File:** `shoggoth/hf.py:192`, `:265`
**Severity:** low
**What:** both the tree API and `/resolve` use `main`; HF repos default to whatever `default_branch` says.
**Failure:** a repo whose default branch is `master` (still common for older mirrors) lists as "Hugging Face doesn't have that model or file", and the quant list is empty with no way to tell why.
**Fix:** read `default_branch` from the model API (already fetched in `lookup`) or fall back from `main` to `master` on 404.

### H3. Unbounded search cache and unbounded response reads
**File:** `shoggoth/hf.py:329-331`, `:353-355`, `:134-139`
**Severity:** low
**What:** `_cache` is a plain dict keyed by every query string ever searched, with no eviction; `_get_json` reads the whole response body with no cap.
**Failure:** a long session with many searches grows memory permanently; a very large tree/API reply (or a hostile repo with a huge file list) is loaded into RAM before any validation.
**Fix:** keep a small LRU (say 100 entries) and pass a maximum read size to `_get_json`.

---

## 3. `shoggoth/pipkgs.py` and `shoggoth/pi/zest-skillsmith.js`

### P1. The approval card does not show the contents of the extra files
**File:** `shoggoth/pi/zest-skillsmith.js:112-117`
**Severity:** medium
**What:** the card promises the full skill and includes `preview: skillText(s)` — name, description and body — but `files` are sent as `{path, bytes}` only. The person approves the SKILL.md without seeing what goes into `templates/x.md` or `scripts/y.md`.
**Failure:** a model (or a prompt injection reaching it) writes innocuous steps in `body` and hides different instructions in a file. The person presses Save, and every future conversation that loads the skill reads the unseen file. The test at `tests/ui/skillsmith.test.mjs:79-90` only checks that the file is written, never that its content was shown.
**Fix:** include each file's content (with per-file and total limits) in the card, or save the skill switched off and show the files in the Skills page before it can be turned on. At minimum, reject `files` until the card can show them.

### P2. `save_skill` is not read-only to the gate, so plan mode blocks it and ask/edits ask twice
**File:** `shoggoth/pi/zest-skillsmith.js:1-6`, `:106-120`; `shoggoth/pi/zest-gate.js:20`, `:124-127`
**Severity:** low
**What:** the comment and the plan say the tool always asks "in every mode, including auto and read-only plan". But the gate only lets through `READ_ONLY` tools in plan mode; `save_skill` is not in that set, so the gate blocks it before `execute()` ever runs. In `ask` and `edits`, the gate shows its own generic card first and then `execute()` shows the preview card, so one save means two approvals.
**Failure:** asking for a skill in plan mode returns "Shoggoth is in read-only (plan) mode" instead of the preview, and in the normal modes the person is prompted twice, the first time without the preview.
**Fix:** add `save_skill` to `READ_ONLY` in `zest-gate.js` (it writes nothing before its own confirmation) and let the extension's card be the only prompt, or teach the gate to render the preview and state explicitly that plan mode refuses. Update the two comments to match whichever behaviour is chosen. This only becomes live when the extension is added to `agent.py`'s `-e` list.

### P3. A failed replace loses the old skill; the preview passes control characters through
**File:** `shoggoth/pi/zest-skillsmith.js:58-77`, validation at `:17-50`
**Severity:** low
**What:** `write()` calls `fs.rmSync(dir, {recursive: true, force: true})` before `fs.renameSync(tmp, dir)`; if the rename fails, the catch removes the temp folder and the old skill is already gone. Separately, `validate()` rejects NUL but allows C0/C1 controls and Unicode bidi overrides in `body`, `description` and file contents.
**Failure:** a disk-full or permission error while replacing destroys an existing skill. Or a model includes U+202E/U+202C in a step and the approval preview renders the text in a different order from what is saved.
**Fix:** rename the old directory to `dir.old-<pid>`, rename the new one into place, and restore on failure; reject C0/C1 and bidi control characters in all text fields.

### P4. A curated entry without an integrity value is accepted
**File:** `shoggoth/pipkgs.py:275`, list parsing at `:54-57`
**Severity:** low
**What:** `expect=entry.get("integrity") or None` means a missing `integrity` silently falls back to the registry-published sha512. That still protects against tampering in transit, but it breaks the stated rule that every entry is pinned by version *and* sha512 in Shoggoth's own list.
**Failure:** if a bad edit ever ships an entry without `integrity`, Shoggoth trusts whatever npm serves for that version instead of refusing to install. The shipped list is correct today, so this is latent.
**Fix:** require a `sha512-` integrity for enabled entries (both in `curated()` and in `install()`), and reject missing ones with the same "not tested yet" style message.

Pinning itself is otherwise sound: every package and every recursively resolved dependency is checked against the registry's sha512, the tarball host must match the registry, and nothing invokes npm or a lifecycle script (`tests/test_pipkgs.py::NothingRuns` asserts no `Popen`). One nit: `_Job.fetch` allows an `http` tarball URL when the registry is `https` as long as the host matches (`pipkgs.py:203-205`); require the registry's scheme.

---

## 4. `shoggoth/scan.py` and `shoggoth/engines.py`

### S1. Definitions now feed the old "discover" path, which probes hosted services
**File:** `shoggoth/servers.py:138-161`, called from `shoggoth/server.py:803`; `engines.py:344-349`
**Severity:** medium
**What:** `servers.discover()` builds its URL list from every preset with a `url`, with no category filter. Since Wave 0 that list is `engines.d`, which now includes OpenRouter, Nous Portal, OpenCode Zen/Go, Command Code and Gemini (`category: hosted`). `probe()` then sends `GET <url>/v1/models` with no key. It also auto-adds anything that answers 200 (`connect_found`, `servers.py:184-196`).
**Failure:** every call to the connect endpoint contacts all five hosting providers, revealing the user's IP/hostname to services they may never have chosen, and any provider that answers a public 200 is added as a "found" connected server with an empty key. This is exactly the traffic a local-only discovery step should not make.
**Fix:** skip `category == "hosted"` in `discover()` (and anywhere that treats presets as local engines), and only auto-add addresses on loopback/private ranges with a real model list.

### S2. Gemini's base URL is mangled by `normalise` + the `/v1` append
**File:** `shoggoth/engines.d/gemini.yaml:5`; `shoggoth/servers.py:22-38`, `:200-205`, `:241-246`
**Severity:** medium
**What:** `normalise()` strips a trailing `/v1` and `ConnectedServer` then appends `/v1` to whatever it is given. Every hosted definition happens to end in `/v1` except Gemini, whose OpenAI-style endpoint is `…/v1beta/openai/`.
**Failure:** connecting Gemini builds `https://generativelanguage.googleapis.com/v1beta/openai/v1` and every chat request 404s. All other hosted services survive the round trip by coincidence, so a unit test over the definitions would not catch it.
**Fix:** store the exact API base for hosted definitions and stop appending `/v1` for them (or add a `base_path`/`api_base` field and use it verbatim); add a test asserting each hosted definition's final endpoint.

### S3. A definition can point at any host, and `discover` will call it
**File:** `shoggoth/engines.py:61-78` (`_url`), `:306-320` (same id replaces bundled); `shoggoth/servers.py:138-161`
**Severity:** low-medium
**What:** command execution is genuinely impossible — `KEYS` is a closed whitelist, `yaml.safe_load` is used, and probe paths are plain GET paths. But `url`, `key_url` and `docs` accept any http(s) host, and a user definition with an existing id replaces the bundled one. `discover()` probes definition URLs, and for a hosted id the UI will send the key the person pastes to that `url`.
**Failure:** someone pastes a "fix for OpenRouter" definition from a forum; it keeps `id: openrouter` but changes `url` to their host. On Add, Shoggoth probes it, offers it as found, and once connected sends the user's OpenRouter key there. No code executes, but the key leaves for an unexpected host.
**Fix:** for `category: hosted`, pin the host for bundled ids (or require an explicit confirmation when a user definition changes a known id's host), and do not auto-probe user-supplied URLs. Show the destination host next to the key field.

### S4. An IPv6-only listener is probed at the wrong address
**File:** `shoggoth/scan.py:106-107`
**Severity:** low
**What:** every unspecified address is mapped to `127.0.0.1`, including `::` from `/proc/net/tcp6`. A socket bound to `[::]` with `IPV6_V6ONLY=1` accepts only IPv6.
**Failure:** the port is listed (so it is not missed as a socket) but the probe to `127.0.0.1` is refused, so the engine never shows up. The fixture hides this because the same port also has a v4 row.
**Fix:** use `[::1]` for an unspecified tcp6 row, or probe both families and keep the one that answers.

### S5. The scan follows redirects and probes every listening port
**File:** `shoggoth/scan.py:117-135`, `:285-292`
**Severity:** low
**What:** `urlopen` follows 3xx by default, so a local HTTP service can make Shoggoth send a request to any http(s) URL it names. Separately, `_look` sends `GET /v1/models` and then up to one request per distinct signature path to **every** TCP listener, including non-HTTP services (sshd, Postgres, printers), with no `User-Agent`.
**Failure:** a hostile local server redirects Shoggoth to an arbitrary host (leaking a request and the fact that Shoggoth scans); ordinary non-HTTP services get HTTP bytes in their logs and may log a protocol error for the local user. Nothing is authenticated in either case, so the blast radius is small.
**Fix:** install an opener with redirects disabled for scanning, set `User-Agent: Shoggoth/<version>`, and only send definition probes after `/v1/models` has shown an HTTP API (or at least skip well-known non-HTTP ports).

### S6. The scan holds a lock for its whole network phase
**File:** `shoggoth/scan.py:343-348`
**Severity:** low
**What:** `latest()` takes `_lock` and calls `scan()` inside it. With a black-holed port and several signature paths, one port can cost seconds; another request for the Models page or the picker waits for the whole scan.
**Failure:** two page openings at once make the second one hang for the duration of the first scan (and `scan()` itself is not protected when called directly).
**Fix:** compute the result outside the lock and only lock the cache swap, or keep an in-flight `Future` that concurrent callers await.

### S7. Shoggoth's own llama.cpp router is not excluded from scanning
**File:** `shoggoth/scan.py:333-340`; `shoggoth/llamacpp.py:28`
**Severity:** low (latent until wired)
**What:** `_own()` knows only the gateway and `builtin` (Lemonade) ports. The new router listens on `13340-13359`.
**Failure:** once `scan.sources()` is wired in, the user's own router appears in "found running", named "llama.cpp server" by its process, and can be connected a second time — exactly the duplicate the scan's docstring promises to avoid.
**Fix:** have `_own()` include the live `llamacpp` port (and any future provider's), or pass every managed port in from the caller.

### S8. One malformed stored server URL breaks the whole sources reply
**File:** `shoggoth/scan.py:361-372`
**Severity:** low
**What:** `sources()` calls `servers.normalise()`/`_canon()` over saved entries without catching `ValueError`. `servers.all()` filters only on `id`/`url` being present, not valid.
**Failure:** a hand-edited or older `settings.json` with `{"url": "localhost:11434"}` makes `/ui/sources` raise, so the Models page shows nothing at all instead of one bad entry.
**Fix:** validate each stored URL and skip/report the bad one (as `servers.probe` already does).

### S9. YAML aliases are expanded without a budget
**File:** `shoggoth/engines.py:287`
**Severity:** low (unsure)
**What:** `read_file` caps the raw file at 500 KB, but `yaml.safe_load` expands anchors and aliases with no limit. Whether the vendored PyYAML has any guard is not visible here; the upstream library does not bound alias expansion.
**Failure:** a small user definition (the plan adds a "paste a URL" install path) using recursive anchors can consume large amounts of CPU and memory before any schema check runs.
**Fix:** parse to a node graph first and reject excessive alias/depth counts, or cap the expanded node count; a timeout around the load is a cheap backstop.

---

## Missing tests

- `llamacpp`: nothing covers `install()` end to end (`version_of`/glibc path, the env mismatch in L2), `_prune`, `_save_run` permissions, `_download` checksum failure, adoption/`stop()` identity, or two concurrent installs. `_safe_extract` has no directory-entry, hardlink, FIFO or device case.
- `hf`: no test for an existing same-size destination, a file without `lfs.oid`, or a repo whose default branch is not `main`.
- `pipkgs`: no test for an enabled entry with no `integrity`, or that an `http` tarball is refused.
- `scan`: no IPv6-only (`IPV6_V6ONLY`) fixture, no redirect case, no "hosted presets are not probed" test, no test that the managed llama.cpp port is excluded.
- `engines`: no alias-bomb test; no test that a user definition replacing a bundled hosted id is handled safely.
- `skillsmith`: no test that extra-file contents appear in the approval card (P1), and no integration test with `zest-gate.js` for the plan-mode block and double prompt (P2).

---

## What looked solid

- **Tar extraction** (`llamacpp._safe_extract`): absolute paths and `..` components rejected, links deferred until after files and each target resolved inside `dest`, absolute/escaping symlinks refused, `..` in link targets caught lexically, only regular files get data, exec bits preserved and setuid dropped, 2 GiB cap. Hardlinks are skipped rather than mishandled (see L8).
- **Release/build selection**: numeric comparison of `b<number>` tags, drafts and assets-less releases skipped, `v1.0.0` excluded, per-asset `sha256:` digest required, CUDA runtime asset required, `b<tag>` verified with the binary after extraction.
- **`write_presets`** cannot be made to add a newline, section or comment from a key or value; keys are option-shaped and values are scalarised; the file is written atomically.
- **`hf.py` path handling**: `_REPO` and `_safe_name` reject traversal, absolute paths, backslashes, empty and dot components before anything is written; `dest` is built only from validated parts; `.part` renames are atomic; resume verifies the `Content-Range` start, restarts when the server ignores `Range`, and the LFS `oid` is checked at the end.
- **`pipkgs.py`**: nothing shells out to npm, no lifecycle script can run, every package and dependency is sha512-checked against the registry, tarballs must come from the registry host, and extraction drops symlinks/hardlinks/special files with size and package-count caps.
- **`engines.py`**: closed schema with no command/exec keys, http(s)-only URLs without userinfo, plain GET paths only, `yaml.safe_load`, one bad definition (or file) is reported with a line number without taking the others down, the catch-all sorts last, and `engines check` gives the same report offline.
- **`scan.py`**: `/proc/net/tcp{,6}` parsing (state `0A`, hex ports, both families), inode→pid mapping that tolerates other users' processes, process-name-before-signature ordering, sensible 401 handling, and no stored key is ever sent to a discovered port.
- **`zest-skillsmith.js`**: slug/length/duplicate/NUL/`---` checks, a strict relative-path allowlist, reserved `SKILL.md`/`source.json`, 0644 writes, temp-folder-then-rename, `hasUI` required, and only an offered option is accepted as consent.
- **Packaging**: AppStream metainfo and desktop entry carry the Shoggoth name and motto, the image includes `engines.d/` and `recommendations/`, and every downloaded component is pinned by a checksum or npm integrity.
