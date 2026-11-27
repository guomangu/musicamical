#!/usr/bin/env python3
"""Indexeur unifié psychoacoustique -> `samples.db` (SQLite, racine du dépôt). Chantier 1.

Remplace la logique éclatée entre `indexer.py`, `preset_indexer.py`, `index_samples.py`,
`index_project_samples.py` et `sample_prep_lib.py` : une seule table `sound_assets` est la
source unique de vérité pour les samples WAV, les presets VST3 et les instruments SFZ.

Pipeline d'ingestion déterministe (spec §1.1), pour chaque asset :
  1. Nettoyage via `scripts/audio_prep_pb.py` : trim sous -60 dBFS + fondu 2 ms ;
  2. Normalisation à -18.0 LUFS intégrés (`pyloudnorm`, gain via `pedalboard` si dispo) ;
  3. Descripteurs acoustiques : f0 + confiance tonale (Essentia `PitchYinFFT` si présent,
     sinon repli librosa `pyin`, sinon autocorrélation NumPy), centroïde spectral,
     rolloff 85 %, attaque logarithmique, facteur de crête, énergies 20-150 / 150-500 /
     500-4000 / 4000-20000 Hz ;
  4. 7 scores perceptuels normalisés sur [0, 100] (warmth, brightness, roughness, hardness,
     depth, boomy, sharpness) — approximation psychoacoustique déterministe inspirée des
     modèles calibrés `AudioCommons/timbral_models` (mêmes dimensions, calcul spectral local) ;
  5. Embedding sémantique 512-d `clap_embedding` (BLOB float32 L2-normalisé, 2048 octets) :
     LAION CLAP (`laion/clap-htsat-unfused`) quand le modèle est disponible en local, sinon
     projection déterministe (seed fixe) du vecteur de descripteurs psychoacoustiques en 512-d —
     même contrat de colonne, zéro dépendance lourde hors-ligne (recherche par similarité
     cosinus : `semantic_search()` ici, `search_assets_hybrid()` dans `scripts/palette_selector.py`) ;
  6. `modulation_profile_json` pour les `vst3_preset` / `sfz_instrument` : paramètres
     automatisables exposés (filter_cutoff, resonance, env_decay, lfo_rate, ...) extraits du
     .sfz / .vstpreset, utilisés par `synth_render.py` v2 pour les courbes d'automation.

`samples_catalog.json` (racine) est supprimé (chantier 1, spec v2.0 §1.3) : `samples.db` est la
source unique de vérité (SSOT). `migrate_catalog_json()` reste disponible en best-effort pour
importer un ancien catalogue local de projet.

Usage CLI :
  .venv/bin/python mcp-servers/audio-index/unified_indexer.py index <dir|wav...> [--db samples.db]
  .venv/bin/python mcp-servers/audio-index/unified_indexer.py migrate-catalog samples_catalog.json
  .venv/bin/python mcp-servers/audio-index/unified_indexer.py query --role kick [--db samples.db]
  .venv/bin/python mcp-servers/audio-index/unified_indexer.py search --query "dark analog pad" [--role pad]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = os.environ.get("AI_STUDIO_DIR") or str(Path(__file__).resolve().parents[2])
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
for _p in (SCRIPTS_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_DB_PATH = os.environ.get("AI_STUDIO_UNIFIED_DB_PATH") or os.path.join(ROOT, "samples.db")
DEFAULT_CATALOG_PATH = os.path.join(ROOT, "samples_catalog.json")
ANALYSIS_SR = 22050
PITCH_CONFIDENCE_THRESHOLD = 0.75
UNIFIED_SCHEMA_VERSION = 2
EMBEDDING_DIM = 512          # contrat CLAP : vecteur float32 L2-normalisé de 512 dims (2048 octets)
EMBEDDING_SEED = 1337        # projection déterministe hors-ligne (reproductible bit-à-bit)
SEMANTIC_FEATURE_DIM = 32    # descripteurs psychoacoustiques projetés dans 512-d

try:
    import essentia.standard as _es  # type: ignore

    _HAS_ESSENTIA = True
except Exception:  # pragma: no cover
    _es = None
    _HAS_ESSENTIA = False

try:
    import librosa as _librosa  # type: ignore

    _HAS_LIBROSA = True
except Exception:  # pragma: no cover
    _librosa = None
    _HAS_LIBROSA = False

try:
    from audio_prep_pb import prepare_audio, load_audio  # type: ignore
except Exception:  # pragma: no cover - exécution hors dépôt
    prepare_audio = None
    load_audio = None

SOURCE_TYPES = ("wav_sample", "vst3_preset", "sfz_instrument")
ROLE_CATEGORIES = ("kick", "snare", "hihat", "perc", "bass_808", "pad", "keys", "lead", "fx")

# ---------------------------------------------------------------------------
# P0-3 (audit rap_ombre_134 §P0-3) — cloisonnement taxonomique strict par rôle :
# un son tonal (stab/chord/pluck/pad/synth/lead...) ou issu d'un dossier générique
# `other/` ne peut JAMAIS remplir un rôle percussif (kick/snare/hihat/perc).
# ---------------------------------------------------------------------------
PERC_ROLE_CATEGORIES = ("kick", "snare", "hihat", "perc")
TONAL_NAME_TOKENS = ("stab", "chord", "pluck", "pad", "synth", "lead", "arp", "bell",
                     "melody", "piano", "rhodes", "organ", "keys", "vocal", "vox", "string", "brass")
FORBIDDEN_PERC_PATH_SEGMENTS = ("other", "melodic", "tonal", "chords", "pads", "leads",
                                "keys", "vocals", "melody", "oneshot_melodic")


def role_allows_asset(role_category: str, file_path: str = "", is_pitched: Any = None,
                      pitch_confidence: Any = None, ac_roughness: Any = None,
                      air_band_energy: Any = None) -> tuple[bool, str]:
    """Règles dures de compatibilité rôle↔asset (audit P0-3).

    Rôles percussifs : rejet des noms à tokens tonaux, des dossiers interdits (`other/`,
    `chords/`...), et — pour la caisse claire — garde-fou acoustique : pas de hauteur tonale
    dominante (son pitché haute confiance + faible rugosité + peu d'air = stab métallique).
    """
    role = str(role_category or "").lower()
    if role not in PERC_ROLE_CATEGORIES:
        return True, "ok"
    path = str(file_path or "")
    low = path.lower()
    base = os.path.basename(low)
    parts = {p for p in low.replace("\\", "/").split("/") if p}
    for tok in TONAL_NAME_TOKENS:
        if tok in base:
            return False, f"tonal_name_token({tok})"
    bad_dir = parts & set(FORBIDDEN_PERC_PATH_SEGMENTS)
    if bad_dir:
        return False, f"forbidden_dir({sorted(bad_dir)[0]})"
    pitched = bool(is_pitched) if is_pitched is not None else None
    conf = float(pitch_confidence) if pitch_confidence is not None else 0.0
    if role == "snare" and pitched and conf >= 0.9:
        rough = float(ac_roughness) if ac_roughness is not None else 50.0
        air = float(air_band_energy) if air_band_energy is not None else 0.5
        if rough < 25.0 and air < 0.10:
            return False, "snare_tonal_stab_guard(pitched, low roughness, no broadband air)"
    if role in ("hihat", "perc") and pitched and conf >= 0.95:
        return False, "pitched_tonal_not_percussive"
    return True, "ok"


def filter_role_compatible(rows: list[dict[str, Any]], role_category: str) -> list[dict[str, Any]]:
    """Filtre une liste de lignes `sound_assets` avec `role_allows_asset`."""
    out = []
    for r in rows:
        ok, _reason = role_allows_asset(
            role_category,
            str(r.get("file_path") or r.get("path") or ""),
            r.get("is_pitched"), r.get("pitch_confidence"),
            r.get("ac_roughness"), r.get("air_band_energy"),
        )
        if ok:
            out.append(r)
    return out

# S_max (plafond de transposition) par nature de source — invariant v4.2 du dépôt.
SAFE_SEMITONE_RANGE = {"wav_sample_acoustic": 5, "wav_sample_synth": 7, "sfz_instrument": 87, "vst3_preset": 87}

SOUND_ASSETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sound_assets (
    asset_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('wav_sample', 'vst3_preset', 'sfz_instrument')),
    file_path TEXT NOT NULL,
    preset_name TEXT,
    role_category TEXT NOT NULL,
    is_pitched INTEGER NOT NULL,
    detected_midi_note INTEGER,
    pitch_confidence REAL,
    min_safe_midi INTEGER,
    max_safe_midi INTEGER,
    duration_ms REAL NOT NULL,
    log_attack_ms REAL NOT NULL,
    lufs_integrated REAL NOT NULL,
    crest_factor_db REAL NOT NULL,
    spectral_centroid_hz REAL NOT NULL,
    low_band_energy REAL NOT NULL,
    low_mid_energy REAL NOT NULL,
    high_mid_energy REAL NOT NULL,
    air_band_energy REAL NOT NULL,
    ac_warmth REAL NOT NULL,
    ac_brightness REAL NOT NULL,
    ac_roughness REAL NOT NULL,
    ac_hardness REAL NOT NULL,
    ac_depth REAL NOT NULL,
    ac_boomy REAL NOT NULL,
    ac_sharpness REAL NOT NULL,
    embedding_blob BLOB,
    clap_embedding BLOB,
    modulation_profile_json TEXT,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SOUND_ASSETS_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_assets_role_timbre ON sound_assets(role_category, ac_warmth, ac_brightness, ac_roughness);
CREATE INDEX IF NOT EXISTS idx_assets_centroid ON sound_assets(role_category, spectral_centroid_hz);
"""

_ROLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("kick", ("kick", "bd", "bassdrum", "bass drum")),
    ("snare", ("snare", "sd", "rimshot", "clap", "cp")),
    ("hihat", ("hihat", "hi-hat", "hi_hat", "hat", "hh", "oh", "open hat", "open_hat", "openhat")),
    ("bass_808", ("808", "sub", "reese", "bass")),
    ("pad", ("pad", "ambient", "atmosphere", "drone", "texture")),
    ("keys", ("piano", "keys", "rhodes", "epiano", "organ", "chord", "pluck")),
    ("lead", ("lead", "melody", "bell", "mallet", "brass", "arp")),
    ("perc", ("perc", "shaker", "tamb", "conga", "bongo", "tom", "cowbell", "crash", "ride", "cymbal")),
    ("fx", ("fx", "riser", "sweep", "impact", "downlifter", "uplifter", "glitch", "noise", "transition")),
]


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Crée/ouvre `samples.db` avec le schéma unifié `sound_assets` (v2 : migration ALTER TABLE)."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SOUND_ASSETS_SCHEMA)
    # Migration v1 -> v2 : ajout des colonnes clap_embedding / modulation_profile_json.
    existing = {r[1] for r in conn.execute("PRAGMA table_info(sound_assets)").fetchall()}
    for col, decl in (("clap_embedding", "BLOB"), ("modulation_profile_json", "TEXT")):
        if col not in existing:
            conn.execute(f"ALTER TABLE sound_assets ADD COLUMN {col} {decl}")
    # Les index ne sont créés qu'après migration complète (les vieilles bases n'ont pas
    # forcément toutes les colonnes référencées).
    existing = {r[1] for r in conn.execute("PRAGMA table_info(sound_assets)").fetchall()}
    if {"role_category", "ac_warmth", "ac_brightness", "ac_roughness", "spectral_centroid_hz"} <= existing:
        conn.executescript(SOUND_ASSETS_INDEXES)
    conn.execute(f"PRAGMA user_version={UNIFIED_SCHEMA_VERSION}")
    conn.commit()
    return conn


