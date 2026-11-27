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


class TestArchetypeSustainAndOrganicDark(unittest.TestCase):
    """Round 2: chords/pad sustain (release) + dark mode keeps organic presets (no electric wavetable/additive)."""

    def test_chords_pad_release_floors(self):
        import gl_dsl
        for valence in (0.0, -0.6):
            ch = gl_dsl.resolve_archetype("trap_rap", "chords", valence=valence)
            pad = gl_dsl.resolve_archetype("trap_rap", "pad", valence=valence)
            lead = gl_dsl.resolve_archetype("trap_rap", "lead", valence=valence)
            self.assertGreaterEqual(ch["params"]["release_ms"], 800)
            self.assertGreaterEqual(pad["params"]["release_ms"], 1000)
            self.assertGreaterEqual(lead["params"]["release_ms"], 300)

    def test_dark_keeps_organic_plugins(self):
        import gl_dsl
        pad = gl_dsl.resolve_archetype("trap_rap", "pad", valence=-0.6)
        lead = gl_dsl.resolve_archetype("trap_rap", "lead", valence=-0.6)
        counter = gl_dsl.resolve_archetype("trap_rap", "counter", valence=-0.6)
        self.assertEqual(pad["plugin"], "gl.analog")     # pas de gl.wavetable "Formant Choir" électrique
        self.assertEqual(pad.get("preset"), "Velvet Pad")
        self.assertEqual(lead["plugin"], "gl.analog")    # pas de gl.additive "Masked lead"
        self.assertEqual(lead.get("preset"), "Glass Pluck")
        self.assertNotIn("Bell", str(counter.get("preset") or ""))
        self.assertLessEqual(lead["params"]["cutoff_hz"], 9000.0)


class TestBellAndElectronicPolicyDark(unittest.TestCase):
    """Round 2: en mode sombre, cloche inharmonique et noms 'stab/metallic' exclus du lead."""

    def test_bell_forbidden_on_lead_in_dark(self):
        from sample_fit_guard import nature_verdict, candidate_policy
        v_dark, _, _ = nature_verdict("lead_melody", "inharmonic_bell_or_fx", 0.06, valence=-0.55)
        self.assertEqual(v_dark, "forbidden")
        v_light, _, _ = nature_verdict("lead_melody", "inharmonic_bell_or_fx", 0.06, valence=0.0)
        self.assertEqual(v_light, "discouraged")
        pol = candidate_policy("lead_melody", {
            "filepath": "/x/synth-samples/Steel Stab One Shot_D.wav",
            "category": "synth", "sample_nature": "inharmonic_bell_or_fx", "inharmonicity": 0.065,
        }, valence=-0.55)
        self.assertFalse(pol["allowed"], "steel stab doit être exclu du lead en mode sombre")
        pol_light = candidate_policy("lead_melody", {
            "filepath": "/x/synth-samples/Steel Stab One Shot_D.wav",
            "category": "synth", "sample_nature": "inharmonic_bell_or_fx", "inharmonicity": 0.065,
        }, valence=0.0)
        self.assertTrue(pol_light["allowed"], "hors mode sombre la cloche reste jouable (pénalisée)")


class TestGainStagingChordsAudibility(unittest.TestCase):
    """Round 2: chords_pad assez haut dans le profil trap_rap (chords audibles)."""

    def test_trap_rap_chords_pad_target(self):
        from gain_staging_lib import ROLE_TARGET_OFFSETS_DB, ROLE_PRESENCE_FLOOR_DB, _effective_profile_offsets
        lo, target, hi = ROLE_TARGET_OFFSETS_DB["trap_rap"]["chords_pad"]
        self.assertGreaterEqual(target, -6.5, f"chords_pad target trop bas: {target} dB (chords inaudibles)")
        self.assertGreaterEqual(target, ROLE_PRESENCE_FLOOR_DB["chords_pad"])
        for prof in ("trap_rap", "pop_rnb", "lofi_chill", "classical_ambient"):
            _, _, offsets = _effective_profile_offsets(prof, prof != "classical_ambient", None)
            for role, fl in ROLE_PRESENCE_FLOOR_DB.items():
                self.assertGreaterEqual(offsets[role][1], fl, f"{prof}/{role}")


class TestSustainAndBleedRound2(unittest.TestCase):
    """Round 2: blueprint enricher release floors + allow_bleed des accords tenus."""

    def test_enforce_sustain_release_floors(self):
        from blueprint_enricher import enrich_blueprint
        bp = {
            "title": "t", "key": "F", "bpm": 136.0, "mix_profile": "trap_rap",
            "emotion_vector": {"valence": -0.55, "energy": 0.75},
            "structure": [{"section": "verse", "start_bar": 1, "bars": 8, "energy": 0.6},
                          {"section": "chorus", "start_bar": 9, "bars": 8, "energy": 0.9}],
            "tracks": [
                {"name": "chords", "role": "chords", "plugin": "gl.keys",
                 "plugin_params": {"cutoff_hz": 6500.0, "release_ms": 260.0}},
                {"name": "pad", "role": "chords", "plugin": "gl.analog",
                 "plugin_params": {"cutoff_hz": 5500.0, "release_ms": 600.0}},
            ],
        }
        out, report = enrich_blueprint(bp)
        tr = {t["name"]: t for t in out["tracks"]}
        self.assertGreaterEqual(tr["chords"]["plugin_params"]["release_ms"], 800.0)
        self.assertGreaterEqual(tr["pad"]["plugin_params"]["release_ms"], 1000.0)
        self.assertTrue(report["added"].get("sustain_release_floors_enforced"))

    def test_chord_notes_allow_bleed(self):
        from strudel_to_score_ir import build_score_ir_from_strudel
        haps = {
            "beats_per_bar": 4, "bars": 4, "seed": 4208,
            "meta": {"bpm": 136, "key": "F", "mode": "minor", "valence": -0.55,
                     "progression": ["Fm7", "Dbmaj7", "Bbm7", "Cm7"], "sections": []},
            "tracks": {
                "chords": {"events": [{"start": b, "end": b + 1.0, "chord": "Fm7", "gain": 0.6} for b in range(4)]},
                "lead": {"events": [{"start": 0.0, "end": 0.5, "notes": [65], "gain": 0.58}]},
            },
        }
        score, _info = build_score_ir_from_strudel(haps, None)
        chords = score.tracks["chords"].notes
        self.assertTrue(all(n.allow_bleed for n in chords), "les notes d'accords tenues doivent bleeder (allow_bleed)")
        self.assertTrue(all(n.duration_beats >= 3.9 for n in chords), "les accords doivent couvrir la mesure entière")
        lead = score.tracks["lead"].notes
        self.assertTrue(all(not n.allow_bleed for n in lead))


if __name__ == "__main__":
    unittest.main()
