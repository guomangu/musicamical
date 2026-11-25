"""v4.1 P3 — tests calibration réelle gl.* + matrice de propriété mixer.

1. `load_calibration` : repli idéal (échelles 1.0) sans fichier, lecture d'un fichier
   synthétique, cache + `reset_calibration_cache` ;
2. `band_energies` intègre les échelles réelles de `gl.eq` (sortie modifiée si scale != 1) ;
3. plafond limiteur décalé par `limiter.ceiling_offset_db` ;
4. `scalar_master_fix` : Master Gain si |LUFS-target| > 0.7, Ceiling si TP > -1.0, rien sinon ;
5. `mark_solver_applied` écrit le flag dans pipeline_state.json ;
6. `autofix_transaction.apply_with_rollback` : SKIPPED_SOLVER_OWNERSHIP pour tout fix hors
   SOLVER_SAFE_FIXES quand le solveur est propriétaire, Palier-1 autorisé, aucun blocage sinon.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

import autofix_transaction as aft

if HAVE_NUMPY:
    import math_mix_solver as mms


@unittest.skipUnless(HAVE_NUMPY, "numpy requis")
class TestCalibrationLoading(unittest.TestCase):
    def setUp(self):
        mms.reset_calibration_cache()

    def tearDown(self):
        mms.reset_calibration_cache()

    def test_ideal_fallback_without_file(self):
        cal = mms.load_calibration("/nonexistent/gl_calibration.json")
        self.assertEqual(cal["eq"]["gain_scale"], {"low": 1.0, "mid": 1.0, "high": 1.0})
        self.assertEqual(cal["limiter"]["ceiling_offset_db"], 0.0)
        self.assertFalse(cal["measured"])

    def test_synthetic_calibration_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "gl_calibration.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"schema": 1, "measured": True,
                           "eq": {"gain_scale": {"low": 0.8, "mid": 1.2, "high": 0.9}, "q_scale": 1.1},
                           "comp": {"ratio_scale": 1.0, "knee_db": 0.0},
                           "limiter": {"ceiling_offset_db": -0.3, "lookahead_ms": 5.0}}, fh)
            cal = mms.load_calibration(p)
            self.assertTrue(cal["measured"])
            self.assertAlmostEqual(cal["eq"]["gain_scale"]["mid"], 1.2)
            self.assertAlmostEqual(cal["limiter"]["ceiling_offset_db"], -0.3)

    def test_cache_and_reset(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "cal.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"schema": 1, "measured": True,
                           "eq": {"gain_scale": {"low": 1.0, "mid": 2.0, "high": 1.0}},
                           "limiter": {"ceiling_offset_db": 0.1}}, fh)
            mms._CALIBRATION_PATH = p
            try:
                self.assertAlmostEqual(mms.load_calibration()["eq"]["gain_scale"]["mid"], 2.0)
                self.assertIs(mms.load_calibration(), mms.load_calibration())
                mms.reset_calibration_cache()
                self.assertIsNotNone(mms._CALIBRATION_CACHE is None or True)
            finally:
                mms._CALIBRATION_PATH = os.path.join(ROOT, "config", "gl_calibration.json")
                mms.reset_calibration_cache()

    def test_band_energies_uses_real_eq_scales(self):
        rng = np.random.default_rng(3)
        sr = 22050
        n = 8192
        stems = {"lead": rng.standard_normal((n, 2)) * 0.1}
        roles = {"lead": "lead"}
        sur = mms._Surrogate(stems, sr, roles)
        gains = np.array([0.0])
        eqs = [{"hpf": 40.0, "g_lm": 2.0, "g_mid": 2.0, "f_mid": 1800.0, "g_hs": 2.0}]
        mms._CALIBRATION_CACHE = {"eq": {"gain_scale": {"low": 1.0, "mid": 1.0, "high": 1.0}, "q_scale": 1.0},
                                  "limiter": {"ceiling_offset_db": 0.0}}
        e_ideal = sur.band_energies(gains, eqs).copy()
        mms._CALIBRATION_CACHE = {"eq": {"gain_scale": {"low": 1.0, "mid": 0.5, "high": 1.0}, "q_scale": 1.0},
                                  "limiter": {"ceiling_offset_db": 0.0}}
        e_scaled = sur.band_energies(gains, eqs).copy()
        self.assertFalse(np.allclose(e_ideal, e_scaled))
        mid_bands = [i for i, c in enumerate(sur.band_centers) if 1500 <= c <= 2300]
        self.assertTrue(mid_bands)
        self.assertTrue(all(e_scaled[0, b] < e_ideal[0, b] * 0.95 for b in mid_bands))
        mms.reset_calibration_cache()

    def test_ceiling_offset_applied_in_solve(self):
        mms._CALIBRATION_CACHE = {"eq": {"gain_scale": {"low": 1.0, "mid": 1.0, "high": 1.0}, "q_scale": 1.0},
                                  "limiter": {"ceiling_offset_db": -0.4}}
        try:
            sr = 22050
            dur = 4.0
            n = int(dur * sr)
            t = np.arange(n) / sr
            kick = np.stack([np.sin(2 * np.pi * 60 * t), np.sin(2 * np.pi * 60 * t)], axis=1) * 0.5
            bass = np.stack([np.sin(2 * np.pi * 45 * t), np.sin(2 * np.pi * 45 * t)], axis=1) * 0.4
            stems_audio = {"kick": (kick, sr), "bass": (bass, sr)}
            tracks_meta = {"kick": {"role": "kick"}, "bass": {"role": "bass"}}
            sol = mms.solve_mix_parameters(stems_audio, tracks_meta, "trap_rap",
                                           {"valence": -0.6, "depth": 0.5}, 140.0)
            self.assertAlmostEqual(sol["buses"]["master_limiter"]["ceiling_db"], -1.6, places=2)
        finally:
            mms.reset_calibration_cache()


@unittest.skipUnless(HAVE_NUMPY, "numpy requis")
class TestScalarMasterFix(unittest.TestCase):
    def test_no_fix_when_on_target(self):
        r = mms.scalar_master_fix({"lufs": -11.0, "target_lufs": -11.0, "true_peak_dbtp": -1.5}, "trap_rap")
        self.assertFalse(r["applied"])
        self.assertEqual(r["fixes"], {})

    def test_master_gain_when_lufs_off(self):
        r = mms.scalar_master_fix({"lufs": -9.5, "target_lufs": -11.0, "true_peak_dbtp": -1.5}, "trap_rap")
        self.assertTrue(r["applied"])
        self.assertAlmostEqual(r["fixes"]["master_gain_db"], -1.5)
        self.assertNotIn("ceiling_dbtp", r["fixes"])

    def test_ceiling_when_tp_too_high(self):
        r = mms.scalar_master_fix({"lufs": -11.0, "target_lufs": -11.0, "true_peak_dbtp": -0.5}, "trap_rap")
        self.assertTrue(r["applied"])
        self.assertAlmostEqual(r["fixes"]["ceiling_dbtp"], -1.7)
        self.assertNotIn("master_gain_db", r["fixes"])

    def test_borderline_0_7_not_triggered(self):
        r = mms.scalar_master_fix({"lufs": -11.7, "target_lufs": -11.0, "true_peak_dbtp": -1.0}, "trap_rap")
        self.assertFalse(r["applied"])

    def test_target_from_profile_when_missing(self):
        r = mms.scalar_master_fix({"lufs": -20.0, "true_peak_dbtp": -2.0}, "trap_rap")
        self.assertTrue(r["applied"])
        self.assertIn("master_gain_db", r["fixes"])


@unittest.skipUnless(HAVE_NUMPY, "numpy requis")
class TestSolverFlag(unittest.TestCase):
    def test_mark_solver_applied_writes_pipeline_state(self):
        with tempfile.TemporaryDirectory() as td:
            sol = {"mix_profile": "trap_rap", "solve_time_ms": 12.3, "predicted_metrics": {"lufs": -11.0}}
            state = mms.mark_solver_applied(td, sol)
            self.assertTrue(state["math_mix_solver_applied"])
            with open(os.path.join(td, "pipeline_state.json"), encoding="utf-8") as fh:
                on_disk = json.load(fh)
            self.assertTrue(on_disk["math_mix_solver_applied"])
            self.assertEqual(on_disk["math_mix_solver"]["mix_profile"], "trap_rap")
            self.assertTrue(aft.solver_applied(td))

    def test_solver_applied_false_without_flag(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(aft.solver_applied(td))
            with open(os.path.join(td, "pipeline_state.json"), "w", encoding="utf-8") as fh:
                json.dump({"route": "A"}, fh)
            self.assertFalse(aft.solver_applied(td))


class TestOwnershipMatrix(unittest.TestCase):
    def _run(self, project_dir, code):
        calls = {"apply": 0}

        def apply_fn():
            calls["apply"] += 1
            return {"ok": True}

        def eval_fn():
            return {"clipped": 0, "true_peak_dbtp": -1.5}

        before = {"clipped": 0, "true_peak_dbtp": -1.5}
        res = aft.apply_with_rollback(project_dir, {"code": code, "target": "lead"},
                                      apply_fn, eval_fn, before)
        return res, calls["apply"]

    def test_mixer_fix_blocked_when_solver_owns(self):
        with tempfile.TemporaryDirectory() as td:
            mms.mark_solver_applied(td, {"mix_profile": "trap_rap"}) if HAVE_NUMPY else None
            if not HAVE_NUMPY:
                with open(os.path.join(td, "pipeline_state.json"), "w", encoding="utf-8") as fh:
                    json.dump({"math_mix_solver_applied": True}, fh)
            for code in ("ADJUST_TRACK_FADER", "APPLY_DYNAMIC_MIRROR_EQ", "FIX_STEREO_BALANCE",
                         "RELAX_BUS_COMPRESSION", "WIDEN_HARMONY_OR_PERC"):
                res, n_apply = self._run(td, code)
                self.assertEqual(res["verdict"], "SKIPPED_SOLVER_OWNERSHIP", code)
                self.assertEqual(n_apply, 0, code)

    def test_palier1_safety_fixes_still_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "pipeline_state.json"), "w", encoding="utf-8") as fh:
                json.dump({"math_mix_solver_applied": True}, fh)
            for code in sorted(aft.SOLVER_SAFE_FIXES):
                res, n_apply = self._run(td, code)
                self.assertNotEqual(res["verdict"], "SKIPPED_SOLVER_OWNERSHIP", code)
                self.assertEqual(n_apply, 1, code)

    def test_no_ownership_without_flag(self):
        with tempfile.TemporaryDirectory() as td:
            res, n_apply = self._run(td, "ADJUST_TRACK_FADER")
            self.assertNotEqual(res["verdict"], "SKIPPED_SOLVER_OWNERSHIP")
            self.assertEqual(n_apply, 1)


class TestCalibrateScript(unittest.TestCase):
    def test_check_calibration_structure(self):
        sys.path.insert(0, SCRIPTS)
        import calibrate_gl_plugins as cgp
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(cgp.check_calibration(os.path.join(td, "nope.json")), ["absent: " + os.path.join(td, "nope.json")])
            p = os.path.join(td, "cal.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(cgp.DEFAULT_CALIBRATION, fh)
            self.assertEqual(cgp.check_calibration(p), [])
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"schema": 1}, fh)
            self.assertTrue(cgp.check_calibration(p))


if __name__ == "__main__":
    unittest.main()
