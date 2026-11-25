#!/usr/bin/env python3
"""v4.0 — tests bloquants du solveur mathématique conjoint de mixage (spec §6.3).

Suites déterministes 0 GPU sur stems synthétiques étalonnés (numpy + scipy) :
1. conflit spectral sévère (Chords masquant Lead de +6 dB à 1.8 kHz + bas-médiums boueux)
   -> poche miroir creusée + lead_vs_harmony ramené dans [3.0, 9.5] dB en 1 passe ;
2. déséquilibre L/R & perte mono (piste panoramique droite + Side excessif)
   -> |LUFS_L - LUFS_R| <= 0.5 dB et mono_folddown_loss <= 2.0 dB ;
3. invariant de parcimonie (stems déjà dans le corridor) -> EQ dans ±0.3 dB ;
4. source défectueuse (bruit blanc strident à 3 kHz) -> saturated_tracks_for_swap ;
5. budget CPU : 8 stems stéréo de 12 mesures résolus en < 1.0 s.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

try:
    import numpy as np
    from scipy import signal
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False

if HAVE_SCIPY:
    from math_mix_solver import BARK_EDGES_HZ, N_BARK, solve_mix_parameters

SR = 22050
BPM = 140.0
BARS = 12
DUR = BARS * 4.0 * 60.0 / BPM


def _corridor_p50(profile: str = "trap_rap") -> np.ndarray:
    import json
    with open(os.path.join(ROOT, "golden_references", "spectral_corridors.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    return np.asarray(doc["profiles"][profile]["bark24"]["p50"], dtype=np.float64)


def _band_noise(rng, n: int, b: int, sr: int = SR) -> np.ndarray:
    lo = max(float(BARK_EDGES_HZ[b]), 20.0)
    hi = min(float(BARK_EDGES_HZ[b + 1]), sr / 2 - 100.0)
    if hi <= lo + 50.0:
        hi = min(lo + 50.0, sr / 2 - 50.0)
    if lo >= sr / 2 - 50.0:
        return np.zeros(n)
    sos = signal.butter(2, [lo, hi], btype="bandpass", fs=sr, output="sos")
    x = signal.sosfilt(sos, rng.standard_normal(n))
    rms = float(np.sqrt(np.mean(x * x)))
    return x / max(rms, 1e-12)


import math


def make_stem_from_mono(mono: np.ndarray, corr: float = 1.0) -> np.ndarray:
    """Stem stéréo à partir d'un signal mono, avec corrélation L/R cible `corr`."""
    n = len(mono)
    rng = np.random.default_rng(1234)
    indep = rng.standard_normal(n)
    indep /= max(float(np.sqrt(np.mean(indep * indep))), 1e-12)
    c = max(-0.99, min(0.99, corr))
    rms = float(np.sqrt(np.mean(mono * mono)))
    L = mono
    R = c * mono + math.sqrt(max(0.0, 1.0 - c * c)) * indep * rms
    return np.stack([L, R], axis=1)


def make_stem(rng, band_weights: np.ndarray, corr: float = 1.0, peak: float = 0.5) -> np.ndarray:
    """Stem stéréo dont l'énergie par bande Bark suit `band_weights` (composantes unit-RMS)."""
    n = int(SR * DUR)
    mono = np.zeros(n)
    for b in range(N_BARK):
        w = float(band_weights[b])
        if w <= 0:
            continue
        mono += math.sqrt(w) * _band_noise(rng, n, b)
    mono /= max(float(np.max(np.abs(mono))), 1e-9)
    mono *= peak
    return make_stem_from_mono(mono, corr=corr)


