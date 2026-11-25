"""Régression des correctifs « son inaudible/désagréable + volumes inégaux » (session 8552bccb) :
1. reroute des chemins de samples stale (ancienne racine) + exclusion fichiers absents / q2 REJECTED ;
2. root_note des PERCUSSIONS = hauteur notée du kit (kick=36...), jamais le pitch détecté du one-shot ;
3. détection serveur MCP périmé après git pull (alerte RESTART session agent) ;
4. gain staging nominal en calibrage ABSOLU + rapport before/after correct (NOMINAL_APPLIED) ;
5. clamp n_fft dans sample_quality_v2 (plus de UserWarning librosa)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import warnings

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
INDEX_DIR = os.path.join(ROOT, "mcp-servers", "audio-index")
PROD_DIR = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
for _p in (SCRIPTS, INDEX_DIR, PROD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

SR = 22050


def _sine(freq=440.0, amp=0.5, dur=0.5, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class TestRerouteSamplePath(unittest.TestCase):
    def test_existing_path_returned_as_is(self):
        import indexer
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "a.wav")
            with open(p, "w") as fh:
                fh.write("x")
            self.assertEqual(indexer.reroute_sample_path(p), os.path.abspath(p))

    def test_stale_root_rerouted_under_new_root(self):
        import indexer
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(td, "samples", "808", "k.wav")
            os.makedirs(os.path.dirname(real))
            with open(real, "w") as fh:
                fh.write("x")
            stale = "/home/ancien/musicos/samples/808/k.wav"
            old = indexer.STUDIO_DIR
            indexer.STUDIO_DIR = td
            try:
                self.assertEqual(indexer.reroute_sample_path(stale), real)
            finally:
                indexer.STUDIO_DIR = old

    def test_missing_returns_none(self):
        import indexer
        self.assertIsNone(indexer.reroute_sample_path("/nowhere/samples/x.wav"))
        self.assertIsNone(indexer.reroute_sample_path(""))


class TestPaletteBuilderFilters(unittest.TestCase):
    def _make_db(self, td):
        import indexer
        sdir = os.path.join(td, "samples")
        os.makedirs(sdir)
        sf.write(os.path.join(sdir, "lead_clean.wav"), _sine(440.0, 0.4), SR)
        sf.write(os.path.join(sdir, "lead_gone.wav"), _sine(660.0, 0.4), SR)
        db = os.path.join(td, "samples.db")
        indexer.index_directory(samples_dir=sdir, db_path=db, workers=1)
        return db, sdir

    def test_q2_rejected_and_missing_files_excluded(self):
        import sqlite3
        from palette_builder import propose_palettes
        with tempfile.TemporaryDirectory() as td:
            db, sdir = self._make_db(td)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            sid = conn.execute("SELECT id FROM samples WHERE path LIKE '%lead_clean%'").fetchone()["id"]
            conn.execute("UPDATE sample_quality_v2 SET status='REJECTED' WHERE sample_id=?", (sid,))
            conn.commit()
            conn.close()
            os.remove(os.path.join(sdir, "lead_gone.wav"))  # fichier supprimé après indexation
            res = propose_palettes(mix_profile="trap_rap", target_key="C minor",
                                   roles=["lead_melody"], allow_gl_synths=False, db_path=db)
            paths = [str(s.get("filepath", "")) for p in (res.get("palettes") or []) for s in (p.get("slots") or [])]
            self.assertFalse(any("lead_clean" in p for p in paths), f"REJECTED toujours proposé: {paths}")
            self.assertFalse(any("lead_gone" in p for p in paths), f"fichier absent toujours proposé: {paths}")


class TestResolvePaletteRobust(unittest.TestCase):
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

    def test_drum_root_note_is_notated_pitch_and_stale_path_rerouted(self):
        import indexer
        import palette_builder
        import prod_server
        with tempfile.TemporaryDirectory() as td:
            # vrai fichier sous <td>/samples/808/kick.wav ; la DB pointerait vers une vieille racine
            real = os.path.join(td, "samples", "808", "kick.wav")
            os.makedirs(os.path.dirname(real))
            with open(real, "w") as fh:
                fh.write("x")
            stale = "/home/olduser/musicos/samples/808/kick.wav"
            old_dir = indexer.STUDIO_DIR
            indexer.STUDIO_DIR = td
            # score_ir : le kit joue des notes à 36 (kick GM)
            proj = os.path.join(td, "proj")
            os.makedirs(proj)
            with open(os.path.join(proj, "score_ir.json"), "w", encoding="utf-8") as fh:
                json.dump({"tracks": {"kick": {"notes": [{"pitch": 36}, {"pitch": 36}, {"pitch": 35}]},
                                      "lead": {"notes": [{"pitch": 63}]}}}, fh)
            orig = palette_builder.propose_palettes

            def fake_propose(**kwargs):
                return {"palettes": [{"palette_id": "palette_A", "score": 0.9, "theme": "organic",
                                      "slots": [
                                          {"role": "kick", "source_type": "sample", "filepath": stale,
                                           "midi_root": 84, "recommended_fine_tune_ct": 0,
                                           "comfort_score": 0.9, "centroid_hz": 150.0},
                                          {"role": "lead_melody", "source_type": "sample",
                                           "filepath": "/nonexistent/lead.wav", "midi_root": 60,
                                           "recommended_fine_tune_ct": 5, "comfort_score": 0.9}]}]}
            palette_builder.propose_palettes = fake_propose
            try:
                bp = self._bp()
                out = prod_server._resolve_palette(proj, bp, "auto")
                self.assertTrue(out.get("resolved"), out)
                kick = bp["tracks"][0]
                self.assertEqual(kick["source_type"], "sample")
                self.assertEqual(kick["sample_path"], real)          # chemin re-rooté
                self.assertEqual(kick["root_note"], 36)               # hauteur notée, PAS 84
                lead = bp["tracks"][1]
                # lead : chemin introuvable -> repli synthé (jamais de sample fantôme)
                self.assertEqual(lead["source_type"], "gridline_native")
                self.assertNotIn("sample_path", lead)
            finally:
                palette_builder.propose_palettes = orig
                indexer.STUDIO_DIR = old_dir

    def test_server_stale_detection(self):
        import prod_server
        self.assertFalse(prod_server._server_stale())  # code inchangé depuis l'import
        old = prod_server._SELF_SHA_AT_BOOT
        prod_server._SELF_SHA_AT_BOOT = "deadbeef000"
        try:
            self.assertTrue(prod_server._server_stale())
        finally:
            prod_server._SELF_SHA_AT_BOOT = old


class TestGainStagingNominalAbsolute(unittest.TestCase):
    def test_anchor_untouched_others_set_absolute(self):
        import song_builder
        from gain_staging_lib import apply_nominal_balance
        chans = [
            song_builder.ChannelSpec("Kick", "kick", 1, role="kick", plugin="gl.kick", volume=0.76),
            song_builder.ChannelSpec("HiHat", "hihat", 2, role="hihat_perc", plugin="gl.hat", volume=0.52),
            song_builder.ChannelSpec("Pad", "pad", 3, role="chords_pad", plugin="gl.keys", volume=0.12),
        ]
        meta = {"mix_profile": "trap_rap", "has_drums": True}
        out = apply_nominal_balance(chans, meta)
        by = {c.name: c.volume for c in out}
        self.assertAlmostEqual(by["Kick"], 0.76, places=3)                       # ancre inchangée
        self.assertAlmostEqual(by["HiHat"], round(0.76 * 10 ** (-10.0 / 40.0), 4), places=3)  # -10 dB
        self.assertAlmostEqual(by["Pad"], round(0.76 * 10 ** (-8.0 / 40.0), 4), places=3)     # -8 dB, relevée de 0.12
        self.assertGreater(by["Pad"], 0.12)

    def test_report_before_after_and_nominal_status(self):
        import song_builder
        with tempfile.TemporaryDirectory() as td:
            chans = [
                song_builder.ChannelSpec("Kick", "kick", 1, role="kick", plugin="gl.kick", volume=0.76),
                song_builder.ChannelSpec("Pad", "pad", 2, role="chords_pad", plugin="gl.keys", volume=0.12),
            ]
            meta = {"mix_profile": "trap_rap", "has_drums": True}
            _eff, _ins, report = song_builder._apply_auto_gain_staging(td, chans, meta, auto_gain_stage=True)
            self.assertEqual(report["soundcheck_matched_count"], 0)
            self.assertTrue(report["plan"].get("nominal_balance"))
            by_key = {t["track_key"]: t for t in report["tracks"]}
            pad = by_key["pad"]
            self.assertAlmostEqual(pad["volume_before"], 0.12, places=3)   # vrai before (avant mutation)
            self.assertGreater(pad["volume_after"], pad["volume_before"])
            self.assertEqual(pad["status"], "NOMINAL_APPLIED")
            kick = by_key["kick"]
            self.assertAlmostEqual(kick["volume_before"], 0.76, places=3)
            self.assertEqual(kick["status"], "UNCHANGED")  # ancre : inchangée
            self.assertTrue(os.path.isfile(os.path.join(td, "renders", "gain_staging_report.json")))


class TestNfftClampNoWarning(unittest.TestCase):
    def test_short_signal_no_nfft_warning(self):
        import sample_quality_v2 as q2
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "tiny.wav")
            sf.write(p, _sine(440.0, 0.5, dur=300 / SR), SR)  # 300 échantillons seulement
            with warnings.catch_warnings(record=True) as rec:
                warnings.simplefilter("always")
                prof = q2.analyze_file_v2(p, max_seconds=8.0)
            self.assertIsNotNone(prof)
            nfft_warns = [str(w.message) for w in rec if "n_fft" in str(w.message)]
            self.assertEqual(nfft_warns, [], f"UserWarning n_fft encore émis: {nfft_warns}")


if __name__ == "__main__":
    unittest.main()
