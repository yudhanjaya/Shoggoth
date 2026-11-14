"""The memory estimate: what the conversation costs per model shape and cache type. Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import gguf  # noqa: E402

CTX = 32768


def attn_of(arch, **kv):
    return gguf._attention({"%s.%s" % (arch, k.replace("__", ".")): v for k, v in kv.items()}, arch)


class Hybrid(unittest.TestCase):
    def setUp(self):
        # LFM2 style: 16 layers, only 6 hold a cache; the others store 0
        self.kvs = [0, 0, 8, 0, 0, 8, 0, 0, 8, 0, 8, 0, 8, 0, 8, 0]
        self.a = attn_of("lfm2", block_count=16, embedding_length=2048, attention__head_count=32, attention__head_count_kv=self.kvs)

    def test_only_layers_with_a_cache_count(self):
        n = sum(1 for x in self.kvs if x)
        self.assertEqual(self.a["per_layer_kv"], n * 8 * 128)
        self.assertEqual(gguf.kv_cache_gb(self.a, CTX), round(n * 8 * 128 * 2 * CTX / 1e9, 2))

    def test_cheaper_than_treating_every_layer_as_full(self):
        every = {k: v for k, v in self.a.items() if k != "per_layer_kv"}
        self.assertLess(gguf.kv_cache_gb(self.a, CTX), gguf.kv_cache_gb(every, CTX))

    def test_full_every_is_not_applied_on_top(self):
        a = attn_of("lfm2", block_count=16, embedding_length=2048, attention__head_count=32, attention__head_count_kv=self.kvs, full_attention_interval=2)
        self.assertNotIn("full_every", a)
        self.assertEqual(gguf.kv_cache_gb(a, CTX), gguf.kv_cache_gb(self.a, CTX))

    def test_scalar_with_interval_still_divides_layers(self):
        a = attn_of("qwen35", block_count=40, embedding_length=4096, attention__head_count=16, attention__head_count_kv=4,
                    attention__key_length=256, attention__value_length=256, full_attention_interval=4)
        self.assertEqual(a["full_every"], 4)
        self.assertEqual(gguf.kv_cache_gb(a, CTX), round(10 * 4 * 512 * 2 * CTX / 1e9, 2))


class Windowed(unittest.TestCase):
    def test_gemma_style_window_saves_memory_past_the_window(self):
        a = attn_of("gemma3", block_count=48, embedding_length=3840, attention__head_count=16, attention__head_count_kv=8,
                    attention__key_length=256, attention__value_length=256, attention__sliding_window=1024)
        a["arch"] = "gemma3"
        full = dict(a, window=0)
        self.assertLess(gguf.kv_cache_gb(a, 131072), gguf.kv_cache_gb(full, 131072))
        self.assertEqual(gguf.kv_cache_gb(a, 512), gguf.kv_cache_gb(full, 512))          # inside the window nothing is saved


class Moe(unittest.TestCase):
    def test_scalar_kv_heads_unchanged(self):
        a = attn_of("qwen3moe", block_count=48, embedding_length=2048, attention__head_count=32, attention__head_count_kv=4, attention__key_length=128)
        self.assertEqual(a["kv_heads"], 4)
        self.assertNotIn("per_layer_kv", a)
        self.assertEqual(gguf.kv_cache_gb(a, CTX), round(48 * 4 * 256 * 2 * CTX / 1e9, 2))

    def test_unknown_shape_is_free(self):
        self.assertEqual(gguf.kv_cache_gb({}, CTX), 0.0)
        self.assertEqual(attn_of("x", block_count=0), {})


class CacheType(unittest.TestCase):
    a = {"layers": 48, "kv_heads": 8, "dk": 128, "dv": 128}

    def test_default_is_f16(self):
        self.assertEqual(gguf.kv_cache_gb(self.a, CTX), gguf.kv_cache_gb(self.a, CTX, cache_type="f16"))
        self.assertEqual(gguf.kv_cache_gb(self.a, CTX, cache_type="bf16"), gguf.kv_cache_gb(self.a, CTX))

    def test_block_sizes(self):
        self.assertEqual(gguf.CACHE_BYTES["q8_0"], 34 / 32)
        self.assertEqual(gguf.CACHE_BYTES["q4_0"], 18 / 32)
        self.assertEqual(gguf.CACHE_BYTES["q5_1"], 24 / 32)

    def test_smaller_types_cost_less(self):
        sizes = [gguf.kv_cache_gb(self.a, 131072, cache_type=t) for t in ("f16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0")]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertAlmostEqual(sizes[1] / sizes[0], 34 / 64, places=2)
        self.assertEqual(gguf.kv_cache_gb(self.a, 131072, cache_type="iq4_nl"), gguf.kv_cache_gb(self.a, 131072, cache_type="q4_0"))

    def test_unknown_name_falls_back(self):
        self.assertEqual(gguf.kv_cache_gb(self.a, CTX, cache_type="nope"), gguf.kv_cache_gb(self.a, CTX))

    def test_applies_to_hybrids(self):
        h = {"layers": 16, "kv_heads": 8, "dk": 128, "dv": 128, "per_layer_kv": 6 * 8 * 256}
        self.assertLess(gguf.kv_cache_gb(h, 131072, cache_type="q8_0"), gguf.kv_cache_gb(h, 131072))


if __name__ == "__main__":
    unittest.main()
