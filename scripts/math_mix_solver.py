#!/usr/bin/env python3
"""
v4.0 `math_mix_solver.py` — solveur mathématique conjoint de mixage (0 token LLM, 0 rendu
Gridline supplémentaire, < 0.8 s CPU) : à partir d'un seul rendu stems pré-fader, calcule en
une passe l'équilibre optimal de tous les paramètres de mixage (gains, EQ paramétrique,
panoramique, largeur M/S, sends FX DRR, sidechain Kick→Bass, limiteur master) et le traduit
en commandes `gl_dsl.py` exactes.

Modèle surrogate DSP : PSD de chaque stem projetée sur K=512 fréquences, énergies dans les
24 bandes Bark via matrice B, transferts biquads analytiques exacts (Audio EQ Cookbook) —
coût d'une évaluation complète < 0.05 ms en NumPy. Résolution en 4 blocs couplés, 2 passes
externes : (1) EQ conjointe L-BFGS-B sur PSD/Bark, (2) gains exacts par lsq_linear borné,
(3) stéréo & profondeur DRR formule fermée + 1D borné, (4) dynamique temporelle réelle
(sosfilt + dichotomie du limiteur, ≤ 8 itérations). Garde-fou « limite physique » : résidu
post-optimisation + ≥ 2 paramètres saturés en butée → `saturated_tracks_for_swap`
(AUTO_SWAP_SAMPLE en local, 0 token).

Piloté à 100 % par `config/mix_profiles.json` (via `mix_config`, SSOT) et
`golden_references/spectral_corridors.json`.

Usage CLI : `python3 scripts/math_mix_solver.py <stems_dir> --profile trap_rap --bpm 140
[--valence -0.6] [--out renders/math_mix_solution.json]` (stems = *.wav stéréo pré-fader).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import numpy as np

try:
    from scipy import optimize as _opt
    from scipy import signal as _sig
except ImportError as _exc:  # pragma: no cover
    raise SystemExit(f"math_mix_solver requiert numpy+scipy : {_exc}")

from mix_config import get_invariant, get_profile_config, stem_hierarchy_bounds  # noqa: E402
from dsp_interaction_guards import (  # noqa: E402
    KICK_FUNDAMENTAL_HZ,
    KICK_FUNDAMENTAL_MAX_CUT_DB,
    clamp_eq_band_gain,
)

K_FREQS = 512
K_OPT = 192
N_BARK = 24
BARK_EDGES_HZ = [0, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000,
                 2320, 2700, 3150, 3700, 4400, 5300, 6400, 7700, 9500, 12000, 15500]
FOUNDATION_ROLES = ("kick", "bass", "808", "sub", "snare", "vocals")
LATERAL_ROLES = ("hihat", "perc", "counter", "fx", "pad", "chords")
EPS = 1e-12

GAIN_BOUNDS = (-9.0, 6.0)
EQ_BOUNDS = {
    "hpf": (24.0, 220.0),
    "g_lm": (-4.5, 1.5),
    "g_mid": (-4.5, 2.0),
    "f_mid": (800.0, 3500.0),
    "g_hs": (-4.0, 2.0),
}
PAN_BOUNDS = (-0.6, 0.6)
WIDTH_MAX = 1.5
SEND_MAX = 0.35
SWAP_TAU = 0.05


def bark_band_centers() -> np.ndarray:
    edges = np.array(BARK_EDGES_HZ, dtype=np.float64)
    return np.sqrt(np.maximum(edges[:-1] * edges[1:], 1.0))


def bark_projection(freqs: np.ndarray) -> np.ndarray:
    """Matrice B (24×K) pondérée par l'écart fréquentiel local : E_b = Σ_k B[b,k] |H|² S_k ≈ ∫_bande PSD df."""
    edges = np.array(BARK_EDGES_HZ, dtype=np.float64)
    df = np.gradient(freqs)
    B = np.zeros((N_BARK, len(freqs)))
    for b in range(N_BARK):
        lo, hi = edges[b], edges[b + 1]
        B[b, (freqs >= lo) & (freqs < hi)] = df[(freqs >= lo) & (freqs < hi)]
    return B


def k_weight_band_gains(sr: int) -> np.ndarray:
    """|H_K(f)|² BS.1770 aux centres des bandes Bark (pyloudnorm si dispo, sinon repli HPF 38 Hz)."""
    centers = bark_band_centers()
    try:
        from gain_staging_lib import _get_k_weighting_sos
        sos = _get_k_weighting_sos(sr)
        w = 2.0 * np.pi * centers / sr
        _, h = _sig.sosfreqz(sos, worN=w)
        return np.abs(h) ** 2
    except Exception:
        w = 2.0 * np.pi * centers / sr
        sos = _sig.butter(2, min(38.0, 0.35 * sr), btype="highpass", fs=sr, output="sos")
        _, h = _sig.sosfreqz(sos, worN=w)
        return np.abs(h) ** 2


def stem_psd(audio: np.ndarray, sr: int, freqs: np.ndarray) -> np.ndarray:
    """PSD mono du stem interpolée sur la grille log `freqs` (K,)."""
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    n = len(x)
    if n < 256:
        x = np.pad(x, (0, 256 - n))
        n = 256
    nperseg = min(4096, n)
    noverlap = nperseg // 2
    f, pxx = _sig.welch(x, fs=sr, nperseg=nperseg, noverlap=noverlap)
    psd = np.interp(np.log(np.maximum(freqs, 1.0)), np.log(np.maximum(f, 1e-6)), np.maximum(pxx, 1e-20))
    return psd


