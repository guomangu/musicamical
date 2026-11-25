#!/usr/bin/env python3
"""v4.0 — tests du pont Strudel headless -> ScoreIR (scripts/strudel_eval.mjs + strudel_to_score_ir.py).

Couverture :
- contrat « 1 piste $ = 1 role atomique » (kick et clap jamais sur le meme canal) ;
- garde-fous 0 token sur l'exemple Trap fautif de l'analyse v4.0 (6 bugs) : registre 808
  clamp [31, 48], apex Lead <= 74, frottement b2 Lead/accord corrige, voice leading + LIL,
  clamp frontiere 20 ms + sanitize_note_overlaps, FullCoverageCoreValidator ;
- sortie score_ir.json + arrangement_plan.json valide contre schemas/arrangement_plan.schema.json ;
- determinisme du seed (2 evaluations Node identiques) ;
- test bout-en-bout Node reel (skip si node/@strudel absent) sur assets/composition_trap_example.strudel.
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
for _d in (SCRIPTS, os.path.join(ROOT, "mcp-servers", "audio-analyzer")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import strudel_to_score_ir as bridge  # noqa: E402
from score_ir import ScoreIR  # noqa: E402

NODE = shutil.which("node")
STRUDEL_DEPS = os.path.isdir(os.path.join(SCRIPTS, "node_modules", "@strudel", "core"))
HAVE_STRUDEL = bool(NODE and STRUDEL_DEPS)


def _buggy_trap_haps() -> dict:
    """Haps JSON synthetique = exemple Trap 25 lignes de l'analyse v4.0 (avec ses 6 bugs)."""
    bars = 8
    bpb = 4
    prog = []
    for sym in ("Cm7", "Abmaj7", "Fm7", "G7"):
        prog += [sym, sym]
    tracks = {
        # bug #1/#4 : accords .slow(2) -> un symbole toutes les 2 mesures
        "chords": {"events": [{"start": 2 * i, "end": 2 * i + 2, "chord": prog[i], "gain": 0.7} for i in range(8)], "automations": {}},
        # bug #2 : 808 dans l'infrason (c1=24, ab0=20, g0=19, f0=17)
        "bass": {"events": [
            {"start": 0, "end": 1, "notes": [24], "gain": 0.95},
            {"start": 1, "end": 2, "notes": [24], "gain": 0.95},
            {"start": 2, "end": 3, "notes": [20, 19], "gain": 0.95},
            {"start": 3, "end": 4, "notes": [17], "gain": 0.95},
        ] * 2, "automations": {}},
        # bug #5 corrige par le contrat : kick et clap = 2 pistes separees
        "kick": {"events": [{"start": i, "end": i + 0.25, "s": "bd", "gain": 0.95} for i in range(0, 32, 2)], "automations": {}},
        "clap": {"events": [{"start": i + 1, "end": i + 1.25, "s": "cp", "gain": 0.8} for i in range(0, 32, 4)], "automations": {}},
        # bug #3 : bb4 (70) contre B naturel de G7 + transposition aleatoire eb6 (87)
        "lead": {"events": [
            {"start": 6, "end": 7, "notes": [70], "gain": 0.6},
            {"start": 7, "end": 8, "notes": [87], "gain": 0.6},
        ] + [{"start": float(i), "end": i + 1, "notes": [72], "gain": 0.6} for i in range(0, 6)], "automations": {}},
        "hihat": {"events": [{"start": i * 0.5, "end": i * 0.5 + 0.25, "s": "hh", "gain": 0.45} for i in range(64)], "automations": {}},
    }
    return {
        "schema_version": "4.0",
        "source": "synthetic_trap.strudel",
        "meta": {"bpm": 140, "key": "C", "mode": "minor",
                 "time_signature": {"numerator": 4, "denominator": 4}, "seed": 42,
                 "sections": [{"name": "verse", "bars": 4}, {"name": "chorus", "bars": 4}],
                 "progression": prog, "mix_profile": "trap_rap", "valence": -0.6},
        "bars": bars, "beats_per_bar": bpb, "seed": 42,
        "tracks": tracks, "errors": [],
    }


class TestStrudelBridgeGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="strudel_bridge_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.haps = _buggy_trap_haps()
        self.haps_path = os.path.join(self.tmp, "composition.strudel.haps.json")
        with open(self.haps_path, "w", encoding="utf-8") as fh:
            json.dump(self.haps, fh)

    def _run(self, **kw):
        return bridge.strudel_to_score_ir(self.tmp, haps_path=self.haps_path, run_eval=False, **kw)

    def test_one_stack_one_atomic_role(self):
        rep = self._run()
        self.assertTrue(os.path.isfile(rep["score_ir"]))
        score = ScoreIR.load(rep["score_ir"])
        self.assertIn("kick", score.tracks)
        self.assertIn("clap", score.tracks)
        self.assertIsNot(score.tracks["kick"], score.tracks["clap"])
        self.assertEqual(score.tracks["kick"].role, "drums")

    def test_bass_register_clamped_31_48(self):
        rep = self._run()
        score = ScoreIR.load(rep["score_ir"])
        bass = [n.pitch for n in score.tracks["bass"].notes]
        self.assertTrue(bass)
        self.assertTrue(all(31 <= p <= 48 for p in bass), f"registre 808 hors [31,48] : {bass}")
        self.assertGreaterEqual(rep["guards"]["bass_register_clamped"], 1)
        raw = [p for ev in self.haps["tracks"]["bass"]["events"] for p in ev.get("notes", [])]
        self.assertTrue(any(p < 31 for p in raw), "le fixture doit contenir l'infrason (bug #2)")

    def test_lead_apex_clamped_and_b2_fixed(self):
        rep = self._run()
        score = ScoreIR.load(rep["score_ir"])
        lead = [n.pitch for n in score.tracks["lead"].notes]
        self.assertTrue(all(p <= 74 for p in lead), f"apex Lead > 74 : {lead}")
        self.assertGreaterEqual(rep["guards"]["lead_apex_clamped"], 1)
        self.assertGreaterEqual(rep["lead_b2_fixed"], 1, "le clash Bb/B (bug #3) doit etre corrige")
        kinds = [e.get("kind") for e in rep["evaluate"]["errors"]]
        self.assertNotIn("b2_boundary_clash", kinds)
        self.assertNotIn("low_interval_violation", kinds)

    def test_compile_gate_and_full_coverage(self):
        rep = self._run()
        self.assertTrue(rep["compile"]["passed"], rep["compile"])
        self.assertEqual(rep["compile"]["b2_boundary_clashes"], 0)
        self.assertEqual(rep["compile"]["mono_overlaps"], 0)
        self.assertTrue(rep["full_coverage"]["passed"], rep["full_coverage"]["issues"][:5])
        self.assertTrue(rep["evaluate"]["passed"], rep["evaluate"]["errors"][:5])
        self.assertTrue(rep["ok"])

    def test_arrangement_plan_emitted_and_valid(self):
        rep = self._run()
        plan_path = rep["arrangement_plan"]
        self.assertTrue(os.path.isfile(plan_path))
        self.assertNotIsInstance(rep["plan_valid"], str)
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        self.assertEqual(plan["meta"]["bpm"], 140.0)
        self.assertTrue(plan["meta"]["has_drums"])
        self.assertIn("degree_progression", plan["harmonic_core"])
        for sec in plan["sections"]:
            self.assertIn("active_roles", sec)
            for role in sec["active_roles"]:
                self.assertIn(role, plan["motifs"])
                self.assertIn(sec["name"], plan["motifs"][role])
        from song_builder import load_arrangement_plan
        load_arrangement_plan(plan_path, validate=True)

    def test_chord_symbol_to_roman(self):
        self.assertEqual(bridge.symbol_to_roman("Cm7", 0), "i7")
        self.assertEqual(bridge.symbol_to_roman("Abmaj7", 0), "bVImaj7")
        self.assertEqual(bridge.symbol_to_roman("G7", 0), "V7")
        self.assertEqual(bridge.symbol_to_roman("Fm7", 0), "iv7")
        parsed = bridge.parse_chord_symbol("G7")
        self.assertEqual(parsed["chord_pcs"], [2, 5, 7, 11])

    def test_determinism_same_seed_same_score_ir(self):
        r1 = self._run()
        s1 = ScoreIR.load(r1["score_ir"]).to_dict()
        r2 = self._run()
        s2 = ScoreIR.load(r2["score_ir"]).to_dict()
        self.assertEqual(json.dumps(s1, sort_keys=True), json.dumps(s2, sort_keys=True))


@unittest.skipUnless(HAVE_STRUDEL, "node + @strudel/core requis (npm install dans scripts/)")
class TestStrudelEvalNode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="strudel_eval_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.strudel = os.path.join(ROOT, "assets", "composition_trap_example.strudel")
        self.assertTrue(os.path.isfile(self.strudel))

    def _eval(self, out, bars=32, seed=42):
        cmd = [NODE, os.path.join(SCRIPTS, "strudel_eval.mjs"), self.strudel,
               "--bars", str(bars), "--seed", str(seed), "--out", out, "--compact"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)

    def test_eval_produces_haps_and_is_deterministic(self):
        h1 = self._eval(os.path.join(self.tmp, "h1.json"))
        h2 = self._eval(os.path.join(self.tmp, "h2.json"))
        self.assertEqual(json.dumps(h1, sort_keys=True), json.dumps(h2, sort_keys=True))
        self.assertEqual(h1["errors"], [])
        for name in ("kick", "snare", "hihat", "bass", "chords", "lead"):
            self.assertIn(name, h1["tracks"])
            self.assertTrue(h1["tracks"][name]["events"])
        self.assertEqual(h1["meta"]["progression"][:4], ["Cm7", "Cm7", "Abmaj7", "Abmaj7"])
        self.assertEqual(h1["bars"], 32)
        schema_path = os.path.join(ROOT, "schemas", "strudel_haps.schema.json")
        import jsonschema
        with open(schema_path, encoding="utf-8") as fh:
            jsonschema.validate(h1, json.load(fh))

    def test_end_to_end_bridge_from_strudel(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj, exist_ok=True)
        shutil.copy(self.strudel, os.path.join(proj, "composition.strudel"))
        rep = bridge.strudel_to_score_ir(proj, run_eval=True, bars=32, seed=42)
        self.assertTrue(rep["ok"], json.dumps(rep, default=str)[:600])
        score = ScoreIR.load(rep["score_ir"])
        bass = [n.pitch for n in score.tracks["bass"].notes]
        self.assertTrue(all(31 <= p <= 48 for p in bass))
        lead = [n.pitch for n in score.tracks["lead"].notes]
        self.assertTrue(all(p <= 74 for p in lead))
        self.assertTrue(os.path.isfile(rep["arrangement_plan"]))
        self.assertTrue(os.path.isfile(rep["haps"]))


if __name__ == "__main__":
    unittest.main()
