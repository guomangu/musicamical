"""v4.1 P4 — corridors externes (masters commerciaux) + proxy PQ sklearn + mutation_ratio_max.

1. `build_external_corridors` sur masters WAV synthétiques (3/profil × 2 profils) :
   provenance `external_pro_reference`, bark24 [P10,P50,P90] de 24 bandes sommant ~1,
   métriques agrégées, warnings si < min_masters ; `check_external_corridors` (CI) ;
2. `math_mix_solver.load_corridors` préfère le JSON externe quand il existe à côté
   (et renseigne `reference_provenance`) ;
3. `pq_proxy.PQProxy` : entraînement Ridge sur dataset synthétique (relation connue),
   predict < 10 ms et proche de la vérité, dégradation None sans modèle,
   `append_record` / `check_model` ;
4. `strudel_to_score_ir.MUTATION_RATIO_MAX == 0.25` et diagnostic ERR_OVER_REPAIRED.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

try:
    import numpy as np
    import soundfile as sf
    HAVE_AUDIO = True
except ImportError:
    HAVE_AUDIO = False

try:
    import sklearn  # noqa: F401
    HAVE_SKLEARN = True
except ImportError:
    HAVE_SKLEARN = False

import build_external_corridors as bec
import math_mix_solver as mms
import pq_proxy
import strudel_to_score_ir as bridge


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile requis")
class TestExternalCorridors(unittest.TestCase):
    def _write_master(self, path: str, sr: int = 22050, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)
        n = int(5.0 * sr)
        t = np.arange(n) / sr
        x = 0.2 * rng.standard_normal(n) + 0.15 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 55 * t)
        x = x / max(float(np.max(np.abs(x))), 1e-6) * 0.7
        stereo = np.stack([x, np.roll(x, 7)], axis=1)
        sf.write(path, stereo.astype(np.float32), sr)

    def test_build_and_check(self):
        with tempfile.TemporaryDirectory() as td:
            ext = os.path.join(td, "external_masters")
            for prof in ("trap_rap", "pop_rnb"):
                os.makedirs(os.path.join(ext, prof))
                for i in range(3):
                    self._write_master(os.path.join(ext, prof, f"master{i}.wav"), seed=i + 1)
            doc = bec.build_external_corridors(ext, min_masters=3)
            self.assertEqual(doc["provenance"], "external_pro_reference")
            self.assertEqual(set(doc["profiles"].keys()), {"trap_rap", "pop_rnb"})
            for prof, entry in doc["profiles"].items():
                self.assertEqual(entry["provenance"], "external_pro_reference")
                self.assertEqual(entry["sample_count"], 3)
                p50 = entry["bark24"]["p50"]
                self.assertEqual(len(p50), 24)
                self.assertAlmostEqual(sum(p50), 1.0, delta=0.01)
                for k in ("lufs", "lra_lu", "crest_factor_db", "low_end_share", "air_share"):
                    self.assertIn(k, entry["metrics"])
                    self.assertIn("p50", entry["metrics"][k])
            self.assertTrue(any("jazz_acoustic" in w for w in doc["warnings"]))
            out = os.path.join(td, "corridors_external.json")
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            self.assertEqual(bec.check_external_corridors(out), [])
            self.assertTrue(bec.check_external_corridors(os.path.join(td, "nope.json")))

    def test_load_corridors_prefers_external(self):
        with tempfile.TemporaryDirectory() as td:
            base = os.path.join(td, "spectral_corridors.json")
            ext = os.path.join(td, "spectral_corridors_external.json")
            bark = [1.0 / 24.0] * 24
            with open(base, "w", encoding="utf-8") as fh:
                json.dump({"profiles": {"trap_rap": {"provenance": "internal_bootstrap",
                                                      "bark24": {"p10": bark, "p50": bark, "p90": bark},
                                                      "metrics": {}}}}, fh)
            with open(ext, "w", encoding="utf-8") as fh:
                ext_bark = [0.5 / 24.0] * 24 + [0.5] + [0.0] * 0  # placeholder, replaced below
                ext_bark = [1.0 / 24.0] * 23 + [1.0 - 23.0 / 24.0]
                json.dump({"provenance": "external_pro_reference",
                           "profiles": {"trap_rap": {"provenance": "external_pro_reference",
                                                      "bark24": {"p10": ext_bark, "p50": ext_bark, "p90": ext_bark},
                                                      "metrics": {"lufs": {"p50": -9.0}}}}}, fh)
            p10, p50, p90, metrics = mms.load_corridors(base, "trap_rap", 0.0)
            self.assertEqual(metrics.get("reference_provenance"), "external_pro_reference")
            self.assertAlmostEqual(float(p50[-1]), 1.0 - 23.0 / 24.0, places=6)
            # sans fichier externe -> provenance du fichier de base
            os.remove(ext)
            _, _, _, metrics2 = mms.load_corridors(base, "trap_rap", 0.0)
            self.assertEqual(metrics2.get("reference_provenance"), "internal_bootstrap")


@unittest.skipUnless(HAVE_SKLEARN and HAVE_AUDIO, "sklearn requis")
class TestPQProxy(unittest.TestCase):
    def _dataset(self, td: str, n: int = 30) -> str:
        path = os.path.join(td, "pq_dataset.jsonl")
        rng = np.random.default_rng(11)
        with open(path, "w", encoding="utf-8") as fh:
            for i in range(n):
                feats = {k: float(rng.uniform(-1, 1)) for k in pq_proxy.FEATURE_KEYS}
                feats["lufs"] = float(rng.uniform(-16, -8))
                feats["corridor_score"] = float(rng.uniform(0.5, 1.0))
                feats["mutation_ratio"] = float(rng.uniform(0.0, 0.25))
                pq = 8.2 + 0.15 * feats["corridor_score"] - 2.0 * feats["mutation_ratio"] + 0.05 * (feats["lufs"] + 11.0)
                fh.write(json.dumps({"features": feats, "pq": round(pq, 3), "project": f"p{i}"}) + "\n")
        return path

    def test_train_predict_fast_and_close(self):
        with tempfile.TemporaryDirectory() as td:
            ds = self._dataset(td)
            proxy = pq_proxy.PQProxy.train(ds)
            self.assertTrue(proxy.trained)
            self.assertEqual(proxy.n_samples, 30)
            feats = {k: 0.0 for k in pq_proxy.FEATURE_KEYS}
            feats["corridor_score"] = 0.9
            feats["mutation_ratio"] = 0.05
            feats["lufs"] = -11.0
            t0 = time.perf_counter()
            val = proxy.predict(feats)
            ms = (time.perf_counter() - t0) * 1000.0
            self.assertIsNotNone(val)
            self.assertLess(ms, 10.0)
            self.assertAlmostEqual(val, 8.2 + 0.15 * 0.9 - 2.0 * 0.05, delta=0.35)
            model_path = os.path.join(td, "pq_proxy.joblib")
            proxy.save(model_path)
            loaded = pq_proxy.PQProxy.load(model_path)
            self.assertEqual(loaded.predict(feats), val)
            self.assertEqual(pq_proxy.check_model(model_path), [])

    def test_degrades_none_without_model(self):
        proxy = pq_proxy.PQProxy()  # non entraîné
        self.assertIsNone(proxy.predict({"lufs": -11.0}))
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(pq_proxy.PQProxy.load(os.path.join(td, "nope.joblib")).predict({}))
            ds = os.path.join(td, "empty.jsonl")
            self.assertFalse(pq_proxy.PQProxy.train(ds).trained)
            self.assertTrue(pq_proxy.check_model(os.path.join(td, "nope.joblib")))

    def test_append_record(self):
        with tempfile.TemporaryDirectory() as td:
            ds = os.path.join(td, "pq_dataset.jsonl")
            n = pq_proxy.append_record(ds, {"lufs": -11.0}, 8.05, "song1")
            self.assertEqual(n, 1)
            rows = pq_proxy.load_dataset(ds)
            self.assertEqual(rows[0][1], 8.05)
            self.assertEqual(rows[0][2], "song1")
            self.assertEqual(len(rows[0][0]), len(pq_proxy.FEATURE_KEYS))


class TestMutationBudget(unittest.TestCase):
    def test_mutation_ratio_max_and_diagnostic(self):
        self.assertAlmostEqual(bridge.MUTATION_RATIO_MAX, 0.25)
        self.assertTrue(hasattr(bridge, "apply_score_ir_patch"))
        src = open(os.path.join(SCRIPTS, "strudel_to_score_ir.py"), encoding="utf-8").read()
        self.assertIn("ERR_OVER_REPAIRED", src)


if __name__ == "__main__":
    unittest.main()
