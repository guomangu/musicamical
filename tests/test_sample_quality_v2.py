"""v4.1 — tests Audio Intelligence v2 (`scripts/sample_quality_v2.py`).

Jalons d'acceptation du pack MIR v2, en Python in-process (decode once via libsndfile) :
1. Quality gate physique : sinus -3 dBFS -> PASS ; sinus écrêté -> REJECTED CLIPPED ;
   sinus + bruit -20 dB -> REJECTED NOISY_FLOOR ; faux WAV (brickwall 11 kHz à 44,1 kHz)
   -> REJECTED UPSAMPLED_LOSSY. Budget < 15 ms/fichier cible (assert < 50 ms en CI).
2. Morphologie : 808 synthétique (120->45 Hz en 50 ms, T30 ~1.6 s, tanh CF ~5 dB)
   -> tags [808, envelope:long_sustain, timbre:saturated_distorted], pitch_midi 29 ±1.
3. Anti-masquage : collision spectrale Bark24 pondérée graves + HPF suggéré.
4. Phase Kick/808 : opposition de phase -> invert_polarity, sommation sans annulation.
5. Câblage indexeur : table sample_quality_v2 peuplée, REJECTED exclus de la sélection.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
INDEX_DIR = os.path.join(ROOT, "mcp-servers", "audio-index")
for _p in (SCRIPTS, INDEX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import sample_quality_v2 as q2  # noqa: E402

SR = 22050


def _sine(freq=1000.0, amp_db=-3.0, sr=SR, dur=1.0):
    t = np.arange(int(dur * sr)) / sr
    return (10.0 ** (amp_db / 20.0)) * np.sin(2 * np.pi * freq * t)


def _synth_808(sr=SR, dur=2.0):
    tt = np.arange(int(dur * sr)) / sr
    f0 = np.where(tt < 0.05, 120.0, 45.0 + 75.0 * np.exp(-(tt - 0.05) / 0.02))
    phase = 2 * np.pi * np.cumsum(f0) / sr
    env = 10.0 ** (-30.0 * tt / (20.0 * 1.6))
    return np.tanh(5.0 * env * np.sin(phase))


class TestQualityGate(unittest.TestCase):
    def test_jalon1_four_fixtures(self):
        rng = np.random.default_rng(3)
        sine = _sine()
        clipped = np.clip(1.26 * np.sin(2 * np.pi * 1000.0 * np.arange(SR) / SR), -1.0, 1.0)
        noisy = sine + 0.1 * rng.standard_normal(SR)
        sr_h = 44100
        wide = rng.standard_normal(sr_h)
        wide = wide / max(float(np.max(np.abs(wide))), 1e-9) * 0.9
        sp = np.fft.rfft(wide)
        fr = np.fft.rfftfreq(len(wide), 1.0 / sr_h)
        sp[fr > 11000.0] = 0.0
        lp11 = np.fft.irfft(sp, n=len(wide))

        g_sine = q2.quality_gate_v2(sine, SR)
        g_clip = q2.quality_gate_v2(clipped, SR)
        g_noisy = q2.quality_gate_v2(noisy, SR)
        g_lp11 = q2.quality_gate_v2(lp11, sr_h)

        self.assertEqual(g_sine["status"], "PASS", g_sine)
        self.assertEqual(g_sine["defects"], [])
        self.assertLess(g_sine["true_peak_dbtp"], -2.5)
        self.assertTrue(any(d.startswith("CLIPPED") for d in g_clip["defects"]), g_clip)
        self.assertEqual(g_clip["status"], "REJECTED")
        self.assertTrue(any(d.startswith("NOISY_FLOOR") for d in g_noisy["defects"]), g_noisy)
        self.assertTrue(any(d.startswith("UPSAMPLED_LOSSY") for d in g_lp11["defects"]), g_lp11)
        self.assertEqual(g_lp11["status"], "REJECTED")
        # contrôle : bruit blanc pleine bande à 44,1 kHz -> pas de faux WAV
        g_wide = q2.quality_gate_v2(wide, sr_h)
        self.assertFalse(any(d.startswith("UPSAMPLED_LOSSY") for d in g_wide["defects"]), g_wide)

    def test_quality_gate_budget(self):
        sine = _sine(dur=1.0)
        t0 = time.perf_counter()
        for _ in range(5):
            q2.quality_gate_v2(sine, SR)
        ms = (time.perf_counter() - t0) / 5 * 1000.0
        self.assertLess(ms, 50.0, f"quality gate trop lent: {ms:.1f} ms/fichier (cible 15 ms)")

    def test_true_peak_oversampled(self):
        # deux échantillons pleins suivis d'un demi -> pic inter-échantillon > 0 dBFS
        x = np.zeros(SR)
        x[100:104] = 1.0
        x[104] = 0.5
        tp = q2.true_peak_dbtp(x, SR)
        self.assertGreater(tp, 0.0, f"TP inter-échantillons non détecté: {tp}")

    def test_dc_and_trim(self):
        x = _sine() + 0.05
        self.assertAlmostEqual(float(np.mean(q2.remove_dc_offset(x))), 0.0, places=6)
        padded = np.concatenate([np.zeros(SR // 2), _sine(dur=0.5), np.zeros(SR // 2)])
        trimmed = q2.smart_trim(padded, SR)
        self.assertLess(len(trimmed), len(padded))
        self.assertGreaterEqual(len(trimmed), int(0.5 * SR))


class TestMorphology808(unittest.TestCase):
    def test_jalon2_synth_808_tags(self):
        m = q2.profile_morphology(_synth_808(), SR)
        for tag in ("808", "envelope:long_sustain", "timbre:saturated_distorted"):
            self.assertIn(tag, m["tags"], m["tags"])
        self.assertEqual(m["role"], "808")
        self.assertIsNotNone(m["pitch_midi"])
        self.assertLessEqual(abs(m["pitch_midi"] - 29), 1, m["pitch_midi"])
        self.assertTrue(m["has_pitch_drop"])
        self.assertGreater(m["pitch_drop_hz"], 35.0)
        self.assertGreaterEqual(m["decay_t30_ms"], 1100.0)
        self.assertLess(m["crest_factor_db"], 6.5)
        self.assertEqual(len(m["bark_24"]), 24)
        self.assertAlmostEqual(sum(m["bark_24"]), 1.0, delta=0.01)

    def test_clean_sub_not_saturated(self):
        # sinus 45 Hz propre avec attaque courte -> ni saturated_distorted
        t = np.arange(int(1.0 * SR)) / SR
        x = 0.8 * np.sin(2 * np.pi * 45.0 * t) * np.exp(-t / 0.3)
        m = q2.profile_morphology(x, SR)
        self.assertNotIn("timbre:saturated_distorted", m["tags"], m["tags"])


class TestMaskingAndPhase(unittest.TestCase):
    def test_spectral_collision_low_bands_weighted(self):
        bark_low = [0.0] * 24
        bark_low[0] = 0.6
        bark_low[1] = 0.3
        bark_low[2] = 0.1
        bark_high = [0.0] * 24
        bark_high[20] = 1.0
        c_low = q2.spectral_collision(bark_low, bark_low)["collision"]
        c_high = q2.spectral_collision(bark_low, bark_high)["collision"]
        self.assertIsNotNone(c_low)
        self.assertGreater(c_low, c_high * 10.0, (c_low, c_high))
        hpf = q2.suggest_hpf_hz(bark_low, bark_low)
        self.assertGreaterEqual(hpf, 80.0)
        self.assertLessEqual(hpf, 500.0)

    def test_phase_alignment_invert_and_none(self):
        n = int(0.3 * SR)
        kick = np.zeros(n)
        kick[: int(0.05 * SR)] = np.sin(2 * np.pi * 45.0 * np.arange(int(0.05 * SR)) / SR)
        bass = np.sin(2 * np.pi * 45.0 * np.arange(n) / SR)
        pa_bad = q2.phase_alignment(kick, -bass, SR)
        self.assertEqual(pa_bad["action"], "invert_polarity")
        self.assertTrue(pa_bad["no_cancellation"])
        self.assertGreater(pa_bad["band_rms_sum_after"], pa_bad["band_rms_sum_before"])
        pa_ok = q2.phase_alignment(kick, bass, SR)
        self.assertEqual(pa_ok["action"], "none")
        self.assertTrue(pa_ok["no_cancellation"])


class TestIndexerWiring(unittest.TestCase):
    def test_index_directory_writes_q2_and_selection_excludes_rejected(self):
        import indexer
        with tempfile.TemporaryDirectory() as td:
            sdir = os.path.join(td, "samples")
            os.makedirs(sdir)
            sf.write(os.path.join(sdir, "good.wav"), _sine().astype(np.float32), SR)
            sf.write(os.path.join(sdir, "clipped.wav"),
                     np.clip(1.26 * np.sin(2 * np.pi * 220.0 * np.arange(SR) / SR), -1, 1).astype(np.float32), SR)
            db = os.path.join(td, "samples.db")
            rep = indexer.index_directory(samples_dir=sdir, db_path=db, workers=1)
            self.assertEqual(rep.get("q2_pass", 0), 1)
            self.assertEqual(rep.get("q2_rejected", 0), 1)
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT s.path, q2.status, q2.defects_json FROM samples s "
                                "JOIN sample_quality_v2 q2 ON q2.sample_id = s.id").fetchall()
            self.assertEqual(len(rows), 2)
            statuses = {os.path.basename(p): st for p, st, _ in rows}
            self.assertEqual(statuses["good.wav"], "PASS")
            self.assertEqual(statuses["clipped.wav"], "REJECTED")
            # le sample rejeté est aussi marqué hard_reject côté DSP
            hr = conn.execute("SELECT d.hard_reject, d.reject_reasons FROM samples s "
                              "JOIN sample_dsp_metrics d ON d.sample_id = s.id WHERE s.path LIKE '%clipped%'").fetchone()
            self.assertEqual(hr[0], 1)
            self.assertIn("CLIPPED", hr[1])
            conn.close()
            # sélection : le rejeté n'est plus proposé
            import select_sample
            res = select_sample.select_best_from_db(db_path=db, role="lead_melody", top_k=5, with_clap=False)
            paths = [c.get("path", "") for c in res.get("candidates", [])] + [str(res.get("best", {}).get("path", ""))]
            self.assertFalse(any("clipped" in p for p in paths), paths)


class TestSelftest(unittest.TestCase):
    def test_selftest_all_pass(self):
        rep = q2.selftest()
        self.assertTrue(rep["all_pass"], json.dumps(rep["checks"], default=str))


if __name__ == "__main__":
    unittest.main()
