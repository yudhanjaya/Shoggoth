# Security model

Shoggoth's UI can change settings, start engines, and run commands inside a folder. Its listeners are therefore authenticated and, by default, bound to loopback.

## Listeners

| Listener | Default | Purpose | Authentication |
| --- | --- | --- | --- |
| UI server | `127.0.0.1:13306` | The browser UI and `/ui/*`, `/api/*` | Session cookie, or bearer token |
| Gateway | `127.0.0.1:13308` | OpenAI-compatible endpoints for other applications | API key, always required |
| Gateway, LAN side | `0.0.0.0:13308` when sharing is on | The same endpoints for other Shoggoths on the network | HTTPS only, plus that Shoggoth's own pairing key |
| Built-in llama.cpp router | Private port | Inference | Random API key, known only to Shoggoth |

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
- The gateway can be opened to other Shoggoths on the local network from the Server page. The network side is HTTPS only. Any holder of the API key, or a Shoggoth paired with this computer, can then use the loaded models over that encrypted connection. **Make a new key** invalidates the previous key immediately.
- The gateway cannot download, delete, or install anything, or change settings.

## Sharing with other Shoggoths

- The network side of the gateway speaks HTTPS only, TLS 1.2 or newer. The certificate is self-signed and made once, on the first share, then kept in the config folder.
- Plain HTTP from another device is not served. The connection is answered with a short message that tells the person to use `https://` and to trust the certificate shown on the Server page.
- Pairing uses a 6-digit code. The code is shown on the server and typed on the client, and the exchange runs over the TLS link the client has already pinned to the certificate fingerprint it saw in the beacon.
- Each paired client gets its own key. The server keeps only a hash of each key, so the keys cannot be read back out of the settings file.
- **Remove** revokes a key at once.
- The beacon is a UDP packet every 2 seconds on port 13309. It carries the computer's name, a profile id, the Shoggoth version, the gateway port, the certificate fingerprint and the model count. It carries no model names, chats, folders or keys.
- Keys for the hosted services (OpenRouter, Nous Portal, OpenCode, Command Code and Google Gemini) are stored in the settings file, which is kept at mode `600`.

## Agent approval gate

Pi does not ask for permission before acting. The `shoggoth/pi/zest-gate.js` extension hooks `tool_call` and enforces the approval mode (`ask`, `edits`, `auto`, `plan`). Reads pass; writes, edits, and commands prompt. See [AGENT-RUNTIME.md](AGENT-RUNTIME.md#approval-gate) for the mechanics.

The gate is a policy prompt, not a sandbox. An approved command runs with the user's privileges. Run agents against folders that can be recovered, or inside a container, when the model or its inputs are untrusted.

Related controls:

- Pi runs with project trust declined (`--no-approve`, `defaultProjectTrust: never`), so a folder cannot load its own Pi extensions.
- Pi's own skill discovery is disabled. The model sees only the skills enabled on the Skills page.
- Pi starts offline, with telemetry and update checks disabled.

## Supply chain

- Downloaded components (llama.cpp, Pi, whisper.cpp and Piper, and the AppImage build inputs) are verified against pinned or published checksums before use. Pi's npm tarball is verified against the registry's sha512 integrity value. GitHub publishes no checksum for Piper's Linux tarballs, so their sha256 values are kept in `tts.py`; each voice from Hugging Face is checked against its published sha256.
- Skills installed from a link are downloaded as plain files from `codeload.github.com` (40 MB cap, 3,000 files, no path escapes). Nothing from the download is executed at install time. A skill's scripts run only if the model runs them, which requires approval in `ask` mode.
- Engines added by users must be approved once. Approval is keyed to a fingerprint of the engine's files, so any change prompts again. Engines bundled with Shoggoth are trusted. An engine definition file (`engines.d/*.yaml`) is data: it can name an engine and describe how to recognise it, but it cannot carry a command, and a built-in hosted service cannot be pointed at another address.

## Pi packages

A Pi package is npm code that runs inside Pi. Shoggoth installs only the pinned entries in `shoggoth/recommendations/pi-packages.yaml`, at an exact version, checked against the sha512 npm publishes. It never runs npm or a lifecycle script: the tarball is unpacked, and a dependency from anywhere except the npm registry is refused. All entries shipped with 5.0 are switched off until they have been tested.

Once installed and turned on, a package's code runs in Pi's own process, with the user's permissions. The approval gate covers Pi's tools, not an extension's own code, so a package can do what any program on the computer can. Shoggoth says "Runs code on your computer" next to it for that reason.

## Attachments

A file attached in a chat is written as it came and never executed. Its name is cleaned: folder parts, control characters and direction-changing characters are refused, and one file is capped at 50 MB. A PDF is read with `pdftotext` or rendered with `pdftoppm` only if poppler is installed; no content from a file is passed to a shell.
