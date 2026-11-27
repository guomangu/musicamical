#!/usr/bin/env python3
"""Préparation déterministe des samples avant indexation (Chantier 1.1 / 5 de la spec).

Chaîne unique et reproductible, inspirée de `spotify/pedalboard` + `csl-pku/pyloudnorm` :
  1. Trim du silence initial/final sous -60 dBFS avec micro-fondu de 2 ms
     aux passages à zéro (aucun clic).
  2. Normalisation de sonie à exactement -18.0 LUFS intégrés (ITU-R BS.1770-4
     via `pyloudnorm`) pour une comparaison équitable entre échantillons.
  3. Application du gain via `pedalboard` quand la librairie est disponible,
     sinon gain NumPy équivalent (même résultat, chaîne déterministe).

Remplace les manipulations audio manuelles de `sample_prep_lib.py` (déprécié).

Usage CLI :
  .venv/bin/python scripts/audio_prep_pb.py <in.wav> [out.wav] [--target-lufs -18.0]
"""
from __future__ import annotations

import argparse
import os

import numpy as np

TARGET_LUFS = -18.0
SILENCE_THRESHOLD_DBFS = -60.0
EDGE_FADE_MS = 2.0

try:
    import pyloudnorm as _pyloudnorm

    _HAS_PYLOUDNORM = True
except Exception:  # pragma: no cover - pyloudnorm est dans requirements.txt
    _pyloudnorm = None
    _HAS_PYLOUDNORM = False

try:
    from pedalboard import Gain as _PB_Gain, Pedalboard as _Pedalboard

    _HAS_PEDALBOARD = True
except Exception:  # pragma: no cover - pedalboard optionnel (voir requirements.txt)
    _PB_Gain = None
    _Pedalboard = None
    _HAS_PEDALBOARD = False


