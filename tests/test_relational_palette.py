#!/usr/bin/env python3
"""Tests de la sélection relationnelle de palette (Chantier 3, spec §3) :
`scripts/palette_selector.py` — assemblage séquentiel en 4 étapes contraint sur `samples.db`
+ score combiné S_palette = w1·(1-D_corridor) + w2·(1-M_spectral) + w3·P_timbre.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "mcp-servers", "audio-index")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import unified_indexer as ui  # noqa: E402
import palette_selector as ps  # noqa: E402

SR = 22050


def _write_wav(path: str, freq: float | None, dur: float = 0.4, seed: int = 5) -> None:
    n = int(dur * SR)
    if freq is None:
        rng = np.random.default_rng(seed)
        audio = (rng.standard_normal(n) * 0.2).astype(np.float32)
    else:
        t = np.arange(n) / SR
        audio = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(path, audio, SR)


def _insert(conn: sqlite3.Connection, asset_id: str, role: str, path: str, **m) -> None:
    defaults = dict(
        source_type="wav_sample", preset_name=None, is_pitched=0, detected_midi_note=None,
        pitch_confidence=0.0, min_safe_midi=21, max_safe_midi=108, duration_ms=400.0,
        log_attack_ms=1.0, lufs_integrated=-18.0, crest_factor_db=10.0, spectral_centroid_hz=1000.0,
        low_band_energy=0.25, low_mid_energy=0.25, high_mid_energy=0.4, air_band_energy=0.1,
        ac_warmth=50.0, ac_brightness=50.0, ac_roughness=20.0, ac_hardness=50.0,
        ac_depth=50.0, ac_boomy=30.0, ac_sharpness=30.0, embedding_blob=None,
    )
    defaults.update(m)
    row = {"asset_id": asset_id, "file_path": path, "role_category": role, **defaults}
    cols = ", ".join(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO sound_assets ({cols}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )
    conn.commit()


def _seed_db(tmp: str, kick_hz: float) -> str:
    """Base minimale cohérente : 1 kick (fichier réel à kick_hz), snare, hihat, 2 basses, pad, keys, 2 leads."""
    db = os.path.join(tmp, "samples.db")
    conn = ui.init_db(db)
    kick_p = os.path.join(tmp, "kick.wav")
    _write_wav(kick_p, kick_hz)
    _insert(conn, "kick1", "kick", kick_p, ac_hardness=80.0, crest_factor_db=15.0, ac_brightness=40.0,
            low_band_energy=0.7, low_mid_energy=0.2, spectral_centroid_hz=120.0)
    snare_p = os.path.join(tmp, "snare.wav")
    _write_wav(snare_p, None, seed=11)
    _insert(conn, "snare1", "snare", snare_p, spectral_centroid_hz=2500.0, ac_brightness=75.0,
            air_band_energy=0.3, high_mid_energy=0.5)
    hat_p = os.path.join(tmp, "hat.wav")
    _write_wav(hat_p, None, seed=13)
    _insert(conn, "hat1", "hihat", hat_p, air_band_energy=0.5, ac_roughness=10.0, spectral_centroid_hz=7000.0)
    # basses : warm (70-140 Hz) vs sub boomy (<50 Hz)
    bass_warm_p = os.path.join(tmp, "bass_warm.wav")
    _write_wav(bass_warm_p, 100.0)
    _insert(conn, "bass_warm", "bass_808", bass_warm_p, ac_warmth=70.0, ac_boomy=30.0, ac_roughness=15.0,
            low_mid_energy=0.5, low_band_energy=0.3, spectral_centroid_hz=150.0, is_pitched=1,
            detected_midi_note=36, pitch_confidence=0.9)
    bass_sub_p = os.path.join(tmp, "bass_sub.wav")
    _write_wav(bass_sub_p, 40.0)
    _insert(conn, "bass_sub", "bass_808", bass_sub_p, ac_warmth=50.0, ac_boomy=80.0, ac_roughness=10.0,
            low_band_energy=0.7, low_mid_energy=0.15, spectral_centroid_hz=60.0, is_pitched=1,
            detected_midi_note=28, pitch_confidence=0.9)
    # harmonie : pad conforme (attaque lente) vs keys à attaque rapide (exclu si bpm >= 120)
    pad_p = os.path.join(tmp, "pad.wav")
    _write_wav(pad_p, 440.0)
    _insert(conn, "pad1", "pad", pad_p, spectral_centroid_hz=800.0, ac_brightness=40.0, ac_roughness=20.0,
            ac_warmth=70.0, ac_depth=60.0, log_attack_ms=2.0)
    keys_p = os.path.join(tmp, "keys.wav")
    _write_wav(keys_p, 520.0)
    _insert(conn, "keys1", "keys", keys_p, spectral_centroid_hz=500.0, ac_brightness=45.0, ac_roughness=25.0,
            ac_warmth=60.0, log_attack_ms=0.5)
    # leads : brillant (centroïde >= 1.5x pad, brightness >= pad+15) vs terne
    lead_p = os.path.join(tmp, "lead.wav")
    _write_wav(lead_p, 880.0)
    _insert(conn, "lead_bright", "lead", lead_p, spectral_centroid_hz=3000.0, ac_brightness=80.0,
            ac_roughness=15.0, is_pitched=1, detected_midi_note=69, pitch_confidence=0.95,
            min_safe_midi=40, max_safe_midi=90)
    lead2_p = os.path.join(tmp, "lead2.wav")
    _write_wav(lead2_p, 700.0)
    _insert(conn, "lead_dim", "lead", lead2_p, spectral_centroid_hz=1300.0, ac_brightness=50.0,
            is_pitched=1, detected_midi_note=65, pitch_confidence=0.9)
    conn.close()
    return db


class TestRelationalPalette(unittest.TestCase):
    def test_deep_kick_selects_warm_bass(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed_db(tmp, kick_hz=45.0)  # f_kick < 55 Hz -> basse 70-140 Hz, ac_warmth > 60
            res = ps.select_palette(db, "trap_rap", bpm=140, key="C:minor")
            self.assertLess(res["f_kick_hz"], 55.0)
            self.assertEqual(res["palette"]["bass_808"], "bass_warm")
            self.assertEqual(res["palette"]["snare"], "snare1")
            self.assertEqual(res["palette"]["kick"], "kick1")
            # étape 3 : pad à attaque lente (bpm 140 -> dense), pas le keys rapide
            self.assertEqual(res["palette"]["pad"], "pad1")
            # étape 4 : lead brillant (centroïde 3000 >= 1.5*800, brightness 80 >= 40+15)
            self.assertEqual(res["palette"]["lead"], "lead_bright")
            self.assertEqual(res["lead_range"], [40, 90])
            # score combiné
            for k in ("S_palette", "D_corridor", "M_spectral", "P_timbre"):
                self.assertIn(k, res)
                self.assertGreaterEqual(res[k], 0.0)
                self.assertLessEqual(res[k], 1.0)
            self.assertTrue(res["low_interval_limit"]["ok"])

    def test_punchy_kick_selects_sub_bass(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed_db(tmp, kick_hz=65.0)  # f_kick >= 55 Hz -> sub < 50 Hz, ac_boomy > 65
            res = ps.select_palette(db, "trap_rap", bpm=140, key="C:minor")
            self.assertGreaterEqual(res["f_kick_hz"], 55.0)
            self.assertEqual(res["palette"]["bass_808"], "bass_sub")

    def test_spectral_overlap_and_corridor_distance(self):
        a = [0.5] + [0.5 / 23] * 23
        self.assertAlmostEqual(ps.spectral_overlap_m(a, a), 1.0, places=6)
        b = [0.0] * 23 + [1.0]
        self.assertLess(ps.spectral_overlap_m(a, b), 0.05)
        corridor = {"bark24": {"p10": [x * 0.9 for x in a], "p90": [min(1.0, x * 1.1) for x in a]}}
        self.assertAlmostEqual(ps.corridor_distance(a, corridor), 0.0, places=6)
        far = [1.0] + [0.0] * 23
        self.assertGreater(ps.corridor_distance(far, corridor), 0.4)
        self.assertLessEqual(ps.corridor_distance(far, corridor), 1.0)

    def test_low_interval_limits(self):
        ok, _ = ps.low_interval_limit_ok([36, 48, 60])  # octaves >= MIDI 36 : OK
        self.assertTrue(ok)
        ok, msg = ps.low_interval_limit_ok([47, 48])  # seconde mineure sous MIDI 60 : interdit
        self.assertFalse(ok)
        self.assertIn("seconde mineure", msg)
        ok, msg = ps.low_interval_limit_ok([50, 53])  # tierce mineure sous MIDI 52 : interdit
        self.assertFalse(ok)
        ok, _ = ps.low_interval_limit_ok([55, 58])  # tierce mineure au-dessus de 52 : OK
        self.assertTrue(ok)

    def test_pleasantness_index_bounds(self):
        rows = [{"ac_warmth": 90.0, "ac_depth": 80.0, "ac_roughness": 10.0, "ac_sharpness": 20.0},
                {"ac_warmth": 20.0, "ac_depth": 10.0, "ac_roughness": 90.0, "ac_sharpness": 95.0}]
        p = ps.pleasantness_index(rows)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)
        self.assertGreater(ps.pleasantness_index(rows[:1]), ps.pleasantness_index(rows[1:]))


if __name__ == "__main__":
    unittest.main()