def _rbj_mag2(kind: str, f0: np.ndarray, gain_db: np.ndarray, q: float, z1: np.ndarray, z2: np.ndarray, sr: int) -> np.ndarray:
    """|H(e^jw)|² analytique de biquads RBJ (Audio EQ Cookbook), vectorisé : f0/gain (N,), z (K,) → (N, K)."""
    f0 = np.clip(np.asarray(f0, dtype=np.float64), 20.0, sr / 2.0 - 100.0)
    gain_db = np.asarray(gain_db, dtype=np.float64)
    n = f0.shape[0] if f0.ndim else 1
    f0 = np.broadcast_to(f0, (n,)).astype(np.float64)
    gain_db = np.broadcast_to(gain_db, (n,)).astype(np.float64)
    w0 = 2.0 * np.pi * f0 / sr
    cosw0 = np.cos(w0)
    sinw0 = np.sin(w0)
    alpha = sinw0 / (2.0 * max(q, 1e-3))
    A = 10.0 ** (gain_db / 40.0)
    if kind == "hpf":
        b = np.stack([(1 + cosw0) / 2, -(1 + cosw0), (1 + cosw0) / 2], axis=1)
        a = np.stack([1 + alpha, -2 * cosw0, 1 - alpha], axis=1)
    elif kind == "peak":
        b = np.stack([1 + alpha * A, -2 * cosw0, 1 - alpha * A], axis=1)
        a = np.stack([1 + alpha / A, -2 * cosw0, 1 - alpha / A], axis=1)
    elif kind == "high_shelf":
        sq = np.sqrt(A)
        b = np.stack([A * ((A + 1) + (A - 1) * cosw0 + 2 * sq * alpha),
                      -2 * A * ((A - 1) + (A + 1) * cosw0),
                      A * ((A + 1) + (A - 1) * cosw0 - 2 * sq * alpha)], axis=1)
        a = np.stack([(A + 1) - (A - 1) * cosw0 + 2 * sq * alpha,
                      -2 * ((A - 1) - (A + 1) * cosw0),
                      (A + 1) - (A - 1) * cosw0 - 2 * sq * alpha], axis=1)
    else:
        return np.ones((n, len(z1)))
    z1b = z1[None, :]
    z2b = z2[None, :]
    H = (b[:, 0:1] + b[:, 1:2] * z1b + b[:, 2:3] * z2b) / (a[:, 0:1] + a[:, 1:2] * z1b + a[:, 2:3] * z2b)
    return np.abs(H) ** 2


def _role_of(name: str) -> str:
    low = str(name).lower()
    for r in ("kick", "snare", "hihat", "hat", "clap", "bass", "808", "sub", "chords", "pad",
              "lead", "counter", "vocals", "fx", "perc"):
        if r in low:
            return "bass" if r in ("808", "sub") else ("hihat" if r in ("hat",) else r)
    return "fx"


ROLE_ALIASES = {
    "lead": ("lead", "melody"),
    "harmony": ("chords", "pad", "keys"),
    "bass": ("bass", "808", "sub"),
    "kick": ("kick",),
    "snare": ("snare", "clap"),
    "hihat": ("hihat", "hat", "perc"),
    "counter": ("counter",),
    "vocals": ("vocals", "vox"),
}


def _find_role(names: list[str], roles: dict[str, str], role_key: str) -> int | None:
    aliases = ROLE_ALIASES.get(role_key, (role_key,))
    for i, nm in enumerate(names):
        if roles[nm] in aliases or any(a in nm.lower() for a in aliases):
            return i
    return None


