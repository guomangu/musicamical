#!/usr/bin/env python3
"""v4.1 — régression des bugs bloquants rencontrés en session réelle (RETEX 2026-11-25) :
  (a) `compile_song.py` / `target_fingerprint.py` : `%` non échappé dans une help string
      argparse -> ValueError (py3.14) / TypeError (py3.12) au `--help` ;
  (b) `deep_diagnostics.run_deep_audit` : `issues` référencé avant déclaration
      (UnboundLocalError) + `L`/`R` indéfinis dans le détail mono-folddown (NameError) ;
  (c) `render_and_audit._apply_autofix_mutations` : `_tp_limit` défini seulement dans
      `render_and_evaluate` -> NameError dès qu'un autofix limiteur se déclenche ;
  (d) pont Strudel : `seed` du `@strudel-meta` écrasé par le `--seed 42` par défaut du pont.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "mcp-servers", "audio-analyzer"), os.path.join(ROOT, "mcp-servers", "audio-index")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import numpy as np

NODE = shutil.which("node")
STRUDEL_DEPS = os.path.isdir(os.path.join(ROOT, "scripts", "node_modules", "@strudel", "core"))
HAVE_STRUDEL = bool(NODE and STRUDEL_DEPS)


def _py_files():
    out = []
    for base in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "mcp-servers")):
        for r, d, fns in os.walk(base):
            d[:] = [x for x in d if x not in ("__pycache__", "node_modules")]
            for fn in fns:
                if fn.endswith(".py"):
                    out.append(os.path.join(r, fn))
    return out


class TestArgparseHelpPercent(unittest.TestCase):
    def test_no_bare_percent_in_any_help_string(self):
        bad = []
        for p in _py_files():
            with open(p, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg == "help" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        if re.search(r"%(?!\(|%)", kw.value.value):
                            bad.append(f"{os.path.relpath(p, ROOT)}: {kw.value.value[:60]!r}")
        self.assertEqual(bad, [], f"help argparse avec % non échappé (crash --help) : {bad}")

    def test_compile_song_help_runs(self):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "compile_song.py"), "--help"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertIn("full_coverage", proc.stdout)

    def test_target_fingerprint_help_runs(self):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "target_fingerprint.py"), "--help"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])


class TestDeepAuditNoUnboundLocal(unittest.TestCase):
    def test_low_plr_wav_runs_and_reports_issues(self):
        import soundfile as sf
        from deep_diagnostics import run_deep_audit
        with tempfile.TemporaryDirectory() as tmp:
            sr = 22050
            t = np.arange(int(4.0 * sr)) / sr
            loud = 0.9 * np.sin(2 * np.pi * 220.0 * t)  # PLR ~0 < 7.5 -> branche climax_pumping
            data = np.stack([loud, loud], axis=1).astype(np.float32)
            path = os.path.join(tmp, "loud.wav")
            sf.write(path, data, sr)
            out = run_deep_audit(path, {}, data, sr, target_genre="trap_rap", use_neural=False)
            self.assertIsInstance(out, dict)
            issues = out.get("issues_detected", out.get("issues"))
            self.assertIsInstance(issues, list)
            cats = {i.get("category") for i in issues}
            self.assertIn("climax_pumping", cats)  # le PLR faible a bien été diagnostiqué


class TestAutofixTpLimitLocal(unittest.TestCase):
    def test_tp_limit_assigned_inside_function(self):
        with open(os.path.join(ROOT, "scripts", "render_and_audit.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_apply_autofix_mutations")
        assigned = {t.id for n in ast.walk(fn) for t in getattr(n, "targets", []) if isinstance(t, ast.Name)}
        self.assertIn("_tp_limit", assigned, "_tp_limit doit être recalculé localement (NameError sinon)")

    def test_call_does_not_raise_namelocal(self):
        import render_and_audit as ra
        summary = {
            "quality_gate": {"failures": ["true_peak_dbtp=-0.5 > -1.0"]},
            "clipped_samples": 3,
            "true_peak_dbtp": -0.5,
            "integrated_lufs": -20.0,
            "target_lufs": -11.0,
            "effective_thresholds": {"low_mids_max": 30.0, "sharp_max": 1.9},
            "bands_pct": {"low_mids": 10.0},
            "psychoacoustics": {"sharpness_din_acum": 1.0},
            "stereo": {"correlation_low_end_150hz": 0.9},
            "resonances_third_oct": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            try:
                ra._apply_autofix_mutations(tmp, summary, {})
            except Exception as exc:
                self.assertNotIsInstance(exc, NameError)
                self.assertNotIn("_tp_limit", str(exc))


@unittest.skipUnless(HAVE_STRUDEL, "node + @strudel requis")
class TestSeedPropagation(unittest.TestCase):
    def test_meta_seed_wins_over_default(self):
        import strudel_to_score_ir as bridge
        with tempfile.TemporaryDirectory() as tmp:
            src = (
                '// @strudel-meta {"bpm": 140, "key": "C", "mode": "minor", "seed": 1337, "bars": 4}\n'
                '$chords: chord("<Cm7 G7>")\n'
                '$lead: note("<c5 e5 g5 c6>")\n'
            )
            with open(os.path.join(tmp, "composition.strudel"), "w", encoding="utf-8") as fh:
                fh.write(src)
            report = bridge.strudel_to_score_ir(tmp, run_eval=True, bars=4, seed=0)
            self.assertEqual(report.get("seed"), 1337, "meta.seed=1337 doit être propagé (pas le défaut 42)")


if __name__ == "__main__":
    unittest.main()