def corridor_matched_stems(seed: int = 7, roles: tuple = ("kick", "bass", "chords", "lead", "hihat", "snare"), bpm: float = BPM) -> tuple[dict, dict]:
    """Stems dont la somme a exactement la distribution Bark p50 du corridor trap_rap :
    chaque bande Bark est générée une fois (unit-RMS) et possédée par un seul rôle,
    donc la distribution sommée du mix = p50 ∈ [p10, p90] dans la métrique même du surrogate."""
    import json
    rng = np.random.default_rng(seed)
    with open(os.path.join(ROOT, "golden_references", "spectral_corridors.json"), encoding="utf-8") as fh:
        bark = json.load(fh)["profiles"]["trap_rap"]["bark24"]
    p50 = np.asarray(bark["p50"], dtype=np.float64)
    p10 = np.asarray(bark["p10"], dtype=np.float64)
    p90 = np.asarray(bark["p90"], dtype=np.float64)
    target = np.clip(p50, p10, p90)
    n = int(SR * BARS * 4.0 * 60.0 / bpm)
    band_noises = {b: _band_noise(rng, n, b) for b in range(N_BARK)}
    band_owner = {}
    for b in range(N_BARK):
        if b <= 2:
            band_owner[b] = "kick"
        elif b <= 5:
            band_owner[b] = "bass"
        elif b <= 13:
            band_owner[b] = "chords"
        elif b <= 17:
            band_owner[b] = "lead"
        else:
            band_owner[b] = "hihat"
    mix = np.zeros(n)
    for b in range(N_BARK):
        mix += math.sqrt(float(target[b])) * band_noises[b]
    scale = 0.9 / max(float(np.max(np.abs(mix))), 1e-9)
    stems, meta = {}, {}
    for role in roles:
        mono = np.zeros(n)
        for b, owner in band_owner.items():
            if owner == role:
                mono += math.sqrt(float(target[b])) * band_noises[b]
        mono *= scale
        if not np.any(mono):
            mono = np.zeros(n)
        stems[role] = (make_stem_from_mono(mono, corr=1.0), SR)
        meta[role] = {"role": role, "wetness_score": 0.5, "initial_pan": 0.0}
    return stems, meta


def calibrated_stems(seed: int = 17, roles: tuple = ("kick", "bass", "chords", "lead", "hihat"), bpm: float = BPM) -> tuple[dict, dict]:
    """Stems calibrés pour que la distribution Bark du mix (dans la métrique propre du
    surrogate, HPF de départ appliqués, gains nuls) soit ≈ p50 du corridor, avec le Lead
    dominant de >= +3 dB sur Chords dans 500 Hz–4 kHz : aucune correction n'est nécessaire."""
    import json
    import math_mix_solver as M
    rng = np.random.default_rng(seed)
    with open(os.path.join(ROOT, "golden_references", "spectral_corridors.json"), encoding="utf-8") as fh:
        bark = json.load(fh)["profiles"]["trap_rap"]["bark24"]
    p50 = np.clip(np.asarray(bark["p50"], dtype=np.float64), np.asarray(bark["p10"]), np.asarray(bark["p90"]))
    n = int(SR * BARS * 4.0 * 60.0 / bpm)
    band_owner = {b: ("kick" if b <= 2 else "chords" if b <= 4 else "lead" if b <= 17 else "hihat")
                  for b in range(N_BARK)}
    w = {r: np.zeros(N_BARK) for r in roles}
    for b in range(N_BARK):
        if band_owner[b] in w:
            w[band_owner[b]][b] += float(p50[b])

    def build(w):
        noises = {b: _band_noise(rng, n, b) for b in range(N_BARK)}
        mix = np.zeros(n)
        for role in roles:
            for b in range(N_BARK):
                if w[role][b] > 0:
                    mix += math.sqrt(w[role][b]) * noises[b]
        scale = 0.9 / max(float(np.max(np.abs(mix))), 1e-9)
        stems = {}
        for role in roles:
            mono = np.zeros(n)
            for b in range(N_BARK):
                if w[role][b] > 0:
                    mono += math.sqrt(w[role][b]) * noises[b]
            stems[role] = (make_stem_from_mono(mono * scale, corr=1.0), SR)
        return stems

    for _ in range(4):
        stems = build(w)
        sur = M._Surrogate({r: stems[r][0] for r in stems}, SR, {r: M._role_of(r) for r in stems})
        eqs0 = [dict(hpf=80.0 if M._role_of(r) in M.FOUNDATION_ROLES else 120.0,
                     g_lm=0.0, g_mid=0.0, f_mid=1800.0, g_hs=0.0) for r in sur.names]
        E = sur.band_energies(np.zeros(sur.N), eqs0)
        En = E.sum(axis=0)
        En = En / max(En.sum(), 1e-12)
        factor = p50 / np.maximum(En, 1e-9)
        for role in roles:
            w[role] = w[role] * factor
        tot = sum(w[r].sum() for r in roles)
        for r in roles:
            w[r] = w[r] / max(tot, 1e-12)
    stems = build(w)
    meta = {r: {"role": r, "wetness_score": 0.5, "initial_pan": 0.0} for r in roles}
    return stems, meta