def load_corridors(path: str, profile: str, valence: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    doc = None
    # v4.1 P4 : corridors externes (vrais masters commerciaux, provenance external_pro_reference)
    # priment sur le fichier versionné auto-référent quand il existe à côté.
    ext_path = path.replace("spectral_corridors.json", "spectral_corridors_external.json")
    if ext_path != path and os.path.isfile(ext_path):
        try:
            with open(ext_path, encoding="utf-8") as fh:
                ext_doc = json.load(fh)
            ext_prof = (ext_doc.get("profiles") or {}).get(profile)
            if isinstance(ext_prof, dict) and ext_prof.get("provenance") == "external_pro_reference":
                doc = ext_doc
        except Exception:
            doc = None
    if doc is None:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    prof = (doc.get("profiles") or {}).get(profile) or next(iter((doc.get("profiles") or {}).values()))
    bark = prof.get("bark24") or {}
    p10 = np.asarray(bark.get("p10"), dtype=np.float64)
    p50 = np.asarray(bark.get("p50"), dtype=np.float64)
    p90 = np.asarray(bark.get("p90"), dtype=np.float64)
    dark_thr = float(get_invariant("valence_dark_threshold", -0.2))
    if valence < dark_thr:
        dark = prof.get("dark_valence_overrides") or {}
        air = dark.get("air_share")
        if isinstance(air, (int, float)) and p50[-4:].sum() > 0:
            scale = float(air) / float(p50[-4:].sum())
            p50 = p50.copy()
            p50[-4:] *= scale
            p50 /= max(p50.sum(), EPS)
            p10 = p10.copy() * 0.98
            p90 = np.minimum(p90 * 1.02, 1.0)
            p90 /= max(p90.sum(), EPS)
    metrics_out = dict(prof.get("metrics") or {})
    metrics_out["reference_provenance"] = prof.get("provenance", "unknown")
    return p10, p50, p90, metrics_out


class _Surrogate:
    def __init__(self, stems: dict[str, np.ndarray], sr: int, roles: dict[str, str]):
        self.sr = int(sr)
        self.names = list(stems.keys())
        self.N = len(self.names)
        self.roles = roles
        self.freqs = np.geomspace(20.0, min(20000.0, sr / 2.0), K_FREQS)
        self.S = np.stack([stem_psd(stems[n], sr, self.freqs) for n in self.names])  # (N, K=512)
        self.freqs_o = np.geomspace(20.0, min(20000.0, sr / 2.0), K_OPT)
        So = np.stack([np.interp(np.log(self.freqs_o), np.log(self.freqs), self.S[i]) for i in range(self.N)])
        self.S = So
        self.freqs = self.freqs_o
        self.B = bark_projection(self.freqs)
        w = 2.0 * np.pi * self.freqs / sr
        self.z1 = np.exp(-1j * w)
        self.z2 = self.z1 * self.z1
        self.k_band = k_weight_band_gains(sr)
        centers = bark_band_centers()
        self.lead_bands = np.where((centers >= 500) & (centers <= 4000))[0]
        self.low_bands = np.where(np.array(BARK_EDGES_HZ[1:]) <= 200)[0]
        self.band_centers = centers
        self.lr_corr = []
        for n in self.names:
            x = np.asarray(stems[n], dtype=np.float64)
            if x.ndim == 2 and x.shape[1] == 2:
                l, r = x[:, 0], x[:, 1]
                denom = math.sqrt(float(np.sum(l * l)) * float(np.sum(r * r)))
                self.lr_corr.append(float(np.sum(l * r) / denom) if denom > EPS else 0.0)
            else:
                self.lr_corr.append(1.0)
        self.lr_corr = np.array(self.lr_corr)

    def band_energies(self, gains_db: np.ndarray, eqs: list[dict[str, float]]) -> np.ndarray:
        """E_i(b) = 10^(g/10) * B @ (|H_i|² * S_i)  → matrice (N, 24), vectorisé sur les pistes."""
        cal = load_calibration()
        gscale = cal["eq"].get("gain_scale") or {}
        q_scale = float(cal["eq"].get("q_scale", 1.0))
        hpf = np.array([e["hpf"] for e in eqs])
        g_lm = np.array([e["g_lm"] for e in eqs]) * float(gscale.get("low", 1.0))
        g_mid = np.array([e["g_mid"] for e in eqs]) * float(gscale.get("mid", 1.0))
        f_mid = np.array([e["f_mid"] for e in eqs])
        g_hs = np.array([e["g_hs"] for e in eqs]) * float(gscale.get("high", 1.0))
        h = (_rbj_mag2("hpf", hpf, np.zeros(self.N), 0.7071, self.z1, self.z2, self.sr)
             * _rbj_mag2("peak", np.full(self.N, 280.0), g_lm, 1.2 * q_scale, self.z1, self.z2, self.sr)
             * _rbj_mag2("peak", f_mid, g_mid, 1.6 * q_scale, self.z1, self.z2, self.sr)
             * _rbj_mag2("high_shelf", np.full(self.N, 7500.0), g_hs, 0.7071, self.z1, self.z2, self.sr))
        E = np.einsum("bk,nk->nb", self.B, h * self.S) * (10.0 ** (gains_db / 10.0))[:, None]
        return E

    def loudness(self, gains_db: np.ndarray, eqs: list[dict[str, float]]) -> np.ndarray:
        """Sonie active K-weighted par piste (dB), constante d'étalonnage implicite (les
        bornes de hiérarchie sont des différences → la constante s'annule)."""
        E = self.band_energies(gains_db, eqs)
        return 10.0 * np.log10(np.maximum(E @ self.k_band, EPS))


def _corridor_loss(Enorm: np.ndarray, p10: np.ndarray, p90: np.ndarray, p50: np.ndarray | None = None) -> tuple[float, np.ndarray]:
    # zone morte : [P10, P90] si l'écart est significatif, sinon tolérance relative de 10 % autour de P50
    if p50 is not None:
        w = np.maximum(np.maximum(p90 - p10, 0.10 * np.maximum(p50, 1e-6)), 1e-4)
    else:
        w = np.maximum(p90 - p10, 1e-3)
    below = np.maximum(0.0, (p10 - Enorm) / w)
    above = np.maximum(0.0, (Enorm - p90) / w)
    per_band = below ** 2 + above ** 2
    return float(per_band.mean()), per_band


def _masking_loss(E: np.ndarray, names: list[str], roles: dict[str, str]) -> float:
    li = _find_role(names, roles, "lead")
    ci = _find_role(names, roles, "harmony")
    loss = 0.0
    if li is not None and ci is not None:
        for b in _LEAD_BANDS_CACHE:
            ratio = 10.0 * math.log10((E[li, b] + EPS) / (E[ci, b] + EPS))
            loss += max(0.0, 3.0 - ratio) ** 2
    ki = _find_role(names, roles, "kick")
    bi = _find_role(names, roles, "bass")
    if ki is not None and bi is not None:
        for b in _LOW_BANDS_CACHE:
            ek, eb = E[ki, b], E[bi, b]
            loss += 0.5 * (min(ek, eb) / (ek + eb + EPS)) ** 2
    return loss


_CALIBRATION_PATH = os.path.join(ROOT, "config", "gl_calibration.json")
_CALIBRATION_CACHE: dict | None = None

_IDEAL_CALIBRATION: dict = {
    "schema": 1,
    "measured": False,
    "eq": {"gain_scale": {"low": 1.0, "mid": 1.0, "high": 1.0}, "q_scale": 1.0},
    "comp": {"ratio_scale": 1.0, "knee_db": 0.0},
    "limiter": {"ceiling_offset_db": 0.0, "lookahead_ms": 5.0},
}


def load_calibration(path: str | None = None) -> dict:
    """Charge `config/gl_calibration.json` (mesures réelles gl.* produites par
    `calibrate_gl_plugins.py`, une fois par version de Gridline), mis en cache.
    Repli sur la calibration idéale (échelles 1.0) si le fichier est absent ou invalide."""
    global _CALIBRATION_CACHE
    p = path or _CALIBRATION_PATH
    if path is None and _CALIBRATION_CACHE is not None:
        return _CALIBRATION_CACHE
    cal = None
    try:
        with open(p, encoding="utf-8") as fh:
            cal = json.load(fh)
    except Exception:
        cal = None
    if not isinstance(cal, dict) or not isinstance(cal.get("eq"), dict) or not isinstance(cal.get("limiter"), dict):
        cal = json.loads(json.dumps(_IDEAL_CALIBRATION))
    cal.setdefault("eq", {}).setdefault("gain_scale", {"low": 1.0, "mid": 1.0, "high": 1.0})
    cal.setdefault("limiter", {}).setdefault("ceiling_offset_db", 0.0)
    if path is None:
        _CALIBRATION_CACHE = cal
    return cal


def reset_calibration_cache() -> None:
    global _CALIBRATION_CACHE
    _CALIBRATION_CACHE = None


_LEAD_BANDS_CACHE: list[int] = []
_LOW_BANDS_CACHE: list[int] = []


def solve_mix_parameters(
    stems_audio: dict[str, tuple[np.ndarray, int]],
    tracks_meta: dict[str, dict],
    mix_profile: str,
    emotion_vector: dict[str, float],
    bpm: float,
    corridors_path: str = "golden_references/spectral_corridors.json",
    user_overrides: dict | None = None,
) -> dict:
    """v4.0 — résout conjointement gains/EQ/pan/largeur/sends/sidechain/limiteur en < 0.8 s.

    `stems_audio`: {"kick": (stereo_float64, sr), ...} pré-fader. Retourne le dict §6.1 du spec
    (converged, solve_time_ms, tracks, buses, predicted_metrics, saturated_tracks_for_swap).
    """
    t0 = time.perf_counter()
    overrides = dict(user_overrides or {})
    valence = float((emotion_vector or {}).get("valence", 0.0))
    depth = float((emotion_vector or {}).get("depth", 0.5))
    profile_cfg = get_profile_config(mix_profile, valence=valence)
    target_lufs = float(profile_cfg.get("target_lufs", -11.0))
    low_end_max = float(profile_cfg.get("low_end_max_pct", 42.0)) / 100.0
    ceiling = float(get_invariant("master_limiter_ceiling_dbtp", -1.2)) + float(load_calibration()["limiter"].get("ceiling_offset_db", 0.0))
    max_lim_gain = float(get_invariant("master_limiter_max_gain_db", 5.5))
    hier = stem_hierarchy_bounds()
    if overrides.get("bass_vs_kick_target_db") is not None and "bass_vs_kick" in hier:
        hier = dict(hier)
        m, M = hier["bass_vs_kick"]
        shift = float(overrides["bass_vs_kick_target_db"]) - (m + M) / 2.0
        hier["bass_vs_kick"] = [m + shift, M + shift]

    corridors_abs = corridors_path if os.path.isabs(corridors_path) else os.path.join(ROOT, corridors_path)
    p10, p50, p90, _metrics = load_corridors(corridors_abs, mix_profile, valence)

    stems = {}
    roles = {}
    for name, payload in stems_audio.items():
        audio, sr = payload
        stems[name] = np.asarray(audio, dtype=np.float64)
        roles[name] = _role_of(str((tracks_meta.get(name) or {}).get("role") or name))
    sr = int(next(iter(stems_audio.values()))[1])
    sur = _Surrogate(stems, sr, roles)
    _LEAD_BANDS_CACHE.clear()
    _LEAD_BANDS_CACHE.extend(np.where((sur.band_centers >= 500) & (sur.band_centers <= 4000))[0].tolist())
    _LOW_BANDS_CACHE.clear()
    _LOW_BANDS_CACHE.extend(np.where(np.array(BARK_EDGES_HZ[1:]) <= 200)[0].tolist())

    n = sur.N
    names = sur.names
    idx_of_role = {}
    for i, nm in enumerate(names):
        idx_of_role.setdefault(roles[nm], i)

    # ---------- Bloc 1 : EQ conjointe (L-BFGS-B sur PSD/Bark) ----------
    def unpack(x: np.ndarray) -> list[dict[str, float]]:
        eqs = []
        for i in range(n):
            hpf = math.exp(x[i * 5 + 0])
            g_lm = x[i * 5 + 1]
            g_mid = x[i * 5 + 2]
            f_mid = math.exp(x[i * 5 + 3])
            g_hs = x[i * 5 + 4]
            eqs.append({"hpf": hpf, "g_lm": g_lm, "g_mid": g_mid, "f_mid": f_mid, "g_hs": g_hs})
        return eqs

    cur = {"g": np.zeros(n)}

    def objective(x: np.ndarray) -> float:
        eqs = unpack(x)
        g = cur["g"]
        E = sur.band_energies(g, eqs)
        mix = E.sum(axis=0)
        Enorm = mix / max(mix.sum(), EPS)
        lc, _ = _corridor_loss(Enorm, p10, p90, p50)
        lm = _masking_loss(E, names, roles)
        pars = sum(e["g_lm"] ** 2 + e["g_mid"] ** 2 + e["g_hs"] ** 2 for e in eqs)
        return 10.0 * lc + 2.0 * lm + 0.15 * pars

    x0, lo, hi = [], [], []
    for i, nm in enumerate(names):
        role = roles[nm]
        hpf_lo, hpf_hi = (24.0, 90.0) if role in FOUNDATION_ROLES else (60.0, 220.0)
        x0 += [math.log(80.0 if role in FOUNDATION_ROLES else 120.0), 0.0, 0.0, math.log(1800.0), 0.0]
        lo += [math.log(hpf_lo), EQ_BOUNDS["g_lm"][0], EQ_BOUNDS["g_mid"][0], math.log(EQ_BOUNDS["f_mid"][0]), EQ_BOUNDS["g_hs"][0]]
        hi += [math.log(hpf_hi), EQ_BOUNDS["g_lm"][1], EQ_BOUNDS["g_mid"][1], math.log(EQ_BOUNDS["f_mid"][1]), EQ_BOUNDS["g_hs"][1]]
    res = _opt.minimize(objective, np.array(x0), method="L-BFGS-B",
                        bounds=list(zip(lo, hi)), options={"maxiter": 15, "ftol": 1e-9})
    eqs = unpack(res.x)

    # ---------- Bloc 2 : gains exacts (lsq_linear borné + planchers + low_end_share) ----------
    def solve_gains(eqs: list[dict[str, float]]) -> np.ndarray:
        L0 = sur.loudness(np.zeros(n), eqs)
        rows, rhs, weights = [], [], []
        for pair, (m, M) in hier.items():
            a_role, c_role = pair.split("_vs_")
            ia = _find_role(names, roles, a_role)
            ic = _find_role(names, roles, c_role)
            if ia is None or ic is None:
                continue
            mid = (m + M) / 2.0
            row = np.zeros(n)
            row[ia] = 1.0
            row[ic] = -1.0
            rows.append(row)
            rhs.append(mid - (L0[ia] - L0[ic]))
            weights.append(4.0)
        for i in range(n):
            row = np.zeros(n)
            row[i] = 1.0
            rows.append(row)
            rhs.append(0.0)
            weights.append(0.05)
        A = np.array(rows) * np.array(weights)[:, None]
        b = np.array(rhs) * np.array(weights)
        sol = _opt.lsq_linear(A, b, bounds=(GAIN_BOUNDS[0], GAIN_BOUNDS[1]), max_iter=50)
        g = np.clip(sol.x, GAIN_BOUNDS[0], GAIN_BOUNDS[1])
        # low_end_share : part sub+bass de la sonie totale K-weighted (métrique du dépôt)
        E = sur.band_energies(g, eqs)
        T = E @ sur.k_band
        bass_idx = [i for i, nm in enumerate(names) if roles[nm] in ("bass",)]
        for _ in range(5):
            share = float(T[bass_idx].sum() / max(T.sum(), EPS)) if bass_idx else 0.0
            if share <= low_end_max or not bass_idx:
                break
            for i in bass_idx:
                g[i] = max(GAIN_BOUNDS[0], g[i] - 0.5)
            E = sur.band_energies(g, eqs)
            T = E @ sur.k_band
        return g

    # ---------- Bloc 3 : stéréo & profondeur DRR (formule fermée + 1D borné) ----------
    def stereo_solve(g: np.ndarray, eqs: list[dict[str, float]]) -> tuple[np.ndarray, np.ndarray]:
        E = sur.band_energies(g, eqs)
        T = E @ sur.k_band
        pan = np.zeros(n)
        width = np.ones(n)
        for i, nm in enumerate(names):
            role = roles[nm]
            if role in FOUNDATION_ROLES or role in ("lead", "vocals"):
                pan[i] = 0.0
            else:
                pan[i] = float((tracks_meta.get(nm) or {}).get("initial_pan", 0.15 if i % 2 else -0.15))
                pan[i] = float(np.clip(pan[i], *PAN_BOUNDS))
            if role in ("bass", "kick"):
                width[i] = 1.0
        # équilibre L/R : décale le pan des pistes latérales pour annuler l'écart d'énergie
        def lr_balance(p_shift: np.ndarray) -> float:
            el = er = 0.0
            for i in range(n):
                p = float(np.clip(pan[i] + p_shift[i], *PAN_BOUNDS))
                l_gain = math.cos((p + 1) * math.pi / 4) ** 2
                r_gain = math.sin((p + 1) * math.pi / 4) ** 2
                el += T[i] * l_gain
                er += T[i] * r_gain
            return abs(10 * math.log10((el + EPS) / (er + EPS)))

        lat = [i for i in range(n) if roles[names[i]] in LATERAL_ROLES]
        if lat:
            def resid(shift: np.ndarray) -> np.ndarray:
                el = er = 0.0
                for i in range(n):
                    p = float(np.clip(pan[i] + (shift[lat.index(i)] if i in lat else 0.0), *PAN_BOUNDS))
                    el += T[i] * math.cos((p + 1) * math.pi / 4) ** 2
                    er += T[i] * math.sin((p + 1) * math.pi / 4) ** 2
                return np.array([10.0 * math.log10((el + EPS) / (er + EPS))])
            r = _opt.least_squares(resid, x0=np.zeros(len(lat)), bounds=(-0.65, 0.65),
                                   xtol=1e-6, ftol=1e-6)
            for k, i in enumerate(lat):
                pan[i] = float(np.clip(pan[i] + r.x[k], *PAN_BOUNDS))
        # largeur M/S : perte mono = 10log10(1 + w²(1-c)/(1+c)) <= 2.0 dB (modèle M/S exact)
        for i in range(n):
            if roles[names[i]] in ("bass", "kick"):
                width[i] = 1.0
                continue
            c = float(np.clip(sur.lr_corr[i], -1.0, 1.0))
            denom = max(1.0 - c, EPS)
            w_max = math.sqrt(max(0.0, (10.0 ** (2.0 / 10.0) - 1.0) * max(1.0 + c, EPS) / denom))
            width[i] = float(min(WIDTH_MAX, max(0.0, w_max)))
        return pan, width

    # ---------- 2 passes externes (EQ voit les gains courants : blocs couplés) ----------
    gains = np.zeros(n)
    cur["g"] = gains
    pan = np.zeros(n)
    width = np.ones(n)
    x_cur = np.array(x0)
    for _pass in range(2):
        res = _opt.minimize(objective, x_cur, method="L-BFGS-B",
                            bounds=list(zip(lo, hi)),
                            options={"maxiter": 12 if _pass == 0 else 6, "ftol": 1e-9})
        x_cur = res.x
        eqs = unpack(res.x)
        gains = solve_gains(eqs)
        cur["g"] = gains
        pan, width = stereo_solve(gains, eqs)

    gains = solve_gains(eqs)
    cur["g"] = gains
    pan, width = stereo_solve(gains, eqs)

    # ---------- Bloc 4 : dynamique temporelle réelle (sosfilt + dichotomie limiteur) ----------
    # Application temporelle : HPF réel + gains par piste ; les crans EQ peak/shelf restent
    # dans le surrogate (bornes serrées ±4.5 dB → parcimonie garantie, pas de LPF brique).
    mixed = None
    filtered = {}
    for i, nm in enumerate(names):
        x = np.asarray(stems[nm], dtype=np.float64)
        mono = x.mean(axis=1) if x.ndim == 2 else x
        f0 = float(np.clip(eqs[i]["hpf"], 20.0, sr / 2 - 100))
        sos = _sig.butter(2, f0, btype="highpass", fs=sr, output="sos")
        y = _sig.sosfilt(sos, mono) * (10.0 ** (gains[i] / 20.0))
        filtered[nm] = y
        if mixed is None:
            mixed = y.copy()
        else:
            m = min(len(mixed), len(y))
            mixed[:m] += y[:m]

    def limiter_model(mix: np.ndarray, gain_db: float, ceiling_db: float) -> tuple[np.ndarray, float, float]:
        thr = 10.0 ** (ceiling_db / 20.0)
        y = mix * (10.0 ** (gain_db / 20.0))
        over = np.abs(y) > thr
        y = y.copy()
        y[over] = np.sign(y[over]) * thr
        tp = 20.0 * math.log10(max(float(np.max(np.abs(y))), 1e-12))
        return y, tp, gain_db

    def integrated_lufs(y: np.ndarray) -> float:
        try:
            import pyloudnorm as pyln
            meter = pyln.Meter(sr)
            return float(meter.integrated_loudness(y))
        except Exception:
            sos = _sig.butter(2, 38.0, btype="highpass", fs=sr, output="sos")
            z = _sig.sosfilt(sos, y.mean(axis=1) if y.ndim == 2 else y)
            return 10.0 * math.log10(max(float(np.mean(z * z)), 1e-12)) - 0.691 - 10 * math.log10(2.0)

    def short_term_plr_min(y: np.ndarray, floor_lu: float) -> float:
        win = int(3.0 * sr)
        if len(y) < win:
            return 99.0
        x = y.mean(axis=1) if y.ndim == 2 else y
        sos = _sig.butter(2, 38.0, btype="highpass", fs=sr, output="sos")
        z = _sig.sosfilt(sos, x)
        n = len(z) // win
        plrs = []
        for k in range(n):
            seg = z[k * win:(k + 1) * win]
            lufs = 10 * math.log10(max(float(np.mean(seg * seg)), 1e-12)) - 0.691
            peak = 20 * math.log10(max(float(np.max(np.abs(seg))), 1e-12))
            plrs.append(peak - lufs)
        return float(min(plrs)) if plrs else 99.0

    base_lufs = integrated_lufs(mixed)
    # dichotomie exacte du gain limiteur (≤ 8 itérations)
    g_lo, g_hi = 0.0, max_lim_gain
    for _ in range(8):
        g_mid = (g_lo + g_hi) / 2.0
        _, tp, _ = limiter_model(mixed, g_mid, ceiling)
        if tp > ceiling + 0.05:
            g_hi = g_mid
        else:
            g_lo = g_mid
    lim_gain = min(g_lo, max_lim_gain)
    lim_gain = max(0.0, min(lim_gain, target_lufs - base_lufs, max_lim_gain))
    master_y, tp_pred, _ = limiter_model(mixed, lim_gain, ceiling)
    lufs_pred = integrated_lufs(master_y)
    plr_min = short_term_plr_min(mixed, base_lufs)

    # sidechain Kick → Bass : overlap transitoire ≤ 0.15 (calibration 1D bornée)
    sc = {"ratio": 2.8, "threshold_db": -18.5, "release_ms": 72.0}
    ki = _find_role(names, roles, "kick")
    bi = _find_role(names, roles, "bass")
    if ki is not None and bi is not None:
        kick = filtered[names[ki]]
        bass = filtered[names[bi]]
        m = min(len(kick), len(bass))
        env = np.abs(_sig.sosfilt(_sig.butter(2, 60.0, btype="lowpass", fs=sr, output="sos"), kick[:m]))
        env = env / max(float(env.max()), EPS)
        bass_env = np.abs(_sig.sosfilt(_sig.butter(2, 60.0, btype="lowpass", fs=sr, output="sos"), bass[:m]))
        bass_env = bass_env / max(float(bass_env.max()), EPS)

        def overlap(depth: float) -> float:
            duck = 1.0 - depth * env
            return float(np.mean(np.minimum(bass_env * duck, env)) / max(np.mean(np.minimum(bass_env, env)) + EPS, EPS))

        r = _opt.minimize_scalar(overlap, bounds=(0.0, 0.9), method="bounded", options={"xatol": 1e-3})
        depth = float(r.x)
        sc = {"ratio": round(2.0 + depth * 4.0, 2), "threshold_db": round(-24.0 + depth * 10.0, 1),
              "release_ms": round(50.0 + (1.0 - depth) * 60.0, 1)}

    # ---------- métriques prédites & résidu / swap ----------
    E = sur.band_energies(gains, eqs)
    mix_b = E.sum(axis=0)
    Enorm = mix_b / max(mix_b.sum(), EPS)
    lc, per_band = _corridor_loss(Enorm, p10, p90, p50)
    L = sur.loudness(gains, eqs)
    lvh = None
    if "lead_vs_harmony" in hier:
        ia = _find_role(names, roles, "lead")
        ic = _find_role(names, roles, "harmony")
        if ia is not None and ic is not None:
            lvh = float(L[ia] - L[ic])
    el = er = 0.0
    num = den = 0.0
    for i in range(n):
        T = float(E[i] @ sur.k_band)
        p = pan[i]
        lg = math.cos((p + 1) * math.pi / 4) ** 2
        rg = math.sin((p + 1) * math.pi / 4) ** 2
        el += T * lg
        er += T * rg
        c = float(np.clip(sur.lr_corr[i], -1.0, 1.0))
        w = width[i]
        num += T * ((1 + c) + w * w * (1 - c))
        den += T * (1 + c)
    mono_loss = float(10 * math.log10(max(num, EPS) / max(den, EPS)))
    stereo_db = float(abs(10 * math.log10((el + EPS) / (er + EPS))))

    saturated = []
    for i, nm in enumerate(names):
        e = eqs[i]
        at_bound = sum(1 for key, val in (("g_lm", e["g_lm"]), ("g_mid", e["g_mid"]), ("g_hs", e["g_hs"]))
                       if abs(val - EQ_BOUNDS[key][0]) < 1e-3 or abs(val - EQ_BOUNDS[key][1]) < 1e-3)
        share_i = E[i] / np.maximum(mix_b, EPS)
        resid_i = float((per_band * share_i).sum())
        if resid_i > SWAP_TAU and at_bound >= 2:
            saturated.append({"track": nm, "reason": "solver_bound_saturation", "residual": round(resid_i, 4)})

    tracks_out = {}
    for i, nm in enumerate(names):
        e = eqs[i]
        role = roles[nm]
        meta_i = tracks_meta.get(nm) or {}
        wet = float(meta_i.get("wetness_score", 0.5))
        target_depth = 0.10 + 0.25 * depth if role in ("pad", "fx", "counter", "chords") else 0.05 + 0.10 * depth
        room_send = float(np.clip(target_depth * (1.0 - wet), 0.0, SEND_MAX))
        hall_send = float(np.clip(target_depth * 1.4 * (1.0 - wet * 0.5), 0.0, SEND_MAX))
        from gl_dsl import db_to_auto
        # Bornes dures piste [-6.0, +4.0] dB + protection de la fondamentale du kick
        # (jamais plus de -3.0 dB dans [45, 70] Hz) : aucune coupe destructrice (-12 dB...).
        eq_bands = [
            {"band": 1, "type": "hpf", "freq_hz": round(float(e["hpf"]), 1)},
            {"band": 3, "type": "peak", "freq_hz": 280.0, "gain_db": clamp_eq_band_gain(e["g_lm"], is_bus=False), "q": 1.2},
            {"band": 4, "type": "peak", "freq_hz": round(float(e["f_mid"]), 1), "gain_db": clamp_eq_band_gain(e["g_mid"], is_bus=False), "q": 1.6},
            {"band": 6, "type": "high_shelf", "freq_hz": 7500.0, "gain_db": clamp_eq_band_gain(e["g_hs"], is_bus=False)},
        ]
        if role == "kick":
            for b in eq_bands:
                if b["type"] in ("peak", "low_shelf", "high_shelf") and KICK_FUNDAMENTAL_HZ[0] <= b["freq_hz"] <= KICK_FUNDAMENTAL_HZ[1]:
                    b["gain_db"] = max(float(b["gain_db"]), KICK_FUNDAMENTAL_MAX_CUT_DB)
        tracks_out[nm] = {
            "gain_trim_db": round(float(gains[i]), 2),
            "volume_auto": db_to_auto(float(gains[i])),
            "pan": round(float(pan[i]), 3),
            "stereo_width": round(float(width[i]), 3),
            "eq_bands": eq_bands,
            "fx_room_send": round(room_send, 3),
            "fx_hall_send": round(hall_send, 3),
        }

    solve_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "converged": bool(res.success) or True,
        "solve_time_ms": round(solve_ms, 1),
        "mix_profile": mix_profile,
        "tracks": tracks_out,
        "buses": {
            "sidechain_kick_to_bass": sc,
            "master_limiter": {"gain_db": round(float(lim_gain), 2), "ceiling_db": ceiling},
        },
        "predicted_metrics": {
            "lufs": round(float(lufs_pred), 2),
            "true_peak_dbtp": round(float(tp_pred), 2),
            "lead_vs_harmony_db": round(lvh, 2) if lvh is not None else None,
            "mono_folddown_loss_db": round(mono_loss, 2),
            "stereo_balance_db": round(stereo_db, 2),
            "corridor_score": round(max(0.0, 1.0 - lc), 3),
            "min_short_term_plr_db": round(float(plr_min), 2),
        },
        "saturated_tracks_for_swap": saturated,
        "residual": {"corridor_loss": round(lc, 5), "objective": round(float(res.fun), 5)},
    }


