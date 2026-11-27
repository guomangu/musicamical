#!/usr/bin/env python3
"""Tests du moteur de synthèse programmable (Chantier 2) : rendu NATIF à la hauteur exacte
(pas de pitch-shift de one-shot), politique de séparation WAV/Synth, et indexation unifiée
des presets SFZ via note de référence (`scripts/index_vst_presets.py`).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "mcp-servers", "audio-index"),
           os.path.join(ROOT, "mcp-servers", "ai-studio-prod")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import numpy as np  # noqa: E402

import synth_render as sr  # noqa: E402
import unified_indexer as ui  # noqa: E402
import index_vst_presets as ivp  # noqa: E402


class TestNativePitchRendering(unittest.TestCase):
    def test_octave_rendered_natively_not_shifted(self):
        # MIDI 60 vs 72 rendus séparément : la f0 mesurée doit doubler (rendu natif, ratio 2.0).
        sr_hz = 22050
        a60 = sr.render_note(60, 0.8, sr=sr_hz, timbre={"harmonics": 8, "attack_s": 0.002})
        a72 = sr.render_note(72, 0.8, sr=sr_hz, timbre={"harmonics": 8, "attack_s": 0.002})
        f60, c60 = ui.estimate_f0(a60, sr_hz)
        f72, c72 = ui.estimate_f0(a72, sr_hz)
        self.assertGreater(c60, 0.75)
        self.assertGreater(c72, 0.75)
        self.assertAlmostEqual(f72 / f60, 2.0, delta=0.05)
        self.assertAlmostEqual(f60, 261.63, delta=8.0)

    def test_render_notes_polyphony(self):
        notes = [(60, 0.0, 0.3, 100), (64, 0.2, 0.3, 90), (67, 0.4, 0.5, 100)]
        out = sr.render_notes(notes, sr=22050, timbre=sr.SynthEngine.default_timbre("keys"))
        self.assertEqual(out.dtype, np.float32)
        self.assertGreater(out.size, int(0.9 * 22050))
        self.assertLessEqual(float(np.max(np.abs(out))), 1.0)

    def test_render_role_returns_zero_shift(self):
        engine = sr.SynthEngine()
        out = engine.render_role("lead", [(69, 0.0, 0.5, 100)], source_type="sfz_instrument", sr=22050)
        self.assertEqual(out["semitone_shift"], 0.0)
        self.assertIn(out["backend"], ("builtin", "dawdreamer"))
        self.assertGreater(out["audio"].size, 0)


class TestRenderPolicy(unittest.TestCase):
    def test_tonal_role_forbids_wav_oneshot(self):
        with self.assertRaises(sr.PitchShiftForbidden):
            sr.check_render_policy("lead", "wav_sample", 0.0)
        with self.assertRaises(sr.PitchShiftForbidden):
            sr.check_render_policy("bass_808", "wav_sample", 0.0)

    def test_percussive_wav_tolerance_2_semitones(self):
        sr.check_render_policy("kick", "wav_sample", 2.0)   # accord kick : OK
        sr.check_render_policy("kick", "wav_sample", -1.5)  # OK
        with self.assertRaises(sr.PitchShiftForbidden):
            sr.check_render_policy("kick", "wav_sample", 3.0)
        with self.assertRaises(sr.PitchShiftForbidden):
            sr.check_render_policy("snare", "wav_sample", -5.0)

    def test_tonal_virtual_instruments_allowed(self):
        sr.check_render_policy("pad", "vst3_preset", 0.0)
        sr.check_render_policy("keys", "sfz_instrument", 0.0)

    def test_percussive_role_rejects_synth_engine(self):
        engine = sr.SynthEngine()
        with self.assertRaises(sr.PitchShiftForbidden):
            engine.render_role("kick", [(36, 0.0, 0.2, 127)], source_type="wav_sample", sr=22050)


class TestSfzPresetIndexing(unittest.TestCase):
    def test_parse_sfz_regions(self):
        with tempfile.TemporaryDirectory() as tmp:
            sfz = os.path.join(tmp, "mini.sfz")
            with open(sfz, "w", encoding="utf-8") as fh:
                fh.write('<region> sample=kick.wav lokey=0 hikey=127 pitch_keycenter=36\n'
                         '<region> sample=snare.wav lokey=38 hikey=127 pitch_keycenter=38\n')
            regions = ivp.parse_sfz_regions(sfz)
            self.assertEqual(len(regions), 2)
            self.assertEqual(regions[0]["pitch_keycenter"], "36")

    def test_index_sfz_bank_renders_reference_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            sfz = os.path.join(tmp, "mini_keys.sfz")
            with open(sfz, "w", encoding="utf-8") as fh:
                fh.write('<region> sample=x.wav lokey=0 hikey=127 pitch_keycenter=60\n')
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            engine = sr.SynthEngine()
            result = ivp.index_sfz_bank(conn, engine, sfz)
            self.assertIsNotNone(result)
            self.assertEqual(result["source_type"] if "source_type" in result else "sfz_instrument", "sfz_instrument")
            self.assertEqual(result["role_category"], "keys")
            self.assertEqual(result["reference_note_midi"], ivp.REFERENCE_NOTE_MID)
            # la note de référence C4 rendue nativement est détectée à MIDI 60
            self.assertEqual(result["detected_midi_note"], 60)
            self.assertGreaterEqual(result["pitch_confidence"], 0.75)
            self.assertAlmostEqual(result["lufs_integrated"], -18.0, delta=0.6)
            rows = ui.query_assets(conn, role_category="keys", source_type="sfz_instrument")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["preset_name"], "mini_keys")
            conn.close()

    def test_index_soundbanks_on_real_tree(self):
        # Les banques SFZ versionnées du dépôt sont indexables hors-ligne (backend builtin).
        with tempfile.TemporaryDirectory() as tmp:
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            result = ivp.index_soundbanks(conn, os.path.join(ROOT, "soundbanks"))
            self.assertGreaterEqual(result["indexed"], 3)  # headroom-piano (x4 sfz), jRhodes3c, merry-orks
            self.assertGreaterEqual(len(result["sfz_banks"]), 3)
            for a in result["assets"]:
                self.assertEqual(a["asset_id"].split("_")[0], "sfz")
            conn.close()


class TestExpressiveAutomationV2(unittest.TestCase):
    """Chantier 2 (spec v2.0 §2.1) : 3 couches d'automation expressive déterministes."""

    def test_velocity_cutoff_coupling(self):
        # u(n) = base + delta_max*(v-64)/64 : monotone en vélocité, clampé [0,1]
        self.assertAlmostEqual(sr.velocity_cutoff_factor(64), 0.5)
        self.assertGreater(sr.velocity_cutoff_factor(120), sr.velocity_cutoff_factor(64))
        self.assertGreater(sr.velocity_cutoff_factor(64), sr.velocity_cutoff_factor(10))
        self.assertGreater(sr.velocity_cutoff_factor(127), 0.99)
        self.assertLess(sr.velocity_cutoff_factor(1), 0.01)

    def test_macro_breathing_curve(self):
        curve = sr.macro_breathing_curve(16, "pad")
        self.assertEqual(len(curve), 16)
        self.assertTrue(all(0.85 - 1e-9 <= c <= 1.15 + 1e-9 for c in curve))
        self.assertAlmostEqual(sum(curve) / len(curve), 1.0, places=1)
        self.assertEqual(sr.macro_breathing_curve(8, "kick"), [1.0] * 8)  # kick non modulé

    def test_adaptive_release(self):
        # gap nul -> release minimal ; grand gap -> release de base ; monotone
        r0 = sr.adaptive_release_s(0.4, 0.0, 0.3)
        r1 = sr.adaptive_release_s(0.4, 0.05, 0.3)
        r2 = sr.adaptive_release_s(0.4, 2.0, 0.3)
        self.assertGreaterEqual(r1, r0)
        self.assertGreaterEqual(r2, r1)
        self.assertLessEqual(r2, 0.3 + 1e-9)
        self.assertGreaterEqual(r0, 0.03)

    def test_build_automation_contract(self):
        notes = [(60, 0.0, 0.4, 100), (64, 0.5, 0.4, 40), (67, 1.0, 0.8, 120)]
        auto = sr.build_automation(notes, 140.0, "lead", timbre=sr.SynthEngine.default_timbre("lead"))
        self.assertEqual(len(auto["per_note"]), 3)
        self.assertEqual(len(auto["cc74_curve"]), 3)
        self.assertEqual(auto["role"], "lead")
        self.assertEqual(auto["bpm"], 140.0)
        # vélocité 40 -> cutoff plus fermé que vélocité 120 (même rôle)
        self.assertLess(auto["per_note"][1]["cutoff_u"], auto["per_note"][2]["cutoff_u"])

    def test_velocity_produces_distinct_spectral_centroids(self):
        # Critère de validation spec §2 (étape 2) : deux notes de vélocités différentes
        # produisent des centroïdes spectraux distincts (accent plus brillant).
        sr_hz = 22050
        timbre = {"harmonics": 24, "rolloff": 0.8, "lowpass_hz": 3000.0,
                  "attack_s": 0.01, "decay_s": 0.2, "sustain": 0.75, "release_s": 0.15}
        soft = sr.render_notes([(72, 0.0, 0.5, 40)], sr=sr_hz, timbre=timbre, bpm=140.0, role="lead")
        hard = sr.render_notes([(72, 0.0, 0.5, 120)], sr=sr_hz, timbre=timbre, bpm=140.0, role="lead")
        c_soft = ui.spectral_features(soft.astype(np.float64), sr_hz)["spectral_centroid_hz"]
        c_hard = ui.spectral_features(hard.astype(np.float64), sr_hz)["spectral_centroid_hz"]
        self.assertGreater(c_hard, c_soft * 1.05,
                           f"accent vélocité 120 moins brillant que note douce : {c_hard} vs {c_soft}")

    def test_render_role_returns_automation_report(self):
        engine = sr.SynthEngine()
        out = engine.render_role("pad", [(60, 0.0, 1.0, 90), (64, 1.0, 1.0, 100)],
                                 source_type="sfz_instrument", sr=22050, bpm=120.0)
        self.assertIn("automation", out)
        self.assertEqual(out["automation"]["n_notes"], 2)
        self.assertEqual(out["automation"]["role"], "pad")


if __name__ == "__main__":
    unittest.main()
