#!/usr/bin/env python3
"""v4.3 chantier D3 — SSOT strict & cohérence docs/config (critères d'acceptation du plan) :

- `config/mix_profiles.json` porte la section `sample_guard_invariants` (S_max single/multi-zone,
  `sampler_lpf_hz`, `pleasantness_caps.crest_max_db`) et `palette_scoring_weights` ;
- les consommateurs (blueprint_safety, sample_fit_guard, timbre_pleasantness, song_builder)
  lisent ces constantes via `scripts/mix_config.py` (plus de doublon codé en dur : le LPF pad
  6.0 kHz de song_builder et le CREST en dur de timbre_pleasantness ont disparu) ;
- aucune valeur chiffrée de seuil du guide `DOCUMENTATION_IA/` ne diverge du SSOT
  (`-1.2 dBTP`, `6.5 kHz`, `CREST 19.5 dB`) ;
- le bloc canonique `js` du README ne viole aucune règle du tableau des pièges
  (pas de `degradeBy` sur `$lead`) et le README reste ≤ 750 tokens (3000 cars).
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
IA_DIR = os.path.join(ROOT, "DOCUMENTATION_IA")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


class TestSampleGuardInvariantsSSOT(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "config", "mix_profiles.json"), encoding="utf-8") as fh:
            cls.cfg = json.load(fh)

    def test_config_has_sample_guard_invariants(self):
        sgi = self.cfg.get("sample_guard_invariants") or {}
        for key in ("max_semitone_shift_single_zone", "max_semitone_shift_multizone_or_derived",
                    "sampler_lpf_hz", "pleasantness_caps"):
            self.assertIn(key, sgi)
        self.assertEqual(sgi["max_semitone_shift_single_zone"]["lead_melody"], 7)
        self.assertEqual(sgi["max_semitone_shift_multizone_or_derived"]["chord_keys"], 10)
        self.assertEqual(sgi["sampler_lpf_hz"]["pad"], 6500)
        self.assertEqual(sgi["sampler_lpf_hz"]["lead"], 9500)
        self.assertEqual(sgi["pleasantness_caps"]["crest_max_db"]["lead_melody"], 19.5)
        self.assertIn("palette_scoring_weights", self.cfg)
        self.assertEqual(self.cfg["palette_scoring_weights"]["alpha_intent"], 0.45)

    def test_mix_config_accessors(self):
        import mix_config
        self.assertEqual(mix_config.max_semitone_shift_single_zone("chord_keys"), 7)
        self.assertEqual(mix_config.max_semitone_shift_multizone("pad_atmosphere"), 10)
        self.assertEqual(mix_config.sampler_lpf_hz("pad"), 6500.0)
        self.assertEqual(mix_config.sampler_lpf_hz("lead"), 9500.0)
        self.assertEqual(mix_config.crest_max_db("pad_atmosphere"), 14.5)
        w = mix_config.palette_scoring_weights()
        self.assertAlmostEqual(w["alpha"] + w["beta"] + w["gamma"], 1.0)
        self.assertEqual(w["lambda"], 0.25)

    def test_consumers_read_ssot_no_hardcoded_duplicates(self):
        consumers = {
            "blueprint_safety.py": ["mix_config", "max_semitone_shift_st"],
            "sample_fit_guard.py": ["sample_guard_invariants", "max_semitone_shift_single_zone"],
            "timbre_pleasantness.py": ["mix_config", "crest_max_db"],
            "song_builder.py": ["sampler_lpf_hz"],
        }
        for fname, needles in consumers.items():
            src = open(os.path.join(SCRIPTS, fname), encoding="utf-8").read()
            ast.parse(src)
            for needle in needles:
                self.assertIn(needle, src, f"{fname} doit lire le SSOT via mix_config ({needle})")
        # plus de constantes dures litigieuses
        sb = open(os.path.join(SCRIPTS, "song_builder.py"), encoding="utf-8").read()
        self.assertNotIn("spec.lpf_hz = 6000.0", sb, "LPF pad 6.0 kHz codé en dur (contredit le SSOT 6.5 kHz)")
        tp = open(os.path.join(SCRIPTS, "timbre_pleasantness.py"), encoding="utf-8").read()
        self.assertIn("_crest_max_db_tonal", tp)

    def test_shift_caps_flow_from_config(self):
        from sample_fit_guard import effective_shift_caps, register_fit
        cat, cap_multi, cap_single = effective_shift_caps({"filepath": "piano_c4.wav"}, role="lead_melody")
        self.assertEqual(cap_single, 7)
        self.assertEqual(cap_multi, 9)
        rf = register_fit(60, [60, 67, 69], {"filepath": "piano_c4.wav"}, role="lead_melody")
        self.assertEqual(rf["cap_single_zone"], 7)
        self.assertTrue(rf["needs_multizone"], "s_max=9 > 7 -> multizone requis (D4)")


class TestDocsMatchSSOT(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(IA_DIR, "01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md"), encoding="utf-8") as fh:
            cls.guide = fh.read()
        with open(os.path.join(IA_DIR, "README.md"), encoding="utf-8") as fh:
            cls.readme = fh.read()
        with open(os.path.join(ROOT, "config", "mix_profiles.json"), encoding="utf-8") as fh:
            cls.cfg = json.load(fh)

    def test_numeric_thresholds_match_config(self):
        import mix_config
        ceiling = mix_config.get_invariant("master_limiter_ceiling_dbtp")
        self.assertEqual(ceiling, -1.2)
        self.assertIn("-1.2 dBTP", self.guide)
        self.assertEqual(mix_config.source_lpf_floor_hz("chords"), 6500.0)
        self.assertIn("6.5 kHz", self.guide)
        self.assertEqual(mix_config.crest_max_db("lead_melody"), 19.5)
        self.assertIn("19,5 dB", self.guide)

    def test_canonical_block_has_no_degradeby_on_lead(self):
        m = re.search(r"```js\n(.*?)```", self.readme, re.S)
        self.assertIsNotNone(m)
        block = m.group(1)
        lead_lines = [ln for ln in block.split("\n") if ln.strip().startswith("$lead")]
        self.assertTrue(lead_lines, "le bloc canonique doit contenir une piste $lead")
        for ln in lead_lines:
            self.assertNotIn("degradeBy", ln, "piège §7 : jamais de degradeBy sur le $lead")
        # la variation seedée reste sur la rythmique
        self.assertIn('hats: "rolls"', block)

    def test_readme_under_750_tokens(self):
        self.assertLessEqual(len(self.readme), 3000, f"README = {len(self.readme)} cars > 3000 (750 tokens)")

    def test_guide_under_30k_and_contradictions_removed(self):
        self.assertLess(os.path.getsize(os.path.join(IA_DIR, "01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md")), 30_000)
        # plus de contournement V=0.0 imposé dans le tableau @meta
        self.assertNotIn("**garder V=0.0**", self.guide)
        # Partie 2 cohérente avec la politique v4.2 (vrais samples en priorité)
        self.assertIn("vrais samples", self.guide)
        self.assertNotIn("palette `auto` = synthés natifs", self.guide)
        # dur= multi-signatures documenté
        self.assertIn("B_bar", self.guide)
        self.assertIn("dur=", self.guide)


if __name__ == "__main__":
    unittest.main()
