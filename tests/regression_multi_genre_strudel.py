#!/usr/bin/env python3
"""v4.1 P6 — parité CI 7/7 du pont Strudel : les 7 fixtures `tests/fixtures/strudel_<profile>.strudel`
passent lint -> eval (Node headless) -> pont Strudel -> solveur mathématique, avec asserts
structurels (0 erreur eval, mutation <= 0.25, 0 diagnostic, full_coverage OK, plan valide,
registre basse [31,48], sections conformes au `form=` du `@meta`). La partie rendu + PQ >= 7.8
n'est exécutée que si le binaire `gridline` est présent (profil CI hebdomadaire / à la demande).

Usage : .venv/bin/python -m unittest tests.regression_multi_genre_strudel -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

NODE = shutil.which("node")
STRUDEL_DEPS = os.path.isdir(os.path.join(SCRIPTS, "node_modules", "@strudel", "core"))
HAVE_STRUDEL = bool(NODE and STRUDEL_DEPS)
HAVE_GRIDLINE = bool(shutil.which("gridline"))
try:
    import numpy as np
    import soundfile as sf
    HAVE_AUDIO = True
except ImportError:
    HAVE_AUDIO = False

import strudel_to_score_ir as bridge  # noqa: E402

# (profile, bpm attendu, has_drums, bars de test)
PROFILE_SPECS = [
    ("trap_rap", 140.0, True, 24),
    ("pop_rnb", 116.0, True, 24),
    ("edm_club", 126.0, True, 24),
    ("rock_metal", 132.0, True, 28),
    ("lofi_chill", 82.0, True, 24),
    ("jazz_acoustic", 96.0, True, 24),
    ("classical_ambient", 72.0, False, 24),
]


def _fixture(profile: str) -> str:
    return os.path.join(FIXTURES, f"strudel_{profile}.strudel")


@unittest.skipUnless(HAVE_STRUDEL, "node + @strudel/core requis (npm install dans scripts/)")
class TestStrudelFixtures7of7(unittest.TestCase):
    def _lint(self, path: str) -> subprocess.CompletedProcess:
        return subprocess.run([NODE, os.path.join(SCRIPTS, "strudel_lint.mjs"), path],
                              capture_output=True, text=True, timeout=60)

    def _check_profile(self, profile: str, bpm: float, has_drums: bool, bars: int):
        path = _fixture(profile)
        self.assertTrue(os.path.isfile(path), f"fixture manquante: {path}")
        # 1) lint : 0 erreur
        proc = self._lint(path)
        self.assertEqual(proc.returncode, 0, f"{profile} lint rc={proc.returncode}: {proc.stdout[-300:]} {proc.stderr[-300:]}")
        # 2) eval Node headless -> haps valides contre le schéma
        with tempfile.TemporaryDirectory() as tmp:
            spath = os.path.join(tmp, "composition.strudel")
            with open(path, encoding="utf-8") as fh:
                src_text = fh.read()
            with open(spath, "w", encoding="utf-8") as fh:
                fh.write(src_text)
            ev = subprocess.run([NODE, os.path.join(SCRIPTS, "strudel_eval.mjs"), spath,
                                 "--bars", str(bars), "--compact"],
                                capture_output=True, text=True, timeout=120)
            self.assertEqual(ev.returncode, 0, f"{profile} eval rc={ev.returncode}: {ev.stderr[-300:]}")
            haps = json.loads(ev.stdout)
            self.assertEqual(haps.get("errors"), [], f"{profile}: erreurs eval {haps['errors']}")
            import jsonschema
            with open(os.path.join(ROOT, "schemas", "strudel_haps.schema.json"), encoding="utf-8") as fh:
                jsonschema.validate(haps, json.load(fh))
            self.assertAlmostEqual(float(haps["meta"]["bpm"]), bpm, places=1, msg=f"{profile}: bpm meta")
            self.assertEqual(haps["meta"].get("mix_profile"), profile, f"{profile}: mix_profile meta")
            tracks = haps["tracks"]
            if has_drums:
                self.assertIn("kick", tracks, f"{profile}: kit -> piste kick attendue")
                self.assertIn("hihat", tracks, f"{profile}: kit -> piste hihat attendue")
            else:
                self.assertNotIn("kick", tracks, f"{profile}: has_drums=false -> pas de piste kick")
            self.assertIn("bass", tracks)
            self.assertIn("lead", tracks)
            # sections du @meta form= présentes dans les métadonnées
            sec_names = [s["name"] for s in haps["meta"].get("sections", [])]
            self.assertIn("verse", sec_names, f"{profile}: sections {sec_names}")
            self.assertIn("chorus", sec_names, f"{profile}: sections {sec_names}")
            # 3) pont complet
            report = bridge.strudel_to_score_ir(tmp, run_eval=True, bars=bars, seed=0)
            self.assertTrue(report.get("ok"), f"{profile}: ok=False {json.dumps(report, default=str)[:400]}")
            self.assertEqual(report.get("diagnostics"), [], f"{profile}: {report.get('diagnostics')}")
            for tn, r in (report.get("mutation") or {}).items():
                self.assertLessEqual(r, bridge.MUTATION_RATIO_MAX, f"{profile}: mutation {tn}={r}")
            g = report["guards"]
            self.assertEqual(g.get("bass_register_clamped", 0), 0, f"{profile}: octave-folds basse {g}")
            self.assertTrue(report["full_coverage"]["passed"], f"{profile}: fc {report['full_coverage']}")
            self.assertTrue(report["plan_valid"] is True, f"{profile}: plan_valid={report['plan_valid']}")
            self.assertEqual([s["name"] for s in report["sections"]], sec_names,
                             f"{profile}: sections pont {report['sections']} vs meta {sec_names}")
            # registre basse dans [31,48] sur le ScoreIR produit
            with open(report["score_ir"], encoding="utf-8") as fh:
                ir = json.load(fh)
            tracks_ir = ir.get("tracks") or {}
            if isinstance(tracks_ir, list):
                tracks_ir = {str(t.get("name", "")): t for t in tracks_ir}
            bass_track = next((t for nm, t in tracks_ir.items() if "bass" in nm.lower()), None)
            self.assertIsNotNone(bass_track, f"{profile}: piste bass dans score_ir")
            for n in bass_track.get("notes", []):
                p = n.get("pitch") if isinstance(n, dict) else n
                if isinstance(p, (int, float)):
                    self.assertGreaterEqual(p, 31, f"{profile}: basse MIDI {p} < 31")
                    self.assertLessEqual(p, 48, f"{profile}: basse MIDI {p} > 48")
            return report, haps

    def test_all_7_profiles_structural(self):
        for profile, bpm, has_drums, bars in PROFILE_SPECS:
            with self.subTest(profile=profile):
                self._check_profile(profile, bpm, has_drums, bars)

    def test_determinism_same_seed(self):
        profile, _, _, bars = PROFILE_SPECS[0]
        path = _fixture(profile)
        outs = []
        for _ in range(2):
            proc = subprocess.run([NODE, os.path.join(SCRIPTS, "strudel_eval.mjs"), path,
                                   "--bars", str(bars), "--compact"],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0)
            outs.append(proc.stdout)
        self.assertEqual(outs[0], outs[1], f"{profile}: non-déterministe malgré seed fixe")


@unittest.skipUnless(HAVE_STRUDEL and HAVE_AUDIO, "node + @strudel + numpy/soundfile requis")
class TestStrudelFixturesSolver(unittest.TestCase):
    def test_solver_on_synthetic_stems_per_profile(self):
        import math_mix_solver as mms
        sr = 22050
        for profile, bpm, has_drums, bars in PROFILE_SPECS:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                report, haps = TestStrudelFixtures7of7()._check_profile(profile, bpm, has_drums, bars)
                # stems synthétiques par rôle depuis les haps (1 sine/note, pas du bruit)
                stems_dir = os.path.join(tmp, "stems")
                os.makedirs(stems_dir, exist_ok=True)
                dur = max(1.0, bars * 4.0 * 60.0 / bpm / 8.0)
                n = int(dur * sr)
                t = np.arange(n) / sr
                for name, tr in haps["tracks"].items():
                    freqs = [110.0]
                    for ev in (tr.get("events") or [])[:8]:
                        for p in ev.get("notes") or []:
                            freqs.append(440.0 * 2.0 ** ((float(p) - 69) / 12.0))
                    x = sum(0.08 * np.sin(2 * np.pi * f * t) for f in freqs[:6])
                    x = x / max(float(np.max(np.abs(x))), 1e-6) * 0.5
                    sf.write(os.path.join(stems_dir, f"{name}.wav"),
                             np.stack([x, np.roll(x, 5)], axis=1).astype(np.float32), sr)
                sol = mms.solve_from_stems_dir(stems_dir, profile, bpm, valence=-0.5 if profile in ("trap_rap", "classical_ambient") else 0.2)
                self.assertTrue(sol.get("converged"), f"{profile}: non convergé")
                self.assertLess(sol["solve_time_ms"], 800.0, f"{profile}: solve {sol['solve_time_ms']} ms")
                self.assertIn("lufs", sol["predicted_metrics"])
                self.assertGreaterEqual(len(sol["tracks"]), 3)


@unittest.skipUnless(HAVE_STRUDEL and HAVE_GRIDLINE, "node + binaire gridline requis (rendu PQ, CI hebdomadaire)")
class TestStrudelFixturesRenderPQ(unittest.TestCase):
    def test_render_and_pq_gate_7_of_7(self):
        from render_and_audit import render_and_evaluate
        for profile, bpm, has_drums, bars in PROFILE_SPECS:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                shutil.copy(_fixture(profile), os.path.join(tmp, "composition.strudel"))
                report, _ = TestStrudelFixtures7of7()._check_profile(profile, bpm, has_drums, bars)
                res = render_and_evaluate(tmp, profile=profile, no_neural=True,
                                          no_drums=not has_drums, bpm=bpm)
                pq = (res.get("quality_gate") or {}).get("pq") or res.get("pq")
                self.assertIsNotNone(pq, f"{profile}: PQ absent du rapport")
                self.assertGreaterEqual(float(pq), 7.8, f"{profile}: PQ={pq} < 7.8")


if __name__ == "__main__":
    unittest.main()
