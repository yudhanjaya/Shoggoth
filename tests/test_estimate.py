"""The memory estimate: what the conversation costs per model shape and cache type. Run: python3 -m unittest discover tests"""
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import gguf  # noqa: E402
import library  # noqa: E402
import modelmeta  # noqa: E402
import provbase  # noqa: E402
import store  # noqa: E402

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


class Provider:
    """Just enough of an engine for fit(): the rules live on provbase.Base."""
    id = "x"
    need_gb = provbase.Base.need_gb
    fit = provbase.Base.fit


GB = 1e9
TEN_GB = {"best_gpu_gb": 10, "vram_gb": 10, "ram_gb": 64, "gpus": ["card"]}
SMALL_BOX = {"best_gpu_gb": 8, "vram_gb": 8, "ram_gb": 16, "gpus": ["card"]}


def dense(size_gb, top=131072):
    """A 24-layer model with 4 key/value heads of 128 per layer."""
    return {"size_bytes": int(size_gb * GB), "context": top, "attn": {"layers": 24, "kv_heads": 4, "dk": 128, "dv": 128}, "ids": {"x": "m"}}


def experts(size_gb, share=0.9, top=262144):
    e = dense(size_gb, top)
    e.update(experts=128, expert_share=share, attn={"layers": 40, "kv_heads": 2, "dk": 128, "dv": 128})
    return e


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        s = store.load()
        s["chat"] = {"ctx": "auto"}
        store.save(s)
        modelmeta.update("m", cache_type="q8_0")                # what a llama.cpp load uses; the stand-in engine has no preset

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pick(self, e, machine, preset_id=None):
        return library.pick_context(e, machine, Provider(), preset_id)


class AutoContext(Isolated):
    def test_a_small_model_gets_everything_it_supports(self):
        r = self.pick(dense(2), TEN_GB)
        self.assertEqual((r["ctx"], r["ctx_auto"]), (131072, True))

    def test_a_big_model_gets_less(self):
        r = self.pick(dense(7), TEN_GB)
        self.assertIn(r["ctx"], (16384, 32768, 65536))
        self.assertTrue(r["ctx_why"])

    def test_it_is_the_largest_step_that_fits(self):
        e = dense(6)
        r = self.pick(e, TEN_GB)
        bigger = [c for c in (16384, 32768, 65536, 131072) if c > r["ctx"]]
        self.assertTrue(bigger)
        ent = dict(e, kv_ctx=bigger[0], kv_gb=gguf.kv_cache_gb(e["attn"], bigger[0], cache_type="q8_0"))
        self.assertNotEqual(Provider().fit(ent, TEN_GB)[0], "fits")

    def test_the_model_s_own_maximum_is_a_step(self):
        self.assertEqual(self.pick(dense(1, top=40960), TEN_GB)["ctx"], 40960)

    def test_nothing_fits_still_answers(self):
        self.assertEqual(self.pick(dense(30), SMALL_BOX)["ctx"], 16384)

    def test_a_number_chosen_in_settings_wins(self):
        s = store.load()
        s["chat"] = {"ctx": 8192}
        store.save(s)
        r = self.pick(dense(2), TEN_GB)
        self.assertEqual((r["ctx"], r["ctx_auto"]), (8192, False))

    def test_the_cache_is_priced_as_saved_for_the_model(self):
        r = self.pick(dense(2), TEN_GB)
        self.assertEqual(r["kv_gb"], gguf.kv_cache_gb(dense(2)["attn"], r["ctx"], cache_type="q8_0"))

    def test_a_measured_size_replaces_the_estimate_at_the_same_context(self):
        e = dict(dense(2), ids={"x": "m1"})
        library.record_measured("m1", 3.3, 16384)
        ent, _, _ = library._price(e, TEN_GB, Provider(), 16384, 0)
        self.assertEqual(Provider().need_gb(ent), 3.3)
        ent, _, _ = library._price(e, TEN_GB, Provider(), 32768, 0)
        self.assertNotEqual(Provider().need_gb(ent), 3.3)
        self.assertEqual(modelmeta.get("m1")["measured_gb"], 3.3)


