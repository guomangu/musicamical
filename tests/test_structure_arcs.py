#!/usr/bin/env python3
"""Chantier 4 (spec v2.0 §4.1) — arcs structurels & énergétiques de référence :
`structure_arcs` dans `spectral_corridors.json` (bootstrap_golden_refs.py) +
comparaison de l'arc d'un morceau via `macro_structure_audit.compare_energy_arc` /
`audit_energy_arc_vs_reference`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
ANALYZER_DIR = os.path.join(ROOT, "mcp-servers", "audio-analyzer")
for _d in (SCRIPTS_DIR, ANALYZER_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import bootstrap_golden_refs as bgr  # noqa: E402
import macro_structure_audit as msa  # noqa: E402

CORRIDORS = os.path.join(ROOT, "golden_references", "spectral_corridors.json")
SR = 22050


class TestStructureArcsFile(unittest.TestCase):
    def test_all_profiles_have_structure_arcs(self):
        with open(CORRIDORS, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data.get("schema_version"), "3.2")
        profiles = data["profiles"]
        self.assertEqual(len(profiles), 7)
        for prof, entry in profiles.items():
            arcs = entry.get("structure_arcs")
            self.assertIsNotNone(arcs, f"{prof} sans structure_arcs")
            sections = arcs["sections"]
            self.assertGreaterEqual(len(sections), 4)
            total = 0
            for s in sections:
                self.assertIn(s["name"], bgr.SECTION_NAMES)
                self.assertGreater(s["bars"], 0)
                self.assertGreaterEqual(s["lufs_rel_db"], -12.0)
                self.assertLessEqual(s["lufs_rel_db"], 6.0)
                self.assertTrue(set(s["stems"].keys()) <= set(bgr.STEM_NAMES))
                for v in s["stems"].values():
                    self.assertGreaterEqual(v, 0.0)
                    self.assertLessEqual(v, 1.0)
                total += s["bars"]
            self.assertEqual(total, arcs["total_bars"])
            self.assertEqual(len(arcs["energy_curve_lufs_rel"]), total)

    def test_analytic_arc_template(self):
        arc = bgr._analytic_structure_arc("edm_club")
        names = [s["name"] for s in arc["sections"]]
        self.assertIn("drop", names)  # EDM : le chorus devient un drop
        self.assertIn("outro", names)
        self.assertGreater(arc["total_bars"], 0)


class TestCompareEnergyArc(unittest.TestCase):
    def test_matching_arc_scores_higher_than_flat(self):
        arcs = bgr._analytic_structure_arc("trap_rap")
        ref = arcs["energy_curve_lufs_rel"]
        good = msa.compare_energy_arc(list(ref), arcs)
        flat = msa.compare_energy_arc([0.0] * len(ref), arcs)
        self.assertGreater(good["score"], 0.9)
        self.assertLess(flat["score"], good["score"])
        self.assertEqual(good["n_bars"], len(ref))
        self.assertEqual(len(good["sections"]), len(arcs["sections"]))

    def test_load_structure_arcs_from_committed_file(self):
        arcs = msa.load_structure_arcs("trap_rap")
        self.assertIsNotNone(arcs)
        self.assertIn("sections", arcs)
        self.assertIsNone(msa.load_structure_arcs("trap_rap", "/nonexistent/corridors.json"))

    def test_audit_energy_arc_on_synthetic_render(self):
        bpm = 120.0
        bar_s = 4 * 60.0 / bpm
        arcs = bgr._analytic_structure_arc("trap_rap")
        n_bars = arcs["total_bars"]
        n = int(n_bars * bar_s * SR)
        t = np.arange(n) / SR
        audio = np.zeros(n)
        bar = 0
        for s in arcs["sections"]:
            g = 10.0 ** (s["lufs_rel_db"] / 20.0)
            for b in range(s["bars"]):
                i0, i1 = int(bar * bar_s * SR), int((bar + 1) * bar_s * SR)
                audio[i0:i1] += g * 0.2 * np.sin(2 * np.pi * 220.0 * np.arange(i1 - i0) / SR)
                bar += 1
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "master.wav")
            sf.write(wav, audio.astype(np.float32), SR)
            res = msa.audit_energy_arc_vs_reference(wav, bpm=bpm, profile="trap_rap")
        self.assertTrue(res["available"])
        self.assertEqual(res["n_bars"], n_bars)
        self.assertGreater(res["arc_score"], 0.5, f"arc={res['arc_score']} sections={res['sections']}")

    def test_extract_structure_arc_from_audio(self):
        bpm = 120.0
        bar_s = 4 * 60.0 / bpm
        n_bars = 16
        n = int(n_bars * bar_s * SR)
        t = np.arange(n) / SR
        audio = np.zeros(n)
        for b in range(n_bars):
            g = 0.05 if b < 4 else (0.3 if b >= 8 else 0.15)
            seg = slice(int(b * bar_s * SR), int((b + 1) * bar_s * SR))
            audio[seg] += g * np.sin(2 * np.pi * 220.0 * t[: seg.stop - seg.start])
        arc = bgr.extract_structure_arc_from_audio(audio, SR, bpm)
        self.assertEqual(arc["total_bars"], n_bars)
        self.assertEqual(len(arc["energy_curve_lufs_rel"]), n_bars)
        self.assertGreaterEqual(len(arc["sections"]), 2)
        self.assertIn(arc["provenance"], ("measured_master_energy",))


if __name__ == "__main__":
    unittest.main()