def solve(stems, meta, **kw):
    args = dict(mix_profile="trap_rap",
                emotion_vector={"valence": -0.6, "tension": 0.5, "energy": 0.7, "depth": 0.5},
                bpm=BPM)
    args.update(kw)
    return solve_mix_parameters(stems, meta, **args)


@unittest.skipUnless(HAVE_SCIPY, "numpy+scipy requis")
class TestMathMixSolver(unittest.TestCase):
    def test_output_contract(self):
        stems, meta = corridor_matched_stems()
        sol = solve(stems, meta)
        for key in ("converged", "solve_time_ms", "tracks", "buses", "predicted_metrics", "saturated_tracks_for_swap"):
            self.assertIn(key, sol)
        self.assertIn("sidechain_kick_to_bass", sol["buses"])
        self.assertIn("master_limiter", sol["buses"])
        self.assertEqual(sol["buses"]["master_limiter"]["ceiling_db"], -1.2)
        for pm in ("lufs", "true_peak_dbtp", "lead_vs_harmony_db", "mono_folddown_loss_db", "corridor_score", "min_short_term_plr_db"):
            self.assertIn(pm, sol["predicted_metrics"])
        for name, tr in sol["tracks"].items():
            for k in ("gain_trim_db", "volume_auto", "pan", "stereo_width", "eq_bands", "fx_room_send", "fx_hall_send"):
                self.assertIn(k, tr, f"{name} manque {k}")
            for b in tr["eq_bands"]:
                self.assertIn("type", b)
                self.assertNotEqual(b["type"], "lowpass", "jamais de LPF brique")

    def test_severe_spectral_conflict_fixed_in_one_pass(self):
        stems, meta = corridor_matched_stems(seed=11)
        # Chords masquent Lead de +6 dB à ~1.8 kHz (bande 12) + bas-médiums boueux (bande 3-4)
        chords = stems["chords"][0].copy()
        n = chords.shape[0]
        rng = np.random.default_rng(3)
        ref = float(np.sqrt(np.mean(chords * chords)))
        boost18 = 10.0 ** (6.0 / 20.0)
        add18 = (boost18 - 1.0) * math.sqrt(0.05) * _band_noise(rng, n, 12) * ref
        mud = 10.0 ** (8.0 / 20.0)
        addmud = (mud - 1.0) * math.sqrt(0.08) * _band_noise(rng, n, 3) * ref
        for add in (add18, addmud):
            chords += np.stack([add, add], axis=1)
        stems["chords"] = (chords, SR)
        sol = solve(stems, meta)
        ch_eq = next(b for b in sol["tracks"]["chords"]["eq_bands"] if b["type"] == "peak" and b["freq_hz"] > 800)
        self.assertLess(ch_eq["gain_db"], -0.5, f"poche miroir non creusée : {ch_eq}")
        lvh = sol["predicted_metrics"]["lead_vs_harmony_db"]
        self.assertIsNotNone(lvh)
        self.assertGreaterEqual(lvh, 3.0, f"lead_vs_harmony={lvh} < 3.0")
        self.assertLessEqual(lvh, 9.5, f"lead_vs_harmony={lvh} > 9.5")

    def test_lr_imbalance_and_mono_loss(self):
        stems, meta = corridor_matched_stems(seed=13)
        # hihat panoramique droite (corr ~ 0 => Side excessif)
        rng = np.random.default_rng(5)
        w = np.full(N_BARK, 1e-4)
        w[18:] = 0.05
        hh = make_stem(rng, w, corr=0.0, peak=0.7)
        stems["hihat"] = (hh, SR)
        meta["hihat"]["initial_pan"] = 0.6
        sol = solve(stems, meta)
        self.assertLessEqual(sol["predicted_metrics"]["stereo_balance_db"], 0.5,
                             f"stereo_balance={sol['predicted_metrics']['stereo_balance_db']}")
        self.assertLessEqual(sol["predicted_metrics"]["mono_folddown_loss_db"], 2.0,
                             f"mono_folddown={sol['predicted_metrics']['mono_folddown_loss_db']}")
        self.assertLessEqual(sol["tracks"]["hihat"]["pan"], 0.6)

    def test_parsimony_invariant(self):
        import tempfile
        import math_mix_solver as M
        stems, meta = calibrated_stems(seed=17, roles=("chords", "lead", "hihat"))
        eqs0_of = lambda names: [dict(hpf=80.0 if M._role_of(r) in M.FOUNDATION_ROLES else 120.0,
                                      g_lm=0.0, g_mid=0.0, f_mid=1800.0, g_hs=0.0) for r in names]
        # auto-étalonnage : amener lead_vs_harmony au milieu de sa borne [3.0, 9.5] -> mid 6.25
        for _ in range(8):
            sur = M._Surrogate({n: stems[n][0] for n in stems}, SR, {n: M._role_of(n) for n in stems})
            L = sur.loudness(np.zeros(sur.N), eqs0_of(sur.names))
            li = M._find_role(sur.names, sur.roles, "lead")
            ci = M._find_role(sur.names, sur.roles, "harmony")
            diff = float(L[li] - L[ci])
            if abs(diff - 6.25) < 0.3:
                break
            f = 10.0 ** ((6.25 - diff) / 20.0)
            y, sr = stems["lead"]
            stems["lead"] = (y * f, sr)
        sur = M._Surrogate({n: stems[n][0] for n in stems}, SR, {n: M._role_of(n) for n in stems})
        E0 = sur.band_energies(np.zeros(sur.N), eqs0_of(sur.names))
        En = E0.sum(axis=0)
        En = En / En.sum()
        with tempfile.TemporaryDirectory() as td:
            corridors = {"schema_version": "test", "profiles": {"trap_rap": {
                "bark24": {"p10": En.tolist(), "p50": En.tolist(), "p90": En.tolist()},
                "metrics": {}, "dark_valence_overrides": {}}}}
            cpath = os.path.join(td, "corridors.json")
            with open(cpath, "w", encoding="utf-8") as fh:
                json.dump(corridors, fh)
            sol = solve(stems, meta, corridors_path=cpath,
                        emotion_vector={"valence": 0.0, "tension": 0.4, "energy": 0.7, "depth": 0.5})
        for name, tr in sol["tracks"].items():
            for b in tr["eq_bands"]:
                if b["type"] == "hpf":
                    continue
                self.assertLessEqual(abs(b.get("gain_db", 0.0)), 0.3,
                                     f"sur-traitement sur {name} : {b}")
            self.assertLessEqual(abs(tr["gain_trim_db"]), 6.0)

    def test_defective_source_detected_for_swap(self):
        stems, meta = corridor_matched_stems(seed=19)
        rng = np.random.default_rng(23)
        n = int(SR * DUR)
        white = rng.standard_normal(n)
        white /= max(float(np.sqrt(np.mean(white * white))), 1e-12)
        harsh = white + (10.0 ** (20.0 / 20.0)) * _band_noise(rng, n, 14)  # bruit blanc + strident ~3 kHz
        harsh = harsh / max(float(np.max(np.abs(harsh))), 1e-12) * 0.9
        stems["fx"] = (np.stack([harsh, harsh], axis=1), SR)
        meta["fx"] = {"role": "fx", "wetness_score": 0.2, "initial_pan": 0.3}
        sol = solve(stems, meta)
        swapped = [s["track"] for s in sol["saturated_tracks_for_swap"]]
        self.assertIn("fx", swapped, f"fx non détecté : swap={swapped}, residual={sol['residual']}")
        self.assertEqual(sol["saturated_tracks_for_swap"][0]["reason"], "solver_bound_saturation")

    def test_cpu_budget_8_stems_12_bars(self):
        roles = ("kick", "snare", "hihat", "bass", "chords", "lead", "counter", "fx")
        stems, meta = corridor_matched_stems(seed=29, roles=roles, bpm=200.0)
        for role in ("counter", "fx"):
            rng = np.random.default_rng(31)
            w = np.full(N_BARK, 1e-4)
            w[10:16] = 0.04 if role == "counter" else 0.0
            w[16:22] = 0.05 if role == "fx" else 0.0
            stems[role] = (make_stem(rng, w, corr=0.5, peak=0.4), SR)
            meta[role] = {"role": role, "wetness_score": 0.4, "initial_pan": 0.2}
        t0 = time.perf_counter()
        sol = solve(stems, meta)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.0, f"solve en {elapsed:.2f}s >= 1.0s")
        self.assertLess(sol["solve_time_ms"], 1000.0)
        self.assertTrue(sol["converged"])


if __name__ == "__main__":
    unittest.main()
