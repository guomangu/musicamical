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


if __name__ == "__main__":
    unittest.main()