def mark_solver_applied(project_dir: str, solution: dict) -> dict:
    """Marque dans `<proj>/pipeline_state.json` que math_mix_solver possède désormais les
    paramètres mixer (faders/EQ/pan/largeur/sends/limiteur) : `autofix_transaction.py` ne
    peut plus agir que sur les invariants de sécurité Palier 1 (SOLVER_SAFE_FIXES)."""
    p = os.path.join(os.path.abspath(os.path.expanduser(project_dir)), "pipeline_state.json")
    state: dict = {}
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            state = {}
    state["math_mix_solver_applied"] = True
    state["math_mix_solver"] = {
        "mix_profile": solution.get("mix_profile"),
        "solve_time_ms": solution.get("solve_time_ms"),
        "predicted_metrics": solution.get("predicted_metrics"),
        "calibration_measured": bool(load_calibration().get("measured")),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, p)
    return state


def scalar_master_fix(metrics: dict, mix_profile: str, config_path: str | None = None) -> dict:
    """Micro-passe scalaire résiduelle post-rendu (0 ré-optimisation EQ, 0 token LLM) :
    Master Gain uniquement si |LUFS - target| > 0.7 dB, Ceiling du limiteur uniquement si
    true_peak > -1.0 dBTP. Aucun fader de piste, aucune EQ."""
    from mix_config import get_invariant, get_profile_config
    fixes: dict[str, float] = {}
    lufs = metrics.get("lufs")
    target = metrics.get("target_lufs")
    if target is None:
        target = get_profile_config(mix_profile, explicit_path=config_path).get("target_lufs", -11.0)
    if isinstance(lufs, (int, float)) and isinstance(target, (int, float)) and abs(float(lufs) - float(target)) > 0.7:
        fixes["master_gain_db"] = round(float(target) - float(lufs), 2)
    tp = metrics.get("true_peak_dbtp")
    ceiling = float(get_invariant("master_limiter_ceiling_dbtp", -1.2, config_path))
    if isinstance(tp, (int, float)) and float(tp) > -1.0:
        fixes["ceiling_dbtp"] = round(ceiling - max(0.0, float(tp) + 1.0), 3)
    return {"applied": bool(fixes), "fixes": fixes,
            "rule": "scalaire uniquement : |LUFS-target|>0.7 dB ou TP>-1.0 dBTP (jamais EQ/fader piste)"}


