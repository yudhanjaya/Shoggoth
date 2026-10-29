"""Understanding llama-server's command line: parsing, checking, and a structured schema of every option."""
import os
import re

from util import cached, run


# ---------------------------------------------------------------- llama-server arguments
def lem_split(text):
    """Split an argument string exactly the way Lemonade does: on spaces, with '...' or "..." grouping and no escapes."""
    out, cur, quote, started = [], "", None, False
    for c in text or "":
        if quote is None and c in "\"'":
            quote, started = c, True
        elif quote and c == quote:
            quote = None
        elif quote is None and c == " ":
            if cur or started:
                out.append(cur)
            cur, started = "", False
        else:
            cur += c
    if cur or started:
        out.append(cur)
    return out, quote is not None   # tokens, and whether a quote was left open


def lem_quote(tok):
    """Quote one token so Lemonade reads it back unchanged (None if it contains both kinds of quote)."""
    if tok and not re.search(r"[\s'\"]", tok):
        return tok
    if "'" not in tok:
        return "'" + tok + "'"
    if '"' not in tok:
        return '"' + tok + '"'
    return None


# Lemonade refuses these because it sets them itself.
MANAGED_FLAGS = {
    "-m": "the model path", "--model": "the model path", "--port": "the port",
    "--ctx-size": "the context window", "-c": "the context window",
    "-ngl": "GPU layers", "--gpu-layers": "GPU layers", "--n-gpu-layers": "GPU layers",
    "--jinja": "the chat template engine", "--mmproj": "the multimodal projector", "-mm": "the multimodal projector",
    "--embeddings": "embedding mode", "--embedding": "embedding mode",
    "--reranking": "reranking mode", "--rerank": "reranking mode",
}
# Options people know from vLLM / SGLang that llama.cpp spells differently.
FOREIGN_HINTS = {
    "--tool-call-parser": "That is a vLLM/SGLang option. llama.cpp reads tool calls through the model's chat template, and --jinja is already on. To use a different template, try --chat-template-file.",
    "--enable-auto-tool-choice": "That is a vLLM option. llama.cpp enables tool calling through the chat template (--jinja is already on).",
    "--reasoning-parser": "That is a vLLM/SGLang option. llama.cpp has --reasoning-format and --reasoning for thinking models.",
    "--max-model-len": "That is a vLLM option. Set the Context window field instead.",
    "--tensor-parallel-size": "That is a vLLM option. llama.cpp splits across GPUs with --split-mode and --tensor-split.",
}


def known_flags(bin_path):
    """Every flag the llama-server binary documents in --help."""
    def build():
        if not bin_path or not os.path.isfile(bin_path):
            return None
        code, out = run([bin_path, "--help"], 20)
        flags = set()
        for line in out.splitlines():
            if not line.startswith("-"):
                continue
            head = re.split(r"\s{2,}", re.sub(r",\s+", ", ", line.strip()), maxsplit=1)[0]
            for tok in re.findall(r"--?\[?[A-Za-z0-9][\w\-\[\]]*", head):
                m = re.fullmatch(r"(--)\[no-\](.+)", tok)
                if m:
                    flags.update({m.group(1) + m.group(2), m.group(1) + "no-" + m.group(2)})
                else:
                    flags.add(tok)
        return flags or None
    return cached(f"flags:{bin_path}:{os.path.getmtime(bin_path) if bin_path and os.path.isfile(bin_path) else 0}", 3600, build)


# ---------------------------------------------------------------- argument schema
# Flags that are not model settings (or that would break Lemonade's own connection to llama-server).
_HIDDEN = {"-h", "--help", "--usage", "--version", "--license", "--completion-bash", "--list-devices",
           "--cache-list", "--host", "--port", "--api-key", "--api-key-file", "--path", "--alias", "-a",
           "--tags", "--reuse-port", "--timeout", "-to", "--threads-http"}
_HIDDEN_PREFIXES = ("--ssl", "--api-", "--webui", "--no-webui", "--models-", "--hf-", "-hf", "--model-url",
                    "-mu", "--mmproj", "-mm", "--docker", "--tools", "--offline")


def _hidden(flag):
    return flag in _HIDDEN or flag in MANAGED_FLAGS or flag.startswith(_HIDDEN_PREFIXES)


def _clean_desc(lines):
    text = " ".join(lines)
    text = re.sub(r"\(env:[^)]*\)", "", text)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)       # markdown links
    return re.sub(r"\s+", " ", text).strip()


