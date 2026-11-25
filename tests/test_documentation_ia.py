"""Garde-fous du dossier racine `DOCUMENTATION_IA/` (doc de production pour l'agent IA :
4 documents compacts, liens valides, exemple canonique + pre-flight + agréabilité, aucun /home)."""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IA_DIR = os.path.join(ROOT, "DOCUMENTATION_IA")


class TestDocumentationIA(unittest.TestCase):
    def test_folder_and_four_docs_exist(self):
        self.assertTrue(os.path.isdir(IA_DIR))
        names = sorted(f for f in os.listdir(IA_DIR) if f.endswith(".md"))
        self.assertEqual(names, ["01_COMPOSER_AVEC_STRUDEL.md", "02_PRODUIRE_LE_MORCEAU.md",
                                 "03_SONS_AGRÉABLES.md", "README.md"])

    def test_docs_are_compact(self):
        for f in os.listdir(IA_DIR):
            if f.endswith(".md"):
                size = os.path.getsize(os.path.join(IA_DIR, f))
                self.assertLess(size, 12_000, f"{f} trop gros ({size} o) : garder compact")

    def test_internal_links_resolve(self):
        link_re = re.compile(r"\]\(([^)\s]+\.md)(?:#[^)]*)?\)")
        for f in os.listdir(IA_DIR):
            if not f.endswith(".md"):
                continue
            with open(os.path.join(IA_DIR, f), encoding="utf-8") as fh:
                text = fh.read()
            for m in link_re.finditer(text):
                target = m.group(1)
                if target.startswith(("http://", "https://")):
                    continue
                resolved = os.path.normpath(os.path.join(IA_DIR, target))
                self.assertTrue(os.path.isfile(resolved), f"{f} -> {target} cassé")

    def test_key_content_present(self):
        with open(os.path.join(IA_DIR, "01_COMPOSER_AVEC_STRUDEL.md"), encoding="utf-8") as fh:
            comp = fh.read()
        self.assertIn("// @meta 140 C:minor trap_rap", comp)
        self.assertIn('deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4)', comp)  # exemple canonique gate-PASS
        self.assertIn("preflight_strudel_gate.py", comp)
        self.assertIn("gap_fill", comp)
        self.assertIn("{0, 3, 6, 7}", comp)  # intersection b2-safe
        with open(os.path.join(IA_DIR, "02_PRODUIRE_LE_MORCEAU.md"), encoding="utf-8") as fh:
            prod = fh.read()
        for tool in ("studio_prepare", "studio_compose", "studio_refine"):
            self.assertIn(tool, prod)
        self.assertIn("auto_prepare", prod)
        self.assertIn("semantic_patch", prod)
        self.assertIn("1 800 tokens", prod)
        with open(os.path.join(IA_DIR, "03_SONS_AGRÉABLES.md"), encoding="utf-8") as fh:
            sons = fh.read()
        for token in ("pleasantness_gate", "SWAP", "palette_timbre_coherence", "pick_gl_plugin",
                      "organize_samples.py", "palette_id=\"auto\"", "triton"):
            self.assertIn(token, sons)

    def test_no_hardcoded_home_paths(self):
        for f in os.listdir(IA_DIR):
            if f.endswith(".md"):
                with open(os.path.join(IA_DIR, f), encoding="utf-8") as fh:
                    self.assertNotIn("/home/", fh.read(), f)

    def test_pointed_from_readme_sommaire_rules(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            self.assertIn("DOCUMENTATION_IA/", fh.read())
        with open(os.path.join(ROOT, "docs", "00_SOMMAIRE.md"), encoding="utf-8") as fh:
            self.assertIn("DOCUMENTATION_IA/", fh.read())
        with open(os.path.join(ROOT, ".rules"), encoding="utf-8") as fh:
            self.assertIn("DOCUMENTATION_IA/", fh.read())


if __name__ == "__main__":
    unittest.main()
