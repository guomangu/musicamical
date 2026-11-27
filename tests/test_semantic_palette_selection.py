#!/usr/bin/env python3
"""v4.3 chantier D1 — sélection sémantique de palette par `intent` (score composite S(p)).

Critères d'acceptation du plan v4.3 :
- avec `intent="dark hypnotic minimal mental rap, muted keys"`, une palette sombre 7/8
  (vrais samples) bat une palette lumineuse 8/8 hors-sujet (couverture saturante à 85 %) ;
- `studio_compose(..., intent=...)` transmet l'intention à `propose_palettes` (le flux 1 appel
  sélectionne la même palette que le flux 2 temps `studio_prepare` + `studio_compose`) ;
- sans intent, le comportement v4.2 (« max samples » d'abord) est conservé.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
PROD = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
INDEX = os.path.join(ROOT, "mcp-servers", "audio-index")
for _d in (SCRIPTS, PROD, INDEX):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from indexer import load_semantic_anchors  # noqa: E402
from palette_builder import (  # noqa: E402
    DEFAULT_ANCHORS_NPZ,
    build_intent_vector,
    composite_palette_score,
    palette_intent_score,
)

DARK_ANCHORS = ["mood_dark_nocturnal", "mood_sad_melancholic", "bri_dark_warm", "mood_hypnotic_minimal"]
BRIGHT_ANCHORS = ["bri_bright_shimmer", "bri_clear_present", "mood_uplifting_joyful", "mood_energetic_club"]


def _mean_vec(anchors: dict, ids: list[str]) -> np.ndarray:
    emb = anchors["embeddings"]
    idxs = [anchors["ids"].index(a) for a in ids if a in anchors["ids"]]
    v = np.mean(emb[idxs], axis=0)
    return (v / np.linalg.norm(v)).astype(np.float32)


def _palette(n_samples: int, n_tracks: int, clap_vec) -> tuple[dict, list[dict]]:
    slots = []
    items = []
    roles = ["kick", "snare_clap", "hihat_perc", "bass_sub", "chord_keys", "pad_atmosphere", "lead_melody", "fx"]
    for i in range(n_tracks):
        role = roles[i % len(roles)]
        is_sample = i < n_samples
        slot = {"role": role, "source_type": "sample" if is_sample else "gridline_native",
                "filepath": f"/tmp/fake_{role}.wav" if is_sample else None}
        slots.append(slot)
        it = dict(slot)
        it["_clap_vec"] = clap_vec
        items.append(it)
    pal = {"slots": slots, "timbre_coherence": {"score": 0.8}}
    return pal, items


class TestIntentVector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anchors = load_semantic_anchors(DEFAULT_ANCHORS_NPZ)

    def test_anchors_loaded(self):
        self.assertIsNotNone(self.anchors)
        self.assertGreaterEqual(len(self.anchors["ids"]), 60)

    def test_intent_vector_prefers_dark_anchors(self):
        iv = build_intent_vector("dark hypnotic minimal mental rap, deep 808, muted keys", self.anchors)
        self.assertIsNotNone(iv)
        dark = _mean_vec(self.anchors, DARK_ANCHORS)
        bright = _mean_vec(self.anchors, BRIGHT_ANCHORS)
        sim_dark = float(np.dot(iv, dark))
        sim_bright = float(np.dot(iv, bright))
        self.assertGreater(sim_dark, sim_bright, f"intent dark doit coller aux ancres sombres ({sim_dark:.3f} vs {sim_bright:.3f})")

    def test_intent_vector_none_on_empty(self):
        self.assertIsNone(build_intent_vector("", self.anchors))
        self.assertIsNone(build_intent_vector("zzz qqq", self.anchors))


class TestCompositePaletteScore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anchors = load_semantic_anchors(DEFAULT_ANCHORS_NPZ)
        cls.iv = build_intent_vector("dark hypnotic minimal mental rap, deep 808, muted keys", cls.anchors)

    def test_dark_7_8_beats_bright_8_8(self):
        dark_vec = _mean_vec(self.anchors, DARK_ANCHORS)
        bright_vec = _mean_vec(self.anchors, BRIGHT_ANCHORS)
        dark_pal, dark_items = _palette(7, 8, dark_vec)
        bright_pal, bright_items = _palette(8, 8, bright_vec)
        s_dark = composite_palette_score(dark_pal, items=dark_items, intent_vec=self.iv)["score"]
        s_bright = composite_palette_score(bright_pal, items=bright_items, intent_vec=self.iv)["score"]
        self.assertGreater(s_dark, s_bright, f"palette sombre 7/8 ({s_dark}) doit battre lumineuse 8/8 ({s_bright})")
        self.assertLessEqual(s_dark, 1.0)
        self.assertGreaterEqual(s_dark, 0.0)

    def test_no_intent_transfers_alpha_on_beta(self):
        dark_pal, dark_items = _palette(7, 8, _mean_vec(self.anchors, DARK_ANCHORS))
        comp = composite_palette_score(dark_pal, items=dark_items, intent_vec=None)
        # sans intent : S_intent neutre 0.5, alpha transféré sur beta -> pas de None
        self.assertIsNotNone(comp["score"])
        self.assertFalse(comp["clap_off"])

    def test_clap_off_when_no_vectors(self):
        pal, items = _palette(8, 8, None)
        for it in items:
            it["_clap_vec"] = None
        comp = composite_palette_score(pal, items=items, intent_vec=self.iv)
        self.assertTrue(comp["clap_off"])

    def test_fallback_penalty_on_pivot_role(self):
        vec = _mean_vec(self.anchors, DARK_ANCHORS)
        pal, items = _palette(3, 8, vec)  # bass_sub (index 3) reste en repli synth
        pool = {"bass_sub": [{"source_type": "sample", "filepath": "/tmp/ok.wav",
                              "_policy_tier": "preferred", "_gate_verdict": "PASS", "_range_ok": True}]}
        comp = composite_palette_score(pal, items=items, intent_vec=self.iv, sample_pool=pool)
        self.assertGreater(comp["p_fallback"], 0.0)


class TestIntentPlumbing(unittest.TestCase):
    def test_propose_palettes_accepts_intent(self):
        import palette_builder
        sig = inspect.signature(palette_builder.propose_palettes)
        self.assertIn("intent", sig.parameters)
        self.assertIn("palette_strategy", sig.parameters)

    def test_resolve_palette_forwards_intent_and_ranks_by_composite(self):
        import prod_server
        import palette_builder
        captured: dict = {}

        def fake_propose(**kw):
            captured.update(kw)
            vec = None
            anchors = load_semantic_anchors(DEFAULT_ANCHORS_NPZ)
            iv = build_intent_vector(kw.get("intent") or "", anchors)
            dark_pal, dark_items = _palette(7, 8, _mean_vec(anchors, DARK_ANCHORS))
            bright_pal, bright_items = _palette(8, 8, _mean_vec(anchors, BRIGHT_ANCHORS))
            for p, its in ((dark_pal, dark_items), (bright_pal, bright_items)):
                p["palette_id"] = "dark" if p is dark_pal else "bright"
                p["score"] = composite_palette_score(p, items=its, intent_vec=iv)["score"]
                p["slots"] = its
                for it in its:
                    it.pop("_clap_vec", None)
            return {"palettes": [bright_pal, dark_pal], "selection_trace": {"total_excluded": 0},
                    "clap_coverage": 1.0, "clap_off": False}

        orig = palette_builder.propose_palettes
        palette_builder.propose_palettes = fake_propose
        try:
            with tempfile.TemporaryDirectory() as tmp:
                bp = {"mix_profile": "trap_rap", "key": "C", "harmonic_core": {"mode": "minor"},
                      "bpm": 140, "emotion_vector": {"valence": -0.6}, "tracks": [], "seed": 1}
                out = prod_server._resolve_palette(tmp, bp, "auto", intent="dark hypnotic minimal mental rap, muted keys")
        finally:
            palette_builder.propose_palettes = orig
        self.assertEqual(captured.get("intent"), "dark hypnotic minimal mental rap, muted keys")
        self.assertTrue(out.get("resolved"))
        self.assertEqual(out.get("palette_id"), "dark", "le score composite doit choisir la palette sombre malgré 7/8 < 8/8")

    def test_compose_project_has_intent_and_render_mode_params(self):
        import prod_server
        sig = inspect.signature(prod_server.compose_project)
        for name in ("intent", "palette_strategy", "render_mode"):
            self.assertIn(name, sig.parameters)
        sig2 = inspect.signature(prod_server.studio_compose)
        for name in ("intent", "palette_strategy", "render_mode"):
            self.assertIn(name, sig2.parameters)


if __name__ == "__main__":
    unittest.main()
