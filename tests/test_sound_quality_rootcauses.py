#!/usr/bin/env python3
"""v4.3 regression tests — root causes of poor render quality (boomy / inaudible master).

Covers the fixes of the "analyse qualité sonore" pass:
  1. autofix rule 6b reads `bands_pct` (not the absent nested `spectral_bands`) -> low-end trim fires;
  2. `gl.limiter` look-ahead is calibrated (config/gl_calibration.json, 5 ms) -> true peak contained;
  3. deterministic velocity humanization in `haps_to_tracks` (drum caps, lead floor);
  4. flat section energy arc is normalized in `enrich_blueprint`;
  5. `plugin_params.cutoff_hz` is clamped to the SSOT LPF floor (lead >= 9000 Hz);
  6. blueprint safety reports `cutoff_below_lpf_floor` as a warning.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


class TestSummaryBandsPct(unittest.TestCase):
    def test_reads_flat_bands_pct_first(self):
        from mix_config import summary_bands_pct
        s = {"bands_pct": {"sub": 10.45, "bass": 72.41}, "spectral_bands": {"sub": {"energy_pct": 99.0}}}
        self.assertEqual(summary_bands_pct(s), {"sub": 10.45, "bass": 72.41})

    def test_fallback_nested_spectral_bands(self):
        from mix_config import summary_bands_pct
        s = {"spectral_bands": {"sub": {"energy_pct": 5.0}, "bass": {"energy_pct": 7.5}}}
        self.assertEqual(summary_bands_pct(s), {"sub": 5.0, "bass": 7.5})

    def test_empty_summary(self):
        from mix_config import summary_bands_pct
        self.assertEqual(summary_bands_pct({}), {})


class TestLimiterLookahead(unittest.TestCase):
    def test_calibrated_lookahead_from_config(self):
        import gl_dsl
        self.assertEqual(gl_dsl.calibrated_lookahead_s(), 0.005)

    def test_limiter_master_uses_calibrated_lookahead_by_default(self):
        import warnings
        import gl_dsl
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lim = gl_dsl.limiter_master(gain_db=5.5, ceiling_db=-2.2)
        self.assertEqual(lim["Attack (look-ahead)"], 0.005)
        self.assertEqual(lim["0.2 dB safety"], 1)

    def test_explicit_lookahead_respected_and_clamped(self):
        import gl_dsl
        self.assertEqual(gl_dsl.limiter_master(0.0, -2.2, look_ahead_s=0.002)["Attack (look-ahead)"], 0.002)
        self.assertEqual(gl_dsl.limiter_master(0.0, -2.2, look_ahead_s=99.0)["Attack (look-ahead)"], 0.02)


class TestVelocityHumanization(unittest.TestCase):
    def _haps(self) -> dict:
        events_hat = [
            {"start": i * 0.125, "end": i * 0.125 + 0.1, "s": "hh"} for i in range(32)
        ]
        return {
            "schema_version": "4.3",
            "beats_per_bar": 4,
            "bars": 4,
            "seed": 2048,
            "meta": {"seed": 2048, "progression": ["Fm7"] * 4, "sections": []},
            "tracks": {
                "hihat": {"events": events_hat},
                "kick": {"events": [{"start": b, "end": b + 0.06, "s": "bd"} for b in (0.0, 1.0, 2.0, 3.0)]},
                "lead": {"events": [{"start": 0.0, "end": 2.0, "notes": [65], "gain": 0.56}]},
            },
        }

    def test_deterministic_and_humanized(self):
        from strudel_to_score_ir import haps_to_tracks
        t1, _, _ = haps_to_tracks(self._haps())
        t2, _, _ = haps_to_tracks(self._haps())
        self.assertEqual(t1, t2, "humanization must be deterministic for a fixed seed")
        hats = [n["velocity"] for n in t1["hihat"]]
        self.assertLessEqual(max(hats), 96, "hats must be capped below max velocity (machine-gun harshness)")
        self.assertGreater(len(set(hats)), 1, "hat velocities must vary (accent + jitter)")
        kicks = [n["velocity"] for n in t1["kick"]]
        self.assertLessEqual(max(kicks), 118)
        self.assertGreater(min(kicks), 20)
        leads = [n["velocity"] for n in t1["lead"]]
        self.assertGreaterEqual(min(leads), 72, "lead velocity floor keeps the lead audible over drums")


class TestBlueprintEnricherFixes(unittest.TestCase):
    def _bp(self) -> dict:
        return {
            "title": "t", "key": "F", "bpm": 136.0, "mix_profile": "trap_rap",
            "emotion_vector": {"valence": -0.65, "tension": 0.72, "energy": 0.75, "depth": 0.78},
            "structure": [
                {"section": "intro", "start_bar": 1, "bars": 4, "energy": 0.7},
                {"section": "verse", "start_bar": 5, "bars": 16, "energy": 0.7},
                {"section": "chorus", "start_bar": 21, "bars": 8, "energy": 0.7},
                {"section": "outro", "start_bar": 29, "bars": 4, "energy": 0.7},
            ],
            "tracks": [
                {"name": "lead", "role": "lead", "source_type": "gridline_native", "plugin": "gl.additive",
                 "plugin_params": {"cutoff_hz": 4200.0}},
            ],
        }

    def test_flat_energy_arc_normalized(self):
        from blueprint_enricher import enrich_blueprint
        bp, report = enrich_blueprint(self._bp())
        energies = [s["energy"] for s in bp["structure"]]
        self.assertGreater(max(energies) - min(energies), 0.1, "flat arc must become a real arc")
        by_name = {s["section"]: s["energy"] for s in bp["structure"]}
        self.assertGreater(by_name["chorus"], by_name["verse"])
        self.assertGreater(by_name["verse"], by_name["intro"])
        self.assertGreater(by_name["intro"], by_name["outro"])
        self.assertTrue(report["added"].get("structure_energy_arc_normalized"))

    def test_non_flat_arc_untouched(self):
        from blueprint_enricher import enrich_blueprint
        bp = self._bp()
        bp["structure"][0]["energy"] = 0.3
        bp["structure"][2]["energy"] = 0.95
        out, _ = enrich_blueprint(bp)
        self.assertEqual(out["structure"][0]["energy"], 0.3)
        self.assertEqual(out["structure"][2]["energy"], 0.95)

    def test_cutoff_clamped_to_lpf_floor(self):
        from blueprint_enricher import enrich_blueprint
        bp, report = enrich_blueprint(self._bp())
        lead = next(t for t in bp["tracks"] if t["name"] == "lead")
        self.assertGreaterEqual(lead["plugin_params"]["cutoff_hz"], 9000.0)
        self.assertTrue(report["added"].get("cutoff_lpf_floors_clamped"))


class TestBlueprintSafetyCutoffWarning(unittest.TestCase):
    def test_cutoff_below_floor_warns(self):
        from blueprint_safety import blueprint_musical_safety_issues
        bp = {
            "key": "F", "bpm": 136.0,
            "tracks": [
                {"name": "lead", "role": "lead", "source_type": "gridline_native", "plugin": "gl.additive",
                 "plugin_params": {"cutoff_hz": 4200.0}},
            ],
        }
        issues = blueprint_musical_safety_issues(bp)
        codes = [i.get("code") for i in issues]
        self.assertIn("cutoff_below_lpf_floor", codes)
        iss = next(i for i in issues if i.get("code") == "cutoff_below_lpf_floor")
        self.assertEqual(iss["severity"], "warning")
        self.assertEqual(iss["floor_hz"], 9000.0)


if __name__ == "__main__":
    unittest.main()
