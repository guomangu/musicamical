#!/usr/bin/env python3
"""Tests du pipeline d'indexation unifié psychoacoustique (Chantier 1) :
`mcp-servers/audio-index/unified_indexer.py` -> `samples.db` (table `sound_assets`) +
`scripts/audio_prep_pb.py` (trim -60 dBFS + -18 LUFS).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "mcp-servers", "audio-index")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import unified_indexer as ui  # noqa: E402
from audio_prep_pb import prepare_audio, measure_lufs, trim_silence  # noqa: E402

SR = 22050


def _write_tone(path: str, freq: float, dur: float = 0.5, sr: int = SR, pad_s: float = 0.2, amp: float = 0.3) -> None:
    n_pad = int(pad_s * sr)
    n = int(dur * sr)
    t = np.arange(n) / sr
    tone = amp * np.sin(2 * np.pi * freq * t)
    audio = np.concatenate([np.zeros(n_pad, dtype=np.float32), tone, np.zeros(n_pad, dtype=np.float32)])
    sf.write(path, audio, sr)


def _write_noise(path: str, dur: float = 0.3, sr: int = SR, seed: int = 7, hp: float = 2000.0) -> None:
    rng = np.random.default_rng(seed)
    audio = rng.standard_normal(int(dur * sr)).astype(np.float32) * 0.2
    from scipy.signal import butter, sosfilt
    sos = butter(4, hp, btype="highpass", fs=sr, output="sos")
    sf.write(path, sosfilt(sos, audio).astype(np.float32), sr)


class TestAudioPrep(unittest.TestCase):
    def test_trim_and_lufs_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "tone.wav")
            _write_tone(p, 220.0, pad_s=0.3)
            audio, sr = ui._load_mono(p)
            trimmed, start = trim_silence(audio, sr)
            self.assertGreater(start, 0, "le silence initial doit être tronqué")
            self.assertLess(trimmed.size, audio.size)
            prep = prepare_audio(audio, sr)
            self.assertAlmostEqual(prep["lufs_after"], -18.0, delta=0.5)
            self.assertAlmostEqual(measure_lufs(prep["audio"], sr), -18.0, delta=0.5)

    def test_prepare_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "tone.wav")
            _write_tone(p, 330.0)
            audio, sr = ui._load_mono(p)
            a = prepare_audio(audio, sr)["audio"]
            b = prepare_audio(audio, sr)["audio"]
            np.testing.assert_array_equal(a, b)


class TestUnifiedIndexer(unittest.TestCase):
    def test_schema_matches_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(sound_assets)").fetchall()}
            expected = {
                "asset_id", "source_type", "file_path", "preset_name", "role_category", "is_pitched",
                "detected_midi_note", "pitch_confidence", "min_safe_midi", "max_safe_midi",
                "duration_ms", "log_attack_ms", "lufs_integrated", "crest_factor_db",
                "spectral_centroid_hz", "low_band_energy", "low_mid_energy", "high_mid_energy",
                "air_band_energy", "ac_warmth", "ac_brightness", "ac_roughness", "ac_hardness",
                "ac_depth", "ac_boomy", "ac_sharpness", "embedding_blob", "indexed_at",
            }
            self.assertTrue(expected.issubset(cols), f"colonnes manquantes: {expected - cols}")
            idx = {r[1] for r in conn.execute("PRAGMA index_list(sound_assets)").fetchall()}
            self.assertIn("idx_assets_role_timbre", idx)
            self.assertIn("idx_assets_centroid", idx)
            conn.close()

    def test_index_wav_tonal_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "samples.db")
            conn = ui.init_db(db)
            p = os.path.join(tmp, "piano_c4.wav")
            _write_tone(p, 261.63, dur=0.6)
            row = ui.index_file(conn, p)
            self.assertEqual(row["role_category"], "keys")
            self.assertEqual(row["is_pitched"], 1)
            self.assertEqual(row["detected_midi_note"], 60)
            self.assertGreaterEqual(row["pitch_confidence"], ui.PITCH_CONFIDENCE_THRESHOLD)
            self.assertAlmostEqual(row["lufs_integrated"], -18.0, delta=0.6)
            for k in ("ac_warmth", "ac_brightness", "ac_roughness", "ac_hardness",
                      "ac_depth", "ac_boomy", "ac_sharpness"):
                self.assertGreaterEqual(row[k], 0.0)
                self.assertLessEqual(row[k], 100.0)
            bands = row["low_band_energy"] + row["low_mid_energy"] + row["high_mid_energy"] + row["air_band_energy"]
            self.assertAlmostEqual(bands, 1.0, places=3)
            # tessiture sûre : ±5 st pour un one-shot acoustique
            self.assertEqual(row["min_safe_midi"], 55)
            self.assertEqual(row["max_safe_midi"], 65)
            rows = ui.query_assets(conn, role_category="keys")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["asset_id"], row["asset_id"])
            conn.close()

    def test_index_percussive_noise_not_pitched(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            p = os.path.join(tmp, "snare_01.wav")
            _write_noise(p, hp=1500.0)
            row = ui.index_file(conn, p)
            self.assertEqual(row["role_category"], "snare")
            self.assertEqual(row["is_pitched"], 0)
            self.assertIsNone(row["detected_midi_note"])
            self.assertGreater(row["spectral_centroid_hz"], 1500.0)
            conn.close()

    def test_role_inference_and_source_type_check(self):
        self.assertEqual(ui.infer_role_category("/x/Kick_808_Am.wav"), "kick")
        self.assertEqual(ui.infer_role_category("/x/Sub Bass 808.wav"), "bass_808")
        self.assertEqual(ui.infer_role_category("/x/dark_pad.wav"), "pad")
        self.assertEqual(ui.infer_role_category("/x/riser.wav"), "fx")
        with tempfile.TemporaryDirectory() as tmp:
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            with self.assertRaises(ValueError):
                ui.index_file(conn, "x.wav", source_type="midi")
            conn.close()

    def test_migrate_catalog_json(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "kick.wav")
            _write_tone(wav, 55.0, dur=0.3)
            catalog = {"samples": [{"path": wav, "category": "kick", "midi_root": 36,
                                    "duration_ms": 300.0, "lufs": -18.0}],
                       "user_samples_locked": True}
            cat_path = os.path.join(tmp, "samples_catalog.json")
            with open(cat_path, "w", encoding="utf-8") as fh:
                json.dump(catalog, fh)
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            stats = ui.migrate_catalog_json(conn, cat_path)
            self.assertEqual(stats["imported"], 1)
            rows = ui.query_assets(conn, role_category="kick")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["detected_midi_note"], 36)
            conn.close()

    def test_catalog_root_removed_db_is_ssot(self):
        # Chantier 1 (spec v2.0 §1.3) : samples_catalog.json est supprimé de la racine et
        # gitignoré ; samples.db (table sound_assets) est la source unique de vérité.
        cat_path = os.path.join(ROOT, "samples_catalog.json")
        self.assertFalse(os.path.exists(cat_path), "samples_catalog.json doit être supprimé de la racine")
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            gitignore = fh.read()
        self.assertIn("samples_catalog.json", gitignore)
        db_path = os.path.join(ROOT, "samples.db")
        self.assertTrue(os.path.isfile(db_path), "samples.db (SSOT) doit exister à la racine")
        self.assertGreater(os.path.getsize(db_path), 0, "samples.db doit être peuplé (> 0 octet)")
        self.assertTrue(ui.DEFAULT_DB_PATH.endswith("samples.db"))

    def test_schema_v2_clap_and_modulation_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(sound_assets)").fetchall()}
            self.assertIn("clap_embedding", cols)
            self.assertIn("modulation_profile_json", cols)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            conn.close()
            # migration v1 -> v2 sur une vieille base
            import sqlite3
            old = os.path.join(tmp, "old.db")
            c = sqlite3.connect(old)
            c.execute("CREATE TABLE sound_assets (asset_id TEXT PRIMARY KEY, embedding_blob BLOB)")
            c.execute("PRAGMA user_version=1")
            c.commit()
            c.close()
            conn = ui.init_db(old)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(sound_assets)").fetchall()}
            self.assertIn("clap_embedding", cols)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            conn.close()

    def test_embedding_blob_contract_and_determinism(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            p = os.path.join(tmp, "pad_dark.wav")
            _write_tone(p, 180.0, dur=0.6)
            row = ui.index_file(conn, p)
            self.assertEqual(row.get("embedding_backend"), "deterministic-projection")
            r = conn.execute("SELECT clap_embedding, embedding_blob FROM sound_assets").fetchone()
            self.assertIsNotNone(r["clap_embedding"])
            self.assertEqual(len(r["clap_embedding"]), 2048)  # 512 float32
            self.assertEqual(r["clap_embedding"], r["embedding_blob"])
            v = ui.decode_embedding(r["clap_embedding"])
            self.assertAlmostEqual(float(np.linalg.norm(v)), 1.0, places=5)
            # déterminisme bit-à-bit
            row2 = ui.index_file(conn, p)
            r2 = conn.execute("SELECT clap_embedding FROM sound_assets WHERE asset_id=?", (row["asset_id"],)).fetchone()
            self.assertEqual(r2["clap_embedding"], r["clap_embedding"])
            self.assertEqual(ui.text_embedding_blob("dark pad"), ui.text_embedding_blob("dark pad"))
            conn.close()

    def test_semantic_search_ranks_sub_over_pad(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            pad = os.path.join(tmp, "pad_dark.wav")
            _write_tone(pad, 220.0, dur=0.6)
            sub = os.path.join(tmp, "bass_808_sub.wav")
            _write_tone(sub, 45.0, dur=0.6)
            ui.index_file(conn, pad)
            ui.index_file(conn, sub)
            res = ui.semantic_search(conn, "dark sub bass 808 heavy looming", top_k=2)
            self.assertEqual(len(res), 2)
            self.assertIn("bass_808_sub", res[0]["file_path"])
            self.assertGreater(res[0]["semantic_score"], res[1]["semantic_score"])
            res_role = ui.semantic_search(conn, "sub 808", role_category="bass_808", top_k=5)
            self.assertTrue(all(r["role_category"] == "bass_808" for r in res_role))
            conn.close()

    def test_modulation_profile_sfz(self):
        with tempfile.TemporaryDirectory() as tmp:
            sfz = os.path.join(tmp, "mini.sfz")
            with open(sfz, "w", encoding="utf-8") as fh:
                fh.write('<region> sample=x.wav lokey=0 hikey=127 cutoff=2400 resonance=3 ampeg_decay=0.4 lfo01_rate=5.5\n')
            prof = ui.extract_modulation_profile(sfz, "sfz_instrument")
            self.assertIsNotNone(prof)
            keys = set(prof.keys())
            self.assertIn("sfz:cutoff", keys)
            self.assertIn("sfz:lfo01_rate", keys)
            self.assertIsNone(ui.extract_modulation_profile(sfz, "wav_sample"))
            conn = ui.init_db(os.path.join(tmp, "samples.db"))
            import numpy as np
            tone = (0.3 * np.sin(2 * np.pi * 261.63 * np.arange(int(0.5 * 22050)) / 22050)).astype(np.float32)
            ui.index_audio_array(conn, tone, 22050, asset_id="sfz_test", file_path=sfz,
                                 source_type="sfz_instrument", role_category="keys", preset_name="mini")
            r = conn.execute("SELECT modulation_profile_json FROM sound_assets WHERE asset_id='sfz_test'").fetchone()
            self.assertIsNotNone(r["modulation_profile_json"])
            import json as _json
            self.assertIn("sfz:cutoff", _json.loads(r["modulation_profile_json"]))
            conn.close()


if __name__ == "__main__":
    unittest.main()
