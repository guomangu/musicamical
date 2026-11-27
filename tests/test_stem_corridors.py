#!/usr/bin/env python3
"""Tests des corridors spectraux PAR STEM (Chantier 4) : `spectral_corridors.json` schema 3.2
avec cibles drums/bass/other/vocals (bark24 + métriques + plages AudioCommons), séparation
Demucs optionnelle (`separate_stems_demucs` / `build_stem_corridors_from_masters`).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
ANALYZER_DIR = os.path.join(ROOT, "mcp-servers", "audio-analyzer")
for _d in (SCRIPTS_DIR, ANALYZER_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import bootstrap_golden_refs as bgr  # noqa: E402

CORRIDORS = os.path.join(ROOT, "golden_references", "spectral_corridors.json")


class TestStemCorridorsFile(unittest.TestCase):
    def test_schema_32_and_per_stem_targets(self):
        with open(CORRIDORS, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data.get("schema_version"), "3.2")
        profiles = data["profiles"]
        self.assertEqual(len(profiles), 7)
        for prof, entry in profiles.items():
            stems = entry.get("stems")
            self.assertIsNotNone(stems, f"{prof} sans stems")
            for stem in ("drums", "bass", "other", "vocals"):
                s = stems[stem]
                for k in ("p10", "p50", "p90"):
                    self.assertEqual(len(s["bark24"][k]), 24)
                    self.assertAlmostEqual(sum(s["bark24"][k]), 1.0, places=3)
                self.assertIn("spectral_centroid_hz", s["metrics"])
                c = s["metrics"]["spectral_centroid_hz"]
                self.assertLessEqual(c["p10"], c["p50"])
                self.assertLessEqual(c["p50"], c["p90"])
                self.assertIn("crest_factor_db", s["metrics"])
                self.assertIn("ac_targets", s)
                for ac in ("ac_warmth", "ac_brightness", "ac_roughness"):
                    if ac in s["ac_targets"]:
                        lo, hi = s["ac_targets"][ac]
                        self.assertLessEqual(lo, hi)
                        self.assertGreaterEqual(lo, 0.0)
                        self.assertLessEqual(hi, 100.0)
            # la basse doit cibler le grave, la batterie un centroïde bien plus haut
            self.assertLess(stems["bass"]["metrics"]["spectral_centroid_hz"]["p50"],
                            stems["drums"]["metrics"]["spectral_centroid_hz"]["p50"])

    def test_analytic_stem_targets_structure(self):
        targets = bgr._analytic_stem_targets("trap_rap")
        self.assertEqual(set(targets.keys()), {"drums", "bass", "other", "vocals"})
        for stem, s in targets.items():
            self.assertEqual(s["provenance"], "analytic_stem_prior")
            self.assertEqual(len(s["bark24"]["p50"]), 24)
            self.assertAlmostEqual(sum(s["bark24"]["p50"]), 1.0, places=3)


class TestDemucsOptional(unittest.TestCase):
    def test_separate_stems_graceful_without_demucs(self):
        try:
            import demucs  # noqa: F401
            import torch  # noqa: F401
            has = True
        except Exception:
            has = False
        with tempfile.TemporaryDirectory() as tmp:
            import numpy as np
            import soundfile as sf
            wav = os.path.join(tmp, "mix.wav")
            t = np.arange(int(0.5 * 22050)) / 22050
            sf.write(wav, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 22050)
            if not has:
                with self.assertRaises(RuntimeError) as ctx:
                    bgr.separate_stems_demucs(wav, os.path.join(tmp, "stems"))
                self.assertIn("demucs", str(ctx.exception).lower())
            else:
                out = bgr.separate_stems_demucs(wav, os.path.join(tmp, "stems"))
                for stem in ("drums", "bass", "other", "vocals"):
                    self.assertIn(stem, out)
                    self.assertTrue(os.path.isfile(out[stem]))

    def test_build_stem_corridors_from_masters_noop_without_masters(self):
        with tempfile.TemporaryDirectory() as tmp:
            corridors = os.path.join(tmp, "corridors.json")
            shutil.copy(CORRIDORS, corridors)
            with open(corridors, encoding="utf-8") as fh:
                before = fh.read()
            res = bgr.build_stem_corridors_from_masters(
                external_dir=os.path.join(tmp, "empty_masters"), corridors_path=corridors
            )
            self.assertEqual(res["updated_profiles"], 0)
            with open(corridors, encoding="utf-8") as fh:
                after = fh.read()
            self.assertEqual(before, after, "aucun master externe -> fichier inchangé")
            self.assertIn("demucs_available", res)


if __name__ == "__main__":
    unittest.main()
