#!/usr/bin/env python3
"""Suite de non-régression complète du correctif « RAPPORT_DIAGNOSTIC_SONORITES_SAMPLES.md ».

Garantit que les 4 causes racines + le verrou « trou de serrure » de registre ne peuvent
PLUS JAMAIS se reproduire, quel que soit le genre musical (Boom Bap, Dark Trap, Hard Drill,
Lo-Fi, Jazz, EDM, Ambient) :
  1. Indexeur (`indexer.py`) : purge de TOUTES les lignes mortes (même après déplacement du
     dépôt `musicos -> musicamical`), catégorisation exacte (`Bass Guitar -> bass`,
     `DBD_PADS_CHORDS -> synth`), et mise à niveau DSP sans perte des embeddings CLAP.
  2. DSP & Quality v2 (`audio_metrics.py`, `sample_quality_v2.py`) : pas de faux rejet
     `NOISY_FLOOR` sur cordes/pads propres, détection exacte de fondamentale G3=55 Hz (sans
     verrou sous-octave ni rejet `percussion`), tolérance micro-stéréo naturelle (`rho=-0.22`),
     cloche à shimmer mono-note jouable, tout en bloquant strictement accords/dyades/chops 2 notes.
  3. Diversité par genre & intention (`palette_builder.py` + `prod_server._resolve_palette`) :
     Boom Bap -> Rhodes/Piano + Bass Guitar ; Dark Trap -> Pad/Bell/Synth + 808 ;
     Hard Drill -> Synth/Pad/Bell + 808 (zéro override Rhodes codé en dur !).
  4. Multi-zone automatique (`sample_prep_lib.py` + `song_builder.py` + `blueprint_safety.py`) :
     plus de « trou de serrure » 2 demi-tons sur accords [53..72], expansion automatique en
     2-3 zones <= +/-4 st/zone routées sur le même insert mixeur, et cache instantané.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
INDEX_DIR = os.path.join(ROOT, "mcp-servers", "audio-index")
PROD_DIR = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
for _d in (SCRIPTS, INDEX_DIR, PROD_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

SR = 44100


def _write_wav(path: str, data: np.ndarray, sr: int = SR) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sf.write(path, np.asarray(data, dtype=np.float32), sr, subtype="PCM_24")
    return path


def _tone(freq: float, dur: float = 1.6, sr: int = SR, harmonics: list[tuple[float, float]] | None = None,
          attack_s: float = 0.015, decay_tau: float = 0.9) -> np.ndarray:
    t = np.arange(int(dur * sr)) / sr
    sig = np.zeros_like(t)
    for mult, amp in (harmonics or [(1.0, 1.0), (2.0, 0.35), (3.0, 0.12)]):
        sig += amp * np.sin(2.0 * np.pi * freq * mult * t)
    env = np.minimum(1.0, t / max(attack_s, 1e-4)) * np.exp(-t / max(decay_tau, 0.05))
    sig *= env
    pk = float(np.max(np.abs(sig))) + 1e-12
    return (0.7 * sig / pk).astype(np.float32)


class TestIndexerStalePruneAndCategory(unittest.TestCase):
    def test_prunes_dead_rows_from_moved_repo_root(self):
        import indexer

        with tempfile.TemporaryDirectory() as td:
            sdir = os.path.join(td, "samples")
            os.makedirs(sdir)
            _write_wav(os.path.join(sdir, "piano_c4.wav"), _tone(261.63))
            db = os.path.join(td, "samples.db")
            indexer.index_directory(sdir, db, with_dsp=True, workers=1)

            # Injecter une entrée fantôme provenant d'un ancien chemin de dépôt déplacé
            conn = sqlite3.connect(db)
            conn.execute(
                """INSERT INTO samples (path, filename, pack_name, rel_path, ext, file_mtime, file_size,
                   category, is_loop, duration_sec, sample_rate, channels, tags)
                   VALUES ('/old_moved_repo/musicos/samples/piano/ghost.wav', 'ghost.wav', 'piano',
                           'piano/ghost.wav', '.wav', 0.0, 1000, 'piano', 0, 1.0, 44100, 1, 'piano')"""
            )
            ghost_id = conn.execute("SELECT id FROM samples WHERE filename='ghost.wav'").fetchone()[0]
            conn.execute("INSERT INTO sample_dsp_metrics (sample_id, playable_chromatically) VALUES (?, 1)", (ghost_id,))
            conn.commit()
            conn.close()

            rep = indexer.index_directory(sdir, db, with_dsp=True, workers=1)
            self.assertEqual(rep["pruned"], 1, rep)
            self.assertEqual(rep["total"], 1, rep)

            conn = sqlite3.connect(db)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM samples WHERE filename='ghost.wav'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sample_dsp_metrics WHERE sample_id=?", (ghost_id,)).fetchone()[0], 0)
            conn.close()

    def test_category_rules_bass_guitar_and_pads_chords(self):
        import indexer

        cat_bg, _, _ = indexer._infer_category_and_pack(
            "Lofi/Bass/Cymatics - Eternity Bass Guitar 1 - C.wav",
            "Cymatics - Eternity Bass Guitar 1 - C.wav",
            1.8,
        )
        self.assertEqual(cat_bg, "bass", "Bass Guitar doit être classé 'bass', jamais 'guitar'")

        cat_str, _, _ = indexer._infer_category_and_pack(
            "Loopmasters/DBD_PADS_CHORDS/DBD_HI STR_C_1.wav",
            "DBD_HI STR_C_1.wav",
            4.0,
        )
        self.assertEqual(cat_str, "synth", "DBD_PADS_CHORDS string/pad doit être classé 'synth', pas 'piano'")

        cat_pno, _, _ = indexer._infer_category_and_pack("piano/rhodes_mk1_d4.wav", "rhodes_mk1_d4.wav", 2.5)
        self.assertEqual(cat_pno, "piano")

    def test_dsp_version_upgrade_preserves_clap_embeddings(self):
        import indexer

        with tempfile.TemporaryDirectory() as td:
            sdir = os.path.join(td, "samples")
            os.makedirs(sdir)
            _write_wav(os.path.join(sdir, "synth_c4.wav"), _tone(261.63))
            db = os.path.join(td, "samples.db")
            indexer.index_directory(sdir, db, with_dsp=True, workers=1)

            fake_vec = np.ones(512, dtype=np.float32)
            fake_vec /= np.linalg.norm(fake_vec)
            conn = sqlite3.connect(db)
            conn.execute("UPDATE samples SET clap_embedding = ?", (fake_vec.tobytes(),))
            conn.execute("UPDATE db_meta SET value = '1' WHERE key = 'dsp_metrics_version'")
            conn.commit()
            conn.close()

            rep = indexer.index_directory(sdir, db, with_dsp=True, workers=1)
            self.assertGreaterEqual(rep["dsp_indexed"], 1)
            conn = sqlite3.connect(db)
            blob = conn.execute("SELECT clap_embedding FROM samples").fetchone()[0]
            ver = conn.execute("SELECT value FROM db_meta WHERE key='dsp_metrics_version'").fetchone()[0]
            conn.close()
            self.assertIsNotNone(blob, "La mise à niveau DSP ne doit jamais effacer clap_embedding")
            self.assertEqual(int(ver), indexer.DSP_METRICS_VERSION)


class TestDspAndQualityV2NoFalseRejects(unittest.TestCase):
    def test_clean_sustained_string_passes_q2_while_noisy_fails(self):
        import sample_quality_v2 as q2

        t = np.arange(int(3.0 * SR)) / SR
        # Cordes soutenues riches (vibrato naturel + 6 harmoniques, amplitude stable -> dyn < 15 dB)
        vib = 1.0 + 0.004 * np.sin(2.0 * np.pi * 5.2 * t)
        phase = 2.0 * np.pi * np.cumsum(523.25 * vib) / SR
        strings = (
            0.55 * np.sin(phase)
            + 0.30 * np.sin(2.0 * phase)
            + 0.18 * np.sin(3.0 * phase)
            + 0.10 * np.sin(4.0 * phase)
            + 0.05 * np.sin(5.0 * phase)
        )
        strings = 0.65 * strings / (float(np.max(np.abs(strings))) + 1e-12)
        g_str = q2.quality_gate_v2(strings, SR)
        self.assertEqual(g_str["status"], "PASS", f"Cordes soutenues propres rejetées: {g_str}")

        # Contrôle négatif : sinus + bruit blanc -20 dB doit TOUJOURS être REJECTED NOISY_FLOOR
        rng = np.random.default_rng(7)
        noisy = 0.7 * np.sin(2.0 * np.pi * 1000.0 * t[:SR]) + 0.1 * rng.standard_normal(SR)
        g_noisy = q2.quality_gate_v2(noisy, SR)
        self.assertEqual(g_noisy["status"], "REJECTED", g_noisy)
        self.assertIn("NOISY_FLOOR", g_noisy["defects"])

    def test_warm_g3_pad_not_misclassified_as_percussion_or_suboctave(self):
        import audio_metrics as am

        with tempfile.TemporaryDirectory() as td:
            # Pad chaud pur à G3 (196 Hz, quasi 100 % d'énergie < 250 Hz -> is_low=True)
            p = _write_wav(os.path.join(td, "pad_cloud_g3.wav"), _tone(196.0, dur=2.0, harmonics=[(1.0, 1.0), (2.0, 0.15)], attack_s=0.12, decay_tau=2.0))
            entry, _ = am.analyze_single_file(p, with_clap=False)
            dsp = entry["dsp"]
            self.assertTrue(dsp["playable_chromatically"], dsp)
            self.assertEqual(dsp["sample_nature"], "mono_oneshot", dsp)
            self.assertEqual(dsp["midi_root"], 55, f"G3 attendu (MIDI 55), obtenu: {dsp}")

    def test_natural_stereo_piano_mic_delay_passes_while_antiphase_rejected(self):
        import audio_metrics as am

        mono = _tone(369.99, dur=1.6)  # F#4
        # Léger décalage micro stéréo (~0.8 ms -> rho ~ -0.25) : son acoustique naturel, PAS inversion polaire
        delay = int(0.00080 * SR)
        stereo_ok = np.column_stack([mono, np.concatenate([np.zeros(delay, dtype=np.float32), mono[:-delay]])])
        # Vraie inversion polaire (L = -R -> rho = -1.0)
        stereo_bad = np.column_stack([mono, -mono])

        with tempfile.TemporaryDirectory() as td:
            p_ok = _write_wav(os.path.join(td, "piano_stereo_ok.wav"), stereo_ok)
            p_bad = _write_wav(os.path.join(td, "piano_antiphase.wav"), stereo_bad)
            e_ok, _ = am.analyze_single_file(p_ok, with_clap=False)
            e_bad, _ = am.analyze_single_file(p_bad, with_clap=False)
            self.assertFalse(e_ok["hard_reject"], e_ok["reject_reasons"])
            self.assertTrue(e_bad["hard_reject"], "Inversion polaire totale doit être hard_reject")

    def test_bell_with_shimmer_playable_but_real_chord_blocked(self):
        import audio_metrics as am

        t = np.arange(int(1.2 * SR)) / SR
        env = np.exp(-t / 0.35)
        # Cloche C5 (523.25 Hz) avec léger shimmer/chorus (±60 ct, amplitude 0.25)
        bell = env * (
            1.0 * np.sin(2.0 * np.pi * 523.25 * t)
            + 0.24 * np.sin(2.0 * np.pi * 505.0 * t)
            + 0.22 * np.sin(2.0 * np.pi * 542.0 * t)
            + 0.30 * np.sin(2.0 * np.pi * 1046.5 * t)
        )
        bell = (0.7 * bell / float(np.max(np.abs(bell)))).astype(np.float32)

        # Vrai accord C mineur (C4 + Eb4 + G4)
        chord = env * (
            np.sin(2.0 * np.pi * 261.63 * t)
            + np.sin(2.0 * np.pi * 311.13 * t)
            + np.sin(2.0 * np.pi * 392.00 * t)
        )
        chord = (0.7 * chord / float(np.max(np.abs(chord)))).astype(np.float32)

        with tempfile.TemporaryDirectory() as td:
            p_bell = _write_wav(os.path.join(td, "glass_bell_c5.wav"), bell)
            p_chord = _write_wav(os.path.join(td, "cm_chord.wav"), chord)
            e_bell, _ = am.analyze_single_file(p_bell, with_clap=False)
            e_chord, _ = am.analyze_single_file(p_chord, with_clap=False)
            self.assertTrue(e_bell["dsp"]["playable_chromatically"], e_bell["dsp"])
            self.assertEqual(e_bell["dsp"]["midi_root"], 72, e_bell["dsp"])
            self.assertFalse(e_chord["dsp"]["playable_chromatically"], e_chord["dsp"])
            self.assertEqual(e_chord["dsp"]["sample_nature"], "poly_chord_or_dyad", e_chord["dsp"])


class TestGenreDiverseIntentSelection(unittest.TestCase):
    """Vérifie que Boom Bap, Dark Trap et Hard Drill choisissent des timbres distincts et fidèles
    au genre (plus de monopole Rhodes/piano sur tous les morceaux), y compris sur accords [53..72]."""

    def _build_diverse_db(self, td: str) -> str:
        import indexer

        sdir = os.path.join(td, "samples")
        # Batterie + 808 + Bass Guitar + Piano/Rhodes + Synth/Pad/Bell/Strings
        _write_wav(os.path.join(sdir, "kick", "kick_punch.wav"), _tone(55.0, dur=0.35, harmonics=[(1.0, 1.0)], decay_tau=0.09))
        _write_wav(os.path.join(sdir, "snare", "snare_tight.wav"), _tone(190.0, dur=0.25, harmonics=[(1.0, 1.0)], decay_tau=0.06))
        rng = np.random.default_rng(11)
        hat = (0.5 * rng.standard_normal(int(0.18 * SR)) * np.exp(-np.arange(int(0.18 * SR)) / (0.04 * SR))).astype(np.float32)
        _write_wav(os.path.join(sdir, "hat", "hat_closed.wav"), hat)
        _write_wav(os.path.join(sdir, "perc", "perc_rim.wav"), _tone(820.0, dur=0.18, harmonics=[(1.0, 1.0)], decay_tau=0.04))

        # 808 vs Bass Guitar
        _write_wav(os.path.join(sdir, "808", "sub_808_c2.wav"), _tone(65.41, dur=1.6, harmonics=[(1.0, 1.0), (2.0, 0.25)], decay_tau=0.9))
        _write_wav(os.path.join(sdir, "Lofi", "Bass", "Eternity Bass Guitar 1 - C.wav"),
                   _tone(65.41, dur=1.6, harmonics=[(1.0, 1.0), (2.0, 0.45), (3.0, 0.2)], decay_tau=0.75))

        # Piano / Rhodes
        _write_wav(os.path.join(sdir, "piano", "rhodes_mk1_d4.wav"), _tone(293.66, dur=2.0, harmonics=[(1.0, 1.0), (2.0, 0.25)], decay_tau=1.1))
        _write_wav(os.path.join(sdir, "piano", "headroom_piano_lead_fs4.wav"), _tone(369.99, dur=1.6, harmonics=[(1.0, 1.0), (2.0, 0.3), (3.0, 0.1)], decay_tau=0.8))

        # Synth / Pad / Bell / Pluck (C5=72 -> test aussi le multi-zone sans trou de serrure sur [53..72] !)
        _write_wav(os.path.join(sdir, "synth", "glass_pad_c5.wav"), _tone(523.25, dur=2.0, harmonics=[(1.0, 1.0), (2.0, 0.2)], attack_s=0.08, decay_tau=1.5))
        _write_wav(os.path.join(sdir, "synth", "dark_synth_pluck_c5.wav"), _tone(523.25, dur=1.5, harmonics=[(1.0, 1.0), (2.0, 0.35), (3.0, 0.12)], decay_tau=0.7))
        _write_wav(os.path.join(sdir, "synth", "hypnotic_bell_c5.wav"), _tone(523.25, dur=1.2, harmonics=[(1.0, 1.0), (2.0, 0.28)], decay_tau=0.55))

        db = os.path.join(td, "samples.db")
        indexer.index_directory(sdir, db, with_dsp=True, workers=1)
        return db

    def _resolve_for_genre(self, td: str, db: str, name: str, profile: str, valence: float,
                           energy: float, intent: str, key: str = "F") -> dict:
        import indexer
        import palette_builder
        import prod_server

        proj = os.path.join(td, name)
        os.makedirs(proj, exist_ok=True)
        # Plage d'accords [53..72] (ambitus=19) qui créait le « trou de serrure » Rhodes D4 !
        sir = {
            "tracks": {
                "kick": {"notes": [{"pitch": 36}]},
                "snare": {"notes": [{"pitch": 38}]},
                "hihat": {"notes": [{"pitch": 42}]},
                "bass": {"notes": [{"pitch": 37}, {"pitch": 44}]},
                "chords": {"notes": [{"pitch": 53}, {"pitch": 60}, {"pitch": 65}, {"pitch": 72}]},
                "lead": {"notes": [{"pitch": 65}, {"pitch": 68}, {"pitch": 70}]},
            }
        }
        with open(os.path.join(proj, "score_ir.json"), "w", encoding="utf-8") as fh:
            json.dump(sir, fh)
        bp = {
            "title": name, "genre": profile, "mix_profile": profile, "has_drums": True, "seed": 42,
            "emotion_vector": {"valence": valence, "tension": 0.6, "energy": energy, "depth": 0.6},
            "intent": intent, "bpm": 140.0 if profile == "trap_rap" else 90.0, "key": key,
            "harmonic_core": {"key": key, "mode": "minor"},
            "tracks": [
                {"name": "kick", "role": "drums", "source_type": "gridline_native", "plugin": "gl.kick", "mixer_track": 1},
                {"name": "snare", "role": "drums", "source_type": "gridline_native", "plugin": "gl.snare", "mixer_track": 2},
                {"name": "hihat", "role": "drums", "source_type": "gridline_native", "plugin": "gl.hat", "mixer_track": 3},
                {"name": "bass", "role": "bass", "source_type": "gridline_native", "plugin": "gl.analog", "mixer_track": 4},
                {"name": "chords", "role": "chords", "source_type": "gridline_native", "plugin": "gl.keys", "mixer_track": 5},
                {"name": "lead", "role": "lead", "source_type": "gridline_native", "plugin": "gl.warmsynth", "mixer_track": 6},
            ],
        }
        old_db_i = indexer.DEFAULT_DB_PATH
        old_db_p = palette_builder.DEFAULT_DB_PATH
        indexer.DEFAULT_DB_PATH = db
        palette_builder.DEFAULT_DB_PATH = db
        try:
            res = prod_server._resolve_palette(proj, bp, "auto", intent=intent)
        finally:
            indexer.DEFAULT_DB_PATH = old_db_i
            palette_builder.DEFAULT_DB_PATH = old_db_p
        return {"res": res, "bp": bp, "tracks": {t["name"]: t for t in bp["tracks"]}}

    def test_boombap_trap_drill_get_distinct_genre_faithful_sounds(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._build_diverse_db(td)

            bb = self._resolve_for_genre(
                td, db, "boombap", "lofi_chill", 0.2, 0.55,
                "warm soulful boom bap jazz rap rhodes piano acoustic bass", key="D"
            )
            trap = self._resolve_for_genre(
                td, db, "dark_trap", "trap_rap", -0.6, 0.75,
                "dark melodic trap 808 hypnotic bell atmospheric pad", key="F"
            )
            drill = self._resolve_for_genre(
                td, db, "hard_drill", "trap_rap", -0.75, 0.90,
                "aggressive hard drill dark 808 slide punchy snare tense synth", key="A"
            )

            # 0 erreur bloquante blueprint_safety sur les 3 morceaux (y compris chords [53..72] en multi-zone)
            for label, pack in (("boombap", bb), ("trap", trap), ("drill", drill)):
                ms = pack["res"].get("musical_safety") or {}
                self.assertEqual(ms.get("errors", 0), 0, f"{label}: musical_safety errors: {ms}")

            # Boom Bap : Rhodes sur chords + Bass Guitar sur bass
            bb_chords = os.path.basename(str(bb["tracks"]["chords"].get("sample_path") or "")).lower()
            bb_bass = os.path.basename(str(bb["tracks"]["bass"].get("sample_path") or "")).lower()
            self.assertIn("rhodes", bb_chords, f"Boom Bap chords doit choisir Rhodes: {bb_chords}")
            self.assertIn("bass guitar", bb_bass, f"Boom Bap bass doit choisir Bass Guitar: {bb_bass}")

            # Dark Trap : Synth/Pad sur chords (PAS Rhodes!), Bell/Pluck/Synth sur lead, 808 sur bass
            tr_chords = os.path.basename(str(trap["tracks"]["chords"].get("sample_path") or "")).lower()
            tr_lead = os.path.basename(str(trap["tracks"]["lead"].get("sample_path") or "")).lower()
            tr_bass = os.path.basename(str(trap["tracks"]["bass"].get("sample_path") or "")).lower()
            self.assertNotIn("rhodes", tr_chords, f"Dark Trap chords ne doit PAS tomber sur Rhodes: {tr_chords}")
            self.assertTrue(any(w in tr_chords for w in ("pad", "synth", "pluck", "bell")), tr_chords)
            self.assertTrue(any(w in tr_lead for w in ("bell", "pluck", "synth", "pad")), tr_lead)
            self.assertIn("808", tr_bass, tr_bass)

            # Hard Drill : Synth/Pluck/Pad sur chords (PAS Rhodes!) et 808 sur bass
            dr_chords = os.path.basename(str(drill["tracks"]["chords"].get("sample_path") or "")).lower()
            dr_lead = os.path.basename(str(drill["tracks"]["lead"].get("sample_path") or "")).lower()
            self.assertNotIn("rhodes", dr_chords, f"Hard Drill chords ne doit PAS tomber sur Rhodes: {dr_chords}")
            self.assertNotIn("rhodes", dr_lead, f"Hard Drill lead ne doit PAS tomber sur Rhodes: {dr_lead}")


class TestMultizoneAutomaticExpansion(unittest.TestCase):
    def test_wide_chords_auto_expand_into_multizone_same_mixer_and_cached(self):
        import song_builder

        with tempfile.TemporaryDirectory() as td:
            wav = _write_wav(os.path.join(td, "glass_pad_c4.wav"), _tone(261.63, dur=1.5))
            ch_chords = song_builder.ChannelSpec(
                "Chords", "chords", 5, "music", plugin="gl.sampler", sample_file=wav, root_note=60, volume=0.68
            )
            ch_bass = song_builder.ChannelSpec(
                "Bass", "bass", 4, "bass", plugin="gl.sampler", sample_file=wav, root_note=36, volume=0.60
            )
            tracks = {
                "chords": [
                    {"start": 0.0, "length": 4.0, "pitch": 53, "velocity": 80},
                    {"start": 0.0, "length": 4.0, "pitch": 60, "velocity": 82},
                    {"start": 0.0, "length": 4.0, "pitch": 65, "velocity": 84},
                    {"start": 0.0, "length": 4.0, "pitch": 72, "velocity": 86},
                ],
                "bass": [
                    {"start": 0.0, "length": 2.0, "pitch": 36, "velocity": 95},
                    {"start": 2.0, "length": 2.0, "pitch": 46, "velocity": 95},
                ],
            }
            autos = [{"name": "Chords LPF", "track": 3, "mixer": 5, "param": "cutoff", "points": []}]

            chs2, trks2, autos2, rep = song_builder._expand_multizone_sampler_channels(
                [ch_bass, ch_chords], tracks, autos, td
            )
            # Chords ([53..72], ambitus=19) -> 3 zones routées sur le MÊME mixer=5 ; Bass jamais splité !
            self.assertEqual(len(rep["expanded"]), 1, rep)
            self.assertEqual(rep["expanded"][0]["track"], "chords")
            chord_zones = [c for c in chs2 if c.track_key.startswith("chords__z")]
            self.assertGreaterEqual(len(chord_zones), 2)
            self.assertTrue(all(c.mixer == 5 for c in chord_zones), "Toutes les zones doivent partager l'insert mixer=5")
            # Aucune note ne doit dépasser +/-4 st de sa zone
            for zc in chord_zones:
                for ev in trks2[zc.track_key]:
                    self.assertLessEqual(abs(int(ev["pitch"]) - int(zc.root_note)), 4, (ev, zc.root_note))
            # Bass intacte (Invariant #3 : jamais de pitch_shift phase-vocoder sur sub < 80 Hz)
            self.assertTrue(any(c.track_key == "bass" for c in chs2))
            # Automations décalées au-delà du nombre total de canaux
            self.assertGreater(autos2[0]["track"], len(chs2))

            # 2e appel : cache hit immédiat (mêmes fichiers WAV réutilisés)
            chs3, _, _, rep3 = song_builder._expand_multizone_sampler_channels(
                [ch_bass, ch_chords], tracks, autos, td
            )
            self.assertEqual(
                [c.sample_file for c in chs2 if c.track_key.startswith("chords__z")],
                [c.sample_file for c in chs3 if c.track_key.startswith("chords__z")],
            )


class TestBlueprintSafetyAndRealBankRegression(unittest.TestCase):
    """Anti-régression sur les bugs réels découverts lors de la vérification banque complète."""

    def test_mono_oneshot_never_flagged_as_melodic_loop_or_bpm_mismatch(self):
        from blueprint_safety import blueprint_musical_safety_issues

        with tempfile.TemporaryDirectory() as td:
            cat_p = os.path.join(td, "samples_catalog.json")
            wav_p = os.path.join(td, "CautionPluck_C.wav")
            _write_wav(wav_p, _tone(523.25, dur=2.0))
            with open(cat_p, "w", encoding="utf-8") as fh:
                json.dump({
                    "samples": [{
                        "filepath": wav_p,
                        "category": "synth",
                        "sample_nature": "mono_oneshot",
                        "playable_chromatically": True,
                        "midi_root": 72,
                        "is_loop": True,  # durée >= 1.8s -> faux flag is_loop historique
                        "bpm": 117.5,
                        "chroma_cqt": [1.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    }]
                }, fh)
            bp = {
                "title": "Test", "bpm": 144.0, "key": "E", "mode": "minor",
                "harmonic_core": {"key": "E", "mode": "minor"},
                "tracks": [{
                    "name": "lead", "role": "lead", "source_type": "sample",
                    "sample_path": wav_p, "root_note": 72, "mixer_track": 1,
                }],
            }
            issues = blueprint_musical_safety_issues(bp, catalog_path=cat_p, notes_by_track={"lead": [67, 71, 74]})
            codes = [i["code"] for i in issues if i.get("severity") == "error"]
            self.assertNotIn("loop_key_mismatch", codes, issues)
            self.assertNotIn("loop_bpm_incompatible", codes, issues)
            self.assertEqual(codes, [], issues)

    def test_real_db_three_rap_genres_distinct_and_zero_safety_errors(self):
        import prod_server
        from blueprint_safety import summarize_musical_safety

        db_path = os.path.join(ROOT, "mcp-servers", "audio-index", "samples.db")
        if not os.path.isfile(db_path):
            self.skipTest("real samples.db not present")

        scenarios = [
            ("boombap", "lofi_chill", 90.0, "D", 1001, -0.25, 0.60,
             "warm soulful boom bap jazz rap rhodes piano acoustic bass",
             {"bass": [38, 41, 45, 47], "chords": [53, 57, 62, 69, 72], "lead": [62, 65, 67]}),
            ("dark_trap", "trap_rap", 140.0, "F", 2002, -0.60, 0.75,
             "dark melodic trap 808 hypnotic bell atmospheric pad",
             {"bass": [37, 41, 44, 46], "chords": [53, 56, 60, 65, 68, 72], "lead": [65, 68, 70]}),
            ("hard_drill", "trap_rap", 144.0, "A", 3003, -0.75, 0.90,
             "aggressive hard drill dark 808 slide punchy snare tense synth",
             {"bass": [36, 40, 43, 45], "chords": [52, 55, 59, 64, 67], "lead": [64, 67, 69]}),
        ]
        chosen: dict[str, dict[str, str]] = {}
        for name, prof, bpm, key, seed, val, ene, intent, nbt in scenarios:
            with tempfile.TemporaryDirectory() as td:
                ir_tracks = {
                    k: {"role": k, "notes": [{"pitch": p} for p in v]} for k, v in nbt.items()
                }
                for dn, dp in (("kick", 36), ("snare", 38), ("hihat", 42)):
                    ir_tracks[dn] = {"role": "drums", "notes": [{"pitch": dp}]}
                with open(os.path.join(td, "score_ir.json"), "w", encoding="utf-8") as fh:
                    json.dump({"tracks": ir_tracks}, fh)
                bp = {
                    "title": name, "bpm": bpm, "key": key, "mode": "minor",
                    "mix_profile": prof, "has_drums": True, "seed": seed,
                    "emotion_vector": {"valence": val, "tension": 0.65, "energy": ene, "depth": 0.6},
                    "intent": intent, "harmonic_core": {"key": key, "mode": "minor"},
                    "tracks": [
                        {"name": "kick", "role": "drums", "source_type": "sample", "mixer_track": 1},
                        {"name": "snare", "role": "drums", "source_type": "sample", "mixer_track": 2},
                        {"name": "hihat", "role": "drums", "source_type": "sample", "mixer_track": 3},
                        {"name": "bass", "role": "bass", "source_type": "sample", "mixer_track": 4},
                        {"name": "chords", "role": "chords", "source_type": "sample", "mixer_track": 5},
                        {"name": "lead", "role": "lead", "source_type": "sample", "mixer_track": 6},
                    ],
                }
                res = prod_server._resolve_palette(td, bp, "auto", intent=intent)
                safety = summarize_musical_safety(bp, notes_by_track=nbt, project_dir=td)
                self.assertEqual(safety.get("errors", 0), 0, f"{name}: {safety}")
                self.assertEqual(res.get("fallback_gate"), "PASS", f"{name}: {res}")
                chosen[name] = {}
                for tr in bp["tracks"]:
                    bn = os.path.basename(str(tr.get("sample_path") or "")).lower()
                    self.assertNotIn("- loop", bn, f"{name}/{tr['name']}: loop leaked: {bn}")
                    self.assertNotIn("riser", bn, f"{name}/{tr['name']}: riser leaked: {bn}")
                    self.assertNotIn("swell", bn, f"{name}/{tr['name']}: swell leaked: {bn}")
                    chosen[name][tr["name"]] = bn

        # Boom Bap : piano/rhodes + bass guitar (pas 808)
        self.assertIn("bass guitar", chosen["boombap"]["bass"])
        self.assertTrue(any(w in chosen["boombap"]["chords"] for w in ("rhodes", "piano")))
        # Dark Trap & Hard Drill : 808 + JAMAIS Rhodes !
        for g in ("dark_trap", "hard_drill"):
            self.assertIn("808", chosen[g]["bass"], f"{g} bass doit être 808: {chosen[g]['bass']}")
            self.assertNotIn("rhodes", chosen[g]["chords"], f"{g} chords ne doit PAS être Rhodes: {chosen[g]['chords']}")
            self.assertNotIn("rhodes", chosen[g]["lead"], f"{g} lead ne doit PAS être Rhodes: {chosen[g]['lead']}")
        # Aucune paire (chords, lead) identique entre les 3 genres
        pairs = {(chosen[g]["chords"], chosen[g]["lead"]) for g in chosen}
        self.assertEqual(len(pairs), 3, f"Les 3 genres doivent avoir des signatures tonales distinctes: {chosen}")


class TestV44RichArrangementAndAntiFranticLead(unittest.TestCase):
    """v4.4 — verrouille les correctifs issus du diagnostic trap_nocturne / trap_soul_wave :
    - kit() 2 mesures riche (5 pistes : kick syncopé, snare, hihat rolls ponctuels, open_hat refrain, perc)
      avec intro/outro harmoniques ;
    - notes tenues `_` dans deg() + arp() harmonique ;
    - détection anti-Lead mitraillette (`lead_not_frantic`) ;
    - séparation stricte hihat fermé vs open_hat ;
    - 10 pistes atomiques passant FullCoverage + BU(4/4) + preflight=PASS."""

    def test_v44_frantic_lead_rejected_and_rich_10track_arrangement_passes(self):
        import shutil
        import prod_server
        import preflight_strudel_gate as pfg
        from strudel_to_score_ir import strudel_to_score_ir

        if not shutil.which("node") or not os.path.isdir(os.path.join(SCRIPTS, "node_modules", "@strudel", "core")):
            self.skipTest("node + @strudel/core requis")

        # 1) Un Lead mitraillette 16 croches uniformes (.slow(2)) est bloqué par lead_not_frantic
        frantic_code = (
            '// @meta 136 F:minor trap_rap V=-0.6 form=I4-V8-C8-O4 seed=101\n'
            'kit("trap_half", {var: 2, hats: "rolls"})\n'
            '$chords: grid("Fm7 Dbmaj7 Bbm7 C7").slow(2).gain(0.7)\n'
            '$bass: bassline("<1 ~ 1 ~>", {mode: "root", glide: 45}).slow(4).in("verse,chorus")\n'
            '$lead: deg("0 3 2 0 ~ 0 3 2 0 ~ 2 3 2 1 0 ~", 4).slow(2).gain(0.65).in("verse,chorus")\n'
        )
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "composition.strudel"), "w", encoding="utf-8") as fh:
                fh.write(frantic_code)
            strudel_to_score_ir(td, run_eval=True, emit_plan=True)
            pf_bad = pfg.preflight(td)
            self.assertFalse(pf_bad["checks"]["lead_not_frantic"]["pass"], pf_bad["checks"])

        # 2) Un morceau v4.4 riche (10 pistes, notes tenues `_`, arp(), Lead .only("chorus")) passe tout !
        rich_code = (
            '// @meta 136 F:minor trap_rap V=-0.6 T=0.7 E=0.7 D=0.7 form=I4-V12-C8-V12-B4-C8-O4 '
            'seed=1011 intent="dark nocturnal melodic trap bell pad 808"\n'
            'kit("trap_half", {var: 2, hats: "rolls"})\n'
            'transition("before:chorus", "drum_fill_and_riser", {style: "snare_roll"})\n'
            '$chords: sectionGrid({default: "Fm7 Dbmaj7 Bbm7 C7", bridge: "Dbmaj7 Abmaj7 Fm7 C7"}).slow(2).gain(0.68)\n'
            '$pad: sectionGrid({default: "Fm7 Dbmaj7 Bbm7 C7", bridge: "Dbmaj7 Abmaj7 Fm7 C7"}).slow(2).gain(0.42).in("intro,chorus,bridge,outro")\n'
            '$arp: arp("0 ~ 2 ~ 1 ~ 3 ~", 4).slow(2).gain(0.40).in("verse,chorus,bridge")\n'
            '$bass: bassline("<1 _ ~ 1 ~ 1 _ ~>", {mode: "root", glide: 45}).slow(2).in("verse,chorus")\n'
            '$lead: deg("0 _ _ ~ 3 _ 2 ~ 0 _ ~ ~ 2 _ 4 _ 3 _ 2 _ 0 _ ~ ~", 4).slow(4).gain(0.58).only("chorus")\n'
        )
        with tempfile.TemporaryDirectory() as td:
            rep = prod_server.compose_project(td, rich_code, palette_id="auto", auto_master=False, auto_prepare=True)
            self.assertTrue(rep.get("ok"), rep)
            self.assertTrue((rep.get("preflight") or {}).get("passed"), rep.get("preflight"))
            self.assertTrue((rep.get("full_coverage") or {}).get("passed"), rep.get("full_coverage"))
            self.assertTrue((rep.get("bottom_up") or {}).get("passed"), rep.get("bottom_up"))
            with open(os.path.join(td, "song_blueprint.json"), encoding="utf-8") as fh:
                bp = json.load(fh)
            with open(os.path.join(td, "arrangement_plan.json"), encoding="utf-8") as fh:
                plan = json.load(fh)
            self.assertEqual(len(bp["tracks"]), 10)
            # Closed hihat ne doit JAMAIS choisir un sample Open Hat
            for tr in bp["tracks"]:
                if tr["name"].lower() == "hihat" and tr.get("sample_path"):
                    self.assertNotIn("open", os.path.basename(tr["sample_path"]).lower())
            # Intro = harmonique sans batterie ; Chorus = 10 pistes riches
            secs = {s["name"]: s["active_roles"] for s in plan["sections"]}
            self.assertNotIn("kick", secs.get("intro", []))
            self.assertIn("chords", secs.get("intro", []))
            self.assertIn("open_hat", secs.get("chorus", []))
            self.assertIn("lead", secs.get("chorus", []))
            self.assertNotIn("lead", secs.get("verse", []))


if __name__ == "__main__":
    unittest.main()
