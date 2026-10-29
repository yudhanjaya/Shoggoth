"""Reads what a GGUF model file says about itself, without loading it: architecture, quantization, native context, name.

Only the header is read (a few kilobytes), and reading stops at the tokenizer, which comes after everything wanted here.
Standard library only.
"""
import os
import re
import struct

MAX_READ = 256 * 1024 * 1024         # how far into the file the metadata may reach (big tokenizers are tens of megabytes)
MAX_KV = 8192

# llama.cpp's file_type numbers, as people know them.
FTYPE = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
         14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS",
         23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M",
         32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4"}
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_SHARD = re.compile(r"^(?P<stem>.*?)-(?P<i>\d{5})-of-(?P<n>\d{5})\.gguf$", re.I)


def shard_of(filename):
    """(stem, index, count) for `name-00002-of-00005.gguf`, or (stem, 1, 1) for a single file."""
    m = _SHARD.match(os.path.basename(filename))
    if m:
        return m.group("stem"), int(m.group("i")), int(m.group("n"))
    return re.sub(r"\.gguf$", "", os.path.basename(filename), flags=re.I), 1, 1


class _Reader:
    def __init__(self, f):
        self.f = f

    def take(self, n):
        b = self.f.read(n)
        if len(b) < n or self.f.tell() > MAX_READ:
            raise ValueError("header too long or cut short")
        return b

    def skip(self, n):
        self.f.seek(n, 1)
        if self.f.tell() > MAX_READ:
            raise ValueError("header too long")

    def skip_strings(self, n):
        """Step over an array of n length-prefixed strings (a tokenizer's words) in memory blocks: far faster than one read per word."""
        f, unpack_from, block = self.f, struct.unpack_from, 1 << 20
        data, pos = f.read(block), 0
        size = len(data)
        for _ in range(n):
            if size - pos < 8:
                data, pos = data[pos:] + f.read(block), 0
                size = len(data)
                if size < 8:
                    raise ValueError("cut short")
            ln = unpack_from("<Q", data, pos)[0]
            pos += 8
            if pos + ln <= size:
                pos += ln
            else:
                f.seek(pos + ln - size, 1)
                data, pos = f.read(block), 0
                size = len(data)
        f.seek(pos - size, 1)                                # hand back what was read ahead
        if f.tell() > MAX_READ:
            raise ValueError("header too long")

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self):
        n = self.unpack("<Q")
        if n > 1 << 20:
            raise ValueError("string too long")
        return self.take(n).decode("utf8", "replace")

    def value(self, t, keep=True):
        """The value, or None for an array (arrays are stepped over: only their length matters here)."""
        if t in _SCALARS:
            return self.unpack(_SCALARS[t])
        if t == 8:
            if keep:
                return self.string()
            self.skip(self.unpack("<Q"))
            return None
        if t == 9:
            et, n = self.unpack("<I"), self.unpack("<Q")
            if n > 1 << 24:
                raise ValueError("array too long")
            if et in _SCALARS:
                self.skip(n * struct.calcsize(_SCALARS[et]))
            elif et == 8:
                self.skip_strings(n)
            else:
                for _ in range(n):
                    self.value(et, keep=False)
            return None
        raise ValueError("unknown value type %d" % t)


_WANTED = (".context_length", ".block_count", ".embedding_length", ".attention.head_count", ".attention.head_count_kv", ".attention.key_length",
           ".attention.value_length", ".attention.sliding_window", ".attention.kv_lora_rank", ".attention.key_length_mla",
           ".rope.dimension_count", ".full_attention_interval")


