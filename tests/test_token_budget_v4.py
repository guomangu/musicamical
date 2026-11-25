#!/usr/bin/env python3
"""v4.0 — budget de tokens du profil Production (spec « Économie maximale de tokens » §4, livrable 4).

Vérifie que la taille combinée (estimée en tokens = cars/4) de :
  1. les schémas des 3 outils MCP de `mcp-servers/ai-studio-prod/prod_server.py`,
  2. le runbook `docs/AGENT_RUNBOOK_V4.md` (préfixe 100 % statique, <= 750 tokens),
  3. un retour type de `studio_prepare` (format tabulaire `format_prepare_compact`, <= 450 cars),
reste sous **1 800 tokens** au total. Teste aussi le patch Strudel chirurgical, le handoff.txt
(<= 350 tokens), le lint Node (<= 2 lignes) et le prélude (grid/deg/track/glide) si node est dispo.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
PROD = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
for _d in (SCRIPTS, PROD):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import prod_server  # noqa: E402

NODE = shutil.which("node")
HAVE_STRUDEL = bool(NODE and os.path.isdir(os.path.join(SCRIPTS, "node_modules", "@strudel", "core")))

TOKENS_PER_CHAR = 4.0
BUDGET_TOTAL_TOKENS = 1800.0


def _tokens(text: str) -> float:
    return len(text) / TOKENS_PER_CHAR


class TestTokenBudgetV4(unittest.TestCase):
    def test_prod_server_exposes_exactly_3_tools(self):
        with open(os.path.join(PROD, "prod_server.py"), encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        def is_tool_dec(d):
            if isinstance(d, ast.Call):
                f = d.func
                return (isinstance(f, ast.Name) and f.id == "_tool") or getattr(f, "attr", "") == "tool"
            return isinstance(d, ast.Name) and d.id == "_tool"
        tools = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and any(is_tool_dec(d) for d in n.decorator_list)]
        self.assertEqual(sorted(n.name for n in tools),
                         ["studio_compose", "studio_prepare", "studio_refine"])

    def test_combined_budget_under_1800_tokens(self):
        with open(os.path.join(PROD, "prod_server.py"), encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        schema_chars = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("studio_"):
                doc = ast.get_docstring(node) or ""
                sig = ast.unparse(node.args)
                schema_chars += len(node.name) + len(sig) + len(doc) + 40
        with open(os.path.join(ROOT, "docs", "AGENT_RUNBOOK_V4.md"), encoding="utf-8") as fh:
            runbook = fh.read()
        prep = prod_server.format_prepare_compact({
            "mix_profile": "trap_rap", "bpm": 142, "key": "C minor", "valence": -0.7,
            "user_samples_locked": True,
            "palettes": [
                {"id": "organic", "tracks": [
                    {"role": "kick", "file": "K01.wav", "root_note": 36},
                    {"role": "snare", "file": "S03.wav", "root_note": 38},
                    {"role": "hat", "file": "H02.wav", "root_note": 42},
                    {"role": "808", "file": "B01.wav", "root_note": 36},
                    {"role": "chords", "file": "gl.keys", "root_note": 60},
                    {"role": "lead", "file": "L04.wav", "root_note": 60},
                ]},
                {"id": "dark_synth", "tracks": [{"role": "kick", "file": "K02.wav", "root_note": 36}]},
                {"id": "hybrid", "tracks": [{"role": "kick", "file": "K03.wav", "root_note": 36}]},
            ],
        })
        self.assertLessEqual(len(prep), prod_server.PREPARE_LIMIT)
        total = _tokens("x" * schema_chars) + _tokens(runbook) + _tokens(prep)
        self.assertLess(total, BUDGET_TOTAL_TOKENS,
                        f"budget dépassé : {total:.0f} tokens (schémas={schema_chars}c, runbook={len(runbook)}c, prepare={len(prep)}c)")
        self.assertLessEqual(_tokens(runbook), 750.0, "runbook V4 > 750 tokens")


class TestPatchAndHandoff(unittest.TestCase):
    def test_apply_strudel_patch_replaces_only_one_track(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "composition.strudel")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('// @strudel-meta {"bpm": 120}\n$kick: s("bd ~ ~ ~")\n$lead: note("c4")\n')
            new = prod_server.apply_strudel_patch(p, {"replace_track": "lead", "code": 'deg("0 2 4", 5)'})
            self.assertIn('$lead: deg("0 2 4", 5)', new)
            self.assertIn('$kick: s("bd ~ ~ ~")', new)
            self.assertNotIn('note("c4")', new)

    def test_handoff_under_350_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(td, exist_ok=True)
            with open(os.path.join(td, "composition.strudel"), "w", encoding="utf-8") as fh:
                fh.write("// @strudel-meta {}\n$kick: s('bd')\n")
            path = prod_server.write_handoff(td, {"ok": True, "n_tracks": 6, "n_notes": 437, "seed": 42,
                                                  "sections": [{"name": "verse"}], "guards": {"bass_register_clamped": 8}})
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertLessEqual(_tokens(text), 350.0)
            self.assertIn("strudel=composition.strudel#", text)


@unittest.skipUnless(HAVE_STRUDEL, "node + @strudel/core requis (npm install dans scripts/)")
class TestStrudelPreludeAndLint(unittest.TestCase):
    def _eval(self, src: str, bars: int = 8):
        with tempfile.NamedTemporaryFile("w", suffix=".strudel", delete=False, encoding="utf-8") as fh:
            fh.write(src)
            path = fh.name
        try:
            proc = subprocess.run([NODE, os.path.join(SCRIPTS, "strudel_eval.mjs"), path,
                                   "--bars", str(bars), "--compact"],
                                  capture_output=True, text=True, timeout=120)
            return proc
        finally:
            os.unlink(path)

    def test_prelude_grid_deg_track_glide(self):
        proc = self._eval(
            '// @strudel-meta {"bpm": 132, "key": "Eb", "mode": "minor", "seed": 7}\n'
            '$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2)\n'
            '$bass: track("bass", note("<c1 c1>"))\n'
            '$lead: deg("0 ~ [2 3] 4", 5).glide(60)\n'
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
        d = json.loads(proc.stdout)
        self.assertEqual(d["errors"], [])
        self.assertEqual(d["meta"]["progression"][:4], ["Cm7", "Cm7", "Abmaj7", "Abmaj7"])
        lead = [e for e in d["tracks"]["lead"]["events"] if e["start"] < 1]
        chord_hits = [e for e in lead if len(e.get("notes", [])) >= 2]
        same_onset = {}
        for e in lead:
            same_onset.setdefault(round(e["start"], 4), []).append(e.get("notes", []))
        simultaneous = any(len(v) > 1 for v in same_onset.values())
        self.assertTrue(chord_hits or simultaneous, f"accord [2 3] attendu, events={lead}")

    def test_lint_returns_max_2_lines(self):
        proc = self._eval('// @strudel-meta {"bpm": 120}\n$lead: deg("0 ~ [2 3"\n$extra: (note("c4")\n')
        self.assertEqual(proc.returncode, 4)
        err_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("ERR L")]
        self.assertGreaterEqual(len(err_lines), 1)
        self.assertLessEqual(len(err_lines), 2)
        self.assertLessEqual(len(proc.stderr), 400, "le lint ne doit jamais renvoyer une stacktrace")

    def test_compose_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(ROOT, "assets", "composition_trap_example.strudel"), encoding="utf-8") as fh:
                src = fh.read()
            rep = prod_server.compose_project(td, src, palette_id="auto", auto_master=False)
            self.assertTrue(rep.get("ok"), json.dumps(rep, default=str)[:400])
            self.assertTrue(os.path.isfile(os.path.join(td, "score_ir.json")))
            self.assertTrue(os.path.isfile(os.path.join(td, "arrangement_plan.json")))
            self.assertTrue(os.path.isfile(os.path.join(td, "handoff.txt")))
            with open(os.path.join(td, "handoff.txt"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertLessEqual(_tokens(text), 350.0)


@unittest.skipUnless(HAVE_STRUDEL, "node + @strudel requis")
class TestV41AutoPrepareAndSemanticPatch(unittest.TestCase):
    """v4.1 P5 — studio_compose(auto_prepare=True) produit seul un blueprint VALIDE contre le schéma,
    et studio_refine(semantic_patch=...) applique des opérateurs micro-tokens sur le ScoreIR."""

    def test_auto_blueprint_valid_against_schema(self):
        import jsonschema
        import prod_server
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(ROOT, "assets", "composition_trap_example.strudel"), encoding="utf-8") as fh:
                src = fh.read()
            rep = prod_server.compose_project(td, src, palette_id="auto", auto_master=False, auto_prepare=True)
            self.assertTrue(rep.get("ok"), json.dumps(rep, default=str)[:300])
            self.assertTrue(rep.get("auto_prepare"))
            bp_path = os.path.join(td, "song_blueprint.json")
            self.assertTrue(os.path.isfile(bp_path))
            with open(bp_path, encoding="utf-8") as fh:
                bp = json.load(fh)
            with open(os.path.join(ROOT, "schemas", "song_blueprint.schema.json"), encoding="utf-8") as fh:
                schema = json.load(fh)
            jsonschema.validate(bp, schema)  # lève si invalide
            self.assertGreaterEqual(len(bp["tracks"]), 1)
            self.assertGreaterEqual(len(bp["harmonic_core"]["chord_progression"]), 1)
            self.assertIn("apex_midi", bp["harmonic_core"]["motif_cell"])

    def test_semantic_patch_via_refine(self):
        import prod_server
        from score_ir import ScoreIR
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(ROOT, "assets", "composition_trap_example.strudel"), encoding="utf-8") as fh:
                src = fh.read()
            rep = prod_server.compose_project(td, src, auto_master=False)
            self.assertTrue(rep.get("ok"))
            before = ScoreIR.load(os.path.join(td, "score_ir.json"))
            lead_before = sorted({int(n.pitch) for n in before.tracks["lead"].notes})
            out = prod_server.refine_project(td, semantic_patch={"lead": {"oct": -1}}, export_mp3=False)
            self.assertTrue(out.get("ok"), json.dumps(out, default=str)[:300])
            self.assertTrue(out.get("semantic_patch", {}).get("applied"))
            after = ScoreIR.load(os.path.join(td, "score_ir.json"))
            lead_after = sorted({int(n.pitch) for n in after.tracks["lead"].notes})
            self.assertEqual(lead_after, sorted({p - 12 for p in lead_before}), "oct:-1 doit baisser d'une octave")


if __name__ == "__main__":
    unittest.main()
