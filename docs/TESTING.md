# Testing Shoggoth

Four layers, from quickest to slowest. The first two run on every push and pull request (`.github/workflows/test.yml`).

| Layer | What it checks | Where | Needs |
| --- | --- | --- | --- |
| Python tests | The server, engines, downloads, migration, the gateway, LAN sharing, speech, docs and copy rules, against fake servers on 127.0.0.1 | `tests/test_*.py` | Python 3.12. No network, no GPU |
| Page tests | The page in jsdom against recorded answers (`tests/ui/api.json`), and that those recordings still match the real server | `tests/ui/*.test.mjs`, `tests/test_fixture_contract.py` | Node 22 and `npm ci --prefix tests/ui` |
| The AppImage | A built AppImage starts, says its version, serves the page, runs its llama.cpp, and stops everything when told to | `packaging/check-appimage.sh` | A built AppImage |
| A real model | Download from Hugging Face, load with the processor-only llama.cpp, get an answer | `packaging/check-real-model.py` | Internet, a few minutes |

Run everything that runs in CI:

```bash
npm ci --prefix tests/ui
python3 -W ignore -m unittest discover tests
```

The Python suite runs every page test too. A new `tests/ui/*.test.mjs` file is picked up without being listed.

## The recorded answers

The page tests answer the page from `tests/ui/api.json`. `tests/test_fixture_contract.py` starts the real server on a throwaway profile and fails when a recorded answer has keys the real one lacks, when the page calls a `/ui` route the server doesn't name, or when it calls an `/api` route the gateway doesn't forward. When it fails, record the answers again from a running Shoggoth (`node tests/ui/smoke.js --record URL TOKEN`) or fix the page.

## Releases

A `v*` tag runs `.github/workflows/release.yml`: the tests, then the build, then a check that the tag and the app's version agree, then `check-appimage.sh`, then the release. A tag with a hyphen (`v5.0.0-beta`) becomes a pre-release, so the update check, which reads the latest full release, doesn't offer it.

## Nightly

`.github/workflows/real-model.yml` builds the AppImage and runs `check-real-model.py` with SmolLM2-135M and with the recommended small pick (LFM3-Omni-2B), every night and by hand from the Actions tab. A failure there is often Hugging Face or a moved file, so read the log before the code.

## Not covered

No test runs a GPU, the real Pi agent on a real model, a browser with real layout (jsdom has none), or two computers sharing models. Those are checked by hand before a release.