def _attention(kv, arch):
    """What it takes to know how much memory the context costs: layers, how many hold a cache, and the size of a cache entry. {} when unknown."""
    g = lambda k, d=0: kv.get("%s.%s" % (arch, k), d)      # noqa: E731
    layers, heads = int(g("block_count") or 0), g("attention.head_count")
    if not layers or not heads:
        return {}
    heads = max(heads) if isinstance(heads, list) else int(heads)
    kvh = g("attention.head_count_kv") or heads
    kvh = max(kvh) if isinstance(kvh, list) else int(kvh)
    emb = int(g("embedding_length") or 0)
    dk = int(g("attention.key_length") or (emb // heads if emb else 0))
    dv = int(g("attention.value_length") or dk)
    out = {"layers": layers, "kv_heads": kvh, "dk": dk, "dv": dv}
    if g("attention.kv_lora_rank"):                          # multi-head latent attention keeps one small latent per token
        out["latent"] = int(g("attention.kv_lora_rank")) + int(g("rope.dimension_count") or 0)
    if g("full_attention_interval"):                         # hybrids with a recurrent part: only every Nth layer holds a cache
        out["full_every"] = int(g("full_attention_interval"))
    if g("attention.sliding_window"):
        out["window"] = int(g("attention.sliding_window"))
    return out


# Share of layers that look at the whole context when a model also uses a sliding window (the rest only keep the window).
_GLOBAL_SHARE = {"gemma3": 1 / 6, "gemma3n": 1 / 5, "gemma4": 1 / 6, "gemma2": 0.5, "gpt-oss": 0.5, "cohere2": 0.25, "llama4": 0.25}


def kv_cache_gb(attn, ctx, bytes_per=2):
    """Memory the key/value cache takes for `ctx` tokens at 16 bits, in GB. A guide, not a promise: it errs towards the full cost."""
    if not attn or not attn.get("layers") or not ctx:
        return 0.0
    layers = attn["layers"]
    if attn.get("full_every"):
        layers = max(1, layers // attn["full_every"])
    per_token = (attn["latent"] if attn.get("latent") else attn["kv_heads"] * (attn["dk"] + attn["dv"])) * bytes_per
    share = 1.0
    if attn.get("window") and ctx > attn["window"]:
        share = _GLOBAL_SHARE.get(attn.get("arch", ""), 0.5)
    window_tokens = min(ctx, attn.get("window") or ctx)
    tokens = ctx * share + window_tokens * (1 - share)
    return round(layers * per_token * tokens / 1e9, 2)


def read_header(path):
    """The facts a header gives, or None when the file isn't a readable GGUF.

    {architecture, name, quant, file_type, context, size_label, type, version, kv}"""
    out = {}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            r = _Reader(f)
            version = r.unpack("<I")
            r.unpack("<Q")                                   # tensor count
            nkv = min(r.unpack("<Q"), MAX_KV)
            tokenizer = False
            for _ in range(nkv):
                key = r.string()
                tokenizer = tokenizer or key.startswith("tokenizer.")
                wanted = key.startswith("general.") or key.endswith(_WANTED)
                v = r.value(r.unpack("<I"), keep=wanted)
                if wanted:
                    out[key] = v
                arch = out.get("general.architecture")
                if arch and "general.file_type" in out and "%s.context_length" % arch in out and "general.name" in out and (tokenizer or "%s.attention.head_count" % arch in out):
                    break                                    # found it all (the model's shape comes before the tokenizer)
    except (OSError, ValueError, struct.error):
        return {} if out else None
    arch = out.get("general.architecture") or ""
    ft = out.get("general.file_type")
    return {"attn": _attention(out, arch), "architecture": arch, "name": out.get("general.name") or "", "file_type": ft, "quant": FTYPE.get(ft, "") if isinstance(ft, int) else "",
            "context": int(out.get("%s.context_length" % arch) or 0), "size_label": out.get("general.size_label") or "",
            "type": out.get("general.type") or "model", "version": version, "kv": len(out)}


_NOT_CHAT = {"clip": "projector", "dflash": "drafter", "parakeet": "speech", "whisper": "speech", "moonshine": "speech", "bert": "embedding",
             "nomic-bert": "embedding", "jina-bert-v2": "embedding", "jina-bert-v3": "embedding", "modernbert": "embedding", "neo-bert": "embedding",
             "t5encoder": "embedding", "wavtokenizer-dec": "speech"}


def role_of(architecture, stem="", folder=""):
    """What a model file is for: chat (a language model to talk to), or projector, drafter, speech, embedding, support, unknown.

    Strata keeps shared pieces (n-gram tables and the like) beside its models; those are support files, not models to pick."""
    if not architecture:
        return "unknown"
    if architecture in _NOT_CHAT:
        return _NOT_CHAT[architecture]
    low = (stem + " " + os.path.basename(folder or "")).lower()
    if "shared" in low or "engram" in low:
        return "support"
    return "chat"
