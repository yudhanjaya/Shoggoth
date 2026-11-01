# Security model

Shoggoth's UI can change settings, start engines, and run commands inside a folder. Its listeners are therefore authenticated and, by default, bound to loopback.

## Listeners

| Listener | Default | Purpose | Authentication |
| --- | --- | --- | --- |
| UI server | `127.0.0.1:13306` | The browser UI and `/ui/*`, `/api/*` | Session cookie, or bearer token |
| Gateway | `127.0.0.1:13308` | OpenAI-compatible endpoints for other applications | API key, always required |
| Built-in Lemonade | Private port | Inference | Random `LEMONADE_API_KEY`, known only to Shoggoth |

## UI authentication

- A secret is stored in `~/.config/shoggoth/ui-token` (mode `600`).
- The window Shoggoth opens carries a one-time code, valid for two minutes and a single use. The server exchanges it for an `HttpOnly`, `SameSite=Strict` cookie.
- Any other request is rejected with `401`. A browser without a session receives a page instructing the user to restart Shoggoth.
- Scripts may send the secret as `Authorization: Bearer <secret>`.
- A second launch of Shoggoth asks the running instance for a fresh one-time code.
- `SHOGGOTH_AUTH=off` disables UI authentication. Use it only on a single-user machine.

## Network exposure

- The UI server rejects requests whose `Host` header does not name the local machine. This mitigates DNS rebinding.
- Use `--host 0.0.0.0` only on a trusted network. The UI is authenticated, but traffic is not encrypted.
- The gateway can be opened to the local network from **Advanced > Server**. Any holder of the API key can then use the loaded models, and traffic is unencrypted. **Make a new key** invalidates the previous key immediately.
- The gateway cannot download, delete, or install anything, or change settings.

## Agent approval gate

Pi does not ask for permission before acting. The `shoggoth/pi/zest-gate.js` extension hooks `tool_call` and enforces the approval mode (`ask`, `edits`, `auto`, `plan`). Reads pass; writes, edits, and commands prompt. See [AGENT-RUNTIME.md](AGENT-RUNTIME.md#approval-gate) for the mechanics.

The gate is a policy prompt, not a sandbox. An approved command runs with the user's privileges. Run agents against folders that can be recovered, or inside a container, when the model or its inputs are untrusted.

Related controls:

- Pi runs with project trust declined (`--no-approve`, `defaultProjectTrust: never`), so a folder cannot load its own Pi extensions.
- Pi's own skill discovery is disabled. The model sees only the skills enabled on the Skills page.
- Pi starts offline, with telemetry and update checks disabled.

## Supply chain

- Downloaded components (Lemonade, Pi, Strata, and the AppImage build inputs) are verified against pinned or published checksums before use. Pi's npm tarball is verified against the registry's sha512 integrity value.
- Skills installed from a link are downloaded as plain files from `codeload.github.com` (40 MB cap, 3,000 files, no path escapes). Nothing from the download is executed at install time. A skill's scripts run only if the model runs them, which requires approval in `ask` mode.
- Engines added by users must be approved once. Approval is keyed to a fingerprint of the engine's files, so any change prompts again. Engines bundled with Shoggoth are trusted.