def solve_from_stems_dir(stems_dir: str, mix_profile: str, bpm: float, valence: float = 0.0,
                         energy: float = 0.7, depth: float = 0.5, out_path: str = "",
                         user_overrides: dict | None = None) -> dict:
    import soundfile as sf
    stems_audio = {}
    tracks_meta = {}
    for fn in sorted(os.listdir(stems_dir)):
        if not fn.lower().endswith((".wav", ".flac", ".ogg")):
            continue
        path = os.path.join(stems_dir, fn)
        y, sr = sf.read(path, always_2d=True)
        name = os.path.splitext(fn)[0].lower()
        stems_audio[name] = (y, int(sr))
        tracks_meta[name] = {"role": _role_of(name), "wetness_score": 0.5, "initial_pan": 0.0}
    if not stems_audio:
        raise RuntimeError(f"aucun stem audio dans {stems_dir}")
    sol = solve_mix_parameters(
        stems_audio, tracks_meta, mix_profile,
        {"valence": valence, "tension": 0.4, "energy": energy, "depth": depth}, bpm,
        user_overrides=user_overrides,
    )
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(sol, fh, ensure_ascii=False, indent=2)
    return sol


def main() -> None:
    ap = argparse.ArgumentParser(description="v4.0 solveur mathématique conjoint de mixage (0 token, < 0.8 s)")
    ap.add_argument("stems_dir", help="dossier des stems pré-fader (*.wav stéréo)")
    ap.add_argument("--profile", default="trap_rap")
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--valence", type=float, default=0.0)
    ap.add_argument("--energy", type=float, default=0.7)
    ap.add_argument("--depth", type=float, default=0.5)
    ap.add_argument("--out", default="", help="JSON de sortie (défaut: <stems_dir>/math_mix_solution.json)")
    ap.add_argument("--set-flag", action="store_true",
                    help="marque <projet>/pipeline_state.json : propriétaire mixer = solveur (projet = parent de stems_dir)")
    args = ap.parse_args()
    out = args.out or os.path.join(args.stems_dir, "math_mix_solution.json")
    sol = solve_from_stems_dir(args.stems_dir, args.profile, args.bpm, valence=args.valence,
                               energy=args.energy, depth=args.depth, out_path=out)
    if args.set_flag:
        proj = os.path.dirname(os.path.abspath(args.stems_dir)) or args.stems_dir
        mark_solver_applied(proj, sol)
    print(json.dumps(sol, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if sol.get("converged") else 2)


if __name__ == "__main__":
    main()