class Presets(Isolated):
    def test_the_choice_is_stored_and_a_persons_own_size_is_kept(self):
        import llamacpp
        e = dict(dense(7), ids={"llamacpp": "m"})
        r = self.pick(e, TEN_GB, "m")
        self.assertEqual(llamacpp.read_presets()["m"]["ctx-size"], str(r["ctx"]))
        presets = llamacpp.read_presets()                        # the person sets another size in the model's preset: it wins
        presets["m"]["ctx-size"] = "12000"
        llamacpp.write_presets(presets, llamacpp.DEFAULTS)
        r2 = self.pick(e, TEN_GB, "m")
        self.assertEqual((r2["ctx"], r2["ctx_auto"]), (12000, False))
        self.assertEqual(llamacpp.read_presets()["m"]["ctx-size"], "12000")


class MoeOffload(unittest.TestCase):
    def test_too_big_for_the_card_but_experts_in_ram_runs_partly_on_the_processor(self):
        e = experts(20)
        self.assertEqual(Provider().fit(dict(e, kv_gb=1, kv_ctx=32768), TEN_GB)[0], "tight")        # as before without the offload
        fit, why = Provider().fit(dict(e, kv_gb=1, kv_ctx=32768, n_cpu_moe=40), TEN_GB)
        self.assertEqual(fit, "partial")
        self.assertIn("processor", why)

    def test_dense_models_ignore_n_cpu_moe(self):
        self.assertNotEqual(Provider().fit(dict(dense(20), n_cpu_moe=40, kv_gb=1), TEN_GB)[0], "partial")

    def test_ram_beyond_the_reserve_must_hold_the_experts(self):
        e = dict(experts(60), n_cpu_moe=40, kv_gb=1)
        self.assertNotEqual(Provider().fit(e, {"best_gpu_gb": 10, "vram_gb": 10, "ram_gb": 32, "gpus": ["c"]})[0], "partial")

    def test_a_card_that_cannot_hold_the_rest_is_not_partial(self):
        self.assertNotEqual(Provider().fit(dict(experts(20, share=0.2), n_cpu_moe=40, kv_gb=1), TEN_GB)[0], "partial")

    def test_fewer_layers_moved_means_less_on_the_processor(self):
        e = experts(20)
        self.assertAlmostEqual(provbase.moe_on_cpu_gb(dict(e, n_cpu_moe=20)), provbase.moe_on_cpu_gb(dict(e, n_cpu_moe=40)) / 2)


class MoeAuto(Isolated):
    def test_auto_offloads_when_nothing_fits_on_the_card(self):
        r = self.pick(experts(20), TEN_GB)
        self.assertTrue(r["moe_auto"])
        self.assertTrue(1 <= r["n_cpu_moe"] <= 40)
        self.assertIn("RAM", r["ctx_why"])

    def test_it_moves_no_more_layers_than_needed(self):
        e = experts(11)
        r = self.pick(e, TEN_GB)
        self.assertTrue(r["moe_auto"])
        if r["n_cpu_moe"] > 1:
            _, fit, _ = library._price(e, TEN_GB, Provider(), r["ctx"], r["n_cpu_moe"] - 1)
            self.assertNotEqual(fit, "fits")

    def test_a_model_that_fits_is_left_alone(self):
        r = self.pick(experts(4), TEN_GB)
        self.assertEqual((r["moe_auto"], r["n_cpu_moe"]), (False, 0))

    def test_no_offload_when_ram_is_short_too(self):
        self.assertFalse(self.pick(experts(200), {"best_gpu_gb": 10, "vram_gb": 10, "ram_gb": 16, "gpus": ["c"]})["moe_auto"])

    def test_the_setting_is_written_to_the_preset(self):
        import llamacpp
        r = self.pick(dict(experts(20), ids={"llamacpp": "big"}), TEN_GB, "big")
        self.assertEqual(llamacpp.read_presets()["big"].get("n-cpu-moe"), str(r["n_cpu_moe"]))


class ExpertBytes(unittest.TestCase):
    def test_reads_the_share_from_the_tensor_list(self):
        names = [("blk.0.attn_q.weight", 640), ("blk.0.ffn_gate_exps.weight", 3200), ("blk.0.ffn_down_exps.weight", 3200), ("token_embd.weight", 960)]
        off, infos = 0, b""
        for n, size in names:
            infos += struct.pack("<Q", len(n)) + n.encode() + struct.pack("<IQIQ", 1, size, 8, off)
            off += size
        blob = b"GGUF" + struct.pack("<IQQ", 3, len(names), 0) + infos
        blob += b"\0" * (-len(blob) % 32) + b"\0" * off
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "m.gguf")
            with open(path, "wb") as f:
                f.write(blob)
            self.assertEqual(gguf.expert_bytes(path), (6400, 8000))
            self.assertEqual(gguf.expert_bytes(os.path.join(d, "nope.gguf")), (0, 0))
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
