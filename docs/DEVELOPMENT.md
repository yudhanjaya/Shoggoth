# Development

## Requirements

- Python 3 (developed on 3.12). No third-party packages are needed.
- For Chat's agent features: [Pi](https://pi.dev) and Node.js 22.19 or later. Install with `npm install -g @earendil-works/pi-coding-agent`, or set `SHOGGOTH_PI` to a `pi` binary.
- For diagrams: Mermaid, fetched once with `./packaging/fetch-vendor.sh` (26 MB, verified against its published checksum).

The AppImage includes Pi, Node.js, Mermaid, and Lemonade.

## Running from source

```bash
git clone https://github.com/yudhanjaya/Shoggoth.git
cd Shoggoth
python3 shoggoth/server.py
```

The UI is served at <http://127.0.0.1:13306> and opens in a browser unless `--no-browser` is given. A source checkout does not include Lemonade. On the first-run screen, **Download Lemonade** fetches it (about 7 MB), or Shoggoth can be pointed at an existing instance.

## Configuration

| Flag | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `--port` | `SHOGGOTH_PORT` | `13306` | UI port |
| `--host` | `SHOGGOTH_HOST` | `127.0.0.1` | UI bind address. See [SECURITY.md](SECURITY.md). |
| `--lemonade HOST:PORT` | `LEMONADE_API_KEY` (its key) | Saved setting | Use this Lemonade and skip first-run |
| `--no-browser` | | | Do not open a browser tab |
| `--app` | `SHOGGOTH_APP_GRACE` (seconds, default 180) | | Exit about two seconds after the last tab closes (used by the AppImage). A tab that vanished without saying goodbye, like a crashed browser, times out after the grace period |
| | `SHOGGOTH_LEMONADE_DIR` | | Directory containing a `lemond` to use as the built-in Lemonade |
| | `SHOGGOTH_PI` | | Path to a `pi` binary |
| | `SHOGGOTH_AUTH=off` | | Disable UI authentication |
| | `XDG_CONFIG_HOME`, `XDG_DATA_HOME` | | Relocate config and data. See [PRIVACY.md](PRIVACY.md). |

A user-run Lemonade can also be configured under **Advanced > Engines > My own server**. Shoggoth does not update an external Lemonade. If it runs as a system service, it may be unable to read model files in the user's home directory.

## Tests

```bash
python3 -m unittest discover tests
```

## Engines

Engines are added as a folder containing `provider.yaml`, with `provider.py` only where YAML cannot express the behavior. No change to Shoggoth's own code is required. See [ENGINES.md](ENGINES.md).

| Command | Purpose |
| --- | --- |
| `python3 shoggoth/server.py providers new <id> [--from llama-server]` | Scaffold an engine folder |
| `providers check <folder> [--json]` | Validate a folder the way Shoggoth reads it. Starts nothing. |
| `providers list` | List loaded engines and folders that failed to load |
| `providers doctor` | Query installed Lemonade with the requests Shoggoth makes. Report schema drift. Run before each release. |

Reference material: `shoggoth/providers/_template/` (annotated template) and `shoggoth/providers/_examples/llama-server/` (complete YAML-only engine, tested end to end).

## Building the AppImage

```bash
./packaging/build-appimage.sh
```

The script downloads a relocatable Python, Node.js, the Pi agent, Lemonade's embeddable build, and `appimagetool`, all pinned by SHA-256. It writes `dist/Shoggoth-<version>-x86_64.AppImage` and a checksum. To bundle a newer Lemonade, update the version and checksum at the top of the script.

## Releasing

1. Update `VERSION` in `shoggoth/server.py`.
2. Run `providers doctor`.
3. Tag and push: `git tag vX.Y.Z && git push --tags`.

Pushing a tag beginning with `v` runs the build in GitHub Actions and attaches the AppImage to the release, together with a `Zest-`-named copy so that Zest 3.x installations can self-update.