def load_audio(path: str, sr: int | None = None, mono: bool = True) -> tuple[np.ndarray, int]:
    """Charge un fichier audio en float32. Resample en `sr` si fourni (resample_poly)."""
    import soundfile as sf
    from scipy.signal import resample_poly
    from math import gcd

    audio, file_sr = sf.read(path, dtype="float32", always_2d=False)
    if mono and audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr is not None and sr != file_sr:
        g = gcd(int(sr), int(file_sr))
        audio = resample_poly(audio, sr // g, file_sr // g).astype(np.float32)
        file_sr = sr
    return np.asarray(audio, dtype=np.float32), int(file_sr)


def measure_lufs(audio: np.ndarray, sr: int) -> float:
    """Sonie intégrée LUFS (BS.1770-4). Repli RMS pondéré si pyloudnorm absent."""
    audio = np.asarray(audio, dtype=np.float64)
    if audio.size == 0:
        return -120.0
    if _HAS_PYLOUDNORM:
        meter = _pyloudnorm.Meter(sr)
        min_len = int(meter.block_size * sr) + 1
        if audio.size < min_len:
            # pyloudnorm exige len > block_size ; le zéro-padding est neutre (les blocs
            # sous le seuil absolu -70 LUFS sont exclus du gating BS.1770).
            audio = np.concatenate([audio, np.zeros(min_len - audio.size, dtype=np.float64)])
        return float(meter.integrated_loudness(audio))
    mean_square = float(np.mean(audio**2)) + 1e-20
    return float(-0.691 + 10.0 * np.log10(mean_square))


def _fade_edges(audio: np.ndarray, sr: int, fade_ms: float) -> np.ndarray:
    """Micro-fondu raised-cosine aux deux extrémités (anti-clic)."""
    n = max(1, int(sr * fade_ms / 1000.0))
    n = min(n, audio.size // 2) if audio.size >= 2 else max(1, audio.size)
    if audio.size < 2 * n:
        n = max(1, audio.size // 2)
    w = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, n))
    out = audio.copy()
    out[:n] *= w
    out[-n:] *= w[::-1]
    return out


def trim_silence(
    audio: np.ndarray,
    sr: int,
    threshold_dbfs: float = SILENCE_THRESHOLD_DBFS,
    fade_ms: float = EDGE_FADE_MS,
) -> tuple[np.ndarray, int]:
    """Supprime le silence initial/final sous `threshold_dbfs`, avec fondu de `fade_ms`."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio, 0
    threshold = 10.0 ** (threshold_dbfs / 20.0)
    env = np.abs(audio)
    above = np.flatnonzero(env > threshold)
    if above.size == 0:
        return audio[:0], 0
    start, end = int(above[0]), int(above[-1]) + 1
    trimmed = audio[start:end]
    trimmed = _fade_edges(trimmed, sr, fade_ms)
    return trimmed.astype(np.float32), int(start)


def apply_gain_pedalboard(audio: np.ndarray, sr: int, gain_db: float) -> np.ndarray:
    """Applique le gain via `pedalboard.Pedalboard([Gain])` si disponible, sinon NumPy."""
    if _HAS_PEDALBOARD and abs(gain_db) > 1e-6:
        board = _Pedalboard([_PB_Gain(gain_db=gain_db)])
        out = board(np.asarray(audio, dtype=np.float32), sr)
        return np.asarray(out, dtype=np.float32)
    return np.asarray(audio * (10.0 ** (gain_db / 20.0)), dtype=np.float32)


def normalize_lufs(
    audio: np.ndarray, sr: int, target_lufs: float = TARGET_LUFS, use_pedalboard: bool = True
) -> tuple[np.ndarray, float, float]:
    """Normalise à `target_lufs` LUFS intégrés. Retourne (audio, lufs_avant, gain_db)."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio, -120.0, 0.0
    lufs_before = measure_lufs(audio, sr)
    gain_db = float(target_lufs - lufs_before)
    if use_pedalboard:
        out = apply_gain_pedalboard(audio, sr, gain_db)
    else:
        out = np.asarray(audio * (10.0 ** (gain_db / 20.0)), dtype=np.float32)
    out = np.clip(out, -0.999, 0.999).astype(np.float32)
    return out, lufs_before, gain_db


def prepare_audio(
    audio: np.ndarray,
    sr: int,
    target_lufs: float = TARGET_LUFS,
    threshold_dbfs: float = SILENCE_THRESHOLD_DBFS,
    fade_ms: float = EDGE_FADE_MS,
    use_pedalboard: bool = True,
) -> dict:
    """Chaîne complète déterministe : trim -> normalisation LUFS. Retourne métriques + audio."""
    audio = np.asarray(audio, dtype=np.float32)
    trimmed, start = trim_silence(audio, sr, threshold_dbfs, fade_ms)
    if trimmed.size == 0:
        trimmed = audio
        start = 0
    normalized, lufs_before, gain_db = normalize_lufs(trimmed, sr, target_lufs, use_pedalboard)
    lufs_after = measure_lufs(normalized, sr)
    return {
        "audio": normalized,
        "sr": int(sr),
        "trimmed_start_sample": int(start),
        "lufs_before": float(lufs_before),
        "lufs_after": float(lufs_after),
        "gain_db": float(gain_db),
        "target_lufs": float(target_lufs),
    }


def prepare_file(
    path_in: str,
    path_out: str | None = None,
    sr: int | None = None,
    target_lufs: float = TARGET_LUFS,
    use_pedalboard: bool = True,
) -> dict:
    """Prépare un fichier sur disque ; écrit un WAV 24-bit si `path_out` est fourni."""
    import soundfile as sf

    audio, file_sr = load_audio(path_in, sr=sr, mono=True)
    prep = prepare_audio(audio, file_sr, target_lufs=target_lufs, use_pedalboard=use_pedalboard)
    if path_out:
        os.makedirs(os.path.dirname(os.path.abspath(path_out)), exist_ok=True)
        sf.write(path_out, prep["audio"], prep["sr"], subtype="PCM_24")
    prep["path_in"] = path_in
    prep["path_out"] = path_out
    return prep


def main() -> int:
    parser = argparse.ArgumentParser(description="Préparation déterministe d'un sample (trim -60 dBFS + -18 LUFS).")
    parser.add_argument("input", help="fichier audio d'entrée")
    parser.add_argument("output", nargs="?", default=None, help="fichier WAV 24-bit de sortie (optionnel)")
    parser.add_argument("--sr", type=int, default=None, help="resample (défaut: natif)")
    parser.add_argument("--target-lufs", type=float, default=TARGET_LUFS)
    parser.add_argument("--no-pedalboard", action="store_true", help="force le gain NumPy (sans pedalboard)")
    args = parser.parse_args()
    prep = prepare_file(args.input, args.output, sr=args.sr, target_lufs=args.target_lufs,
                        use_pedalboard=not args.no_pedalboard)
    print(
        f"prepared={prep['path_in']} -> {prep['path_out'] or '(mémoire)'} "
        f"lufs {prep['lufs_before']:.2f} -> {prep['lufs_after']:.2f} (gain {prep['gain_db']:+.2f} dB, "
        f"trim@{prep['trimmed_start_sample']}, pedalboard={'on' if _HAS_PEDALBOARD and not args.no_pedalboard else 'off'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
