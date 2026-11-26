"""v4.1 — sample_quality_v2.py — Audio Intelligence v2 (qualité physique + morphologie + agencement).

Remplace le stack v1 (sox/aubio/ffmpeg en CLI spawning, node-wav synchrone, meyda main-thread,
essentia.js WASM brut) par un pipeline Python in-process, « decode once » :
  - **décodage unique** via libsndfile (soundfile) en mémoire, AUCUN fork/exec, aucun re-décodage disque ;
  - **quality gate physique** rapide (numpy/scipy, cible < 15 ms/fichier) : True Peak par
    suréchantillonnage x4 (pics inter-échantillons), détection de faux WAV (upsamplé lossy,
    coupure brique à 11/16 kHz), SNR / bruit de fond, offset DC ;
  - **profilage morphologique** (librosa, workers) : contour F0(t) trame 10 ms (équivalent CREPE),
    signature 808 (chute de pitch ΔF0 > 35 Hz en < 80 ms + queue F0 dans [28, 68] Hz), T30,
    HPSS, crest factor sur la part harmonique, rolloff 85 % -> tags (808, long_sustain,
    saturated_distorted / clean_sub...), rôle, pitch MIDI, 24 bandes Bark normalisées ;
  - **anti-masquage** : coefficient de collision spectrale C(A,B) sur 24 bandes Bark pondérées
    graves + fréquence de croisement HPF optimale ;
  - **alignement de phase** Kick/808 : corrélation croisée bande [30, 100] Hz sur les 50 premières
    ms -> action (aucune / inversion de polarité / micro-délai < 8 ms) garantissant l'absence
    d'annulation de phase.

Équivalences v1 -> v2 : ffmpeg pipe -> soundfile (libsndfile) ; libebur128 -> pyloudnorm (déjà) ;
essentia HPSS/ADSR -> librosa.decompose.hpss + enveloppe RMS ; CREPE -> librosa.pyin ;
CLAP (optionnel, déjà supporté via ClapEmbedder) -> repli embedding DSP (bark24 + métriques) ;
sqlite-vec -> recherche vectorielle numpy (semantic_anchors.npz, déjà en place) ;
piscina workers -> ProcessPoolExecutor (déjà utilisé par l'indexeur).

Usage :
  .venv/bin/python scripts/sample_quality_v2.py <fichier.wav> [--json]
  .venv/bin/python scripts/sample_quality_v2.py --selftest    # jalons synthétiques (tests)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import numpy as np

try:
    from scipy import signal as _sig
except ImportError as _exc:  # pragma: no cover
    raise SystemExit(f"sample_quality_v2 requiert scipy : {_exc}")

from audio_metrics import BARK_EDGES_HZ  # noqa: E402  (mêmes bandes que le reste du framework)

N_BARK = len(BARK_EDGES_HZ)
TP_OVERSAMPLE = 4
CLIP_TP_DBTP_MAX = 0.5
CLIP_RUN_MIN = 3
CLIP_ABS = 0.999
DC_OFFSET_MAX = 0.005
SNR_MIN_DB = 30.0
UPSAMPLED_CUTOFFS_HZ = ((10500.0, 11500.0), (15500.0, 16500.0))  # 22.05k et 32k upsamplés
BRICKWALL_DROP_DB = 30.0
F0_TAIL_808_RANGE = (28.0, 68.0)
PITCH_DROP_MIN_HZ = 35.0
PITCH_DROP_WINDOW_MS = 80.0
T30_SHORT_S = 0.30
T30_LONG_S = 1.10
SAT_CF_MAX_DB = 6.5
SAT_ROLLOFF_MIN_HZ = 350.0
CLEAN_CF_MIN_DB = 9.5
CLEAN_ROLLOFF_MAX_HZ = 160.0
PHASE_BAND_HZ = (30.0, 100.0)
PHASE_WINDOW_MS = 50.0
PHASE_MAX_DELAY_MS = 8.0
PHASE_INVERT_CORR = -0.15


# ---------------- ingestion (decode once) ----------------

def decode_once(path: str, max_seconds: float = 8.0) -> tuple[np.ndarray, int]:
    """Décode UNE FOIS via libsndfile (soundfile) -> (stéréo float64, sr), tronqué à max_seconds.
    Aucun subprocess, aucun fichier intermédiaire."""
    import soundfile as sf
    y, sr = sf.read(path, dtype="float64", always_2d=True)
    n = min(len(y), int(max_seconds * sr))
    return np.ascontiguousarray(y[:n]), int(sr)


def remove_dc_offset(x: np.ndarray) -> np.ndarray:
    return x - float(np.mean(x))


def smart_trim(x: np.ndarray, sr: int, thr_dbfs: float = -65.0, preroll_ms: float = 2.0) -> np.ndarray:
    """Trim au premier/dernier échantillon > thr_dbfs avec pré-roll d'attaque (préserve le transitoire)."""
    thr = 10.0 ** (thr_dbfs / 20.0)
    nz = np.where(np.abs(x) > thr)[0]
    if len(nz) == 0:
        return x
    pre = int(preroll_ms * sr / 1000.0)
    return x[max(0, nz[0] - pre): min(len(x), nz[-1] + 1)]


