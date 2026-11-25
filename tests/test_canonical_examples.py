#!/usr/bin/env python3
"""v4.1 P1 — CI des exemples canoniques : le bloc js de docs/AGENT_RUNBOOK_V4.md et
assets/composition_trap_example.strudel doivent passer lint -> eval -> pont Strudel
avec **0 correction de garde-fou** (si l'exemple de référence déclenche un fix, le CI casse)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

NODE = shutil.which("node")
STRUDEL_DEPS = os.path.isdir(os.path.join(SCRIPTS, "node_modules", "@strudel", "core"))
HAVE_STRUDEL = bool(NODE and STRUDEL_DEPS)


def extract_js_block(md_path: str) -> str:
    with open(md_path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"```js\n(.*?)```", text, re.S)
    assert m, f"pas de bloc ```js dans {md_path}"
    return m.group(1)


@unittest.skipUnless(HAVE_STRUDEL, "node + @strudel requis")
class TestCanonicalExamples(unittest.TestCase):
    def _check_source(self, src: str, label: str):
        import strudel_to_score_ir as bridge
        with tempfile.TemporaryDirectory() as tmp:
            spath = os.path.join(tmp, "composition.strudel")
            with open(spath, "w", encoding="utf-8") as fh:
                fh.write(src)
            # 1) lint : 0 erreur
            proc = subprocess.run([NODE, os.path.join(SCRIPTS, "strudel_eval.mjs"), spath, "--bars", "24", "--compact"],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, f"{label} lint/eval rc={proc.returncode}: {proc.stderr[-300:]}")
            haps = json.loads(proc.stdout)
            self.assertEqual(haps["errors"], [], f"{label}: erreurs eval {haps['errors']}")
            # 2) pont complet : 0 correction de garde-fou
            report = bridge.strudel_to_score_ir(tmp, run_eval=True, bars=24, seed=0)
            self.assertTrue(report.get("ok"), f"{label}: ok=False {json.dumps(report, default=str)[:300]}")
            af = report["autofix"]["n_changes"]
            self.assertEqual(af, 0, f"{label}: {af} corrections autofix (attendu 0) : {report['autofix']['changes'][:5]}")
            g = report["guards"]
            self.assertEqual(g.get("bass_register_clamped", 0), 0, f"{label}: octave-folds basse {g}")
            self.assertEqual(g.get("lead_apex_clamped", 0), 0, f"{label}: apex lead {g}")
            mut = report.get("mutation") or {}
            for tn, r in mut.items():
                self.assertLessEqual(r, bridge.MUTATION_RATIO_MAX, f"{label}: mutation {tn}={r}")
            self.assertEqual(report.get("diagnostics"), [], f"{label}: diagnostics {report['diagnostics']}")
            self.assertTrue(report["full_coverage"]["passed"], f"{label}: full_coverage {report['full_coverage']}")
            self.assertTrue(report["plan_valid"] is True, f"{label}: plan_valid={report['plan_valid']}")

    def test_runbook_v4_example_block(self):
        src = extract_js_block(os.path.join(ROOT, "docs", "AGENT_RUNBOOK_V4.md"))
        self._check_source(src, "AGENT_RUNBOOK_V4.md")

    def test_trap_asset_example(self):
        with open(os.path.join(ROOT, "assets", "composition_trap_example.strudel"), encoding="utf-8") as fh:
            self._check_source(fh.read(), "composition_trap_example.strudel")


if __name__ == "__main__":
    unittest.main()