def midi_to_hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def hz_to_midi(hz: float) -> int:
    return int(round(69.0 + 12.0 * math.log2(max(hz, 1e-6) / 440.0)))


def infer_role_category(path: str) -> str:
    """Déduit la `role_category` spec depuis le nom de fichier (règles compactes)."""
    name = os.path.basename(str(path)).lower()
    for role, keys in _ROLE_RULES:
        if any(k in name for k in keys):
            return role
    if re.search(r"(loop|stem|drumless|instrumental)", name):
        return "perc"
    return "fx"


def _load_mono(path: str, sr: int = ANALYSIS_SR) -> tuple[np.ndarray, int]:
    if load_audio is not None:
        return load_audio(path, sr=sr, mono=True)
    import soundfile as sf
    from scipy.signal import resample_poly
    from math import gcd

    audio, file_sr = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr and sr != file_sr:
        g = gcd(int(sr), int(file_sr))
        audio = resample_poly(audio, sr // g, file_sr // g).astype(np.float32)
        file_sr = sr
    return np.asarray(audio, dtype=np.float32), int(file_sr)


def estimate_f0(mono: np.ndarray, sr: int) -> tuple[float, float]:
    """f0 + confiance tonale. Essentia PitchYinFFT > librosa pyin > autocorrélation NumPy.

    Retourne (0.0, 0.0) si non tonal / confiance < seuil (échantillon percussif/atonal).
    """
    mono = np.asarray(mono, dtype=np.float32)
    if mono.size < int(0.05 * sr):
        return 0.0, 0.0
    if _HAS_ESSENTIA:
        try:
            f0, conf = _es.PitchYinFFT()(mono, frameSize=2048, sampleRate=sr)
            f0 = np.asarray(f0, dtype=np.float64)
            conf = np.asarray(conf, dtype=np.float64)
            voiced = f0[conf > 0.0]
            if voiced.size:
                f0_med = float(np.median(voiced))
                conf_med = float(np.median(conf[f0 > 0]))
                return f0_med, conf_med
        except Exception:
            pass
    if _HAS_LIBROSA:
        try:
            f0, voiced_flag, _ = _librosa.pyin(
                mono.astype(np.float64), fmin=50.0, fmax=1000.0, sr=sr, frame_length=2048
            )
            v = np.asarray(f0)[np.asarray(voiced_flag, dtype=bool)]
            v = v[np.isfinite(v) & (v > 0)]
            if v.size:
                return float(np.median(v)), float(min(1.0, 0.5 + 0.5 * float(np.mean(voiced_flag))))
        except Exception:
            pass
    # Repli déterministe : autocorrélation sur fenêtres de 40 ms, médiane des f0 voiced.
    frame = int(0.04 * sr)
    hop = frame // 2
    f0s: list[float] = []
    confs: list[float] = []
    for start in range(0, max(1, mono.size - frame), hop):
        seg = mono[start : start + frame].astype(np.float64)
        if float(np.sqrt(np.mean(seg**2))) < 1e-4:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[frame - 1 :]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lo = max(1, int(sr / 1000.0))
        hi = min(frame - 1, int(sr / 50.0))
        if hi <= lo:
            continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        conf = float(ac[lag])
        if conf > 0.3:
            f0s.append(sr / lag)
            confs.append(conf)
    if not f0s:
        return 0.0, 0.0
    return float(np.median(f0s)), float(np.clip(np.median(confs), 0.0, 1.0))


def spectral_features(mono: np.ndarray, sr: int) -> dict[str, float]:
    """Centroïde spectral, rolloff 85 %, crest, attaque log, énergies de 4 bandes (parts)."""
    from scipy.signal import stft, hilbert

    mono = np.asarray(mono, dtype=np.float64)
    n = mono.size
    if n < 256:
        return {
            "spectral_centroid_hz": 0.0,
            "rolloff85_hz": 0.0,
            "crest_factor_db": 0.0,
            "log_attack_ms": 0.0,
            "low_band_energy": 0.0,
            "low_mid_energy": 0.0,
            "high_mid_energy": 0.0,
            "air_band_energy": 0.0,
            "spectral_flatness": 1.0,
        }
    nperseg = min(2048, n)
    noverlap = nperseg // 2
    freqs, _, Z = stft(mono, fs=sr, nperseg=nperseg, noverlap=noverlap, window="hann", padded=False)
    power = np.abs(Z) ** 2
    mean_power = power.mean(axis=1)
    total = float(mean_power.sum()) + 1e-20
    centroid = float((freqs * mean_power).sum() / total)
    cum = np.cumsum(mean_power) / total
    rolloff = float(freqs[int(np.searchsorted(cum, 0.85))]) if cum.size else 0.0
    peak = float(np.max(np.abs(mono))) + 1e-12
    rms = float(np.sqrt(np.mean(mono**2))) + 1e-12
    crest_db = float(20.0 * np.log10(peak / rms))
    env = np.abs(hilbert(mono))
    win = max(1, int(0.005 * sr))
    env_s = np.convolve(env, np.ones(win) / win, mode="same")
    peak_env = float(np.max(env_s)) + 1e-12
    steady = float(np.median(env_s[int(0.5 * n) :])) + 1e-12
    thresh = 0.9 * min(steady, peak_env)
    idx = int(np.argmax(env_s >= thresh)) if np.any(env_s >= thresh) else n - 1
    t_attack_ms = max(0.1, idx / sr * 1000.0)
    bands = ((20.0, 150.0), (150.0, 500.0), (500.0, 4000.0), (4000.0, 20000.0))
    energies = []
    for lo, hi in bands:
        m = (freqs >= lo) & (freqs < hi)
        energies.append(float(mean_power[m].sum()) if np.any(m) else 0.0)
    band_sum = sum(energies) + 1e-20
    energies = [e / band_sum for e in energies]  # parts normalisées des 4 bandes (somme = 1)
    mag = np.sqrt(mean_power) + 1e-12
    flatness = float(np.exp(np.mean(np.log(mag))) / np.mean(mag))
    return {
        "spectral_centroid_hz": centroid,
        "rolloff85_hz": rolloff,
        "crest_factor_db": crest_db,
        "log_attack_ms": float(math.log10(t_attack_ms)),
        "low_band_energy": energies[0],
        "low_mid_energy": energies[1],
        "high_mid_energy": energies[2],
        "air_band_energy": energies[3],
        "spectral_flatness": float(np.clip(flatness, 0.0, 1.0)),
    }


def audiocommons_scores(feat: dict[str, float], crest_db: float, log_attack_ms: float) -> dict[str, float]:
    """7 dimensions perceptuelles normalisées [0, 100], approximation déterministe des
    modèles calibrés `AudioCommons/timbral_models` (warmth, brightness, roughness, hardness,
    depth, boomy, sharpness) à partir des descripteurs spectraux locaux."""
    c = float(feat["spectral_centroid_hz"])
    lo = float(feat["low_band_energy"])
    lm = float(feat["low_mid_energy"])
    air = float(feat["air_band_energy"])
    flat = float(feat["spectral_flatness"])

    def clip100(x: float) -> float:
        return float(min(100.0, max(0.0, x)))

    warmth = clip100(70.0 + 140.0 * lm - 0.010 * c)
    brightness = clip100(0.0125 * c + 220.0 * air)
    roughness = clip100(55.0 * flat + 0.55 * max(0.0, crest_db - 8.0))
    hardness = clip100(3.0 * max(0.0, crest_db - 6.0) + 22.0 * max(0.0, 2.0 - log_attack_ms))
    depth = clip100(50.0 + 200.0 * lo - 0.004 * c)
    boomy = clip100(220.0 * lo)
    sharpness = clip100(380.0 * air + 0.008 * c - 18.0)
    return {
        "ac_warmth": warmth,
        "ac_brightness": brightness,
        "ac_roughness": roughness,
        "ac_hardness": hardness,
        "ac_depth": depth,
        "ac_boomy": boomy,
        "ac_sharpness": sharpness,
    }


def safe_midi_range(source_type: str, detected_midi: int | None, is_pitched: bool, name: str = "") -> tuple[int, int]:
    """Tessiture sûre sans artefact : ±5 st (acoustique) / ±7 st (synth/808) pour les WAV ;
    pleine plage MIDI pour les instruments virtuels (rendu natif à la hauteur exacte)."""
    if source_type in ("vst3_preset", "sfz_instrument") or not is_pitched or detected_midi is None:
        if source_type in ("vst3_preset", "sfz_instrument"):
            return 21, 108
        return 0, 127
    synth_like = any(k in name.lower() for k in ("synth", "808", "sub", "digital", "electronic"))
    span = SAFE_SEMITONE_RANGE["wav_sample_synth" if synth_like else "wav_sample_acoustic"]
    return max(0, detected_midi - span), min(127, detected_midi + span)


def analyze_audio(mono: np.ndarray, sr: int, source_type: str = "wav_sample", name: str = "") -> dict[str, Any]:
    """Analyse complète d'un signal mono : prep LUFS + descripteurs + scores perceptuels."""
    if prepare_audio is not None:
        prep = prepare_audio(mono, sr)
        mono_prep = prep["audio"]
        lufs = prep["lufs_after"]
    else:  # pragma: no cover
        mono_prep = np.asarray(mono, dtype=np.float32)
        from audio_prep_pb import measure_lufs  # type: ignore

        lufs = measure_lufs(mono_prep, sr)
    feat = spectral_features(mono_prep, sr)
    f0, conf = estimate_f0(mono_prep, sr)
    # Confiance tonale pénalisée par la planéité spectrale : un bruit large bande (flatness
    # élevée) n'est pas tonal même si un estimateur trouve un pic d'autocorrélation.
    conf = float(conf) * (1.0 - float(feat["spectral_flatness"]))
    is_pitched = 1 if (f0 > 0.0 and conf >= PITCH_CONFIDENCE_THRESHOLD) else 0
    detected_midi = hz_to_midi(f0) if is_pitched else None
    scores = audiocommons_scores(feat, feat["crest_factor_db"], feat["log_attack_ms"])
    min_safe, max_safe = safe_midi_range(source_type, detected_midi, bool(is_pitched), name)
    return {
        "is_pitched": is_pitched,
        "detected_midi_note": detected_midi,
        "pitch_confidence": round(float(conf), 4),
        "detected_f0_hz": round(float(f0), 2),
        "min_safe_midi": min_safe,
        "max_safe_midi": max_safe,
        "duration_ms": round(mono_prep.size / sr * 1000.0, 3),
        "log_attack_ms": round(feat["log_attack_ms"], 4),
        "lufs_integrated": round(float(lufs), 3),
        "crest_factor_db": round(feat["crest_factor_db"], 3),
        "spectral_centroid_hz": round(feat["spectral_centroid_hz"], 2),
        "rolloff85_hz": round(feat["rolloff85_hz"], 2),
        "low_band_energy": round(feat["low_band_energy"], 6),
        "low_mid_energy": round(feat["low_mid_energy"], 6),
        "high_mid_energy": round(feat["high_mid_energy"], 6),
        "air_band_energy": round(feat["air_band_energy"], 6),
        **{k: round(v, 2) for k, v in scores.items()},
    }


# ---------------------------------------------------------------------------
# Embeddings sémantiques 512-d (contrat CLAP, spec v2.0 §1.1) + profils de
# modulation (spec v2.0 §1.1) + recherche par similarité cosinus.
# ---------------------------------------------------------------------------

_PROJECTION: np.ndarray | None = None


def _projection_matrix() -> np.ndarray:
    """Matrice de projection fixe (EMBEDDING_DIM x SEMANTIC_FEATURE_DIM), seed EMBEDDING_SEED."""
    global _PROJECTION
    if _PROJECTION is None:
        rng = np.random.default_rng(EMBEDDING_SEED)
        _PROJECTION = rng.standard_normal((EMBEDDING_DIM, SEMANTIC_FEATURE_DIM)).astype(np.float64)
        _PROJECTION /= math.sqrt(SEMANTIC_FEATURE_DIM)
    return _PROJECTION


def semantic_feature_vector(analysis: dict[str, Any]) -> np.ndarray:
    """Vecteur normalisé de descripteurs psychoacoustiques (SEMANTIC_FEATURE_DIM dims).

    Layout : [4 bandes, centroid, crest, lenteur d'attaque, lufs, 7 scores AC,
              pitch_confidence, is_pitched, 12 classes de hauteur one-hot].
    """
    def g(k: str, d: float = 0.0) -> float:
        v = analysis.get(k, d)
        return float(v) if v is not None else float(d)

    is_pitched = bool(g("is_pitched", 0))
    pc_onehot = np.zeros(12)
    if is_pitched:
        pc_onehot[int(g("detected_midi_note", 60) or 60) % 12] = 1.0
    v = np.array([
        g("low_band_energy"), g("low_mid_energy"), g("high_mid_energy"), g("air_band_energy"),
        min(1.0, max(0.0, g("spectral_centroid_hz") / 8000.0)),
        min(1.0, max(0.0, g("crest_factor_db") / 20.0)),
        min(1.0, max(0.0, (g("log_attack_ms") + 2.0) / 4.0)),
        min(1.0, max(0.0, (g("lufs_integrated") + 40.0) / 40.0)),
        g("ac_warmth") / 100.0, g("ac_brightness") / 100.0, g("ac_roughness") / 100.0,
        g("ac_hardness") / 100.0, g("ac_depth") / 100.0, g("ac_boomy") / 100.0,
        g("ac_sharpness") / 100.0,
        min(1.0, max(0.0, g("pitch_confidence", 0.0))), float(is_pitched),
        *pc_onehot,
    ], dtype=np.float64)
    if v.size < SEMANTIC_FEATURE_DIM:
        v = np.concatenate([v, np.zeros(SEMANTIC_FEATURE_DIM - v.size)])
    return v[:SEMANTIC_FEATURE_DIM]


def _anchor(centroid=0.4, crest=0.5, slow=0.5, low=0.2, lowmid=0.25, highmid=0.35, air=0.2,
            warmth=0.5, bright=0.5, rough=0.4, hard=0.4, depth=0.5, boomy=0.3, sharp=0.3,
            pitched=0.0) -> np.ndarray:
    v = np.zeros(SEMANTIC_FEATURE_DIM)
    v[0], v[1], v[2], v[3] = low, lowmid, highmid, air
    v[4], v[5], v[6] = centroid, crest, slow
    v[8], v[9], v[10], v[11], v[12], v[13], v[14] = warmth, bright, rough, hard, depth, boomy, sharp
    v[16] = pitched
    return v


# Ancres sémantiques FR/EN -> vecteur de descripteurs (espace latent audio-texte déterministe).
_TEXT_ANCHORS: list[tuple[tuple[str, ...], np.ndarray]] = [
    (("dark", "sombre", "darkness", "minor", "deep", "nocturne", "melancholic", "triste"), _anchor(centroid=0.15, low=0.65, lowmid=0.25, warmth=0.8, bright=0.2, depth=0.8, boomy=0.65, rough=0.45)),
    (("bright", "brillant", "air", "crisp", "sharp", "aigu", "clair", "airy"), _anchor(centroid=0.8, air=0.6, highmid=0.5, bright=0.9, sharp=0.7, rough=0.3)),
    (("warm", "chaud", "analog", "lofi", "vintage", "tape", "mellow"), _anchor(lowmid=0.6, warmth=0.85, bright=0.35, rough=0.35, depth=0.6)),
    (("sub", "808", "heavy", "lourd", "bass", "grave", "low", "deep bass"), _anchor(centroid=0.08, low=0.9, boomy=0.9, depth=0.8, bright=0.1, pitched=1.0)),
    (("punchy", "hard", "sec", "dry", "tight", "knock", "kick", "punch"), _anchor(crest=0.9, hard=0.9, slow=0.05, centroid=0.1, low=0.7)),
    (("soft", "doux", "douce", "gentle", "velvet"), _anchor(crest=0.3, hard=0.2, slow=0.4, depth=0.7, warmth=0.7)),
    (("pad", "nappe", "ambient", "drone", "atmosphere", "texture", "sustained", "lush"), _anchor(slow=0.9, crest=0.25, centroid=0.3, depth=0.85, warmth=0.7, bright=0.35)),
    (("snare", "caisse", "clap", "rimshot"), _anchor(centroid=0.45, highmid=0.6, bright=0.7, crest=0.7, hard=0.6)),
    (("hat", "hihat", "cymbal", "rapide", "shaker", "ride"), _anchor(centroid=0.85, air=0.8, bright=0.8, rough=0.3, slow=0.05)),
    (("lead", "melody", "mélodie", "brass", "bell", "arp", "pluck", "guitar", "guitare"), _anchor(centroid=0.6, bright=0.75, crest=0.6, slow=0.3, pitched=1.0)),
    (("keys", "piano", "rhodes", "organ", "chord", "epiano", "accord"), _anchor(centroid=0.35, warmth=0.6, pitched=1.0, depth=0.5)),
    (("vocal", "voix", "chant", "voice", "vox", "choir", "chorale"), _anchor(centroid=0.4, lowmid=0.5, warmth=0.6, pitched=1.0)),
    (("fx", "riser", "sweep", "impact", "uplifter", "downlifter", "noise", "glitch", "transition"), _anchor(air=0.5, rough=0.6, crest=0.7, centroid=0.6)),
    (("perc", "conga", "bongo", "tom", "cowbell", "tamb"), _anchor(centroid=0.5, crest=0.65, hard=0.55, slow=0.1)),
    (("acoustic", "acoustique", "jazz", "organic", "naturel"), _anchor(rough=0.25, warmth=0.65, bright=0.45, slow=0.5)),
    (("synth", "synthé", "electronic", "digital", "fm", "analog synth"), _anchor(bright=0.6, rough=0.45, centroid=0.5, hard=0.4)),
    (("loop", "boucle", "sample", "one-shot", "oneshot"), _anchor()),
    (("slow", "lent", "calme", "chill", "ambient"), _anchor(slow=0.85, depth=0.7, crest=0.3)),
    (("fast", "rapide", "energetic", "énergique", "club", "edm"), _anchor(slow=0.2, crest=0.8, hard=0.7, bright=0.6)),
    (("movement", "mouvement", "breathing", "respiration", "évolution", "evolving"), _anchor(depth=0.7, slow=0.7, warmth=0.6)),
]


def text_semantic_feature_vector(query: str) -> np.ndarray:
    """Requête texte libre -> vecteur de descripteurs (ancres FR/EN + jitter déterministe OOV)."""
    q = (query or "").lower()
    toks = re.findall(r"[a-zàâäéèêëîïôöùûüç]+", q)
    acc = np.zeros(SEMANTIC_FEATURE_DIM)
    matched = 0
    for tok in toks:
        for keys, anchor in _TEXT_ANCHORS:
            if tok in keys:
                acc += anchor
                matched += 1
                break
        else:
            # token inconnu : composante déterministe faible (hash) pour ne pas pénaliser
            h = int(hashlib.sha1(tok.encode("utf-8")).hexdigest()[:8], 16)
            rng = np.random.default_rng(h)
            acc += 0.02 * rng.standard_normal(SEMANTIC_FEATURE_DIM)
    if matched == 0:
        acc = _anchor()
    n = float(np.linalg.norm(acc))
    if n > 0:
        acc = acc / n
    return acc


def embed_features(feat: np.ndarray) -> bytes:
    """Projette le vecteur de descripteurs en 512-d, L2-normalise, sérialise en float32 (2048 o)."""
    v = _projection_matrix() @ np.asarray(feat, dtype=np.float64)
    n = float(np.linalg.norm(v))
    if n > 0:
        v = v / n
    return np.asarray(v, dtype=np.float32).tobytes()


def clap_available() -> bool:
    """True si le modèle LAION CLAP est réellement disponible en local (jamais de téléchargement)."""
    if os.environ.get("AI_STUDIO_CLAP", "0") not in ("1", "true", "yes"):
        return False
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        model_path = os.environ.get("AI_STUDIO_CLAP_MODEL", "")
        return bool(model_path and os.path.isdir(model_path))
    except Exception:
        return False


def compute_clap_embedding(mono: np.ndarray, sr: int, analysis: dict[str, Any]) -> tuple[bytes, str]:
    """Embedding 512-d de l'audio : CLAP réel si disponible, sinon projection déterministe."""
    if clap_available():
        try:
            import torch
            from transformers import ClapModel, ClapProcessor
            model = ClapModel.from_pretrained(os.environ["AI_STUDIO_CLAP_MODEL"])
            proc = ClapProcessor.from_pretrained(os.environ["AI_STUDIO_CLAP_MODEL"])
            inputs = proc(audios=[np.asarray(mono, dtype=np.float32)], sampling_rate=sr, return_tensors="pt")
            with torch.no_grad():
                emb = model.get_audio_features(**inputs)
            v = emb[0].detach().cpu().numpy().astype(np.float32)
            v = v / max(float(np.linalg.norm(v)), 1e-12)
            return v.tobytes(), "clap-htsat-unfused"
        except Exception:
            pass
    return embed_features(semantic_feature_vector(analysis)), "deterministic-projection"


def text_embedding_blob(query: str) -> bytes:
    return embed_features(text_semantic_feature_vector(query))


def decode_embedding(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    v = np.frombuffer(blob, dtype=np.float32)
    return v if v.size == EMBEDDING_DIM else None


def embedding_cosine(a: bytes | None, b: bytes | None) -> float:
    va, vb = decode_embedding(a), decode_embedding(b)
    if va is None or vb is None:
        return 0.0
    return float(np.dot(va, vb) / max(float(np.linalg.norm(va)) * float(np.linalg.norm(vb)), 1e-12))


def semantic_search(
    conn: sqlite3.Connection,
    query: str,
    role_category: str | None = None,
    source_type: str | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Recherche sémantique par requête texte libre sur `clap_embedding` (similarité cosinus).

    Retourne les `top_k` assets classés par score sémantique décroissant (champs + `semantic_score`).
    """
    q_blob = text_embedding_blob(query)
    rows = query_assets(conn, role_category=role_category, source_type=source_type, limit=2000)
    scored = []
    for r in rows:
        blob = r.get("clap_embedding") or r.get("embedding_blob")
        s = embedding_cosine(blob, q_blob)
        r = dict(r)
        r["semantic_score"] = round(s, 4)
        scored.append(r)
    scored.sort(key=lambda r: r["semantic_score"], reverse=True)
    return scored[: max(1, int(top_k))]


_MODULATION_OP_RE = re.compile(
    r"(cutoff|resonance|decay|release|attack|sustain|lfo\w*|filter\w*|ampeg_\w*|pitcheg_\w*|volume|pan|chorus|detune|glide)", re.I)


def extract_modulation_profile(path: str, source_type: str) -> dict[str, Any] | None:
    """Paramètres automatisables exposés par un preset VST3 / banque SFZ (spec v2.0 §1.1).

    .sfz : opcodes correspondants (cutoff, resonance, ampeg_decay, lfo01_rate, ...).
    .vstpreset (XML) : attributs `name`/`value` des <PARAM> correspondants.
    """
    if source_type not in ("vst3_preset", "sfz_instrument") or not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except Exception:
        return None
    profile: dict[str, Any] = {}
    if path.lower().endswith(".sfz"):
        for m in re.finditer(r"(\w+)=([^\s<]+)", text):
            op, val = m.group(1), m.group(2)
            if _MODULATION_OP_RE.search(op):
                profile.setdefault(f"sfz:{op}", {"name": op, "default": val})
    else:
        for m in re.finditer(r'<PARAM[^>]*?name="([^"]+)"[^>]*?value="([^"]+)"', text):
            name, val = m.group(1), m.group(2)
            if _MODULATION_OP_RE.search(name):
                profile.setdefault(f"vst3:{name}", {"name": name, "default": val})
    return profile or None


def asset_id_for(path: str) -> str:
    return hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]


_ASSET_COLUMNS = (
    "asset_id", "source_type", "file_path", "preset_name", "role_category", "is_pitched",
    "detected_midi_note", "pitch_confidence", "min_safe_midi", "max_safe_midi", "duration_ms",
    "log_attack_ms", "lufs_integrated", "crest_factor_db", "spectral_centroid_hz",
    "low_band_energy", "low_mid_energy", "high_mid_energy", "air_band_energy",
    "ac_warmth", "ac_brightness", "ac_roughness", "ac_hardness", "ac_depth", "ac_boomy",
    "ac_sharpness", "embedding_blob", "clap_embedding", "modulation_profile_json",
)


def _insert_asset_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Insert/replace générique restreint aux colonnes du schéma `sound_assets`."""
    clean = {k: row.get(k) for k in _ASSET_COLUMNS}
    cols = ", ".join(clean.keys())
    placeholders = ", ".join(":" + k for k in clean.keys())
    conn.execute(f"INSERT OR REPLACE INTO sound_assets ({cols}) VALUES ({placeholders})", clean)
    conn.commit()


def index_file(
    conn: sqlite3.Connection,
    path: str,
    source_type: str = "wav_sample",
    role_category: str | None = None,
    preset_name: str | None = None,
    embedding: bytes | None = None,
    compute_embedding: bool = True,
) -> dict[str, Any]:
    """Ingère un asset audio dans `sound_assets` (pipeline complet déterministe + embedding 512-d)."""
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type inconnu: {source_type}")
    mono, sr = _load_mono(path)
    role = role_category or infer_role_category(path)
    if role not in ROLE_CATEGORIES:
        role = "fx"
    analysis = analyze_audio(mono, sr, source_type=source_type, name=os.path.basename(path))
    emb_backend = "none"
    if embedding is None and compute_embedding:
        embedding, emb_backend = compute_clap_embedding(mono, sr, analysis)
    mod_profile = extract_modulation_profile(path, source_type)
    row = {
        "asset_id": asset_id_for(path),
        "source_type": source_type,
        "file_path": os.path.abspath(path),
        "preset_name": preset_name,
        "role_category": role,
        **analysis,
        "embedding_blob": embedding,
        "clap_embedding": embedding,
        "modulation_profile_json": json.dumps(mod_profile, ensure_ascii=False) if mod_profile else None,
    }
    _insert_asset_row(conn, row)
    return {"asset_id": row["asset_id"], "role_category": role, "embedding_backend": emb_backend, **analysis}


def index_audio_array(
    conn: sqlite3.Connection,
    mono: np.ndarray,
    sr: int,
    *,
    asset_id: str,
    file_path: str,
    source_type: str = "wav_sample",
    role_category: str = "fx",
    preset_name: str | None = None,
    embedding: bytes | None = None,
    compute_embedding: bool = True,
) -> dict[str, Any]:
    """Ingère un signal déjà en mémoire (ex. note de référence rendue pour un preset VST3/SFZ)."""
    analysis = analyze_audio(mono, sr, source_type=source_type, name=os.path.basename(file_path))
    emb_backend = "none"
    if embedding is None and compute_embedding:
        embedding, emb_backend = compute_clap_embedding(mono, sr, analysis)
    mod_profile = extract_modulation_profile(file_path, source_type)
    row = {
        "asset_id": asset_id,
        "source_type": source_type,
        "file_path": file_path,
        "preset_name": preset_name,
        "role_category": role_category,
        **analysis,
        "embedding_blob": embedding,
        "clap_embedding": embedding,
        "modulation_profile_json": json.dumps(mod_profile, ensure_ascii=False) if mod_profile else None,
    }
    _insert_asset_row(conn, row)
    return {"asset_id": asset_id, "role_category": role_category, "embedding_backend": emb_backend, **analysis}


_CATALOG_ROLE_MAP = {
    "808": "bass_808", "kick": "kick", "snare": "snare", "clap": "snare",
    "open_hat": "hihat", "hat": "hihat", "perc": "perc", "vocal": "fx",
    "bass": "bass_808", "guitar": "keys", "piano": "keys", "synth": "lead",
    "loop": "perc", "fx": "fx",
}


def _insert_asset_row_ignore(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    clean = {k: row.get(k) for k in _ASSET_COLUMNS}
    cols = ", ".join(clean.keys())
    placeholders = ", ".join(":" + k for k in clean.keys())
    conn.execute(f"INSERT OR IGNORE INTO sound_assets ({cols}) VALUES ({placeholders})", clean)
    conn.commit()


def migrate_catalog_json(conn: sqlite3.Connection, catalog_path: str = DEFAULT_CATALOG_PATH) -> dict[str, int]:
    """Importe `samples_catalog.json` (déprécié) dans `sound_assets`, best-effort."""
    with open(catalog_path, encoding="utf-8") as fh:
        catalog = json.load(fh)
    samples = catalog.get("samples", []) if isinstance(catalog, dict) else catalog
    imported = skipped = 0
    for s in samples:
        path = s.get("path") or s.get("file_path") or ""
        if not path or not os.path.isfile(path):
            skipped += 1
            continue
        cat = str(s.get("category") or s.get("role") or "").lower()
        role = _CATALOG_ROLE_MAP.get(cat, infer_role_category(path))
        midi_root = s.get("midi_root") or s.get("detected_midi_note")
        try:
            _insert_asset_row_ignore(conn, {
                "asset_id": s.get("asset_id") or asset_id_for(path),
                "source_type": "wav_sample",
                "file_path": os.path.abspath(path),
                "preset_name": None,
                "role_category": role,
                "is_pitched": 1 if midi_root is not None else 0,
                "detected_midi_note": int(midi_root) if midi_root is not None else None,
                "pitch_confidence": s.get("pitch_confidence"),
                "min_safe_midi": None,
                "max_safe_midi": None,
                "duration_ms": float(s.get("duration_ms") or 0.0),
                "log_attack_ms": float(s.get("log_attack_ms") or 0.0),
                "lufs_integrated": float(s.get("lufs") or s.get("lufs_integrated") or -18.0),
                "crest_factor_db": float(s.get("crest_factor_db") or 10.0),
                "spectral_centroid_hz": float(s.get("spectral_centroid_hz") or 1000.0),
                "low_band_energy": float(s.get("low_band_energy") or 0.25),
                "low_mid_energy": float(s.get("low_mid_energy") or 0.2),
                "high_mid_energy": float(s.get("high_mid_energy") or 0.4),
                "air_band_energy": float(s.get("air_band_energy") or 0.05),
                "ac_warmth": float(s.get("ac_warmth") or 50.0),
                "ac_brightness": float(s.get("ac_brightness") or 50.0),
                "ac_roughness": float(s.get("ac_roughness") or 20.0),
                "ac_hardness": float(s.get("ac_hardness") or 30.0),
                "ac_depth": float(s.get("ac_depth") or 40.0),
                "ac_boomy": float(s.get("ac_boomy") or 30.0),
                "ac_sharpness": float(s.get("ac_sharpness") or 30.0),
                "embedding_blob": None,
            })
            imported += 1
        except Exception:
            skipped += 1
    conn.commit()
    return {"imported": imported, "skipped": skipped}


def query_assets(
    conn: sqlite3.Connection,
    role_category: str | None = None,
    source_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = "SELECT * FROM sound_assets WHERE 1=1"
    params: list[Any] = []
    if role_category:
        q += " AND role_category=?"
        params.append(role_category)
    if source_type:
        q += " AND source_type=?"
        params.append(source_type)
    q += " ORDER BY indexed_at DESC LIMIT ?"
    params.append(int(limit))
    return [dict(r) for r in conn.execute(q, params).fetchall()]


def index_paths(conn: sqlite3.Connection, paths: list[str]) -> dict[str, int]:
    """Indexe une liste de fichiers/répertoires (récursif, extensions audio)."""
    exts = {".wav", ".ogg", ".flac", ".mp3", ".aiff", ".aif"}
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, _dirs, fns in os.walk(p):
                for fn in sorted(fns):
                    if os.path.splitext(fn)[1].lower() in exts:
                        files.append(os.path.join(dirpath, fn))
        elif os.path.isfile(p):
            files.append(p)
    indexed = skipped = 0
    for f in sorted(files):
        try:
            index_file(conn, f)
            indexed += 1
        except Exception:
            skipped += 1
    return {"indexed": indexed, "skipped": skipped, "total_files": len(files)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Indexeur unifié psychoacoustique -> samples.db")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="chemin de samples.db (défaut: racine du dépôt)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_index = sub.add_parser("index", help="indexer des fichiers/répertoires")
    p_index.add_argument("paths", nargs="+")
    p_mig = sub.add_parser("migrate-catalog", help="importer samples_catalog.json (déprécié)")
    p_mig.add_argument("catalog", nargs="?", default=DEFAULT_CATALOG_PATH)
    p_q = sub.add_parser("query", help="requêter sound_assets")
    p_q.add_argument("--role", default=None)
    p_q.add_argument("--source-type", default=None)
    p_q.add_argument("--limit", type=int, default=20)
    p_s = sub.add_parser("search", help="recherche sémantique par requête texte libre (clap_embedding)")
    p_s.add_argument("--query", required=True)
    p_s.add_argument("--role", default=None)
    p_s.add_argument("--source-type", default=None)
    p_s.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    conn = init_db(args.db)
    if args.cmd == "index":
        stats = index_paths(conn, args.paths)
        print(json.dumps({"db": args.db, **stats}, ensure_ascii=False))
    elif args.cmd == "migrate-catalog":
        stats = migrate_catalog_json(conn, args.catalog)
        print(json.dumps({"db": args.db, "catalog": args.catalog, **stats}, ensure_ascii=False))
    elif args.cmd == "query":
        rows = query_assets(conn, args.role, args.source_type, args.limit)
        print(json.dumps({"db": args.db, "count": len(rows), "assets": rows}, ensure_ascii=False, default=str))
    elif args.cmd == "search":
        rows = semantic_search(conn, args.query, role_category=args.role, source_type=args.source_type, top_k=args.top_k)
        slim = [{k: v for k, v in r.items() if k in ("asset_id", "source_type", "file_path", "preset_name",
                                                     "role_category", "spectral_centroid_hz", "ac_warmth",
                                                     "ac_brightness", "ac_boomy", "semantic_score")}
                for r in rows]
        print(json.dumps({"db": args.db, "query": args.query, "count": len(slim), "results": slim},
                         ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