# ---------------- quality gate physique (rapide) ----------------

def true_peak_dbtp(x: np.ndarray, sr: int) -> float:
    """True Peak (pics inter-échantillons) par suréchantillonnage x4, norme BS.1770."""
    mono = np.asarray(x, dtype=np.float64)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    up = _sig.resample_poly(mono, TP_OVERSAMPLE, 1)
    return round(20.0 * math.log10(max(float(np.max(np.abs(up))), 1e-12)), 3)


def _max_consecutive_true(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def _brickwall_cutoff_hz(mono: np.ndarray, sr: int, bin_hz: float = 500.0) -> float | None:
    """Détecte une coupure brique (brickwall) à ~11 kHz ou ~16 kHz sur un source large bande.
    Analyse en bins de 500 Hz : dernière bin significative suivie d'une chute > 30 dB."""
    if sr < 44000:
        return None  # à 22,05 kHz natif, la coupure à 11 kHz est le Nyquist du fichier, pas un upsampling
    n = min(len(mono), 1 << 18)
    seg = mono[len(mono) // 2 - n // 2: len(mono) // 2 + n // 2] if n < len(mono) else mono
    win = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft(seg * win)) ** 2
    fr = np.fft.rfftfreq(len(seg), 1.0 / sr)
    edges = np.arange(0.0, fr[-1] + bin_hz, bin_hz)
    e_bins = np.array([float(np.sum(spec[(fr >= edges[i]) & (fr < edges[i + 1])])) for i in range(len(edges) - 1)])
    f_bins = 0.5 * (edges[:-1] + edges[1:])
    ref_bins = (f_bins >= 1000.0) & (f_bins < 10000.0)
    if not np.any(ref_bins):
        return None
    ref_per_bin = float(np.mean(e_bins[ref_bins]))
    thr = ref_per_bin * 10.0 ** (-30.0 / 10.0)
    hi = f_bins >= 8000.0
    if not np.any(hi):
        return None
    sig = np.where(hi & (e_bins > thr))[0]
    if len(sig) == 0:
        return None  # aucun contenu HF significatif (ex. sinus pur) -> pas de coupure détectable
    last = int(sig[-1])
    f_last = float(f_bins[last])
    if last + 1 < len(e_bins) and e_bins[last + 1] < e_bins[last] * 10.0 ** (-BRICKWALL_DROP_DB / 10.0):
        for lo, hi_cut in UPSAMPLED_CUTOFFS_HZ:
            if lo <= f_last <= hi_cut:
                return round(f_last, 1)
    return None


def _snr_spectral(mono: np.ndarray, sr: int) -> tuple[float, float]:
    """(SNR dB, fraction tonale) : puissance des pics spectraux (contenu structuré) vs plancher
    de bruit (médiane du spectre). Sinus pur -> plancher ~0, fraction ~1, SNR ~99 dB (PASS) ;
    sinus + bruit blanc -20 dB -> SNR ~17-20 dB ; bruit blanc seul -> fraction tonale ~0."""
    n = min(len(mono), 1 << 18)
    seg = mono[:n] * np.hanning(n)
    spec = np.abs(np.fft.rfft(seg)) ** 2
    floor = float(np.median(spec)) + 1e-24
    tonal = spec[spec > floor * 100.0]
    p_sig = float(np.sum(tonal))
    p_noise = floor * len(spec)
    snr = 10.0 * math.log10(max(p_sig, 1e-24) / max(p_noise, 1e-24))
    frac = p_sig / max(float(np.sum(spec)), 1e-24)
    return round(float(min(99.0, max(0.0, snr))), 2), round(float(min(1.0, frac)), 4)


def snr_db(mono: np.ndarray, sr: int) -> float:
    return _snr_spectral(mono, sr)[0]


def _time_dynamic_range_db(mono: np.ndarray, sr: int, hop_ms: float = 20.0) -> float:
    """Écart (dB) entre le RMS pic (trame 20 ms) et le 15e percentile (plancher temporel).
    Un signal noyé dans un bruit de fond constant a un écart faible (< 15 dB) ;
    un one-shot percussif propre (hat, snare, clap) décroît vers le silence (écart >= 18 dB)."""
    if len(mono) < int(0.15 * sr):
        return 99.0  # transitoire ultra-court (< 150 ms) : pas de plancher stationnaire mesurable
    hop = max(1, int(hop_ms * sr / 1000.0))
    nf = len(mono) // hop
    if nf < 4:
        return 99.0
    rms = np.sqrt(np.mean(mono[: nf * hop].reshape(nf, hop) ** 2, axis=1)) + 1e-12
    peak_rms = float(np.max(rms))
    q15_rms = float(np.percentile(rms, 15))
    return round(float(20.0 * math.log10(max(peak_rms, 1e-12) / max(q15_rms, 1e-12))), 2)


def quality_gate_v2(x: np.ndarray, sr: int) -> dict:
    """Quality Gate physique v2. Rapide (numpy/scipy uniquement, cible < 15 ms/fichier).
    Retourne {status, true_peak_dbtp, snr_db, dc_offset, defects[]} — REJECTED si defects non vide."""
    mono = np.asarray(x, dtype=np.float64)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    defects: list[str] = []
    dc = float(np.mean(mono)) if len(mono) else 0.0
    if abs(dc) > DC_OFFSET_MAX:
        defects.append("DC_OFFSET")
    tp = true_peak_dbtp(mono, sr)
    clip_run = _max_consecutive_true(np.abs(mono) >= CLIP_ABS)
    if tp > CLIP_TP_DBTP_MAX or clip_run >= CLIP_RUN_MIN:
        defects.append("CLIPPED")
    cutoff = _brickwall_cutoff_hz(mono, sr)
    if cutoff is not None:
        defects.append(f"UPSAMPLED_LOSSY(cutoff={cutoff}Hz)")
    snr, tonal_frac = _snr_spectral(mono, sr)
    dyn_db = _time_dynamic_range_db(mono, sr)
    # NOISY_FLOOR exige à la fois un faible SNR spectral ET un plancher de bruit persistant dans le temps
    # (sinon les percussions large bande propres — hi-hats, snares, claps — sont faussement rejetées).
    if snr < SNR_MIN_DB and tonal_frac >= 0.02 and dyn_db < 15.0:
        defects.append("NOISY_FLOOR")  # signal structuré noyé dans un plancher de bruit persistant
    status = "REJECTED" if defects else "PASS"
    return {
        "status": status,
        "true_peak_dbtp": tp,
        "snr_db": snr,
        "dc_offset": round(dc, 6),
        "defects": defects,
    }


# ---------------- morphologie & tagging ----------------

def _hz_to_midi(f: float) -> float:
    return 69.0 + 12.0 * math.log2(max(f, 1e-6) / 440.0)


def f0_contour(mono: np.ndarray, sr: int, hop_ms: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """Contour F0(t) trame par trame (équivalent CREPE/YIN, 10 ms) via librosa.pyin."""
    import librosa
    f0, voiced, _ = librosa.pyin(
        mono, fmin=27.5, fmax=2000.0, sr=sr,
        hop_length=max(1, int(hop_ms * sr / 1000.0)), frame_length=4096,
    )
    f0 = np.asarray(f0, dtype=np.float64)
    voiced = np.asarray(voiced, dtype=bool)
    f0 = np.where(voiced, f0, np.nan)
    times = np.arange(len(f0)) * hop_ms / 1000.0
    return times, f0


def _t30_ms(mono: np.ndarray, sr: int, hop_ms: float = 10.0) -> float:
    """T30 : durée pour passer du pic d'enveloppe RMS à -30 dB (enveloppe lissée)."""
    hop = max(1, int(hop_ms * sr / 1000.0))
    n = len(mono) // hop
    if n < 4:
        return 0.0
    env = np.sqrt(np.mean(mono[: n * hop].reshape(n, hop) ** 2, axis=1))
    env_db = 20.0 * np.log10(np.maximum(env, 1e-12))
    peak = int(np.argmax(env_db))
    target = env_db[peak] - 30.0
    after = np.where(env_db[peak:] <= target)[0]
    if len(after) == 0:
        return float(len(env_db) - peak) * hop_ms  # jamais atteint -30 dB
    return round(float(after[0]) * hop_ms, 1)


def _bark24(mono: np.ndarray, sr: int) -> list[float]:
    n = min(len(mono), 1 << 18)
    seg = mono[:n]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    fr = np.fft.rfftfreq(len(seg), 1.0 / sr)
    bark = np.zeros(N_BARK)
    for i, (lo, hi) in enumerate(BARK_EDGES_HZ):
        m = (fr >= lo) & (fr < hi)
        if np.any(m):
            bark[i] = float(np.sum(spec[m]))
    tot = float(np.sum(bark)) + 1e-18
    return [round(float(v / tot), 6) for v in bark]


def profile_morphology(mono: np.ndarray, sr: int) -> dict:
    """Profil morphologique & psychoacoustique : rôle, pitch, signature 808, T30, HPSS, tags."""
    import librosa
    mono = np.asarray(mono, dtype=np.float64)
    dur_s = len(mono) / float(sr)
    tags: list[str] = []

    times, f0 = f0_contour(mono, sr)
    voiced_f0 = f0[~np.isnan(f0)]
    pitch_midi = None
    pitch_confidence = 0.0
    has_pitch_drop = False
    drop_hz = 0.0
    f0_tail = None
    if len(voiced_f0):
        pitch_confidence = round(float(np.mean(~np.isnan(f0))), 3)
        f0_tail = float(np.median(voiced_f0[int(0.7 * len(voiced_f0)):]))
        pitch_midi = int(round(_hz_to_midi(float(np.median(voiced_f0)))))
    # F0 de tête par pic spectral des 80 premières ms (robuste aux glissandos rapides)
    head_n = min(len(mono), int(PITCH_DROP_WINDOW_MS * sr / 1000.0))
    f0_head = None
    if head_n >= 256:
        hs = np.abs(np.fft.rfft(mono[:head_n] * np.hanning(head_n)))
        hf = np.fft.rfftfreq(head_n, 1.0 / sr)
        f0_head = float(hf[int(np.argmax(hs))])
    if f0_head is not None and f0_tail is not None:
        drop_hz = f0_head - f0_tail
        has_pitch_drop = drop_hz > PITCH_DROP_MIN_HZ

    t30 = _t30_ms(mono, sr)
    if t30 < T30_SHORT_S * 1000.0:
        tags.append("envelope:percussive_short")
    elif t30 < T30_LONG_S * 1000.0:
        tags.append("envelope:medium")
    else:
        tags.append("envelope:long_sustain")

    # Crest factor temporel (formule spec) : CF = 20 log10(max|x| / rms(x))
    pk = float(np.max(np.abs(mono))) if len(mono) else 0.0
    rm = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
    crest_db = round(20.0 * math.log10(max(pk, 1e-12) / max(rm, 1e-12)), 2)
    # HPSS : ratio d'énergie harmonique au-dessus de la fondamentale (signe de saturation)
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2 if len(mono) else np.zeros(1)
    fr = np.fft.rfftfreq(len(mono), 1.0 / sr) if len(mono) else np.zeros(1)
    tot = float(np.sum(spec)) + 1e-18
    rolloff = float(fr[np.searchsorted(np.cumsum(spec), 0.85 * tot)]) if tot > 0 else 0.0
    centroid = float(np.sum(fr * spec) / tot) if tot > 0 else 0.0
    harm_ratio = 0.0
    if f0_tail is not None and f0_tail > 0:
        try:
            # clamp dur : n_fft <= len(mono) (sinon UserWarning librosa "n_fft too large")
            n_fft = 1 << int(np.floor(np.log2(max(1, min(4096, len(mono))))))
            mag = np.abs(librosa.stft(mono, n_fft=n_fft, hop_length=n_fft // 4))
            _, mag_h = librosa.decompose.hpss(mag)
            fgrid = np.fft.rfftfreq(n_fft, 1.0 / sr)
            e_tot = float(np.sum(mag_h ** 2)) + 1e-18
            harm_band = (fgrid >= 2.5 * f0_tail) & (fgrid <= 8.0 * f0_tail)
            harm_ratio = round(float(np.sum(mag_h[harm_band, :] ** 2)) / e_tot, 4)
        except Exception:
            harm_ratio = 0.0

    is_808 = (f0_tail is not None and F0_TAIL_808_RANGE[0] <= f0_tail <= F0_TAIL_808_RANGE[1]
              and has_pitch_drop)
    if is_808:
        tags.append("808")
    if f0_tail is not None and F0_TAIL_808_RANGE[0] <= f0_tail <= F0_TAIL_808_RANGE[1]:
        tags.append("sub")
    if (crest_db < SAT_CF_MAX_DB and (f0_tail or 1e9) < 65.0
            and (rolloff > SAT_ROLLOFF_MIN_HZ or harm_ratio > 0.15)):
        tags.append("timbre:saturated_distorted")
    elif crest_db >= CLEAN_CF_MIN_DB and rolloff < CLEAN_ROLLOFF_MAX_HZ:
        tags.append("timbre:clean_sub")

    # rôle
    if is_808:
        role = "808"
    elif f0_tail is not None and f0_tail < 110.0 and t30 >= T30_SHORT_S * 1000.0:
        role = "bass"
    elif t30 < T30_SHORT_S * 1000.0 and (f0_tail is None or f0_tail < 200.0):
        role = "kick" if (f0_tail or 0) < 80.0 else "perc"
    elif t30 < T30_SHORT_S * 1000.0:
        role = "snare" if centroid > 1500.0 else "perc"
    elif centroid > 3000.0:
        role = "hihat"
    else:
        role = "synth"

    return {
        "role": role,
        "pitch_midi": pitch_midi,
        "pitch_confidence": pitch_confidence,
        "f0_tail_hz": round(f0_tail, 2) if f0_tail is not None else None,
        "has_pitch_drop": bool(has_pitch_drop),
        "pitch_drop_hz": round(drop_hz, 1),
        "decay_t30_ms": t30,
        "crest_factor_db": crest_db,
        "harmonic_energy_ratio": harm_ratio,
        "spectral_rolloff85_hz": round(rolloff, 1),
        "spectral_centroid_hz": round(centroid, 1),
        "duration_s": round(dur_s, 3),
        "bark_24": _bark24(mono, sr),
        "tags": tags,
    }


# ---------------- anti-masquage (bandes critiques) ----------------

def _bark_weights() -> np.ndarray:
    """Poids forts sur les bandes graves (20-500 Hz = bandes 0..5), 1.0 ailleurs."""
    w = np.ones(N_BARK)
    for b, (lo, hi) in enumerate(BARK_EDGES_HZ):
        if hi <= 510.0:
            w[b] = 4.0
    return w


def spectral_collision(bark_a, bark_b) -> dict:
    """C(A,B) = Σ_b w_b E_A(b) E_B(b) (pondération graves x4) + version normalisée."""
    a = np.asarray(bark_a, dtype=np.float64)
    b = np.asarray(bark_b, dtype=np.float64)
    if len(a) != N_BARK or len(b) != N_BARK:
        return {"collision": None, "collision_norm": None}
    w = _bark_weights()
    c = float(np.sum(w * a * b))
    return {"collision": round(c, 6), "collision_norm": round(c / float(np.sum(w)), 6)}


def suggest_hpf_hz(bark_active, bark_candidate, lo_hz: float = 80.0, hi_hz: float = 500.0) -> float:
    """Fréquence de croisement optimale : borne basse de la première bande où le mix actif
    domine le candidat (HPF ciblé pour dégager le candidat sous cette fréquence)."""
    a = np.asarray(bark_active, dtype=np.float64)
    b = np.asarray(bark_candidate, dtype=np.float64)
    if len(a) != N_BARK or len(b) != N_BARK or float(a.sum()) <= 0:
        return lo_hz
    for i, (lo, hi) in enumerate(BARK_EDGES_HZ):
        if hi > hi_hz:
            break
        if a[i] > 0.05 and a[i] >= b[i]:
            return float(min(max(lo, lo_hz), hi_hz))
    return lo_hz


# ---------------- alignement de phase Kick/808 ----------------

def phase_alignment(kick: np.ndarray, bass: np.ndarray, sr: int) -> dict:
    """Corrélation croisée bande [30, 100] Hz sur les 50 premières ms.
    Retourne l'action (none|invert_polarity|delay_ms) qui maximise la sommation sans annulation :
    rms_bande(somme après) >= max(rms_kick, rms_bass)."""
    sos = _sig.butter(4, [PHASE_BAND_HZ[0], PHASE_BAND_HZ[1]], btype="bandpass", fs=sr, output="sos")
    k = _sig.sosfilt(sos, np.asarray(kick, dtype=np.float64).mean(axis=1) if np.asarray(kick).ndim == 2 else kick)
    b = _sig.sosfilt(sos, np.asarray(bass, dtype=np.float64).mean(axis=1) if np.asarray(bass).ndim == 2 else bass)
    win = max(1, int(PHASE_WINDOW_MS * sr / 1000.0))
    k = k[:win]
    b = b[:min(win, len(b))]
    m = min(len(k), len(b))
    k, b = k[:m], b[:m]
    if m < 16:
        return {"action": "none", "reason": "too_short", "no_cancellation": True}

    def band_rms(x):
        return float(np.sqrt(np.mean(x * x)))

    rk, rb = band_rms(k), band_rms(b)
    sum_before = band_rms(k + b)
    corr0 = float(np.dot(k, b) / max(np.sqrt(np.dot(k, k) * np.dot(b, b)), 1e-12))
    xcorr = _sig.correlate(b, k, mode="full")
    lag = int(np.argmax(xcorr)) - (m - 1)
    lag_ms = lag * 1000.0 / sr

    action = "none"
    k_eff = k.copy()
    if corr0 < PHASE_INVERT_CORR:
        action = "invert_polarity"
        k_eff = -k
    elif abs(lag_ms) <= PHASE_MAX_DELAY_MS and corr0 < 0.85:
        action = f"delay_ms={round(lag_ms, 2)}"
        shift = int(round(lag_ms * sr / 1000.0))
        if shift > 0:
            k_eff = np.concatenate([np.zeros(shift), k[: m - shift]])
        elif shift < 0:
            k_eff = np.concatenate([k[-shift:], np.zeros(-shift)])
        k_eff = k_eff[:m]
    sum_after = band_rms(k_eff + b)
    no_cancellation = bool(sum_after >= 0.99 * max(rk, rb))
    return {
        "action": action,
        "corr0": round(corr0, 3),
        "best_lag_ms": round(lag_ms, 2),
        "band_rms_kick": round(rk, 5),
        "band_rms_bass": round(rb, 5),
        "band_rms_sum_before": round(sum_before, 5),
        "band_rms_sum_after": round(sum_after, 5),
        "no_cancellation": no_cancellation,
    }


# ---------------- profil complet (contrat AudioSampleProfile v2) ----------------

def analyze_file_v2(path: str, max_seconds: float = 8.0) -> dict:
    """Profil complet d'un sample : quality gate + morphologie + tags (contrat v2)."""
    import hashlib
    y, sr = decode_once(path, max_seconds=max_seconds)
    mono = y.mean(axis=1)
    mono_clean = remove_dc_offset(mono)
    q = quality_gate_v2(mono_clean, sr)
    morph = profile_morphology(smart_trim(mono_clean, sr), sr)
    with open(path, "rb") as fh:
        fid = hashlib.md5(fh.read()).hexdigest()[:12]
    return {
        "id": fid,
        "file_path": os.path.abspath(path),
        "sample_rate": int(sr),
        "duration_ms": round(1000.0 * len(mono) / sr, 1),
        "quality": q,
        "morphology": morph,
        "tags": sorted(set(morph["tags"]) | {"defect:" + d.split("(")[0] for d in q["defects"]}),
    }


def quality_row_for_db(sample_id: int, profile: dict) -> dict:
    """Ligne pour la table `sample_quality_v2`."""
    import time as _time
    q = profile["quality"]
    morph = profile["morphology"]
    return {
        "sample_id": int(sample_id),
        "status": q["status"],
        "defects_json": json.dumps(q["defects"]),
        "true_peak_dbtp": q["true_peak_dbtp"],
        "snr_db": q["snr_db"],
        "morphology_json": json.dumps(morph, default=str),
        "bark_24_json": json.dumps(morph.get("bark_24") or []),
        "tags_json": json.dumps(profile.get("tags") or []),
        "updated_at": _time.time(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Audio Intelligence v2 : quality gate + morphologie + anti-masquage + phase")
    ap.add_argument("path", nargs="?", default="", help="fichier audio à profiler")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="jalons synthétiques (comme les tests)")
    args = ap.parse_args()
    if args.selftest:
        rep = selftest()
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        return 0 if rep["all_pass"] else 1
    if not args.path:
        ap.print_help()
        return 2
    prof = analyze_file_v2(args.path)
    print(json.dumps(prof, ensure_ascii=False, indent=2, default=str))
    return 0 if prof["quality"]["status"] == "PASS" else 1


def selftest() -> dict:
    """Jalons d'acceptation synthétiques (miroir de tests/test_sample_quality_v2.py)."""
    import tempfile
    import soundfile as sf
    sr = 22050
    t = np.arange(int(1.0 * sr)) / sr
    sine = 10.0 ** (-3.0 / 20.0) * np.sin(2 * np.pi * 1000.0 * t)
    clipped = np.clip(1.26 * np.sin(2 * np.pi * 1000.0 * t), -1.0, 1.0)
    rng = np.random.default_rng(3)
    noisy = sine + 0.1 * rng.standard_normal(len(t))  # bruit blanc ~-20 dB vs sinus -3 dBFS
    sr_h = 44100  # le faux WAV est un 44,1 kHz upsamplé depuis 22,05 kHz
    t_h = np.arange(int(1.0 * sr_h)) / sr_h
    wide = rng.standard_normal(len(t_h))
    wide = wide / max(float(np.max(np.abs(wide))), 1e-9) * 0.9
    # coupure brique idéale à 11 kHz (équivalent fichier 22,05 kHz upsamplé)
    sp = np.fft.rfft(wide)
    fr = np.fft.rfftfreq(len(wide), 1.0 / sr_h)
    sp[fr > 11000.0] = 0.0
    lp11 = np.fft.irfft(sp, n=len(wide))
    lp11_sr = sr_h
    out = {}
    with tempfile.TemporaryDirectory() as td:
        for name, sig, fsr in (("sine", sine, sr), ("clipped", clipped, sr), ("noisy", noisy, sr), ("lp11", lp11, lp11_sr)):
            p = os.path.join(td, name + ".wav")
            sf.write(p, sig.astype(np.float32), fsr)
            prof = analyze_file_v2(p, max_seconds=2.0)
            out[name] = {"status": prof["quality"]["status"], "defects": prof["quality"]["defects"],
                         "tp": prof["quality"]["true_peak_dbtp"], "snr": prof["quality"]["snr_db"]}
    # 808 synthétique : pitch 120 -> 45 Hz en 50 ms, T30 = 1.6 s, saturation tanh
    dur = 2.0
    tt = np.arange(int(dur * sr)) / sr
    f0 = np.where(tt < 0.05, 120.0, 45.0 + 75.0 * np.exp(-(tt - 0.05) / 0.02))
    phase = 2 * np.pi * np.cumsum(f0) / sr
    env = 10.0 ** (-30.0 * tt / (20.0 * 1.6))
    x808 = env * np.sin(phase)
    x808 = np.tanh(5.0 * x808)  # saturation tanh -> CF ~5 dB
    m = profile_morphology(x808, sr)
    out["synth_808"] = {"tags": m["tags"], "role": m["role"], "pitch_midi": m["pitch_midi"],
                        "t30_ms": m["decay_t30_ms"], "crest_db": m["crest_factor_db"],
                        "has_drop": m["has_pitch_drop"], "drop_hz": m["pitch_drop_hz"]}
    # phase : kick et 808 partagent la bande sub (45 Hz),burst d'attaque pour le kick
    kick = np.zeros(int(0.3 * sr))
    kick[: int(0.05 * sr)] = np.sin(2 * np.pi * 45.0 * np.arange(int(0.05 * sr)) / sr)
    bass45 = np.sin(2 * np.pi * 45.0 * np.arange(int(0.3 * sr)) / sr)
    pa = phase_alignment(kick, -bass45, sr)
    out["phase_inverted"] = pa
    pa_ok = phase_alignment(kick, bass45, sr)
    out["phase_aligned"] = pa_ok
    checks = {
        "sine_pass": out["sine"]["status"] == "PASS",
        "clipped_rejected": out["clipped"]["status"] == "REJECTED" and any(d.startswith("CLIPPED") for d in out["clipped"]["defects"]),
        "noisy_rejected": out["noisy"]["status"] == "REJECTED" and any(d.startswith("NOISY_FLOOR") for d in out["noisy"]["defects"]),
        "lp11_rejected": out["lp11"]["status"] == "REJECTED" and any(d.startswith("UPSAMPLED_LOSSY") for d in out["lp11"]["defects"]),
        "808_tags": all(t in out["synth_808"]["tags"] for t in ("808", "envelope:long_sustain", "timbre:saturated_distorted")),
        "808_pitch": out["synth_808"]["pitch_midi"] is not None and abs(out["synth_808"]["pitch_midi"] - 29) <= 1,
        "phase_fix": out["phase_inverted"]["action"] in ("invert_polarity",) and out["phase_inverted"]["no_cancellation"],
        "phase_aligned_ok": out["phase_aligned"]["no_cancellation"],
    }
    return {"all_pass": all(checks.values()), "checks": checks, "detail": out}


if __name__ == "__main__":
    sys.exit(main())