def _make_entry(flagpart, desc_lines, section):
    pieces = [x.strip() for x in flagpart.split(", ") if x.strip()]
    flags, metavar = [], ""
    for piece in pieces:
        parts = piece.split(None, 1)
        flags.append(parts[0])
        if len(parts) > 1 and not metavar:
            metavar = parts[1].strip()
    # split "-kvo, --kv-offload, -nkvo, --no-kv-offload" into its positive and negative halves
    neg_at = next((i for i, f in enumerate(flags) if f.startswith(("--no-", "-no-"))), None)
    pos, neg = flags, []
    if neg_at is not None:
        k0 = neg_at - 1 if neg_at > 0 and not flags[neg_at - 1].startswith("--") else neg_at
        pos, neg = flags[:k0], flags[k0:]
    if not pos:
        return None
    if any(_hidden(f) for f in pos + neg):
        return None
    ident = next((f for f in pos if f.startswith("--")), pos[0])
    desc = _clean_desc(desc_lines)
    choices, optional = [], False
    m = re.fullmatch(r"[\[{]([^\]}]+)[\]}]", metavar)
    if m:
        choices = [c for c in re.split(r"[|,]", m.group(1)) if c]
        optional = metavar.startswith("[")
    elif "," in metavar and "." not in metavar and re.fullmatch(r"[\w+\-,]+", metavar):
        choices = metavar.split(",")
    if not choices:
        mv = re.search(r"allowed values:\s*([\w+\-, ]+?)(?:\s*\(|$)", desc)
        if mv:
            choices = [c.strip() for c in mv.group(1).split(",") if c.strip()]
    if not choices:
        choices = re.findall(r"(?:^| )- ([\w+\-]+):", " " + " ".join(desc_lines))
    dm = re.search(r"default:\s*([^)]+?)\)", desc)
    default = dm.group(1).strip() if dm else ""
    if neg:
        kind = "pair"
    elif not metavar:
        kind = "bool"
    elif "," in metavar and not m and choices:
        kind = "multi"
    elif choices:
        kind = "choice"
    elif metavar in ("N", "SEED", "INDEX") or re.fullmatch(r"<[\d.]+>", metavar):
        kind = "number"
    else:
        kind = "text"
    return {"id": ident, "flags": pos, "neg": neg, "kind": kind, "metavar": metavar, "choices": choices,
            "optional": optional, "default": default[:40], "desc": desc, "section": section}


def args_schema(bin_path):
    """Every llama-server option, parsed from its own --help, as structured data."""
    def build():
        if not bin_path or not os.path.isfile(bin_path):
            return None
        code, out = run([bin_path, "--help"], 20)
        lines, entries, section, i = out.splitlines(), [], "common", 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r"^-{3,}\s*(.+?)\s*-{3,}$", line)
            if m:
                section = m.group(1).replace(" params", "").strip()
                i += 1
                continue
            if not line.startswith("-"):
                i += 1
                continue
            norm = re.sub(r",\s+", ", ", line.strip())
            parts = re.split(r"\s{2,}", norm, maxsplit=1)
            desc = [parts[1]] if len(parts) > 1 else []
            i += 1
            while i < len(lines) and lines[i].startswith(" " * 10):
                desc.append(lines[i].strip())
                i += 1
            e = _make_entry(parts[0], desc, section)
            if e:
                entries.append(e)
        return entries or None
    key = f"schema:{bin_path}:{os.path.getmtime(bin_path) if bin_path and os.path.isfile(bin_path) else 0}"
    return cached(key, 3600, build)


def parse_args(text, bin_path):
    """Split an argument string into values for known options plus whatever is left over."""
    schema = args_schema(bin_path)
    toks, open_quote = lem_split(text)
    if open_quote or not schema:
        return {"values": {}, "custom": text or "", "known": bool(schema) and not open_quote}
    if not schema:
        return {"values": {}, "custom": text or "", "known": False}
    alias = {}
    for e in schema:
        for f in e["flags"]:
            alias[f] = (e, False)
        for f in e["neg"]:
            alias[f] = (e, True)
    values, custom, i = {}, [], 0
    isflag = lambda t: t.startswith("-") and not re.fullmatch(r"-\d+(\.\d+)?", t)
    while i < len(toks):
        t = toks[i]
        flag, eq, inline = t.partition("=")
        hit = alias.get(flag) if isflag(t) else None
        if not hit:
            custom.append(t)
            if isflag(t) and not eq and i + 1 < len(toks) and not isflag(toks[i + 1]):
                custom.append(toks[i + 1])
                i += 1
            i += 1
            continue
        e, is_neg = hit
        if e["kind"] in ("bool", "pair"):
            values[e["id"]] = ("off" if is_neg else "on") if e["kind"] == "pair" else True
            i += 1
            continue
        val = inline if eq else None
        if val is None and i + 1 < len(toks) and (not isflag(toks[i + 1])):
            val = toks[i + 1]
            i += 1
        if val is None:  # a bare optional-value flag such as "--flash-attn" means "on"
            val = "on" if "on" in e["choices"] else ""
        values[e["id"]] = val.split(",") if e["kind"] == "multi" else val
        i += 1
    return {"values": values, "custom": " ".join(q or t for t in custom for q in [lem_quote(t)]), "known": True}


def check_args(text, bin_path):
    toks, open_quote = lem_split(text)
    if open_quote:
        return {"error": "A quote is never closed.", "problems": [], "verified": False}
    flags = known_flags(bin_path)
    problems, seen = [], set()
    for t in toks:
        if not t.startswith("-") or re.fullmatch(r"-\d+(\.\d+)?", t):
            continue
        flag = t.split("=", 1)[0]
        if flag in seen:
            continue
        seen.add(flag)
        if flag in MANAGED_FLAGS:
            problems.append({"flag": flag, "level": "error",
                             "message": f"Lemonade sets {MANAGED_FLAGS[flag]} itself and rejects this option."})
        elif flags is not None and flag not in flags:
            problems.append({"flag": flag, "level": "warn",
                             "message": FOREIGN_HINTS.get(flag, "This llama-server build doesn't list this option, so loading would probably fail.")})
    return {"problems": problems, "verified": flags is not None}
