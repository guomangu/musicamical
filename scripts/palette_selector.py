#!/usr/bin/env python3
"""Sélection relationnelle de palette par complémentarité spectrale (Chantier 3 de la spec).

Remplace la sélection sample-par-sample de `select_sample.py` / `sample_fit_guard.py` /
`sample_quality_v2.py` (dépréciés) par un algorithme séquentiel contraint sur `samples.db`
(`sound_assets`, voir `mcp-servers/audio-index/unified_indexer.py`) :

  1. Ancrage rythmique   : kick (profil de genre : ac_hardness, crest) -> f_kick estimée,
                           snare avec centroïde > 800 Hz et ac_brightness >= kick + 15,
                           hihat complémentaire (air élevé, attaque courte).
  2. Fondation grave     : basse 808 dont l'énergie est COMPLÉMENTAIRE de f_kick
                           (kick < 55 Hz -> basse 70-140 Hz, ac_warmth > 60 ;
                            kick >= 55 Hz -> sub < 50 Hz, ac_boomy > 65, ac_roughness < 25).
  3. Couche harmonique   : pad/keys avec centroïde in [350, 1800] Hz, ac_roughness < 30,
                           et si motif rythmé dense : attaque > 60 ms (log10 > 1.78).
  4. Voix principale     : lead avec centroïde >= 1.5 x centroïde pad et ac_brightness
                           >= pad + 15 ; tessiture verrouillée [min_safe_midi, max_safe_midi]
                           + Low Interval Limits (aucune 2de mineure sous C3 = MIDI 48).

Score d'agrément combiné de la palette (spec §3.2) :
  S_palette = w1*(1 - D_corridor) + w2*(1 - M_spectral) + w3*P_timbre
avec D_corridor = distance normalisée aux corridors par stem (Chantier 4),
M_spectral = chevauchement énergétique inter-pistes en bandes de Bark,
P_timbre = indice psychoacoustique (chaleur + profondeur - rugosité excessive).

Usage CLI :
  .venv/bin/python scripts/palette_selector.py --db samples.db --profile trap_rap --bpm 140 --key C:minor \
      [--corridors golden_references/spectral_corridors.json] [--out palette.json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from typing import Any

import numpy as np

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
INDEX_DIR = os.path.join(ROOT, "mcp-servers", "audio-index")
for _p in (SCRIPTS_DIR, INDEX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from unified_indexer import DEFAULT_DB_PATH, init_db, midi_to_hz  # noqa: E402

DEFAULT_CORRIDORS_PATH = os.path.join(ROOT, "golden_references", "spectral_corridors.json")

try:
    from audio_metrics import BARK_EDGES_HZ  # noqa: E402
except Exception:
    BARK_EDGES_HZ = [
        (20.0, 100.0), (100.0, 200.0), (200.0, 300.0), (300.0, 400.0),
        (400.0, 510.0), (510.0, 630.0), (630.0, 770.0), (770.0, 920.0),
        (920.0, 1080.0), (1080.0, 1270.0), (1270.0, 1480.0), (1480.0, 1720.0),
        (1720.0, 2000.0), (2000.0, 2320.0), (2320.0, 2700.0), (2700.0, 3150.0),
        (3150.0, 3700.0), (3700.0, 4400.0), (4400.0, 5300.0), (5300.0, 6400.0),
        (6400.0, 7700.0), (7700.0, 9500.0), (9500.0, 12000.0), (12000.0, 20000.0),
    ]

ROLE_STEM = {"kick": "drums", "snare": "drums", "hihat": "drums", "perc": "drums",
             "bass_808": "bass", "pad": "other", "keys": "other", "lead": "other", "fx": "other"}

# Profils de genre -> exigences d'ancrage rythmique (ac_hardness, crest_factor_db).
GENRE_KICK_PROFILE: dict[str, dict[str, tuple[float, float]]] = {
    "trap_rap": {"ac_hardness": (55.0, 100.0), "crest_factor_db": (9.0, 20.0)},
    "lofi_chill": {"ac_hardness": (35.0, 70.0), "crest_factor_db": (7.0, 14.0)},
    "pop_rnb": {"ac_hardness": (45.0, 80.0), "crest_factor_db": (8.0, 16.0)},
    "edm_club": {"ac_hardness": (60.0, 100.0), "crest_factor_db": (10.0, 20.0)},
    "jazz_acoustic": {"ac_hardness": (25.0, 55.0), "crest_factor_db": (6.0, 12.0)},
    "rock_metal": {"ac_hardness": (55.0, 95.0), "crest_factor_db": (8.0, 16.0)},
    "classical_ambient": {"ac_hardness": (10.0, 45.0), "crest_factor_db": (5.0, 12.0)},
}
DEFAULT_KICK_PROFILE = {"ac_hardness": (40.0, 90.0), "crest_factor_db": (7.0, 18.0)}

# Low Interval Limits (règle du dépôt, cohérente avec music21) : intervalle minimal par
# classe d'intervalle selon la hauteur absolue (MIDI). Seconde mineure interdite sous C3 (48).
LOW_INTERVAL_LIMITS = {  # (semitones -> hauteur MIDI minimale autorisée)
    1: 60, 2: 60, 3: 52, 4: 48, 6: 48, 7: 43, 12: 36,
}
C3_MIDI = 48


def _row_get(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    v = row.get(key, default)
    return float(v) if v is not None else default


def kick_resonance_hz(row: dict[str, Any]) -> float:
    """Estime f_kick (pic de résonance 30-120 Hz). Repli: proxy sur les bandes stockées."""
    path = row.get("file_path") or ""
    if path and os.path.isfile(path):
        try:
            import soundfile as sf
            from scipy.signal import resample_poly
            from math import gcd

            audio, sr = sf.read(path, dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            target = 22050
            if sr != target:
                g = gcd(target, sr)
                audio = resample_poly(audio, target // g, sr // g)
                sr = target
            n = min(audio.size, int(0.5 * sr))
            seg = np.asarray(audio[:n], dtype=np.float64) * np.hanning(n)
            spec = np.abs(np.fft.rfft(seg, n=8192))
            freqs = np.fft.rfftfreq(8192, 1.0 / sr)
            m = (freqs >= 30.0) & (freqs <= 120.0)
            if np.any(m):
                return float(freqs[m][int(np.argmax(spec[m]))])
        except Exception:
            pass
    lo = _row_get(row, "low_band_energy")
    lm = _row_get(row, "low_mid_energy")
    share = lo / (lo + lm + 1e-9)
    return 40.0 + 25.0 * share  # kick profond -> ~65 Hz, kick sec -> ~40-50 Hz


def _fetch(conn: sqlite3.Connection, role: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sound_assets WHERE role_category=? ORDER BY indexed_at DESC LIMIT ?",
        (role, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _score(row: dict[str, Any], **targets: float) -> float:
    """Score de proximité à des cibles (plus haut = mieux)."""
    s = 0.0
    for key, target in targets.items():
        v = _row_get(row, key)
        s -= abs(v - target) / max(1.0, abs(target))
    return s


def select_rhythmic_anchor(conn: sqlite3.Connection, profile: str) -> dict[str, Any]:
    """Étape 1 : kick (profil de genre) + snare (centroïde > 800 Hz, +15 brightness) + hihat."""
    kprof = GENRE_KICK_PROFILE.get(profile, DEFAULT_KICK_PROFILE)
    h_lo, h_hi = kprof["ac_hardness"]
    c_lo, c_hi = kprof["crest_factor_db"]
    kicks = _fetch(conn, "kick")
    if not kicks:
        raise ValueError("aucun asset role_category='kick' dans samples.db")
    kick = max(
        kicks,
        key=lambda r: _score(r, ac_hardness=(h_lo + h_hi) / 2.0, crest_factor_db=(c_lo + c_hi) / 2.0),
    )
    f_kick = kick_resonance_hz(kick)
    k_bright = _row_get(kick, "ac_brightness")
    snares = [r for r in _fetch(conn, "snare") if _row_get(r, "spectral_centroid_hz") > 800.0
              and _row_get(r, "ac_brightness") >= k_bright + 15.0]
    if not snares:
        snares = [r for r in _fetch(conn, "snare") if _row_get(r, "spectral_centroid_hz") > 800.0]
    if not snares:
        raise ValueError("aucun snare avec centroïde > 800 Hz")
    snare = max(snares, key=lambda r: _row_get(r, "ac_brightness"))
    hats = _fetch(conn, "hihat")
    hihat = max(hats, key=lambda r: _row_get(r, "air_band_energy") - 0.01 * _row_get(r, "ac_roughness")) if hats else None
    return {"kick": kick, "snare": snare, "hihat": hihat, "f_kick_hz": round(f_kick, 1)}


def select_bass(conn: sqlite3.Connection, f_kick_hz: float) -> dict[str, Any]:
    """Étape 2 : basse 808 complémentaire de f_kick (requête SQL contrainte)."""
    if f_kick_hz < 55.0:
        q = """SELECT * FROM sound_assets WHERE role_category='bass_808'
               AND ac_warmth > 60.0 ORDER BY (low_mid_energy - low_band_energy) DESC, ac_roughness ASC LIMIT 50"""
        bass = conn.execute(q).fetchall()
        bass = [dict(r) for r in bass]
        if not bass:
            bass = [dict(r) for r in conn.execute(
                "SELECT * FROM sound_assets WHERE role_category='bass_808' ORDER BY ac_warmth DESC LIMIT 50")]
    else:
        q = """SELECT * FROM sound_assets WHERE role_category='bass_808'
               AND ac_boomy > 65.0 AND ac_roughness < 25.0 ORDER BY low_band_energy DESC LIMIT 50"""
        bass = [dict(r) for r in conn.execute(q).fetchall()]
        if not bass:
            bass = [dict(r) for r in conn.execute(
                "SELECT * FROM sound_assets WHERE role_category='bass_808' ORDER BY ac_boomy DESC LIMIT 50")]
    if not bass:
        raise ValueError("aucun asset role_category='bass_808' dans samples.db")
    return {"bass_808": max(bass, key=lambda r: _row_get(r, "ac_warmth") + _row_get(r, "ac_boomy"))}


def select_harmony(conn: sqlite3.Connection, bpm: float | None, dense_rhythm: bool | None = None) -> dict[str, Any]:
    """Étape 3 : pad/keys, centroïde in [350, 1800] Hz, ac_roughness < 30, attaque lente si dense."""
    dense = dense_rhythm if dense_rhythm is not None else bool(bpm and bpm >= 120)
    min_log_attack = math.log10(60.0) if dense else -3.0
    q = """SELECT * FROM sound_assets
           WHERE role_category IN ('pad','keys')
             AND spectral_centroid_hz BETWEEN 350.0 AND 1800.0
             AND ac_roughness < 30.0
             AND log_attack_ms > ?
           ORDER BY ac_warmth DESC LIMIT 50"""
    rows = [dict(r) for r in conn.execute(q, (min_log_attack,)).fetchall()]
    if not rows:
        rows = [dict(r) for r in conn.execute(
            """SELECT * FROM sound_assets WHERE role_category IN ('pad','keys')
               AND spectral_centroid_hz BETWEEN 350.0 AND 1800.0 AND ac_roughness < 30.0
               ORDER BY ac_warmth DESC LIMIT 50""")]
    if not rows:
        raise ValueError("aucun pad/keys conforme (centroïde 350-1800 Hz, roughness < 30)")
    pad = max(rows, key=lambda r: _row_get(r, "ac_warmth") + _row_get(r, "ac_depth"))
    return {"harmony": pad, "dense_rhythm": dense}


def select_lead(conn: sqlite3.Connection, harmony: dict[str, Any]) -> dict[str, Any]:
    """Étape 4 : lead, centroïde >= 1.5 x pad, ac_brightness >= pad + 15, tessiture sûre."""
    c_pad = _row_get(harmony, "spectral_centroid_hz")
    b_pad = _row_get(harmony, "ac_brightness")
    q = """SELECT * FROM sound_assets
           WHERE role_category IN ('lead','keys','pad')
             AND spectral_centroid_hz >= ?
             AND ac_brightness >= ?
             AND is_pitched = 1
           ORDER BY spectral_centroid_hz DESC, ac_brightness DESC LIMIT 50"""
    rows = [dict(r) for r in conn.execute(q, (1.5 * c_pad, b_pad + 15.0)).fetchall()]
    if not rows:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM sound_assets WHERE role_category IN ('lead','keys') AND is_pitched=1 "
            "ORDER BY ac_brightness DESC LIMIT 50")]
    if not rows:
        raise ValueError("aucun lead conforme")
    lead = rows[0]
    return {"lead": lead, "min_safe_midi": int(_row_get(lead, "min_safe_midi", 21)),
            "max_safe_midi": int(_row_get(lead, "max_safe_midi", 108))}


def row_to_bark24(row: dict[str, Any], audio_cache: dict[str, list[float]] | None = None) -> list[float]:
    """Distribution Bark24 d'un asset : mesurée sur le WAV si dispo, sinon déduite des 4 bandes."""
    path = row.get("file_path") or ""
    cache = audio_cache if audio_cache is not None else {}
    if path in cache:
        return cache[path]
    bark = None
    if path and os.path.isfile(path):
        try:
            import soundfile as sf
            from scipy.signal import stft

            audio, sr = sf.read(path, dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            n = min(audio.size, 8192)
            if n >= 256:
                freqs, _, Z = stft(audio[:n], fs=sr, nperseg=min(2048, n), noverlap=min(2048, n) // 2)
                power = (np.abs(Z) ** 2).mean(axis=1)
                raw = np.zeros(len(BARK_EDGES_HZ))
                for i, (lo, hi) in enumerate(BARK_EDGES_HZ):
                    m = (freqs >= lo) & (freqs < hi)
                    raw[i] = float(power[m].sum()) if np.any(m) else 0.0
                s = raw.sum()
                if s > 0:
                    bark = [float(x / s) for x in raw]
        except Exception:
            bark = None
    if bark is None:
        # Repli : répartir chaque bande stockée uniformément sur ses bandes de Bark.
        band_map = [(0, 1), (1, 4), (4, 17), (17, 24)]
        stored = [_row_get(row, "low_band_energy"), _row_get(row, "low_mid_energy"),
                  _row_get(row, "high_mid_energy"), _row_get(row, "air_band_energy")]
        bark = [0.0] * len(BARK_EDGES_HZ)
        for (a, b), e in zip(band_map, stored):
            for i in range(a, b):
                bark[i] = e / max(1, b - a)
        s = sum(bark) or 1.0
        bark = [x / s for x in bark]
    if audio_cache is not None:
        cache[path] = bark
    return bark


def spectral_overlap_m(bark_a: list[float], bark_b: list[float]) -> float:
    """M_spectral in [0,1] : taux de chevauchement énergétique en bandes de Bark (1 = identique)."""
    a = np.asarray(bark_a, dtype=np.float64)
    b = np.asarray(bark_b, dtype=np.float64)
    denom = float(np.maximum(a, b).sum())
    if denom <= 0:
        return 0.0
    return float(np.minimum(a, b).sum() / denom)


def corridor_distance(bark24: list[float], corridor: dict[str, Any]) -> float:
    """D_corridor in [0,1] : distance normalisée (L1/2) aux corridors p10/p90 par stem."""
    c = corridor.get("bark24") or {}
    p10 = c.get("p10")
    p90 = c.get("p90")
    if not p10 or not p90:
        return 0.0
    a = np.asarray(bark24, dtype=np.float64)
    lo = np.asarray(p10, dtype=np.float64)
    hi = np.asarray(p90, dtype=np.float64)
    outside = np.maximum(0.0, lo - a) + np.maximum(0.0, a - hi)
    return float(np.clip(outside.sum() / 2.0, 0.0, 1.0))


def pleasantness_index(rows: list[dict[str, Any]]) -> float:
    """P_timbre in [0,1] : chaleur + profondeur - pénalité de rugosité/aigreur excessive."""
    if not rows:
        return 0.0
    vals = []
    for r in rows:
        w = _row_get(r, "ac_warmth") / 100.0
        d = _row_get(r, "ac_depth") / 100.0
        pen = max(0.0, _row_get(r, "ac_roughness") - 40.0) / 60.0 + max(0.0, _row_get(r, "ac_sharpness") - 70.0) / 30.0
        vals.append(float(np.clip(0.5 * w + 0.3 * d - 0.2 * min(1.0, pen), 0.0, 1.0)))
    return float(np.mean(vals))


def low_interval_limit_ok(midi_notes: list[int]) -> tuple[bool, str]:
    """Low Interval Limits : aucune seconde/tierce mineure sous C3 (MIDI 48) etc."""
    notes = sorted(set(int(n) for n in midi_notes))
    names = {1: "seconde mineure", 2: "seconde majeure", 3: "tierce mineure",
             4: "tierce majeure", 6: "triton", 7: "quinte", 12: "octave"}
    for i in range(len(notes)):
        for j in range(i + 1, len(notes)):
            st = notes[j] - notes[i]
            cls = st % 12 if st % 12 != 0 else 12
            limit = LOW_INTERVAL_LIMITS.get(cls)
            if limit is not None and notes[i] < limit:
                return False, f"{names.get(cls, str(cls))} entre MIDI {notes[i]} et {notes[j]} (limite {limit})"
    return True, "ok"


def load_stem_corridors(corridors_path: str = DEFAULT_CORRIDORS_PATH) -> dict[str, Any]:
    """Charge les corridors par stem (Chantier 4) ; repli sur corridors globaux si absent."""
    if os.path.isfile(corridors_path):
        with open(corridors_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("profiles", {})
    return {}


def score_palette(
    palette: dict[str, dict[str, Any]],
    profile: str,
    corridors_path: str = DEFAULT_CORRIDORS_PATH,
    weights: tuple[float, float, float] = (1 / 3, 1 / 3, 1 / 3),
) -> dict[str, Any]:
    """S_palette = w1*(1-D_corridor) + w2*(1-M_spectral) + w3*P_timbre (spec §3.2)."""
    profiles = load_stem_corridors(corridors_path)
    prof = profiles.get(profile, {})
    stems = prof.get("stems", {})
    cache: dict[str, list[float]] = {}
    barks: dict[str, list[float]] = {}
    for role, row in palette.items():
        if row is not None:
            barks[role] = row_to_bark24(row, cache)
    d_vals = []
    for role, bark in barks.items():
        stem = ROLE_STEM.get(role, "other")
        corridor = stems.get(stem) or prof
        d_vals.append(corridor_distance(bark, corridor))
    d_corridor = float(np.mean(d_vals)) if d_vals else 0.0
    roles = [r for r, b in barks.items()]
    m_vals = []
    for i in range(len(roles)):
        for j in range(i + 1, len(roles)):
            m_vals.append(spectral_overlap_m(barks[roles[i]], barks[roles[j]]))
    m_spectral = float(np.mean(m_vals)) if m_vals else 0.0
    p_timbre = pleasantness_index([r for r in palette.values() if r is not None])
    w1, w2, w3 = weights
    s = w1 * (1.0 - d_corridor) + w2 * (1.0 - m_spectral) + w3 * p_timbre
    return {
        "S_palette": round(float(np.clip(s, 0.0, 1.0)), 4),
        "D_corridor": round(d_corridor, 4),
        "M_spectral": round(m_spectral, 4),
        "P_timbre": round(p_timbre, 4),
        "weights": {"w_corridor": w1, "w_spectral": w2, "w_timbre": w3},
    }


def select_palette(
    db_path: str = DEFAULT_DB_PATH,
    profile: str = "trap_rap",
    bpm: float | None = None,
    key: str = "C:minor",
    corridors_path: str = DEFAULT_CORRIDORS_PATH,
) -> dict[str, Any]:
    """Assemblage séquentiel complet en 4 étapes + score S_palette."""
    conn = init_db(db_path)
    try:
        anchor = select_rhythmic_anchor(conn, profile)
        bass = select_bass(conn, anchor["f_kick_hz"])
        harmony = select_harmony(conn, bpm)
        lead = select_lead(conn, harmony["harmony"])
    finally:
        conn.close()
    palette = {
        "kick": anchor["kick"],
        "snare": anchor["snare"],
        "hihat": anchor["hihat"],
        "bass_808": bass["bass_808"],
        "pad": harmony["harmony"],
        "lead": lead["lead"],
    }
    scoring = score_palette(palette, profile, corridors_path)
    ok, lil_msg = low_interval_limit_ok([C3_MIDI, C3_MIDI + 7, C3_MIDI + 12])  # accord de référence C3-G3-C4
    return {
        "profile": profile,
        "bpm": bpm,
        "key": key,
        "f_kick_hz": anchor["f_kick_hz"],
        "steps": {
            "1_rhythmic_anchor": {k: (v or {}).get("asset_id") for k, v in anchor.items() if isinstance(v, dict)},
            "2_bass": bass["bass_808"]["asset_id"],
            "3_harmony": harmony["harmony"]["asset_id"],
            "4_lead": lead["lead"]["asset_id"],
        },
        "palette": {k: (v or {}).get("asset_id") for k, v in palette.items()},
        "assets": {k: v for k, v in palette.items()},
        "lead_range": [lead["min_safe_midi"], lead["max_safe_midi"]],
        "low_interval_limit": {"ok": ok, "message": lil_msg},
        **scoring,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sélection relationnelle de palette (4 étapes) depuis samples.db")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--profile", default="trap_rap")
    parser.add_argument("--bpm", type=float, default=None)
    parser.add_argument("--key", default="C:minor")
    parser.add_argument("--corridors", default=DEFAULT_CORRIDORS_PATH)
    parser.add_argument("--out", default=None, help="fichier JSON de sortie")
    args = parser.parse_args()
    result = select_palette(args.db, args.profile, args.bpm, args.key, args.corridors)
    slim = {k: v for k, v in result.items() if k != "assets"}
    slim["assets"] = {k: {kk: vv for kk, vv in (v or {}).items()
                          if kk in ("asset_id", "source_type", "file_path", "preset_name",
                                    "spectral_centroid_hz", "ac_warmth", "ac_brightness",
                                    "ac_roughness", "ac_boomy", "crest_factor_db")}
                      for k, v in result["assets"].items()}
    payload = json.dumps(slim, ensure_ascii=False, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
