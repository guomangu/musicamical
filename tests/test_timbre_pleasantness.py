"""v4.1 — tests du gate d'agréabilité des timbres (`scripts/timbre_pleasantness.py`) et de
l'organisation des samples racine (`scripts/organize_samples.py`).

1. `pleasantness_gate` : PASS propre / FIX proche des plafonds / SWAP hors plafonds durs par rôle ;
2. `plomp_levelt_roughness` : paire de partiels proches > paire éloignée ; `interval_consonance` octave > triton ;
3. `palette_timbre_coherence` : collision Bark24 graves pénalise plus qu'une répartition aigu/grave ;
4. `pick_gl_plugin` : plugin du registre, archetype selon (profil, valence) ;
5. câblage : `propose_palettes` et `select_best_from_db` écartent le sample « dur » (SWAP) ;
   `prod_server._resolve_palette` branche un vrai sample de l'index sur le blueprint (auto) ;
6. `organize_samples.plan_moves` : fichiers racine classés par nom de fichier, déplacement sans écrasement.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
INDEX_DIR = os.path.join(ROOT, "mcp-servers", "audio-index")
PROD_DIR = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
for _p in (SCRIPTS, INDEX_DIR, PROD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import timbre_pleasantness as tp  # noqa: E402
import organize_samples as org  # noqa: E402

SR = 22050


def _sine(freq=440.0, amp=0.5, dur=1.0, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * t)


class TestPleasantnessGate(unittest.TestCase):
    def test_pass_clean(self):
        g = tp.pleasantness_gate({"r_harsh": 0.1, "roughness": 0.05, "peak_harsh_db": 6.0,
                                  "comfort_score": 0.9, "centroid_hz": 900.0, "crest_factor_db": 9.0},
                                 "lead_melody")
        self.assertEqual(g["verdict"], "PASS", g)
        self.assertGreaterEqual(g["score"], 0.9)

    def test_swap_harsh_lead(self):
        g = tp.pleasantness_gate({"r_harsh": 0.8, "roughness": 0.4, "peak_harsh_db": 16.0,
                                  "comfort_score": 0.9, "centroid_hz": 900.0}, "lead_melody")
        self.assertEqual(g["verdict"], "SWAP", g)
        self.assertTrue(g["reasons"])

    def test_swap_siffant_lead(self):
        g = tp.pleasantness_gate({"r_harsh": 0.1, "roughness": 0.05, "peak_harsh_db": 6.0,
                                  "comfort_score": 0.95, "centroid_hz": 8500.0}, "lead_melody")
        self.assertEqual(g["verdict"], "SWAP", g)
        self.assertTrue(any("centroid" in r for r in g["reasons"]))

    def test_fix_near_caps(self):
        g = tp.pleasantness_gate({"r_harsh": 0.33, "roughness": 0.05, "peak_harsh_db": 6.0,
                                  "comfort_score": 0.95, "centroid_hz": 900.0}, "lead_melody")
        self.assertEqual(g["verdict"], "FIX", g)  # 0.33 > 0.8*0.35 = 0.28

    def test_hihat_tolerates_more(self):
        harsh = tp.pleasantness_gate({"r_harsh": 0.55, "roughness": 0.40, "peak_harsh_db": 17.0,
                                      "comfort_score": 0.8, "centroid_hz": 9000.0}, "hihat_perc")
        self.assertNotEqual(harsh["verdict"], "SWAP", harsh)

    def test_plomp_levelt_roughness(self):
        f = np.array([1000.0, 1030.0, 5000.0])
        m = np.array([1.0, 0.9, 0.8])
        r_close = tp.plomp_levelt_roughness(f, m)
        f2 = np.array([1000.0, 2000.0, 5000.0])
        r_far = tp.plomp_levelt_roughness(f2, m)
        self.assertGreater(r_close, r_far, (r_close, r_far))

    def test_interval_consonance(self):
        self.assertGreater(tp.interval_consonance(440.0, 880.0), 0.9)   # octave
        self.assertGreater(tp.interval_consonance(440.0, 660.0), 0.8)   # quinte
        self.assertLess(tp.interval_consonance(440.0, 622.0), 0.2)       # triton

    def test_palette_coherence(self):
        low = [0.0] * 24
        low[0], low[1], low[2] = 0.6, 0.3, 0.1
        high = [0.0] * 24
        high[20] = 1.0
        bad = tp.palette_timbre_coherence([
            {"role": "bass_sub", "bark_24": low, "centroid_hz": 200.0},
            {"role": "kick", "bark_24": low, "centroid_hz": 150.0},
        ])
        good = tp.palette_timbre_coherence([
            {"role": "bass_sub", "bark_24": low, "centroid_hz": 200.0},
            {"role": "lead_melody", "bark_24": high, "centroid_hz": 6000.0},
        ])
        self.assertLess(bad["score"], good["score"], (bad, good))
        self.assertIsNotNone(bad["max_bark_collision"])

    def test_pick_gl_plugin(self):
        from gl_dsl import SYNTH_REGISTRY
        p = tp.pick_gl_plugin("lead", "trap_rap", 0.0)
        self.assertIn(p["plugin"], SYNTH_REGISTRY)
        p_dark = tp.pick_gl_plugin("lead", "trap_rap", -0.8)
        self.assertIn(p_dark["plugin"], SYNTH_REGISTRY)
        # garde-fou confort : attaque/release/cutoff bornés sur les rôles tonaux
        self.assertGreaterEqual(p["params"].get("attack_ms", 2.0), 2.0)
        self.assertLessEqual(p["params"].get("cutoff_hz", 9000.0), 9000.0)

    def test_pick_gl_plugin_percussive_uses_fallback(self):
        # les rôles percussifs ne doivent PAS hériter d'un archetype tonal (basse/lead)
        self.assertEqual(tp.pick_gl_plugin("kick", "trap_rap", 0.0)["plugin"], "gl.kick")
        self.assertEqual(tp.pick_gl_plugin("snare", "trap_rap", 0.0)["plugin"], "gl.snare")
        self.assertEqual(tp.pick_gl_plugin("hihat", "trap_rap", 0.0)["plugin"], "gl.hat")

    def test_pick_gl_plugin_forces_comfort_even_if_archetype_harsh(self):
        import gl_dsl
        orig = gl_dsl.resolve_archetype
        gl_dsl.resolve_archetype = lambda profile, role, valence=0.0: {
            "plugin": "gl.warmsynth",
            "params": {"attack_ms": 0.2, "release_ms": 10.0, "cutoff_hz": 16000.0},
        }
        try:
            p = tp.pick_gl_plugin("lead_melody", "trap_rap", 0.0)
        finally:
            gl_dsl.resolve_archetype = orig
        self.assertGreaterEqual(p["params"]["attack_ms"], 2.0)
        self.assertGreaterEqual(p["params"]["release_ms"], 60.0)
        self.assertLessEqual(p["params"]["cutoff_hz"], 9000.0)


class TestPaletteWiring(unittest.TestCase):
    def _make_db(self, td: str) -> str:
        import indexer
        sdir = os.path.join(td, "samples")
        os.makedirs(sdir)
        sf.write(os.path.join(sdir, "lead_clean.wav"), _sine(440.0, 0.4).astype(np.float32), SR)
        # sample « dur » : 3.5 kHz fort (r_harsh élevé) mais passe le gate v2 (pas clippé)
        sf.write(os.path.join(sdir, "lead_harsh.wav"), _sine(3500.0, 0.7).astype(np.float32), SR)
        db = os.path.join(td, "samples.db")
        indexer.index_directory(samples_dir=sdir, db_path=db, workers=1)
        return db

    def test_propose_palettes_prefers_pleasant(self):
        from palette_builder import propose_palettes
        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            res = propose_palettes(mix_profile="trap_rap", target_key="C minor",
                                   roles=["lead_melody"], allow_gl_synths=False, db_path=db)
            pals = res.get("palettes") or []
            self.assertTrue(pals)
            slots = pals[0].get("slots") or []
            self.assertTrue(slots)
            paths = [str(s.get("filepath", "")) for s in slots]
            self.assertFalse(any("harsh" in p for p in paths), paths)
            self.assertTrue(any("clean" in p for p in paths), paths)
            self.assertIn("timbre_coherence", pals[0])

    def test_select_excludes_swap(self):
        import select_sample
        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            res = select_sample.select_best_from_db(db_path=db, role="lead_melody", top_k=5, with_clap=False)
            cands = res.get("candidates", [])
            paths = [str(c.get("path", "")) for c in cands] + [str((res.get("best") or {}).get("path", ""))]
            self.assertFalse(any("harsh" in p for p in paths), paths)

    def test_resolve_palette_binds_real_sample(self):
        import indexer
        import prod_server
        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            old_db = indexer.DEFAULT_DB_PATH
            indexer.DEFAULT_DB_PATH = db
            try:
                proj = os.path.join(td, "proj")
                os.makedirs(proj)
                bp = {
                    "title": "t", "genre": "trap_rap", "mix_profile": "trap_rap", "has_drums": True,
                    "seed": 1, "emotion_vector": {"valence": 0.0, "tension": 0.5, "energy": 0.7, "depth": 0.5},
                    "bpm": 140.0, "key": "C", "harmonic_core": {"key": "C", "mode": "minor"},
                    "tracks": [{"name": "lead", "role": "lead", "source_type": "gridline_native",
                                "plugin": "gl.warmsynth", "mixer_track": 1}],
                }
                out = prod_server._resolve_palette(proj, bp, "auto")
                self.assertTrue(out.get("resolved"), out)
                tr = bp["tracks"][0]
                if out.get("n_samples", 0) >= 1:
                    self.assertEqual(tr["source_type"], "sample")
                    self.assertTrue(tr.get("sample_path"))
                    self.assertIsNotNone(tr.get("root_note"))
                    self.assertIn("palette", bp)
            finally:
                indexer.DEFAULT_DB_PATH = old_db


class TestPaletteResolutionV2(unittest.TestCase):
    """v4.1 — _resolve_palette branche les vrais samples pour TOUT palette_id et préfère
    la palette la plus riche en samples réels (anti « 100 % synthés gl.* malgré des samples fournis »)."""

    def _bp(self):
        return {
            "title": "t", "genre": "trap_rap", "mix_profile": "trap_rap", "has_drums": True,
            "seed": 1, "emotion_vector": {"valence": 0.0, "tension": 0.5, "energy": 0.7, "depth": 0.5},
            "bpm": 140.0, "key": "C", "harmonic_core": {"key": "C", "mode": "minor"},
            "tracks": [{"name": "kick", "role": "drums", "source_type": "gridline_native",
                        "plugin": "gl.kick", "mixer_track": 1},
                       {"name": "lead", "role": "lead", "source_type": "gridline_native",
                        "plugin": "gl.warmsynth", "mixer_track": 2}],
        }

    def _patch_propose(self, td):
        import palette_builder
        orig = palette_builder.propose_palettes
        kick_wav = os.path.join(td, "kick.wav")
        lead_wav = os.path.join(td, "lead.wav")
        for p in (kick_wav, lead_wav):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("x")  # fichier réel : reroute_sample_path le garde

        def fake_propose(**kwargs):
            return {"palettes": [
                {"palette_id": "palette_A", "score": 0.99, "theme": "synthetic",
                 "slots": [{"role": "kick", "source_type": "gridline_native", "plugin": "gl.kick",
                            "filepath": None, "midi_root": None, "comfort_score": 0.9},
                           {"role": "lead_melody", "source_type": "gridline_native", "plugin": "gl.warmsynth",
                            "filepath": None, "midi_root": None, "comfort_score": 0.9}]},
                {"palette_id": "palette_B", "score": 0.50, "theme": "organic",
                 "slots": [{"role": "kick", "source_type": "sample", "filepath": kick_wav,
                            "midi_root": 36, "recommended_fine_tune_ct": 3, "comfort_score": 0.9,
                            "centroid_hz": 200.0},
                           {"role": "lead_melody", "source_type": "sample", "filepath": lead_wav,
                            "midi_root": 60, "recommended_fine_tune_ct": 0, "comfort_score": 0.9,
                            "centroid_hz": 1200.0}]},
            ]}

        palette_builder.propose_palettes = fake_propose
        return orig

    def test_auto_prefers_sample_rich_palette_over_higher_score_synth(self):
        import prod_server
        import palette_builder
        with tempfile.TemporaryDirectory() as td:
            orig = self._patch_propose(td)
            try:
                proj = os.path.join(td, "proj")
                os.makedirs(proj)
                bp = self._bp()
                out = prod_server._resolve_palette(proj, bp, "auto")
                self.assertTrue(out.get("resolved"), out)
                self.assertEqual(out.get("palette_id"), "palette_B")  # + de samples réels, malgré score plus faible
                self.assertEqual(out.get("n_samples"), 2, out)
                kick = bp["tracks"][0]
                self.assertEqual(kick["source_type"], "sample")
                self.assertEqual(kick["sample_path"], os.path.join(td, "kick.wav"))  # vrai fichier -> branché
                self.assertEqual(kick["root_note"], 36)
                self.assertEqual(kick["fine_tune_ct"], 3)
                self.assertIn("palette", bp)
            finally:
                palette_builder.propose_palettes = orig

    def test_named_palette_id_also_binds(self):
        import prod_server
        import palette_builder
        with tempfile.TemporaryDirectory() as td:
            orig = self._patch_propose(td)
            try:
                proj = os.path.join(td, "proj")
                os.makedirs(proj)
                bp = self._bp()
                out = prod_server._resolve_palette(proj, bp, "palette_A")
                self.assertTrue(out.get("resolved"), out)
                self.assertEqual(out.get("palette_id"), "palette_A")
                # palette_A = 100 % synthés -> les pistes restent gl.* (jamais de sample sans root_note)
                self.assertEqual(out.get("n_samples"), 0, out)
                for tr in bp["tracks"]:
                    self.assertEqual(tr["source_type"], "gridline_native")
            finally:
                palette_builder.propose_palettes = orig

    def test_palette_line_visible_in_summary_and_handoff(self):
        import prod_server
        line = prod_server._palette_line({"palette": {"palette_id": "palette_B", "n_samples": 5, "theme": "organic"}})
        self.assertIn("palette=palette_B", line)
        self.assertIn("samples=5", line)
        self.assertEqual(prod_server._palette_line({}), "palette=n/a")
        s = prod_server._summarize({"ok": True, "n_tracks": 8, "n_notes": 100, "seed": 1,
                                    "palette": {"palette_id": "palette_B", "n_samples": 5}})
        self.assertIn("palette=palette_B samples=5", s)

    def test_ensure_blueprint_uses_archetype_for_drums_too(self):
        import prod_server
        from gl_dsl import SYNTH_REGISTRY
        with tempfile.TemporaryDirectory() as td:
            haps = {"meta": {"mix_profile": "trap_rap", "valence": 0.0, "bpm": 140, "key": "C",
                             "mode": "minor", "progression": ["Cm7"],
                             "sections": [{"name": "song", "bars": 8}]},
                    "tracks": {"kick": {}, "lead": {}}}
            with open(os.path.join(td, "composition.strudel.haps.json"), "w", encoding="utf-8") as fh:
                json.dump(haps, fh)
            bp = prod_server._ensure_blueprint(td, {})
            by_name = {t["name"]: t for t in bp["tracks"]}
            self.assertEqual(by_name["kick"]["plugin"], "gl.kick")  # percussif -> repli gl.*, pas archetype basse
            self.assertIn(by_name["lead"]["plugin"], SYNTH_REGISTRY)


class TestIndexerQ2Backfill(unittest.TestCase):
    def test_q2_backfilled_for_rows_with_dsp_but_no_q2(self):
        import sqlite3
        import indexer
        with tempfile.TemporaryDirectory() as td:
            sdir = os.path.join(td, "samples")
            os.makedirs(sdir)
            sf.write(os.path.join(sdir, "kick_a.wav"), _sine(60.0, 0.8, 0.5).astype(np.float32), SR)
            db = os.path.join(td, "samples.db")
            indexer.index_directory(samples_dir=sdir, db_path=db, workers=1)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            conn.execute("DELETE FROM sample_quality_v2")
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sample_quality_v2").fetchone()[0], 0)
            stats = indexer.populate_dsp_metrics(conn, force=False, workers=1)
            after = conn.execute("SELECT COUNT(*) FROM sample_quality_v2").fetchone()[0]
            conn.close()
            self.assertGreater(after, 0, "le backfill q2 doit re-analyser les rows DSP existantes sans q2")
            self.assertGreaterEqual(stats.get("q2_pass", 0) + stats.get("q2_rejected", 0), 1, stats)


class TestOrganizeSamples(unittest.TestCase):
    def test_category_for_filename(self):
        self.assertEqual(org.category_for_filename("808_punch_clean.wav"), "808")
        self.assertEqual(org.category_for_filename("10 Kick -hot.wav"), "kick")
        self.assertEqual(org.category_for_filename("chant_hi_c4.wav"), "vocal")
        self.assertEqual(org.category_for_filename("clap_clean.wav"), "clap")
        self.assertEqual(org.category_for_filename("mystery_xyz.wav"), "other")

    def test_plan_and_apply_moves(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = os.path.join(td, "samples")
            os.makedirs(sdir)
            for name in ("808_punch.wav", "kick_hot.wav", "chant_c4.wav", "note.txt"):
                with open(os.path.join(sdir, name), "w") as fh:
                    fh.write("x")
            os.makedirs(os.path.join(sdir, "808"))
            with open(os.path.join(sdir, "808", "808_punch.wav"), "w") as fh:
                fh.write("deja la")
            plan = org.plan_moves(sdir)
            self.assertEqual(len(plan), 3)  # le .txt ignoré
            cats = {os.path.basename(p["src"]): p["category"] for p in plan}
            self.assertEqual(cats["808_punch.wav"], "808")
            self.assertEqual(cats["kick_hot.wav"], "kick")
            self.assertEqual(cats["chant_c4.wav"], "vocal")
            # collision : 808_punch.wav existe déjà dans 808/ -> destination suffixée, pas d'écrasement
            dest = next(p["dest"] for p in plan if p["src"].endswith("808_punch.wav"))
            self.assertIn("-2", dest)
            done = org.apply_moves(plan)
            self.assertEqual(len(done), 3)
            self.assertTrue(os.path.isfile(os.path.join(sdir, "808", "808_punch-2.wav")))
            with open(os.path.join(sdir, "808", "808_punch.wav"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "deja la")
            self.assertFalse(os.path.exists(os.path.join(sdir, "kick_hot.wav")))


if __name__ == "__main__":
    unittest.main()
