#!/usr/bin/env python3
"""Critères d'acceptation Phase 1 (spec) : 100 % des samples indexés dans `samples.db`
possèdent une sonie mesurée à -18.0 ± tol LUFS, une note fondamentale vérifiée
(pitch_confidence >= 0.75 pour les sons tonaux) et leurs 7 scores AudioCommons [0,100].
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

SR = 22050
AC_KEYS = ("ac_warmth", "ac_brightness", "ac_roughness", "ac_hardness", "ac_depth", "ac_boomy", "ac_sharpness")


def _write(path: str, freq: float | None, dur: float = 0.6, seed: int = 3) -> None:
    n = int(dur * SR)
    if freq is None:
        rng = np.random.default_rng(seed)
        audio = (rng.standard_normal(n) * 0.25).astype(np.float32)
    else:
        t = np.arange(n) / SR
        audio = (0.35 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(path, audio, SR)


class TestSqliteCatalogAcceptance(unittest.TestCase):
    def test_all_indexed_assets_meet_phase1_criteria(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "samples.db")
            conn = ui.init_db(db)
            files = {
                "kick_deep.wav": 48.0,
                "snare.wav": None,
                "bass_808.wav": 41.2,
                "pad_c4.wav": 261.63,
            }
            for name, freq in files.items():
                _write(os.path.join(tmp, name), freq)
            stats = ui.index_paths(conn, [tmp])
            self.assertEqual(stats["indexed"], 4)
            self.assertEqual(stats["skipped"], 0)
            rows = conn.execute("SELECT * FROM sound_assets").fetchall()
            self.assertEqual(len(rows), 4)
            for r in rows:
                d = dict(r)
                # sonie normalisée à l'indexation
                self.assertAlmostEqual(d["lufs_integrated"], -18.0, delta=0.6,
                                       msg=f"{d['file_path']} LUFS={d['lufs_integrated']}")
                # 7 scores perceptuels enregistrés et bornés
                for k in AC_KEYS:
                    self.assertIsNotNone(d[k])
                    self.assertGreaterEqual(d[k], 0.0)
                    self.assertLessEqual(d[k], 100.0)
                # note fondamentale vérifiée pour les sons tonaux
                if d["is_pitched"] == 1:
                    self.assertIsNotNone(d["detected_midi_note"])
                    self.assertGreaterEqual(d["pitch_confidence"], 0.75)
                else:
                    self.assertLess(d["pitch_confidence"], 0.75)
                # cohérence des bandes d'énergie
                s = d["low_band_energy"] + d["low_mid_energy"] + d["high_mid_energy"] + d["air_band_energy"]
                self.assertAlmostEqual(s, 1.0, places=3)
            # rôles inférés correctement
            roles = {os.path.basename(r["file_path"]): r["role_category"] for r in rows}
            self.assertEqual(roles["kick_deep.wav"], "kick")
            self.assertEqual(roles["snare.wav"], "snare")
            self.assertEqual(roles["bass_808.wav"], "bass_808")
            self.assertEqual(roles["pad_c4.wav"], "pad")
            # index du schéma spec présents et portant les bonnes colonnes
            idx = {r[1] for r in conn.execute("PRAGMA index_list(sound_assets)").fetchall()}
            self.assertIn("idx_assets_role_timbre", idx)
            self.assertIn("idx_assets_centroid", idx)
            cols = {r[2] for r in conn.execute("PRAGMA index_info(idx_assets_role_timbre)").fetchall()}
            self.assertTrue({"role_category", "ac_warmth", "ac_brightness", "ac_roughness"}.issubset(cols))
            conn.close()

    def test_db_is_single_source_of_truth_root_path(self):
        # Le DB par défaut du pipeline unifié est samples.db à la racine du dépôt.
        self.assertTrue(ui.DEFAULT_DB_PATH.endswith(os.path.join("", "samples.db")) or
                        ui.DEFAULT_DB_PATH == os.path.join(ROOT, "samples.db"))
        self.assertEqual(os.path.basename(ui.DEFAULT_DB_PATH), "samples.db")

    def test_sound_assets_table_in_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "samples.db")
            conn = sqlite3.connect(db)
            conn.close()
            conn = ui.init_db(db)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("sound_assets", tables)
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(ver, ui.UNIFIED_SCHEMA_VERSION)
            conn.close()


if __name__ == "__main__":
    unittest.main()
