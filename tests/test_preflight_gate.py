"""v4.1 — tests du simulateur pre-flight du MIDI Gate (`scripts/preflight_strudel_gate.py`).

1. `degree_table` : dictionnaire degrés ↔ demi-tons (deg 4 en C mineur = +7 st / MIDI 67, deg 8 = 74) ;
2. `b2_safe_degrees` : table b2-safe i-VI-III-VII (Cm7 interdit {1,5}, Bb7 {2,4}, intersection {0,3,6,7}) ;
3. `slow_step` : sémantique de .slow(N) (256 sans slow = 1/64 de mesure, 128 + slow(8) = 0.25 beat) ;
4. `preflight_from_score` / `preflight()` sur HAPS synthétiques : pattern leap+fill PASS
   (gap_fill élevé), pattern à leaps non remplis FAIL avec `bad_leaps` peuplé, b2 restants comptés ;
5. intégration pont : `strudel_to_score_ir()` rapporte `preflight` (clés checks/passed) ;
6. E2E Node (skip si absent) : CLI sur l'exemple canonique -> rc 0, preflight PASS.
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
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
if os.path.join(ROOT, "mcp-servers", "audio-analyzer") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "mcp-servers", "audio-analyzer"))

import preflight_strudel_gate as pf  # noqa: E402

NODE = shutil.which("node")
HAVE_STRUDEL = bool(NODE and os.path.isdir(os.path.join(SCRIPTS, "node_modules", "@strudel", "core")))


class TestPureHelpers(unittest.TestCase):
    def test_degree_table_minor(self):
        t = {r["deg"]: r for r in pf.degree_table("C", "minor", 4, 8)}
        self.assertEqual(t[0]["midi"], 60)
        self.assertEqual(t[4]["semitones"], 7)   # piège : deg 4 = +7 st (G), PAS +4
        self.assertEqual(t[4]["name"], "G")
        self.assertEqual(t[4]["midi"], 67)
        self.assertEqual(t[8]["midi"], 74)       # LEAD_APEX_MAX sans clamp
        self.assertEqual(t[2]["semitones"], 3)   # Eb

    def test_b2_safe_degrees_i_VI_III_VII(self):
        d = pf.b2_safe_degrees(["Cm7", "Abmaj7", "Ebmaj7", "Bb7"], mode="minor")
        self.assertEqual(d["per_chord"]["Cm7"]["unsafe_degs"], [1, 5])
        self.assertEqual(d["per_chord"]["Abmaj7"]["unsafe_degs"], [1, 4, 5])
        self.assertEqual(d["per_chord"]["Bb7"]["unsafe_degs"], [2, 4])
        self.assertEqual(d["safe_everywhere"], [0, 3, 6, 7])  # C, F, Bb, C'

    def test_slow_step_semantics(self):
        self.assertAlmostEqual(pf.slow_step(256, 1)["step_beats"], 0.015625)  # 1/64 de mesure
        s = pf.slow_step(128, 8)
        self.assertEqual(s["degrees_per_bar"], 16.0)
        self.assertAlmostEqual(s["step_beats"], 0.25)
        self.assertAlmostEqual(pf.slow_step(8, 1)["step_beats"], 0.5)


def _haps_with_lead(lead_pitches, prog=("Cm7", "Abmaj7", "Fm7", "G7"), bars=8) -> dict:
    """HAPS synthétique minimal : chords + bass + lead avec les hauteurs données (1 note/beat)."""
    grid = []
    for i, sym in enumerate(prog):
        grid += [sym] * 2
    tracks = {
        "chords": {"events": [{"start": 2 * i, "end": 2 * i + 2, "chord": grid[i % len(grid)], "gain": 0.7}
                              for i in range(bars)], "automations": {}},
        "bass": {"events": [{"start": float(i), "end": i + 1, "notes": [36], "gain": 0.9} for i in range(bars * 4)],
                 "automations": {}},
        "lead": {"events": [{"start": float(i), "end": i + 0.5, "notes": [p], "gain": 0.6}
                            for i, p in enumerate(lead_pitches)], "automations": {}},
    }
    return {
        "schema_version": "4.1",
        "source": "synthetic.strudel",
        "meta": {"bpm": 120, "key": "C", "mode": "minor",
                 "time_signature": {"numerator": 4, "denominator": 4}, "seed": 42,
                 "sections": [{"name": "verse", "bars": bars}],
                 "progression": list(prog), "mix_profile": "trap_rap", "valence": 0.0},
        "bars": bars, "beats_per_bar": 4, "seed": 42,
        "tracks": tracks, "errors": [],
    }


class TestPreflightOnSyntheticHaps(unittest.TestCase):
    def _run(self, lead_pitches):
        import strudel_to_score_ir as st
        haps = _haps_with_lead(lead_pitches)
        score, info = st.build_score_ir_from_strudel(haps, None)
        from score_ir import autofix_score_ir, compile_score_ir_to_notes
        score, _fix = autofix_score_ir(score)
        st.fix_lead_b2_clashes(score)
        tracks, _rep = compile_score_ir_to_notes(score)
        return pf.preflight_from_score(score, bpm=120.0, tracks=tracks)

    def test_leap_fill_pattern_passes_gap_fill(self):
        # C(60) F(65) Eb(63) D(62) ~ D Eb D C : leap +5 suivi de -2 -> rempli
        rep = self._run([60, 65, 63, 62, 60, 62, 63, 62, 60, 65, 63, 62, 60, 62, 63, 60])
        checks = rep["checks"]
        self.assertGreaterEqual(checks["gap_fill"]["value"], 0.60)
        self.assertEqual(checks["b2_clashes_remaining"]["value"], 0)
        self.assertIn("stepwise_ratio", checks)
        self.assertIn("rest_ratio", checks)

    def test_unfilled_leaps_fail_and_are_listed(self):
        # sauts +12 non suivis de mouvement contraire -> bad_leaps peuplé, gap_fill faible
        rep = self._run([60, 72, 84, 72, 60, 84, 60, 72])
        checks = rep["checks"]
        self.assertLess(checks["gap_fill"]["value"], 0.60)
        self.assertGreater(rep["n_bad_leaps"], 0)
        self.assertIn("leap", rep["bad_leaps"][0])
        self.assertIn("start_beat", rep["bad_leaps"][0])

    def test_preflight_full_pipeline_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            haps_path = os.path.join(tmp, "composition.strudel.haps.json")
            with open(haps_path, "w", encoding="utf-8") as fh:
                json.dump(_haps_with_lead([60, 65, 63, 62, 60, 62, 63, 62] * 4), fh)
            rep = pf.preflight(tmp)
            for key in ("passed", "checks", "bad_leaps", "b2_fixed_by_bridge",
                        "lead_apex_clamped", "project", "haps"):
                self.assertIn(key, rep)
            self.assertIn("gap_fill", rep["checks"])


@unittest.skipUnless(HAVE_STRUDEL, "node + @strudel/core requis")
class TestPreflightBridgeIntegration(unittest.TestCase):
    def test_bridge_report_contains_preflight(self):
        import strudel_to_score_ir as bridge
        with tempfile.TemporaryDirectory() as tmp:
            spath = os.path.join(tmp, "composition.strudel")
            with open(os.path.join(ROOT, "assets", "composition_trap_example.strudel"), encoding="utf-8") as fh:
                src = fh.read()
            with open(spath, "w", encoding="utf-8") as fh:
                fh.write(src)
            rep = bridge.strudel_to_score_ir(tmp, run_eval=True, bars=24, seed=0)
            self.assertIn("preflight", rep)
            pfr = rep["preflight"]
            self.assertIn("checks", pfr)
            self.assertTrue(pfr["passed"], f"preflight FAIL: {json.dumps(pfr.get('checks'), default=str)}")

    def test_cli_on_canonical_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            spath = os.path.join(tmp, "composition.strudel")
            with open(os.path.join(ROOT, "assets", "composition_trap_example.strudel"), encoding="utf-8") as fh:
                src = fh.read()
            with open(spath, "w", encoding="utf-8") as fh:
                fh.write(src)
            import strudel_to_score_ir as bridge
            bridge.strudel_to_score_ir(tmp, run_eval=True, bars=24, seed=0)
            proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "preflight_strudel_gate.py"), tmp],
                                  capture_output=True, text=True, timeout=180)
            self.assertEqual(proc.returncode, 0, proc.stdout[-400:] + proc.stderr[-300:])
            rep = json.loads(proc.stdout)
            self.assertTrue(rep["passed"])


if __name__ == "__main__":
    unittest.main()
