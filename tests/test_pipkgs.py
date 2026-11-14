"""Curated Pi packages: installing from a fake npm registry (a local http.server) checks the sha512, refuses tarballs that escape
their folder, resolves dependencies with exact versions, and never runs anything. Run: python3 -m unittest discover tests"""
import base64
import hashlib
import http.server
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import pipkgs  # noqa: E402
import piup  # noqa: E402


def sri(data):
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def tarball(files, extra=None):
    """An npm-style tgz: files is {path under package/: text}; extra is a list of callables that add odd members."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, text in files.items():
            data = text.encode()
            ti = tarfile.TarInfo("package/" + rel)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        for fn in extra or []:
            fn(tf)
    return buf.getvalue()


class Registry:
    """Serves /<name> (all versions) and /<name>/<version> (one) plus /tgz/<name>-<version>.tgz."""

    def __init__(self):
        self.pkgs = {}            # name -> {version: (meta extras, tgz bytes, integrity override)}
        self.hits = []
        reg = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                reg.hits.append(self.path)
                path = self.path.lstrip("/")
                if path.startswith("tgz/"):
                    key = path[4:-4]
                    name, _, ver = key.rpartition("@")
                    entry = reg.pkgs.get(name.replace("__", "/"), {}).get(ver)
                    return self.send(200, entry[1], "application/octet-stream") if entry else self.send(404, b"")
                parts = path.split("/")
                name = "/".join(parts[:2]) if parts[0].startswith("@") else parts[0]
                rest = parts[2:] if parts[0].startswith("@") else parts[1:]
                versions = reg.pkgs.get(name)
                if not versions:
                    return self.send(404, b"{}")
                if not rest:
                    return self.send(200, json.dumps({"name": name, "versions": {v: {} for v in versions}}).encode())
                entry = versions.get(rest[0])
                if not entry:
                    return self.send(404, b"{}")
                extras, tgz, integ = entry
                meta = dict(extras, name=name, version=rest[0],
                            dist={"integrity": integ or sri(tgz), "tarball": "%s/tgz/%s@%s.tgz" % (reg.base, name.replace("/", "__"), rest[0])})
                self.send(200, json.dumps(meta).encode())

            def send(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def add(self, name, version, deps=None, files=None, scripts=None, extra=None, integrity=None, raw=None):
        pj = {"name": name, "version": version, "dependencies": deps or {}}
        if scripts:
            pj["scripts"] = scripts
        data = raw or tarball(dict({"package.json": json.dumps(pj), "index.js": "// " + name}, **(files or {})), extra)
        meta = {"dependencies": deps or {}}
        if scripts:
            meta["scripts"] = scripts
        self.pkgs.setdefault(name, {})[version] = (meta, data, integrity)
        return sri(data)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = os.environ.get("SHOGGOTH_DATA_DIR")
        os.environ["SHOGGOTH_DATA_DIR"] = self.tmp
        self.reg = Registry()
        self._reg = piup.REGISTRY
        piup.REGISTRY = self.reg.base + "/"

    def tearDown(self):
        piup.REGISTRY = self._reg
        self.reg.close()
        if self._env is None:
            os.environ.pop("SHOGGOTH_DATA_DIR", None)
        else:
            os.environ["SHOGGOTH_DATA_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entry(self, name, version, integrity="", enabled=True):
        return [{"name": name, "version": version, "integrity": integrity, "enabled": enabled, "notes": ""}]


class Installing(Base):
    def test_installs_and_lists_and_removes(self):
        integ = self.reg.add("@acme/tool", "1.2.3")
        got = pipkgs.install("@acme/tool", self.entry("@acme/tool", "1.2.3", integ))
        self.assertTrue(os.path.isfile(os.path.join(got["dir"], "package.json")))
        self.assertEqual([(p["name"], p["version"]) for p in pipkgs.installed()], [("@acme/tool", "1.2.3")])
        self.assertEqual(pipkgs.settings_entries(), [got["dir"]])
        self.assertEqual(pipkgs.settings_entries({"other"}), [])
        self.assertTrue(pipkgs.remove("@acme/tool"))
        self.assertEqual(pipkgs.installed(), [])
        self.assertFalse(pipkgs.remove("@acme/tool"))

    def test_the_shipped_list_has_the_five_and_none_is_enabled(self):
        names = [e["name"] for e in pipkgs.curated()]
        self.assertEqual(names, ["bun-docx", "@sttronn/pi-sheets", "@tintinweb/pi-subagents", "billion-context", "@dietrichgebert/ponytail"])
        self.assertFalse(any(e["enabled"] for e in pipkgs.curated()))
        with self.assertRaises(RuntimeError):
            pipkgs.install("bun-docx")                      # disabled
        with self.assertRaises(RuntimeError):
            pipkgs.install("left-pad")                      # not on the list

    def test_an_entry_without_a_version_is_refused(self):
        with self.assertRaises(RuntimeError):
            pipkgs.install("a", self.entry("a", ""))

    def test_integrity_mismatch_in_the_download_is_refused(self):
        self.reg.add("evil", "1.0.0", integrity=sri(b"something else"))
        with self.assertRaises(RuntimeError) as c:
            pipkgs.install("evil", self.entry("evil", "1.0.0"))
        self.assertIn("checksum", str(c.exception))
        self.assertEqual(pipkgs.installed(), [])
        self.assertEqual([d for d in os.listdir(pipkgs.root())], [])          # nothing left behind

    def test_integrity_in_the_list_must_match_the_registry(self):
        self.reg.add("a", "1.0.0")
        with self.assertRaises(RuntimeError):
            pipkgs.install("a", self.entry("a", "1.0.0", sri(b"not it")))
        self.assertEqual(pipkgs.installed(), [])

    def test_a_dependency_with_a_bad_checksum_fails_the_whole_install(self):
        self.reg.add("a", "1.0.0", deps={"b": "^1.0.0"})
        self.reg.add("b", "1.4.0", integrity=sri(b"nope"))
        with self.assertRaises(RuntimeError):
            pipkgs.install("a", self.entry("a", "1.0.0"))
        self.assertEqual(pipkgs.installed(), [])

    def test_path_escape_tarballs_are_rejected(self):
        def escape(tf):
            data = b"pwned"
            ti = tarfile.TarInfo("package/../../escaped.txt")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        self.reg.add("esc", "1.0.0", extra=[escape])
        with self.assertRaises(RuntimeError):
            pipkgs.install("esc", self.entry("esc", "1.0.0"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escaped.txt")))
        self.assertFalse(os.path.exists(os.path.join(pipkgs.root(), "escaped.txt")))
        self.assertEqual(pipkgs.installed(), [])

    def test_absolute_paths_are_rejected(self):
        target = os.path.join(self.tmp, "abs.txt")

        def absolute(tf):
            ti = tarfile.TarInfo(target)
            ti.size = 1
            tf.addfile(ti, io.BytesIO(b"x"))
        self.reg.add("abs", "1.0.0", extra=[absolute])
        with self.assertRaises(RuntimeError):
            pipkgs.install("abs", self.entry("abs", "1.0.0"))
        self.assertFalse(os.path.exists(target))

    def test_symlinks_and_special_files_are_not_unpacked(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)

        def links(tf):
            ln = tarfile.TarInfo("package/link")
            ln.type, ln.linkname = tarfile.SYMTYPE, outside
            tf.addfile(ln)
            hl = tarfile.TarInfo("package/hard")
            hl.type, hl.linkname = tarfile.LNKTYPE, "package/package.json"
            tf.addfile(hl)
            # a file written "through" the symlink must not land outside
            data = b"pwned"
            ti = tarfile.TarInfo("package/link/pwn.txt")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        self.reg.add("sym", "1.0.0", extra=[links])
        try:
            got = pipkgs.install("sym", self.entry("sym", "1.0.0"))
        except RuntimeError:
            got = None                                       # refusing outright is fine too
        self.assertEqual(os.listdir(outside), [])
        if got:
            self.assertFalse(os.path.lexists(os.path.join(got["dir"], "link")) and os.path.islink(os.path.join(got["dir"], "link")))
            self.assertFalse(os.path.lexists(os.path.join(got["dir"], "hard")))

    def test_an_oversized_package_is_rejected(self):
        self.reg.add("big", "1.0.0", files={"blob.bin": "x" * 5000})
        old = pipkgs.MAX_UNPACKED
        pipkgs.MAX_UNPACKED = 2000
        try:
            with self.assertRaises(RuntimeError):
                pipkgs.install("big", self.entry("big", "1.0.0"))
        finally:
            pipkgs.MAX_UNPACKED = old
        self.assertEqual(pipkgs.installed(), [])


class Dependencies(Base):
    def test_resolves_ranges_to_exact_versions_recursively(self):
        self.reg.add("app", "1.0.0", deps={"left": "^1.1.0", "right": "~2.0.1", "star": "*"})
        for v in ("1.0.0", "1.1.0", "1.9.2", "2.0.0"):
            self.reg.add("left", v, deps={"deep": ">=0.1.0 <0.3.0"} if v == "1.9.2" else {})
        for v in ("2.0.0", "2.0.1", "2.0.7", "2.1.0"):
            self.reg.add("right", v)
        for v in ("0.1.0", "0.2.5", "0.3.0"):
            self.reg.add("deep", v)
        self.reg.add("star", "3.0.0")
        self.reg.add("star", "4.0.0-beta.1")
        got = pipkgs.install("app", self.entry("app", "1.0.0"))
        nm = os.path.join(got["dir"], "node_modules")

        def ver(n):
            with open(os.path.join(nm, n, "package.json")) as f:
                return json.load(f)["version"]
        self.assertEqual((ver("left"), ver("right"), ver("deep"), ver("star")), ("1.9.2", "2.0.7", "0.2.5", "3.0.0"))

    def test_a_conflicting_version_is_nested_under_the_package_that_needs_it(self):
        self.reg.add("app", "1.0.0", deps={"a": "1.0.0", "shared": "^1.0.0"})
        self.reg.add("a", "1.0.0", deps={"shared": "^2.0.0"})
        self.reg.add("shared", "1.5.0")
        self.reg.add("shared", "2.3.0")
        got = pipkgs.install("app", self.entry("app", "1.0.0"))

        def ver(*p):
            with open(os.path.join(got["dir"], *p, "package.json")) as f:
                return json.load(f)["version"]
        self.assertEqual(ver("node_modules", "shared"), "1.5.0")
        self.assertEqual(ver("node_modules", "a", "node_modules", "shared"), "2.3.0")

    def test_no_version_fits(self):
        self.reg.add("app", "1.0.0", deps={"gone": "^9.0.0"})
        self.reg.add("gone", "1.0.0")
        with self.assertRaises(RuntimeError):
            pipkgs.install("app", self.entry("app", "1.0.0"))

    def test_dependencies_from_outside_the_registry_are_refused(self):
        for rng in ("git+https://example.com/x.git", "file:../x", "https://example.com/x.tgz", "user/repo", "npm:other@1"):
            self.reg.add("app", "1.0.0", deps={"x": rng})
            with self.assertRaises(RuntimeError, msg=rng):
                pipkgs.install("app", self.entry("app", "1.0.0"))

    def test_a_tarball_hosted_elsewhere_is_refused(self):
        self.reg.add("app", "1.0.0")
        old = self.reg.pkgs["app"]["1.0.0"]
        self.reg.pkgs["app"]["1.0.0"] = old
        orig = pipkgs._meta

        def meta(name, version):
            m = orig(name, version)
            m["dist"]["tarball"] = "http://example.invalid/app.tgz"
            return m
        pipkgs._meta = meta
        try:
            with self.assertRaises(RuntimeError):
                pipkgs.install("app", self.entry("app", "1.0.0"))
        finally:
            pipkgs._meta = orig


class NothingRuns(Base):
    def test_lifecycle_scripts_are_never_run(self):
        marker = os.path.join(self.tmp, "ran")
        script = "touch " + marker
        self.reg.add("app", "1.0.0", deps={"dep": "1.0.0"}, scripts={"preinstall": script, "install": script, "postinstall": script},
                     files={"install.js": "require('fs').writeFileSync(%r, 'x')" % marker})
        self.reg.add("dep", "1.0.0", scripts={"postinstall": script})
        got = pipkgs.install("app", self.entry("app", "1.0.0"))
        self.assertFalse(os.path.exists(marker))
        self.assertTrue(os.path.isfile(os.path.join(got["dir"], "install.js")))        # unpacked, but not run
        self.assertTrue(os.path.isfile(os.path.join(got["dir"], "node_modules", "dep", "package.json")))

    def test_no_subprocess_is_started(self):
        import subprocess
        self.reg.add("app", "1.0.0", deps={"dep": "1.0.0"})
        self.reg.add("dep", "1.0.0")
        real = subprocess.Popen

        def boom(*a, **k):
            raise AssertionError("a process was started")
        subprocess.Popen = boom
        try:
            pipkgs.install("app", self.entry("app", "1.0.0"))
        finally:
            subprocess.Popen = real


class Ranges(unittest.TestCase):
    def test_satisfies(self):
        yes = [("1.2.3", "1.2.3"), ("1.9.0", "^1.2.3"), ("0.2.9", "^0.2.3"), ("0.0.3", "^0.0.3"), ("1.2.9", "~1.2.3"), ("1.2.0", "1.2.x"),
               ("2.0.0", ">=1 <3"), ("1.5.0", "1.x || 3.x"), ("3.1.0", "1.x || 3.x"), ("1.5.0", "1.2.0 - 1.9.0"), ("9.9.9", "*"), ("1.0.0", ""),
               ("1.0.0", ">= 1.0.0"), ("1.4.0", "<2")]
        no = [("2.0.0", "^1.2.3"), ("0.3.0", "^0.2.3"), ("0.0.4", "^0.0.3"), ("1.3.0", "~1.2.3"), ("1.2.2", ">1.2.2"), ("1.0.0-beta.1", "^1.0.0"),
              ("2.0.0", "1.x || 3.x"), ("1.0.0", ">=2")]
        for v, r in yes:
            self.assertTrue(pipkgs.satisfies(v, r), (v, r))
        for v, r in no:
            self.assertFalse(pipkgs.satisfies(v, r), (v, r))


if __name__ == "__main__":
    unittest.main()
