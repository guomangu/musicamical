"""Garde-fous du dossier racine `DOCUMENTATION_IA/` (doc de production pour l'agent IA :
2 documents — README + guide complet — liens valides, contenu clé présent, bonnes pratiques
qualité documentées, aucun chemin /home, pointeurs depuis README/SOMMAIRE/.rules)."""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IA_DIR = os.path.join(ROOT, "DOCUMENTATION_IA")


class TestDocumentationIA(unittest.TestCase):
    def test_folder_and_two_docs_exist(self):
        self.assertTrue(os.path.isdir(IA_DIR))
        names = sorted(f for f in os.listdir(IA_DIR) if f.endswith(".md"))
        self.assertEqual(names, ["01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md", "README.md"])

    def test_docs_are_compact(self):
        for f in os.listdir(IA_DIR):
            if f.endswith(".md"):
                size = os.path.getsize(os.path.join(IA_DIR, f))
                self.assertLess(size, 30_000, f"{f} trop gros ({size} o) : 2 documents, rester compact")

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
        with open(os.path.join(IA_DIR, "01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md"), encoding="utf-8") as fh:
            guide = fh.read()
        # Partie 1 : contrat Strudel + exemple canonique gate-PASS + pre-flight
        for token in ("// @meta 140 C:minor trap_rap",
                      'deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4)',
                      "preflight_strudel_gate.py", "gap_fill", "{0, 3, 6, 7}"):
            self.assertIn(token, guide)
        # Partie 2 : façade 3 outils + scénarios + budget tokens
        for tool in ("studio_prepare", "studio_compose", "studio_refine"):
            self.assertIn(tool, guide)
        for token in ("auto_prepare", "semantic_patch", "1 800 tokens"):
            self.assertIn(token, guide)
        # Parties 3-4 : diagnostic sons + bonnes pratiques qualité développées
        for token in ("pleasantness_gate", "SWAP", 'palette_id="auto"', "samples=N",
                      "root_note", "NOMINAL_APPLIED", "RESTART session agent",
                      "organize_samples.py", "reroute_sample_path", "Bonnes pratiques"):
            self.assertIn(token, guide)
        with open(os.path.join(IA_DIR, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md", readme)
        self.assertIn("2 documents", readme.lower().replace("**2 documents**", "2 documents") + " " + readme)

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
