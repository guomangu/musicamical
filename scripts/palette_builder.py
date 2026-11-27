#!/usr/bin/env python3
"""
Spectral & Timbral Palette Builder (`palette_builder.py`, RFC §2.2).

Reduces the LLM decision space from 5,000+ samples down to 3 complementary,
conflict-free palettes (4-6 sounds each) in < 50 ms on CPU with ZERO PyTorch imports.

Enforces:
  - Strict exclusivity invariant: when `project_samples_dir` is provided, NEVER
    queries external `samples.db` (uses ONLY user-supplied project samples +
    optional native `gl.*` synths if `allow_gl_synths=True`).
  - Chromatic purity (`playable_chromatically == True`) for melodic roles.
  - Bark-24 inter-track overlap penalty (extra weight on Kick/Bass 40-120 Hz
    and Chords/Lead 800-3500 Hz) + attack relay contrast + mood alignment.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from typing import Any

import numpy as np

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
INDEX_DIR = os.path.join(ROOT_DIR, "mcp-servers", "audio-index")
for _d in (SCRIPTS_DIR, INDEX_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from audio_metrics import VALID_ROLES, key_fit_penalty, key_scale_pcs, parse_key_tonic_pc
from blueprint_safety import (  # v3.0 loop guards
    LOOP_KEY_MAX_OUT_OF_SCALE,
    LOOP_REPITCH_MIN_OUT_OF_SCALE,
    best_loop_transposition,
    bpm_compatibility,
    chroma_out_of_scale_ratio,
)
from gl_dsl import SYNTH_REGISTRY, synth_voice
from indexer import (
    DEFAULT_ANCHORS_NPZ,
    DEFAULT_DB_PATH,
    build_timbre_card_from_dsp,
    load_semantic_anchors,
    reroute_sample_path,
)
from select_sample import _fast_comfort_score, _fast_role_score

MELODIC_ROLES = {"bass_sub", "chord_keys", "lead_melody", "pad_atmosphere"}
PERCUSSIVE_ROLES = {"kick", "snare_clap", "hihat_perc"}

# Synthetic calibrated acoustic profiles for native `gl.*` synths (Bark 24 bands, attack_ms, centroid_hz)
def _make_bark_profile(peak_bands: list[tuple[int, float]]) -> list[float]:
    arr = np.full(24, 0.005, dtype=np.float64)
    for center, weight in peak_bands:
        for i in range(24):
            arr[i] += weight * math.exp(-0.5 * ((i - center) / 2.2) ** 2)
    arr /= float(np.sum(arr)) + 1e-12
    return [round(float(x), 5) for x in arr]


_GL_SYNTH_CANDIDATES: dict[str, list[dict[str, Any]]] = {
    "bass_sub": [
        {
            "id": "gl.analog:bass",
            "plugin": "gl.analog",
            "gl_role": "bass",
            "theme": "synthetic",
            "centroid_hz": 180.0,
            "attack_ms": 8.0,
            "role_score": 0.86,
            "comfort_score": 0.94,
            "bark_24": _make_bark_profile([(0, 1.0), (1, 0.8), (2, 0.35)]),
            "timbre_card": "Synthé gl.analog sub/basse chaude mono, attaque contrôlée, propre (gl.* natif)",
        },
        {
            "id": "gl.analog:bass_organic",
            "plugin": "gl.analog",
            "gl_role": "bass",
            "theme": "organic",
            "centroid_hz": 220.0,
            "attack_ms": 8.0,
            "role_score": 0.87,
            "comfort_score": 0.95,
            "bark_24": _make_bark_profile([(0, 0.9), (1, 1.0), (2, 0.5)]),
            "timbre_card": "Basse gl.analog sub/chaude mono juste (+0.6 ct), attaque naturelle (gl.* natif)",
        },
        {
            "id": "gl.wavetable:bass",
            "plugin": "gl.wavetable",
            "gl_role": "bass",
            "theme": "hybrid",
            "centroid_hz": 220.0,
            "attack_ms": 6.0,
            "role_score": 0.85,
            "comfort_score": 0.92,
            "bark_24": _make_bark_profile([(0, 0.9), (1, 0.9), (3, 0.4)]),
            "timbre_card": "Synthé gl.wavetable basse profonde précise, LPF contrôlé (gl.* natif)",
        },
    ],
    "chord_keys": [
        {
            "id": "gl.keys:chords",
            "plugin": "gl.keys",
            "gl_role": "chords",
            "theme": "organic",
            "centroid_hz": 920.0,
            "attack_ms": 22.0,
            "role_score": 0.88,
            "comfort_score": 0.94,
            "bark_24": _make_bark_profile([(3, 0.7), (5, 1.0), (7, 0.6), (9, 0.25)]),
            "timbre_card": "Clavier gl.keys feutré chaud, attaque douce, équilibré (gl.* natif)",
        },
        {
            "id": "gl.warmsynth:chords",
            "plugin": "gl.warmsynth",
            "gl_role": "chords",
            "theme": "synthetic",
            "centroid_hz": 820.0,
            "attack_ms": 55.0,
            "role_score": 0.86,
            "comfort_score": 0.93,
            "bark_24": _make_bark_profile([(3, 0.8), (5, 0.9), (8, 0.5)]),
            "timbre_card": "Synthé gl.warmsynth accords analogiques sombres/chauds, attaque douce (gl.* natif)",
        },
        {
            "id": "gl.dcosynth:chords",
            "plugin": "gl.dcosynth",
            "gl_role": "chords",
            "theme": "hybrid",
            "centroid_hz": 1100.0,
            "attack_ms": 38.0,
            "role_score": 0.85,
            "comfort_score": 0.92,
            "bark_24": _make_bark_profile([(4, 0.8), (6, 0.9), (8, 0.5)]),
            "timbre_card": "Synthé gl.dcosynth accords vintage amples, filtrés doux (gl.* natif)",
        },
    ],
    "pad_atmosphere": [
        {
            "id": "gl.string:pad",
            "plugin": "gl.string",
            "gl_role": "pad",
            "theme": "organic",
            "centroid_hz": 980.0,
            "attack_ms": 240.0,
            "role_score": 0.88,
            "comfort_score": 0.95,
            "bark_24": _make_bark_profile([(3, 0.7), (5, 1.0), (8, 0.55)]),
            "timbre_card": "Cordes gl.string nappe organique chaude, gonflement lent (gl.* natif)",
        },
        {
            "id": "gl.warmsynth:pad",
            "plugin": "gl.warmsynth",
            "gl_role": "pad",
            "theme": "synthetic",
            "centroid_hz": 780.0,
            "attack_ms": 260.0,
            "role_score": 0.87,
            "comfort_score": 0.94,
            "bark_24": _make_bark_profile([(3, 0.9), (5, 0.85), (7, 0.45)]),
            "timbre_card": "Nappe gl.warmsynth sombre analogique, attaque lente soyeuse (gl.* natif)",
        },
        {
            "id": "gl.twinva:pad",
            "plugin": "gl.twinva",
            "gl_role": "pad",
            "theme": "hybrid",
            "centroid_hz": 1050.0,
            "attack_ms": 220.0,
            "role_score": 0.84,
            "comfort_score": 0.92,
            "bark_24": _make_bark_profile([(4, 0.8), (6, 0.9), (9, 0.45)]),
            "timbre_card": "Nappe gl.twinva aérienne veloutée, attaque lente contrôlée (gl.* natif)",
        },
    ],
    "lead_melody": [
        {
            "id": "gl.pluck:pluck",
            "plugin": "gl.pluck",
            "gl_role": "pluck",
            "theme": "organic",
            "centroid_hz": 1850.0,
            "attack_ms": 4.0,
            "role_score": 0.87,
            "comfort_score": 0.93,
            "bark_24": _make_bark_profile([(7, 0.7), (10, 1.0), (13, 0.65)]),
            "timbre_card": "Pluck gl.pluck organique précis, attaque courte sèche, clair (gl.* natif)",
        },
        {
            "id": "gl.fm:bell",
            "plugin": "gl.fm",
            "gl_role": "bell",
            "theme": "synthetic",
            "centroid_hz": 2150.0,
            "attack_ms": 3.0,
            "role_score": 0.86,
            "comfort_score": 0.91,
            "bark_24": _make_bark_profile([(8, 0.7), (11, 1.0), (14, 0.6)]),
            "timbre_card": "Cloche FM gl.fm sombre cristalline (LPF insert), attaque franche (gl.* natif)",
        },
        {
            "id": "gl.analog:lead",
            "plugin": "gl.analog",
            "gl_role": "lead",
            "theme": "hybrid",
            "centroid_hz": 1950.0,
            "attack_ms": 16.0,
            "role_score": 0.86,
            "comfort_score": 0.92,
            "bark_24": _make_bark_profile([(7, 0.6), (10, 0.95), (12, 0.75)]),
            "timbre_card": "Lead gl.analog expressif, attaque souple, présence médium propre (gl.* natif)",
        },
    ],
}

_PALETTE_THEMES: list[tuple[str, str, str]] = [
    ("palette_A", "Organique / Feutrée", "organic"),
    ("palette_B", "Sombre / Synthétique", "synthetic"),
    ("palette_C", "Hybride / Percussive", "hybrid"),
]


def _default_bark_from_six_bands(
    b_sub: float, b_bass: float, b_lowmid: float, b_mid: float, b_harsh: float, b_air: float
) -> list[float]:
    arr = np.zeros(24, dtype=np.float64)
    arr[0] = b_sub
    arr[1:3] = b_bass / 2.0
    arr[3:7] = b_lowmid / 4.0
    arr[7:13] = b_mid / 6.0
    arr[13:19] = b_harsh / 6.0
    arr[19:24] = b_air / 5.0
    s = float(np.sum(arr))
    if s > 1e-12:
        arr /= s
    return arr.tolist()


def _prepare_bark_sqrt(bark_24: list[float] | None) -> np.ndarray | None:
    if not bark_24 or len(bark_24) != 24:
        return None
    a = np.maximum(np.asarray(bark_24, dtype=np.float64), 0.0)
    s = float(np.sum(a))
    if s <= 1e-12:
        return None
    return np.sqrt(a / s)


def _weighted_bark_overlap(c1: dict[str, Any], c2: dict[str, Any]) -> float:
    sq1 = c1.get("_bark_sqrt")
    if sq1 is None:
        sq1 = _prepare_bark_sqrt(c1.get("bark_24"))
        c1["_bark_sqrt"] = sq1
    sq2 = c2.get("_bark_sqrt")
    if sq2 is None:
        sq2 = _prepare_bark_sqrt(c2.get("bark_24"))
        c2["_bark_sqrt"] = sq2
    if sq1 is None or sq2 is None:
        return 0.25
    base_ov = float(sq1.dot(sq2))
    r1, r2 = c1["role"], c2["role"]
    if (r1 == "kick" and r2 == "bass_sub") or (r1 == "bass_sub" and r2 == "kick"):
        low_ov = float(sq1[0] * sq2[0] + sq1[1] * sq2[1])
        return min(1.0, 0.55 * base_ov + 0.70 * low_ov)
    if (r1 == "lead_melody" and r2 in ("chord_keys", "pad_atmosphere")) or (
        r2 == "lead_melody" and r1 in ("chord_keys", "pad_atmosphere")
    ):
        mid_ov = float(sq1[7:17].dot(sq2[7:17]))
        return min(1.0, 0.50 * base_ov + 0.75 * mid_ov)
    return base_ov * 0.65


def _attack_relay_penalty(items: list[dict[str, Any]]) -> float:
    """Enforce V4 attack contrast: penalize melodic layers that share nearly identical attack times."""
    mel = [it for it in items if it["role"] in ("chord_keys", "pad_atmosphere", "lead_melody")]
    if len(mel) < 2:
        return 0.0
    attacks = [float(it.get("attack_ms") or 15.0) for it in mel]
    spread = max(attacks) - min(attacks)
    if spread >= 35.0:
        return 0.0
    if spread >= 15.0:
        return 0.12
    return 0.28


def _mood_match_score(
    item: dict[str, Any],
    emotion_vector: dict[str, float],
    mood_vec: np.ndarray | None,
) -> float:
    val = float(emotion_vector.get("valence", 0.0))
    energy = float(emotion_vector.get("energy", 0.6))
    cent = float(item.get("centroid_hz") or 1200.0)
    att = float(item.get("attack_ms") or 15.0)
    role = item["role"]

    # Deterministic spectral/envelope mood alignment
    dsp_mood = 0.65
    if role in MELODIC_ROLES:
        target_cent = 1100.0 + (val + 1.0) * 950.0  # -1 -> 1.1kHz, +1 -> 3.0kHz
        if role == "bass_sub":
            target_cent = 180.0 + energy * 160.0
        dist = abs(cent - target_cent) / max(900.0, target_cent)
        dsp_mood = max(0.0, min(1.0, 1.0 - 0.55 * dist))
        if val < -0.25 and cent > 3200.0:
            dsp_mood *= 0.65
    else:
        if energy >= 0.65 and att <= 18.0:
            dsp_mood = 0.85
        elif energy < 0.45 and cent < 3500.0:
            dsp_mood = 0.80

    cvec = item.get("_clap_vec")
    if mood_vec is not None and cvec is not None and len(cvec) == len(mood_vec):
        sim = float(np.dot(cvec, mood_vec))
        clap_mood = max(0.0, min(1.0, 0.5 + sim * 1.8))
        return round(0.55 * clap_mood + 0.45 * dsp_mood, 4)
    return round(dsp_mood, 4)


def _build_mood_anchor_vec(emotion_vector: dict[str, float], anchors: dict[str, Any] | None) -> np.ndarray | None:
    if anchors is None:
        return None
    val = float(emotion_vector.get("valence", 0.0))
    energy = float(emotion_vector.get("energy", 0.6))
    tension = float(emotion_vector.get("tension", 0.4))
    emb = anchors["embeddings"]
    axes = anchors["axes"]
    ids = anchors["ids"]

    wanted: list[str] = []
    if val <= -0.3:
        wanted.extend(["mood_sad_melancholic", "mood_dark_nocturnal", "bri_dark_warm"])
        if energy >= 0.65 or tension >= 0.55:
            wanted.extend(["mood_aggressive_menacing", "mood_tense_cinematic"])
    elif val >= 0.3:
        wanted.extend(["mood_uplifting_joyful", "mood_chill_relaxed", "bri_clear_present"])
        if energy >= 0.7:
            wanted.append("mood_energetic_club")
    else:
        wanted.extend(["mood_nostalgic_warm", "mood_hypnotic_minimal", "bri_warm_mellow"])
    if energy <= 0.35:
        wanted.extend(["mood_dreamy_ethereal", "mood_intimate_fragile"])

    idxs = [i for i, aid in enumerate(ids) if aid in wanted]
    if not idxs:
        idxs = [i for i, ax in enumerate(axes) if ax == "texture" and ids[i] == "tex_clean_studio"]
    if not idxs:
        return None
    v = np.mean(emb[idxs], axis=0)
    nrm = float(np.linalg.norm(v))
    return (v / nrm).astype(np.float32) if nrm > 1e-12 else None


# ---------------------------------------------------------------------------
# v4.3 (chantier D1) — intention textuelle -> vecteur sémantique + score composite
# ---------------------------------------------------------------------------

# Synonymes FR/EN -> tokens d'ancre (ids `semantic_anchors.json`) pour la projection lexicale
# de l'`intent` (0 PyTorch : pas de text-tower CLAP au runtime, on projette sur les ancres
# pré-encodées du .npz via recouvrement lexical pondéré).
_INTENT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "sombre": ("dark", "nocturnal", "sad", "melancholic"), "dark": ("dark", "nocturnal"),
    "nocturne": ("nocturnal", "dark"), "hypnotique": ("hypnotic", "minimal"), "hypnotic": ("hypnotic", "minimal"),
    "minimal": ("minimal", "sparse"), "minimaliste": ("minimal", "sparse"),
    "mental": ("introspective", "intimate"), "introspectif": ("introspective", "intimate"),
    "agressif": ("aggressive", "menacing", "festival"), "aggressive": ("aggressive", "menacing"),
    "hard": ("aggressive", "menacing", "punchy", "gritty"), "dur": ("aggressive", "menacing", "punchy"),
    "tense": ("tense", "cinematic", "dark", "menacing"), "tendu": ("tense", "cinematic", "dark"),
    "festival": ("festival", "energetic", "club"), "rêveur": ("dreamy", "cloud", "ethereal"), "dreamy": ("dreamy", "ethereal"),
    "nuage": ("cloud", "dreamy"), "épique": ("cinematic", "epic"), "cinematic": ("cinematic", "epic"),
    "intime": ("intimate", "acoustic", "fragile"), "acoustique": ("acoustic", "intimate"), "acoustic": ("acoustic", "intimate", "organic"),
    "feutré": ("felt", "muted", "warm"), "muted": ("muted", "felt"),
    "poussiéreux": ("dusty", "vintage"), "dusty": ("dusty", "vintage"), "vintage": ("vintage", "dusty"),
    "vitreux": ("glassy", "airy"), "glassy": ("glassy", "airy"), "aérien": ("airy", "glassy"),
    "granuleux": ("gritty", "saturated"), "gritty": ("gritty", "saturated"), "saturé": ("saturated", "gritty"),
    "propre": ("clean", "subby"), "clean": ("clean", "sub"), "sub": ("sub", "clean"),
    "métallique": ("metallic", "harsh"), "metallic": ("metallic", "harsh"), "criard": ("harsh", "metallic"),
    "chaud": ("warm", "mellow"), "warm": ("warm", "mellow"), "froid": ("cold", "glassy"),
    "profond": ("deep", "sub"), "deep": ("deep", "sub"), "808": ("808", "sub", "bass"),
    "lourd": ("heavy", "sub"), "rap": ("rap",), "lent": ("slow", "calm"), "mélancolique": ("melancholic", "sad"),
    "melodic": ("mellow", "silky", "emotional"), "mélodique": ("mellow", "silky", "emotional"),
    "atmospheric": ("atmospheric", "ambient", "pad", "spacious", "ethereal"),
    "atmosphérique": ("atmospheric", "ambient", "pad", "spacious", "ethereal"),
    "punchy": ("punchy", "tight", "dry", "sharp"),
    # Genres & instruments (v4.3+ : diversité réelle entre Boom Bap, Trap, Drill, Lo-Fi, EDM, Jazz, Ambient)
    "trap": ("trap", "808", "dark", "punchy", "synth", "bell"),
    "drill": ("aggressive", "menacing", "dark", "808", "gritty", "tense", "synth"),
    "boombap": ("lofi", "vintage", "dusty", "acoustic", "warm", "rhodes", "piano"),
    "boom": ("vintage", "warm", "acoustic", "lofi"), "bap": ("vintage", "warm", "acoustic", "lofi"),
    "soul": ("warm", "rhodes", "nostalgic", "mellow", "vintage"),
    "soulful": ("warm", "rhodes", "nostalgic", "mellow", "vintage"),
    "jazz": ("warm", "rhodes", "acoustic", "piano", "mellow", "chill"),
    "lofi": ("lofi", "vinyl", "dusty", "vintage", "warm", "chill"),
    "house": ("club", "energetic", "synth", "punchy"), "edm": ("club", "energetic", "synth", "festival"),
    "rock": ("guitar", "aggressive", "acoustic", "punchy"),
    "ambient": ("ambient", "ethereal", "dreamy", "pad", "slow"),
    "piano": ("piano", "felt", "keys"), "rhodes": ("rhodes", "keys", "vintage", "warm"), "keys": ("keys", "piano", "rhodes"),
    "synth": ("synth", "synthesizer", "analog", "pad", "pluck"),
    "synthe": ("synth", "synthesizer", "analog", "pad"), "synthé": ("synth", "synthesizer", "analog", "pad"),
    "pad": ("pad", "atmospheric", "slow", "ambient", "airy"), "nappe": ("pad", "atmospheric", "slow", "ambient"),
    "bell": ("bell", "chime", "glassy", "fm"), "cloche": ("bell", "chime", "glassy", "fm"),
    "pluck": ("pluck", "plucked", "synth", "arpeggio"),
    "guitar": ("guitar", "plucked", "acoustic"), "guitare": ("guitar", "plucked", "acoustic"),
    "strings": ("strings", "orchestral", "bowed", "cinematic"), "cordes": ("strings", "orchestral", "bowed"),
    "bass": ("bass", "sub", "finger"), "basse": ("bass", "sub", "finger"),
    "slide": ("808", "sub", "dark", "gritty"),
    "vocal": ("vocal", "choir", "ethereal"), "choir": ("choir", "vocal", "ethereal"),
}

_TOKEN_RE = re.compile(r"[a-zàâäéèêëîïôöùûüç]+")


def _intent_tokens(intent: str) -> set[str]:
    toks: set[str] = set()
    for t in _TOKEN_RE.findall(str(intent or "").lower()):
        toks.add(t)
        for syn in _INTENT_SYNONYMS.get(t, ()):
            toks.add(syn)
    return toks


def _anchor_json_definitions(json_path: str | None = None) -> list[dict[str, Any]]:
    """Définitions lexicales complètes (incl. axe `style_nuance` v4.3) depuis le JSON —
    le .npz peut être ré-encodé offline plus tard (`build_semantic_anchors.py --populate-db`)."""
    if json_path is None:
        json_path = os.path.join(INDEX_DIR, "semantic_anchors.json")
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [a for a in (data.get("anchors") or []) if isinstance(a, dict)]
    except Exception:
        return []


def build_intent_vector(intent: str, anchors: dict[str, Any] | None,
                        anchors_json_path: str | None = None) -> np.ndarray | None:
    """v4.3 (D1) — projette l'`intent` textuel sur les ancres sémantiques pré-encodées :
    recouvrement lexical (tokens intent + synonymes vs prompts/labels/ids des ancres, avec
    repli `fallback_id` pour les ancres du JSON pas encore ré-encodées dans le .npz), puis
    moyenne pondérée des embeddings CLAP -> vecteur L2-normalisé dans l'espace CLAP.
    0 PyTorch, déterministe, < 5 ms."""
    toks = _intent_tokens(intent)
    if not toks or not anchors:
        return None
    emb = np.asarray(anchors["embeddings"], dtype=np.float32)
    ids = list(anchors["ids"])
    id_to_idx = {aid: i for i, aid in enumerate(ids)}
    prompts = anchors.get("prompts") or []
    fr_labels = anchors.get("fr_labels") or []

    def _anchor_text(i: int) -> set[str]:
        parts = [ids[i]]
        if i < len(prompts):
            parts.append(str(prompts[i]))
        if i < len(fr_labels):
            parts.append(str(fr_labels[i]))
        text = " ".join(parts).lower()
        out = set(_TOKEN_RE.findall(text))
        out |= set(ids[i].split("_"))
        return out

    weights: list[tuple[int, int]] = []
    seen_ids: set[str] = set()
    for i in range(len(ids)):
        w = len(toks & _anchor_text(i))
        if w > 0:
            weights.append((w, i))
            seen_ids.add(ids[i])
    # Ancres définies dans le JSON mais pas encore dans le .npz : repli via `fallback_id`
    for a in _anchor_json_definitions(anchors_json_path):
        aid = str(a.get("id") or "")
        if not aid or aid in seen_ids:
            continue
        text = f"{aid} {a.get('prompt', '')} {a.get('fr_label', '')}".lower()
        tset = set(_TOKEN_RE.findall(text)) | set(aid.split("_"))
        w = len(toks & tset)
        if w <= 0:
            continue
        fb = str(a.get("fallback_id") or "")
        j = id_to_idx.get(fb) if fb else None
        if j is None:
            j = next((i for i in range(len(ids)) if ids[i].split("_")[0] == aid.split("_")[0]), None)
        if j is not None:
            weights.append((w, j))
    if not weights:
        return None
    weights.sort(key=lambda wi: (-wi[0], wi[1]))
    v = np.zeros(emb.shape[1], dtype=np.float32)
    for w, i in weights[:10]:
        v += float(w) * emb[i]
    nrm = float(np.linalg.norm(v))
    return (v / nrm).astype(np.float32) if nrm > 1e-12 else None


def _item_clap_vec(item: dict[str, Any]) -> np.ndarray | None:
    cv = item.get("_clap_vec")
    if cv is None:
        cv = item.get("clap_vec")
    if cv is None:
        return None
    arr = np.asarray(cv, dtype=np.float32)
    if arr.ndim != 1 or arr.size == 0:
        return None
    nrm = float(np.linalg.norm(arr))
    return (arr / nrm).astype(np.float32) if nrm > 1e-12 else None


def palette_intent_score(items: list[dict[str, Any]], intent_vec: np.ndarray | None) -> float | None:
    """v4.3 (D1) — similarité cosinus moyenne entre le vecteur `intent` et les `clap_vec`
    des pistes de la palette. Retourne None si aucun vecteur CLAP (marqueur clap=OFF)."""
    if intent_vec is None:
        return None
    sims: list[float] = []
    for it in items:
        cv = _item_clap_vec(it)
        if cv is not None and cv.size == intent_vec.size:
            sims.append(float(np.dot(cv, intent_vec)))
    if not sims:
        return None
    return round(max(0.0, min(1.0, 0.5 + 0.5 * float(np.mean(sims)))), 4)


_PIVOT_ROLES = ("bass_sub", "chord_keys", "kick")


def composite_palette_score(palette: dict[str, Any],
                            items: list[dict[str, Any]] | None = None,
                            intent_vec: np.ndarray | None = None,
                            weights: dict[str, float] | None = None,
                            sample_pool: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """v4.3 (D1) — score composite borné dans [0, 1] :
    S(p) = α·S_intent + β·S_coherence + γ·S_coverage − λ·P_fallback.
    - S_intent : cosinus moyen intent↔clap_vec (None -> neutre 0.5, α transféré sur β) ;
    - S_coherence : `timbre_coherence.score` (anti-masquage Bark24 + rugosité + DRR) ;
    - S_coverage : min(1, N_real/N_tracks / 0.85) — plateau saturant à 85 % ;
    - P_fallback : pénalité si un rôle pivot (bass/chords/kick) tombe en repli synth
      alors qu'un sample sain existe dans une autre palette/pool.
    Pondérations par défaut depuis `config/mix_profiles.json` (`palette_scoring_weights`)."""
    try:
        from mix_config import palette_scoring_weights as _mc_weights
        weights = weights or _mc_weights()
    except Exception:
        pass
    w = weights or {"alpha": 0.45, "beta": 0.35, "gamma": 0.20, "lambda": 0.25, "coverage_saturation": 0.85}
    alpha, beta, gamma, lam = float(w["alpha"]), float(w["beta"]), float(w["gamma"]), float(w["lambda"])
    sat = float(w.get("coverage_saturation", 0.85))

    slots = palette.get("slots") or []
    items = items if items is not None else []
    n_tracks = max(1, len(slots) or len(items))
    n_real = sum(1 for s in slots if s.get("source_type") in ("sample", "project_sample") and s.get("filepath"))
    coverage = min(1.0, (n_real / n_tracks) / max(1e-6, sat))

    raw_intent = palette_intent_score(items or slots, intent_vec) if intent_vec is not None else None
    clap_off = intent_vec is not None and raw_intent is None
    if raw_intent is None:
        alpha, beta = 0.0, beta + alpha  # intent vide ou clap=OFF : α transféré sur β
        s_intent = 0.5
    else:
        s_intent = raw_intent

    coh = palette.get("timbre_coherence") or {}
    s_coh = float(coh.get("score", 1.0)) if isinstance(coh, dict) else 1.0

    p_fallback = 0.0
    if sample_pool:
        for role in _PIVOT_ROLES:
            slot = next((s for s in slots if s.get("role") == role), None)
            if slot is None:
                continue
            is_fallback = slot.get("source_type") not in ("sample", "project_sample")
            has_healthy = any(
                c.get("source_type") in ("sample", "project_sample") and c.get("filepath")
                and str(c.get("_policy_tier") or c.get("policy_tier") or "neutral") in ("preferred", "neutral")
                and c.get("_gate_verdict") in (None, "PASS", "FIX") and c.get("_range_ok", True)
                for c in (sample_pool.get(role) or [])
            )
            if is_fallback and has_healthy:
                p_fallback += 1.0 / len(_PIVOT_ROLES)

    score = alpha * s_intent + beta * s_coh + gamma * coverage - lam * p_fallback
    return {
        "score": round(max(0.0, min(1.0, score)), 4),
        "s_intent": s_intent,
        "s_coherence": round(s_coh, 4),
        "s_coverage": round(coverage, 4),
        "p_fallback": round(p_fallback, 4),
        "clap_off": bool(clap_off),
    }


def clap_coverage_ratio(candidate_pool: dict[str, list[dict[str, Any]]] | None = None,
                        db_path: str = DEFAULT_DB_PATH) -> float | None:
    """v4.3 (D1) — proportion de samples de l'index disposant d'un `clap_vec` non nul.
    < 0.50 -> marqueur `clap=OFF(dsp_fallback)` (le filtrage sémantique est dégradé)."""
    if candidate_pool:
        total = 0
        with_vec = 0
        for cands in candidate_pool.values():
            for c in cands:
                if c.get("source_type") not in ("sample", "project_sample"):
                    continue
                total += 1
                if _item_clap_vec(c) is not None:
                    with_vec += 1
        if total:
            return round(with_vec / total, 4)
    try:
        if not os.path.isfile(db_path):
            return None
        conn = sqlite3.connect(db_path)
        try:
            has_cards = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sample_semantic_cards'"
            ).fetchone() is not None
            if not has_cards:
                return None
            total = conn.execute("SELECT COUNT(*) FROM sample_semantic_cards").fetchone()[0]
            with_vec = conn.execute(
                "SELECT COUNT(*) FROM sample_semantic_cards WHERE clap_vec IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        if not total:
            return None
        return round(with_vec / total, 4)
    except Exception:
        return None


def _load_project_candidates(
    project_samples_dir: str,
    roles: list[str],
    target_key: str = "",
    bpm: float | None = None,
    relax: bool = False,
    trace_out: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Load candidates strictly from a user project directory (`samples_catalog.json` or live scan).

    `relax=True` (passe 2) : assouplit le seuil de score de rôle (0.30 -> 0.18) pour les
    samples acoustiques légitimes qui passeraient sinon à côté (piano doux, guitare, etc.).
    Les rejets durs (hard_reject, samples corrompus/clippés) et la pureté chromatique des
    rôles mélodiques restent NON négociables. `trace_out` reçoit le décompte par filtre."""
    role_threshold = 0.18 if relax else 0.30
    abs_dir = os.path.abspath(os.path.expanduser(project_samples_dir))
    # Support passing either `<proj>` or `<proj>/Samples`
    if os.path.basename(abs_dir).lower() == "samples" and os.path.isdir(os.path.dirname(abs_dir)):
        proj_root = os.path.dirname(abs_dir)
    else:
        proj_root = abs_dir

    cat_path = os.path.join(proj_root, "samples_catalog.json")
    if os.path.isfile(cat_path):
        with open(cat_path, encoding="utf-8") as fh:
            catalog = json.load(fh)
    else:
        from index_project_samples import index_project_samples

        catalog = index_project_samples(proj_root, with_clap=False, write_catalog=True)

    by_role: dict[str, list[dict[str, Any]]] = {r: [] for r in roles}
    _trace: dict[str, Any] = {"n_catalog": len(catalog.get("samples", [])), "n_hard_reject": 0,
                               "n_after_quality": 0, "per_role": {r: {"n_in": 0, "n_out": 0} for r in roles}} if trace_out is not None else None
    for s in catalog.get("samples", []):
        if s.get("hard_reject"):
            if _trace is not None:
                _trace["n_hard_reject"] += 1
            continue
        if _trace is not None:
            _trace["n_after_quality"] += 1
        playable = bool(s.get("playable_chromatically"))
        role_scores = s.get("role_scores") or {}
        for role in roles:
            if role in MELODIC_ROLES and not playable:
                continue
            # v3.0 key-fit: melodic one-shots AND melodic loops are checked against the song
            # scale. Percussive loops stay exempt (pitch is irrelevant for them).
            pcs = key_scale_pcs(target_key) if target_key else None
            is_loop = bool(s.get("is_loop"))
            loop_nature = str(s.get("loop_nature") or "")
            melodic_loop = is_loop and loop_nature != "percussive_loop"
            repitch_st = 0
            if role in MELODIC_ROLES and target_key and pcs:
                if melodic_loop:
                    chroma = s.get("chroma_cqt")
                    if chroma and len(chroma) == 12:
                        r_out = chroma_out_of_scale_ratio(chroma, pcs)
                        if r_out > LOOP_KEY_MAX_OUT_OF_SCALE:
                            delta, r_after = best_loop_transposition(chroma, pcs)
                            if r_after > LOOP_REPITCH_MIN_OUT_OF_SCALE:
                                continue  # LOOP_KEY_MISMATCH: reject the loop
                            repitch_st = int(delta)
                    else:
                        pen = key_fit_penalty(s.get("midi_root"), target_key, role)
                        if pen >= 0.12:
                            continue
                elif not is_loop:
                    pen = key_fit_penalty(s.get("midi_root"), target_key, role)
                    if pen >= 0.12:
                        continue
            # v3.0 BPM lock for loops (half/same/double tempo only, r_eff in [0.88, 1.14])
            needs_stretch = False
            stretch_rate = None
            if is_loop and bpm is not None and s.get("bpm") is not None:
                comp = bpm_compatibility(float(bpm), float(s["bpm"]))
                if not comp["ok"]:
                    continue  # loop_bpm_incompatible
                if comp["needs_stretch"]:
                    needs_stretch = True
                    stretch_rate = comp["r_eff"]
            r_sc = float(role_scores.get(role, 0.35))
            if role not in (s.get("compatible_roles") or []) and r_sc < role_threshold:
                continue
            if _trace is not None:
                _trace["per_role"][role]["n_in"] += 1
            from sample_fit_guard import infer_sample_category
            by_role[role].append({
                "id": s["filepath"],
                "role": role,
                "source_type": "project_sample",
                "filepath": s["filepath"],
                "category": infer_sample_category(s.get("category"), s["filepath"]),
                "sample_nature": s.get("sample_nature"),
                "plugin": "gl.sampler",
                "theme": "organic",
                "role_score": round(r_sc, 4),
                "comfort_score": float(s.get("comfort_score", 0.8)),
                "centroid_hz": float(s.get("centroid_hz") or 1000.0),
                "attack_ms": float(s.get("attack_ms") or 15.0),
                "r_harsh": s.get("r_harsh"),
                "roughness": s.get("roughness"),
                "peak_harsh_db": s.get("peak_harsh_db"),
                "crest_factor_db": s.get("crest_factor_db"),
                "detected_note": s.get("detected_note"),
                "midi_root": s.get("midi_root"),
                "recommended_fine_tune_ct": int(s.get("recommended_gl_fine_tune_ct") or 0),
                "bpm": s.get("bpm"),
                "is_loop": bool(s.get("is_loop")),
                "loop_nature": loop_nature or None,
                "chroma_cqt": s.get("chroma_cqt"),
                "inharmonicity": float(s.get("inharmonicity") or 0.0),
                "needs_time_stretch": needs_stretch,
                "stretch_rate": stretch_rate,
                "recommended_transposition_st": repitch_st,
                "playable_chromatically": playable,
                "bark_24": s.get("bark_24"),
                "timbre_card": s.get("timbre_card") or s["filename"],
                "_clap_vec": None,
            })
    for role in roles:
        by_role[role].sort(key=lambda x: x["role_score"] * 0.65 + x["comfort_score"] * 0.35, reverse=True)
        if _trace is not None:
            _trace["per_role"][role]["n_out"] = len(by_role[role])
    if trace_out is not None:
        trace_out.update(_trace)
        trace_out["relax"] = bool(relax)
    return by_role


_ROLE_CATEGORY_FILTER: dict[str, tuple[str, ...]] = {
    "kick": ("kick",),
    "bass_sub": ("808", "bass"),
    "snare_clap": ("snare", "clap"),
    "hihat_perc": ("hat", "open_hat", "perc"),
    "lead_melody": ("synth", "piano", "guitar", "vocal", "lead", "other"),
    "pad_atmosphere": ("synth", "piano", "pad", "guitar", "vocal", "other"),
    "chord_keys": ("piano", "guitar", "synth", "pad", "other"),
}


_CONTENT_FP_CACHE: dict[tuple[str, int, int], str] = {}


def _fast_content_fp(fp: str) -> str:
    try:
        st = os.stat(fp)
        key = (fp, int(st.st_size), int(st.st_mtime_ns))
        cached = _CONTENT_FP_CACHE.get(key)
        if cached is not None:
            return cached
        import hashlib as _hashlib
        h = _hashlib.sha256()
        h.update(str(st.st_size).encode("ascii"))
        with open(fp, "rb") as _fh:
            h.update(_fh.read(4096))
        val = h.hexdigest()[:20]
        if len(_CONTENT_FP_CACHE) > 8192:
            _CONTENT_FP_CACHE.clear()
        _CONTENT_FP_CACHE[key] = val
        return val
    except Exception:
        return fp


def _load_sqlite_candidates(
    db_path: str,
    roles: list[str],
    target_key: str,
    per_role_limit: int = 32,
    bpm: float | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Ultra-fast (< 25 ms) indexed SQLite candidate fetch across all roles (0 PyTorch)."""
    abs_db = os.path.abspath(os.path.expanduser(db_path))
    if not os.path.isfile(abs_db):
        return {r: [] for r in roles}

    conn = sqlite3.connect(abs_db)
    has_dsp = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sample_dsp_metrics'").fetchone() is not None
    has_cards = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sample_semantic_cards'").fetchone() is not None
    has_q2 = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sample_quality_v2'").fetchone() is not None
    q2_join = "LEFT JOIN sample_quality_v2 q2 ON q2.sample_id = s.id" if has_q2 else ""
    q2_clause = "COALESCE(q2.status, 'PASS') != 'REJECTED'" if has_q2 else "1=1"  # v2 : REJECTED exclus
    target_pc = parse_key_tonic_pc(target_key) if target_key else None

    role_order_expr = {
        "kick": "(COALESCE(d.band_sub,0) + COALESCE(d.band_bass,0)) - 0.6*COALESCE(d.r_harsh,0) - 0.3*MAX(0, s.duration_sec - 1.0)",
        "bass_sub": "(COALESCE(d.band_sub,0) + COALESCE(d.band_bass,0)) + 0.5*COALESCE(d.r_harm,0) - 0.6*COALESCE(d.r_harsh,0)",
        "snare_clap": "(COALESCE(d.band_lowmid,0) + COALESCE(d.band_mid,0) + COALESCE(d.band_harsh,0)) - 0.5*COALESCE(d.r_harsh,0)",
        "hihat_perc": "(COALESCE(d.band_air,0) + COALESCE(d.band_harsh,0)) - 0.5*COALESCE(d.r_harsh,0)",
        "lead_melody": "(COALESCE(d.r_harm,0) + 0.4*COALESCE(d.playable_chromatically,0) + COALESCE(d.band_mid,0)) - 0.7*COALESCE(d.r_harsh,0)",
        "pad_atmosphere": "(COALESCE(d.band_lowmid,0) + COALESCE(d.band_mid,0) + 0.5*COALESCE(d.r_harm,0)) - 0.7*COALESCE(d.r_harsh,0)",
        "chord_keys": "(COALESCE(d.band_bass,0) + COALESCE(d.band_lowmid,0) + COALESCE(d.band_mid,0) + 0.5*COALESCE(d.r_harm,0)) - 0.7*COALESCE(d.r_harsh,0)",
    }

    by_role: dict[str, list[dict[str, Any]]] = {r: [] for r in roles}
    if not has_dsp:
        conn.close()
        return by_role

    raw_by_role: dict[str, list[Any]] = {r: [] for r in roles}
    all_ids: list[int] = []

    for role in roles:
        cats = _ROLE_CATEGORY_FILTER.get(role)
        base_clauses = ["s.ext NOT IN ('.mid', '.midi')", "COALESCE(d.hard_reject, 0) = 0", q2_clause]
        if role in MELODIC_ROLES:
            base_clauses.append("d.playable_chromatically = 1")
        if role in ("chord_keys", "lead_melody"):
            # Ne pas polluer chords/lead avec des samples graves de basse (root < G2 et centroid < 220 Hz)
            base_clauses.append("(COALESCE(d.recommended_root_midi, 60) >= 43 OR COALESCE(d.centroid_hz, 500) >= 220)")
        if role == "bass_sub":
            # Alignement de phase Kick/808 exigé sur la basse uniquement : les one-shots
            # percussifs (kick/snare/hat/perc) ne doivent JAMAIS être pénalisés sur des
            # métriques de phase/pitch (faux négatifs massifs sinon).
            base_clauses.append("COALESCE(d.phase_corr, 1.0) >= 0.70")
        order_expr = role_order_expr.get(role, "-COALESCE(d.r_harsh, 0)")

        # Stratification par catégorie directement en SQL (window function SQLite, 0 scan complet Python) :
        # empêche qu'une seule famille (ex. piano ou vocal ou 808) ne sature le quota et affame
        # les synthés/pads/guitares/basses acoustiques, tout en gardant < 30 ms sur 5k-10k lignes.
        rows: list[Any] = []
        max_keep = max(int(per_role_limit) * 2, 64)
        if cats:
            ph = ",".join("?" for _ in cats)
            clauses = base_clauses + [f"s.category IN ({ph})"]
            per_cat_quota = max(14, max_keep // max(1, len(cats)) + 4)
            sql = f"""
                SELECT
                    id, path, category, duration_sec, spectral_centroid_hz,
                    sample_nature, playable_chromatically, recommended_root_midi,
                    recommended_fine_tune_ct, detected_note, centroid_hz, r_harsh,
                    peak_harsh_db, roughness, sfm, r_harm, spectral_flux,
                    low_mid_corr, phase_corr, crest_factor_db, attack_ms, needs_fade,
                    band_sub, band_bass, band_lowmid, band_mid, band_harsh, band_air,
                    bark_24_json, bpm, is_loop
                FROM (
                    SELECT
                        s.id, s.path, s.category, s.duration_sec, s.spectral_centroid_hz,
                        d.sample_nature, d.playable_chromatically, d.recommended_root_midi,
                        d.recommended_fine_tune_ct, d.detected_note, d.centroid_hz, d.r_harsh,
                        d.peak_harsh_db, d.roughness, d.sfm, d.r_harm, d.spectral_flux,
                        d.low_mid_corr, d.phase_corr, d.crest_factor_db, d.attack_ms, d.needs_fade,
                        d.band_sub, d.band_bass, d.band_lowmid, d.band_mid, d.band_harsh, d.band_air,
                        d.bark_24_json, s.bpm, s.is_loop,
                        ({order_expr}) AS _ord_score,
                        ROW_NUMBER() OVER (PARTITION BY s.category ORDER BY ({order_expr}) DESC) AS _rn
                    FROM samples s
                    JOIN sample_dsp_metrics d ON d.sample_id = s.id
                    {q2_join}
                    WHERE {' AND '.join(clauses)}
                )
                WHERE _rn <= ?
                ORDER BY _ord_score DESC
                LIMIT ?
            """
            raw_fetched = conn.execute(sql, list(cats) + [per_cat_quota, max_keep + 24]).fetchall()
            for r in raw_fetched:
                rp = r[1] if (r[1] and os.path.isfile(r[1])) else reroute_sample_path(r[1])
                if rp is not None:
                    rows.append((r[0], rp) + tuple(r[2:]))
                    if len(rows) >= max_keep:
                        break

        if len(rows) < 2:
            sql_fb = f"""
                SELECT
                    s.id, s.path, s.category, s.duration_sec, s.spectral_centroid_hz,
                    d.sample_nature, d.playable_chromatically, d.recommended_root_midi,
                    d.recommended_fine_tune_ct, d.detected_note, d.centroid_hz, d.r_harsh,
                    d.peak_harsh_db, d.roughness, d.sfm, d.r_harm, d.spectral_flux,
                    d.low_mid_corr, d.phase_corr, d.crest_factor_db, d.attack_ms, d.needs_fade,
                    d.band_sub, d.band_bass, d.band_lowmid, d.band_mid, d.band_harsh, d.band_air,
                    d.bark_24_json, s.bpm, s.is_loop
                FROM samples s
                JOIN sample_dsp_metrics d ON d.sample_id = s.id
                {q2_join}
                WHERE {' AND '.join(base_clauses)}
                ORDER BY ({order_expr}) DESC
                LIMIT ?
            """
            rows = []
            for r in conn.execute(sql_fb, [max_keep + 24]).fetchall():
                rp = r[1] if (r[1] and os.path.isfile(r[1])) else reroute_sample_path(r[1])
                if rp is not None:
                    rows.append((r[0], rp) + tuple(r[2:]))
                    if len(rows) >= max_keep:
                        break
        raw_by_role[role] = rows
        all_ids.extend(int(r[0]) for r in rows)

    cards_by_id: dict[int, tuple[bytes | None, str | None]] = {}
    if has_cards and all_ids:
        uniq = ",".join(str(x) for x in set(all_ids))
        for cid, blob16, tcard in conn.execute(
            f"SELECT sample_id, clap_vec, timbre_card FROM sample_semantic_cards WHERE sample_id IN ({uniq})"
        ).fetchall():
            cards_by_id[int(cid)] = (blob16, tcard)

    conn.close()

    from sample_fit_guard import infer_sample_category

    _BAD_ONESHOT_NAME_RE = re.compile(
        r"(?:^|[\s_\-\[\(])(?:loop|riser|rise|swell|rev|reverse|1bar|2bar|4bar|8bar|16bar)(?:$|[\s_\-\]\)\.])",
        re.IGNORECASE,
    )
    _EXT_PREF = {".wav": 0, ".flac": 1, ".aiff": 2, ".aif": 2, ".ogg": 3, ".mp3": 4}

    for role in roles:
        cands: list[dict[str, Any]] = []
        seen_real: set[str] = set()
        seen_fp: set[str] = set()
        seen_stem: set[str] = set()
        # Préférer .wav avant .flac/.mp3 pour éviter les doublons de format (ex. DBD_HI STR_C_1.*)
        ordered_rows = sorted(
            raw_by_role[role],
            key=lambda rr: _EXT_PREF.get(os.path.splitext(str(rr[1] or ""))[1].lower(), 9),
        )
        for r in ordered_rows:
            rpath = r[1]
            bn_no_ext = os.path.splitext(os.path.basename(rpath))[0].lower()
            if role != "pad_atmosphere" and _BAD_ONESHOT_NAME_RE.search(bn_no_ext):
                continue  # jamais de loop/riser/swell/reverse dans un rôle one-shot (kick, bass, chords, lead)
            stem_key = os.path.join(os.path.dirname(rpath), bn_no_ext).lower()
            if stem_key in seen_stem:
                continue  # dédoublonnage multi-format (.wav vs .flac/.mp3 du même son)
            if rpath in seen_real:
                continue
            cfp = _fast_content_fp(rpath)
            if cfp in seen_fp:
                continue  # F9 : dédoublonnage contenu (ex. foo.wav vs foo-2.wav)
            seen_stem.add(stem_key)
            seen_real.add(rpath)
            seen_fp.add(cfp)
            r = (r[0], rpath) + tuple(r[2:])
            (
                sid, path, cat, dur, s_cent,
                nat, play_int, root_midi,
                ft_ct, det_note, d_cent, r_harsh,
                pk_h, rough, sfm, r_harm, flux,
                lm_corr, phase_corr, cf_db, att_ms, needs_fade_int,
                b_sub, b_bass, b_lowmid, b_mid, b_harsh, b_air,
                bark_js, bpm_f, loop_int,
            ) = r
            # v3.0 BPM lock for loops from the SQLite bank (jamais sur un mono_oneshot soutenu !)
            if loop_int and str(nat or "") != "mono_oneshot" and bpm is not None and bpm_f is not None:
                if not bpm_compatibility(float(bpm), float(bpm_f))["ok"]:
                    continue
            clap_v16, t_card = cards_by_id.get(int(sid), (None, None))
            dur_f = float(dur or 0.5)
            cent_f = float(d_cent if d_cent is not None else (s_cent or 1000.0))
            att_f = float(att_ms if att_ms is not None else 15.0)
            if role in ("chord_keys", "lead_melody") and att_f > 1500.0:
                continue  # pad/swell à attaque ultra-lente (> 1,5 s) impropre aux accords/mélodies rythmiques
            rh_f = float(r_harsh or 0.0)
            pk_f = float(pk_h or 0.0)
            rg_f = float(rough or 0.0)
            sfm_f = float(sfm if sfm is not None else 0.05)
            harm_f = float(r_harm if r_harm is not None else 0.5)
            flux_f = float(flux if flux is not None else 0.1)
            ph_f = float(phase_corr if phase_corr is not None else 1.0)
            cf_f = float(cf_db if cf_db is not None else 10.0)
            nf = bool(needs_fade_int)

            s_conf = _fast_comfort_score(rh_f, pk_f, rg_f, nf, role)
            s_role = _fast_role_score(
                float(b_sub or 0.0), float(b_bass or 0.0), float(b_lowmid or 0.0),
                float(b_mid or 0.0), float(b_harsh or 0.0), float(b_air or 0.0),
                att_f, cf_f, harm_f, sfm_f, ph_f, pk_f, flux_f, dur_f, role,
            )
            if target_pc is not None and role not in PERCUSSIVE_ROLES and root_midi is not None:
                diff = (target_pc - (int(root_midi) % 12)) % 12
                sh = diff if diff <= 6 else 12 - diff
                if sh == 0:
                    s_role = min(1.0, s_role + 0.04)
                else:
                    s_role = max(0.0, s_role - key_fit_penalty(root_midi, target_key, role))

            bark_24 = None
            if bark_js:
                try:
                    parsed = json.loads(bark_js)
                    if isinstance(parsed, list) and len(parsed) == 24:
                        bark_24 = parsed
                except Exception:
                    bark_24 = None
            if bark_24 is None:
                bark_24 = _default_bark_from_six_bands(
                    float(b_sub or 0.0), float(b_bass or 0.0), float(b_lowmid or 0.0),
                    float(b_mid or 0.0), float(b_harsh or 0.0), float(b_air or 0.0),
                )

            if not t_card:
                t_card, _, _ = build_timbre_card_from_dsp(
                    category=cat,
                    sample_nature=nat,
                    detected_note=det_note,
                    centroid_hz=cent_f,
                    attack_ms=att_f,
                    duration_sec=dur_f,
                    r_harsh=rh_f,
                    r_harm=harm_f,
                    roughness=rg_f,
                    low_mid_corr=lm_corr,
                    playable_chromatically=bool(play_int),
                    comfort_score=s_conf,
                )

            cvec = np.frombuffer(clap_v16, dtype=np.float16).astype(np.float32) if clap_v16 is not None else None
            path_l = path.lower()
            if any(k in path_l for k in ("piano", "guitar", "rhodes", "acoustic", "wood", "felt", "string", "organ")):
                theme = "organic"
            elif any(k in path_l for k in ("synth", "fm", "808", "reese", "sub", "pad", "arp", "saw")):
                theme = "synthetic"
            else:
                theme = "hybrid"

            cands.append({
                "id": path,
                "role": role,
                "source_type": "sample",
                "filepath": path,
                "category": infer_sample_category(cat, path),
                "sample_nature": nat,
                "inharmonicity": 0.25 if str(nat or "") == "inharmonic_bell_or_fx" else 0.0,
                "plugin": "gl.sampler",
                "theme": theme,
                "role_score": round(s_role, 4),
                "comfort_score": round(s_conf, 4),
                "centroid_hz": round(cent_f, 1),
                "attack_ms": round(att_f, 1),
                "detected_note": det_note,
                "midi_root": root_midi,
                "recommended_fine_tune_ct": int(ft_ct or 0),
                "playable_chromatically": bool(play_int),
                "bark_24": bark_24,
                "r_harsh": round(rh_f, 4),
                "roughness": round(rg_f, 4),
                "peak_harsh_db": round(pk_f, 2),
                "crest_factor_db": round(cf_f, 2),
                "bpm": float(bpm_f) if bpm_f is not None else None,
                "is_loop": bool(loop_int),
                "timbre_card": t_card,
                "_clap_vec": cvec,
            })

        cands.sort(key=lambda x: 0.62 * x["role_score"] + 0.38 * x["comfort_score"], reverse=True)
        if len(cands) > per_role_limit:
            # Conserver la diversité des catégories (piano, synth, pad, guitar, bass, 808...) dans le top N
            by_cat: dict[str, list[dict[str, Any]]] = {}
            for c in cands:
                by_cat.setdefault(str(c.get("category") or "other"), []).append(c)
            quota = max(8, per_role_limit // max(1, len(by_cat)))
            diverse: list[dict[str, Any]] = []
            seen_d: set[str] = set()
            for cat_list in by_cat.values():
                for c in cat_list[:quota]:
                    if c["id"] not in seen_d:
                        diverse.append(c)
                        seen_d.add(c["id"])
            for c in cands:
                if len(diverse) >= per_role_limit:
                    break
                if c["id"] not in seen_d:
                    diverse.append(c)
                    seen_d.add(c["id"])
            diverse.sort(key=lambda x: 0.62 * x["role_score"] + 0.38 * x["comfort_score"], reverse=True)
            by_role[role] = diverse[:per_role_limit]
        else:
            by_role[role] = cands

    return by_role


def _build_gl_synth_pool(roles: list[str], valence: float, mix_profile: str = "trap_rap") -> dict[str, list[dict[str, Any]]]:
    pool: dict[str, list[dict[str, Any]]] = {r: [] for r in roles}
    # v3.2 TimbralCohesion : archétype calibré par (profil, rôle, valence) en priorité
    from gl_dsl import resolve_archetype
    arch_map = {}
    _PERC_GL_DEFAULTS = {
        "kick": ("gl.kick", 95.0, 4.0, _make_bark_profile([(0, 1.0), (1, 0.85), (2, 0.35)]), "Kick natif gl.kick punchy, sub propre (gl.* natif)"),
        "snare_clap": ("gl.snare", 1800.0, 5.0, _make_bark_profile([(6, 0.7), (10, 1.0), (14, 0.65)]), "Snare natif gl.snare sec et précis (gl.* natif)"),
        "hihat_perc": ("gl.hat", 6500.0, 2.0, _make_bark_profile([(16, 0.7), (19, 1.0), (22, 0.75)]), "Hi-Hat natif gl.hat net, attaque courte (gl.* natif)"),
    }
    role_map = {"bass_sub": "bass", "chord_keys": "chords", "pad_atmosphere": "chords", "lead_melody": "lead"}
    for role in roles:
        if role in _PERC_GL_DEFAULTS:
            plug, cent, att, bark, card = _PERC_GL_DEFAULTS[role]
            pool[role].append({
                "id": f"{plug}:{role}",
                "role": role,
                "source_type": "gridline_native",
                "plugin": plug,
                "filepath": None,
                "playable_chromatically": False,
                "recommended_fine_tune_ct": 0,
                "theme": "synthetic",
                "role_score": 0.85,
                "comfort_score": 0.92,
                "attack_ms": att,
                "centroid_hz": cent,
                "timbre_card": card,
                "bark_24": bark,
                "_clap_vec": None,
            })
            continue
        arch_map[role] = resolve_archetype(mix_profile, role_map.get(role, role), valence=valence)
    for role in roles:
        if role in _PERC_GL_DEFAULTS:
            continue
        arch_entry = arch_map.get(role)
        # Jamais injecter gl.sampler (sans fichier) dans le pool de synthés natifs
        if arch_entry and arch_entry["plugin"] in SYNTH_REGISTRY and arch_entry["plugin"] != "gl.sampler":
            gl_role = {"bass": "bass", "chords": "chords", "lead": "lead"}.get(role_map.get(role, role), "lead")
            ap = arch_entry["params"]
            voice = synth_voice(
                arch_entry["plugin"], gl_role, valence=valence,
                cutoff_hz=float(ap.get("cutoff_hz")) if ap.get("cutoff_hz") else None,
                attack_ms=float(ap.get("attack_ms")) if ap.get("attack_ms") else None,
                release_ms=float(ap.get("release_ms")) if ap.get("release_ms") else None,
                glide_ms=float(ap.get("glide_ms")) if ap.get("glide_ms") else None,
                vibrato_delay_ms=float(ap.get("vibrato_delay_ms", 220.0)),
            )
            item = {
                "id": f"arch:{mix_profile}:{role}",
                "role": role,
                "source_type": "gridline_native",
                "plugin": arch_entry["plugin"],
                "filepath": None,
                "playable_chromatically": True,
                "recommended_fine_tune_ct": 0,
                "theme": "synthetic",
                "role_score": 0.9,
                "comfort_score": (0.9 if float(ap.get("attack_ms", 3.0)) >= 2.0
                                  and float(ap.get("release_ms", 120.0)) >= 60.0
                                  and float(ap.get("cutoff_hz", 6500.0)) <= 9000.0 else 0.6),
                "attack_ms": ap.get("attack_ms", 3.0),
                "centroid_hz": ap.get("cutoff_hz", 6500.0),
                "timbre_card": f"archetype {mix_profile}/{role}",
                "bark_24": _make_bark_profile([(3, 0.8), (5, 0.9), (7, 0.6)]),
                "gl_dsl_hint": {
                    "plugin": arch_entry["plugin"],
                    "role": gl_role,
                    "cutoff_hz": voice.cutoff_hz,
                    "needs_insert_lpf": voice.needs_insert_lpf,
                    "archetype_params": ap,
                },
                "_clap_vec": None,
            }
            pool[role].append(item)
        for tpl in _GL_SYNTH_CANDIDATES.get(role, []):
            plugin = tpl["plugin"]
            if plugin not in SYNTH_REGISTRY:
                continue
            voice = synth_voice(plugin, tpl["gl_role"], valence=valence)
            item = dict(tpl)
            item["role"] = role
            item["source_type"] = "gridline_native"
            item["filepath"] = None
            item["playable_chromatically"] = True
            item["recommended_fine_tune_ct"] = 0
            item["gl_dsl_hint"] = {
                "plugin": plugin,
                "role": tpl["gl_role"],
                "cutoff_hz": voice.cutoff_hz,
                "needs_insert_lpf": voice.needs_insert_lpf,
            }
            item["_clap_vec"] = None
            pool[role].append(item)
    return pool


def space_coherence_penalty(items: list[dict[str, Any]], max_diff: float = 0.35, weight: float = 0.35) -> float:
    """v3.2 TimbralCohesion — pénalise l'association d'un son très réverbéré (wetness haut)
    avec un son très sec (wetness bas) sans compensation : évite la « boue spatiale » /
    le collage entre pièces acoustiques incompatibles. 0 si tous les wetness sont proches."""
    perc_roles = ("kick", "snare_clap", "hihat_perc")
    mel_roles = ("lead_melody", "chord_keys", "pad_atmosphere", "bass_sub")
    pen = 0.0
    n = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            ra, rb = str(a.get("role", "")), str(b.get("role", ""))
            if (ra in mel_roles and rb in perc_roles) or (ra in perc_roles and rb in mel_roles):
                wa = float(a.get("wetness_score", 0.5))
                wb = float(b.get("wetness_score", 0.5))
                d = abs(wa - wb)
                if d > max_diff:
                    pen += weight * (d - max_diff)
                n += 1
    return round(pen / max(1, n), 4)


def _intent_candidate_bonus(
    cand: dict[str, Any],
    role: str,
    intent_toks: set[str],
    intent_vec: np.ndarray | None,
    mix_profile: str,
    valence: float,
) -> float:
    """v4.3+ : aligne directement chaque candidat sur l'intention textuelle, le genre et CLAP
    (0 PyTorch) pendant la sélection greedy — empêche que les mêmes Rhodes/pianos ne gagnent
    systématiquement sur Trap, Drill, EDM ou Ambient."""
    bonus = 0.0
    # 1. Alignement cosinus CLAP direct avec l'intention (quand disponible)
    if intent_vec is not None:
        cvec = _item_clap_vec(cand)
        if cvec is not None and cvec.size == intent_vec.size:
            sim = float(np.dot(cvec, intent_vec))
            bonus += 0.28 * max(-0.35, min(0.65, sim))

    # 2. Affinité lexicale genre / famille d'instrument (active même si CLAP=OFF)
    if not intent_toks and mix_profile == "trap_rap" and valence >= -0.2:
        return round(bonus, 4)

    hay = f"{os.path.basename(str(cand.get('filepath') or ''))} {cand.get('timbre_card') or ''} {cand.get('category') or ''}".lower()
    cat = str(cand.get("category") or "").lower()
    prof = str(mix_profile or "trap_rap").lower()

    wants_keys_organic = bool(
        intent_toks & {"piano", "rhodes", "keys", "boombap", "boom", "bap", "soul", "soulful", "jazz", "lofi", "felt"}
    ) or (prof in ("lofi_chill", "jazz_acoustic") and not (intent_toks & {"synth", "bell", "pad", "electronic", "drill", "trap"}))
    wants_synth_modern = bool(
        intent_toks & {"trap", "drill", "synth", "synthe", "synthé", "pad", "bell", "cloche", "pluck", "edm", "house", "club", "electronic", "tense", "atmospheric", "strings", "cordes", "ambient", "cinematic", "orchestral"}
    ) or (prof in ("edm_club", "classical_ambient") and not wants_keys_organic)
    wants_guitar = bool(intent_toks & {"guitar", "guitare", "rock", "metal"}) or (prof == "rock_metal" and not wants_synth_modern)
    wants_strings = bool(intent_toks & {"strings", "cordes", "orchestral", "cinematic", "classical", "epic"}) or (
        prof == "classical_ambient" and not wants_keys_organic
    )
    wants_bell = bool(intent_toks & {"bell", "cloche", "chime", "glassy", "crystal", "hypnotic"})
    wants_pad = bool(intent_toks & {"pad", "nappe", "atmospheric", "atmosphérique", "ambient", "ethereal", "dreamy", "cloud"})
    wants_acoustic_bass = bool(
        (intent_toks & {"acoustic", "jazz", "boombap", "boom", "bap", "soul", "soulful", "finger", "upright", "rock"})
        and not (intent_toks & {"808", "trap", "drill", "slide"})
    )
    wants_808 = bool(intent_toks & {"808", "trap", "drill", "slide"}) or (prof == "trap_rap" and not wants_acoustic_bass)

    if role == "bass_sub":
        if wants_acoustic_bass:
            if cat == "bass" or "bass guitar" in hay or "finger" in hay:
                bonus += 0.30
            elif cat == "808" or "808" in hay:
                bonus -= 0.22
        elif wants_808:
            if cat == "808" or "808" in hay:
                bonus += 0.26
            elif "bass guitar" in hay or "upright" in hay or "finger" in hay:
                bonus -= 0.22
    elif role in ("chord_keys", "pad_atmosphere", "lead_melody"):
        is_rhodes = "rhodes" in hay
        is_piano = cat == "piano" or "piano" in hay
        is_synth_like = cat in ("synth", "pad") or any(w in hay for w in ("synth", "pad", "bell", "pluck", "str", "string", "cloud", "kalimba"))
        is_guitar = (cat == "guitar" or "guitar" in hay) and "bass guitar" not in hay
        is_str = any(w in hay for w in ("string", "hi str", "_str_", "orchestr", "choir"))
        is_bell = any(w in hay for w in ("bell", "chime", "kalimba", "mallet", "pluck"))
        is_pad = any(w in hay for w in ("pad", "cloud", "str", "string", "ambient"))

        if wants_synth_modern and not wants_keys_organic:
            if is_synth_like:
                bonus += 0.22
            if is_rhodes:
                bonus -= 0.18  # jamais de Rhodes jazz chaud par défaut sur Trap/Drill/EDM
            elif is_piano:
                bonus -= 0.06
        elif wants_keys_organic and not wants_synth_modern:
            if is_rhodes and ("rhodes" in intent_toks or "soul" in intent_toks or "soulful" in intent_toks or "jazz" in intent_toks or "boombap" in intent_toks):
                bonus += 0.24
            elif is_piano or is_guitar:
                bonus += 0.18
        if wants_guitar and is_guitar:
            bonus += 0.20
        if wants_strings and is_str:
            bonus += 0.22
        if wants_bell and role == "lead_melody" and is_bell:
            bonus += 0.20
        if wants_pad and role in ("chord_keys", "pad_atmosphere") and is_pad:
            bonus += 0.18

    return round(bonus, 4)


def _score_palette(
    items: list[dict[str, Any]],
    emotion_vector: dict[str, float],
    mood_vec: np.ndarray | None,
    lam_overlap: float = 0.28,
    lam_attack: float = 0.22,
    lam_mood: float = 0.25,
    lam_space: float = 0.18,
) -> tuple[float, dict[str, float]]:
    n_items = len(items)
    if n_items == 0:
        return 0.0, {"role_mean": 0.0, "overlap_mean": 0.0, "attack_penalty": 0.0, "mood_mean": 0.0}
    role_sum = 0.0
    mood_sum = 0.0
    for it in items:
        role_sum += 0.65 * it["role_score"] + 0.35 * it["comfort_score"]
        mv = it.get("_mood_cached")
        if mv is None:
            mv = _mood_match_score(it, emotion_vector, mood_vec)
            it["_mood_cached"] = mv
        mood_sum += mv
    role_mean = role_sum / n_items
    mood_mean = mood_sum / n_items

    ov_sum = 0.0
    ov_n = 0
    for i in range(n_items):
        ci = items[i]
        for j in range(i + 1, n_items):
            ov_sum += _weighted_bark_overlap(ci, items[j])
            ov_n += 1
    overlap_mean = (ov_sum / ov_n) if ov_n else 0.0
    att_pen = _attack_relay_penalty(items)

    space_pen = space_coherence_penalty(items)
    raw = role_mean - lam_overlap * overlap_mean - lam_attack * att_pen + lam_mood * mood_mean - lam_space * space_pen
    # Normalize cleanly to [0, 1]
    norm_score = max(0.0, min(1.0, raw / (1.0 + lam_mood)))
    return round(float(norm_score), 4), {
        "role_mean": round(role_mean, 4),
        "overlap_mean": round(overlap_mean, 4),
        "attack_penalty": round(att_pen, 4),
        "mood_mean": round(mood_mean, 4),
        "space_coherence_penalty": space_pen,
    }


def loop_bpm_penalty(cand: dict[str, Any], target_bpm: float | None, tolerance: float = 6.0) -> float:
    """v3.0 BPM lock: penalize loop candidates whose tempo is incompatible with the project.

    One-shots are never penalized (gl.sampler pitches them freely). Loops with unknown BPM
    get no penalty (benefit of the doubt). Only half/same/double tempo is allowed and the
    effective stretch must stay in [0.88, 1.14]; a loop needing a stretch beyond that is
    rejected outright (phase/transient artifacts), a small residual (> 0.05 BPM) only costs
    a penalty because an exact `time_stretch_stereo` is applied at prep time.
    """
    del tolerance  # superseded by the v3.0 ratio lock
    if target_bpm is None or not cand.get("is_loop"):
        return 0.0
    cb = cand.get("bpm")
    if cb is None:
        return 0.0
    comp = bpm_compatibility(float(target_bpm), float(cb))
    if not comp["ok"]:
        return 10.0
    if comp["needs_stretch"]:
        return 0.15
    return 0.0


def palette_slots_to_blueprint_tracks(chosen: list[dict[str, Any]], mixer_start: int = 1) -> list[dict[str, Any]]:
    """Materialize palette slots as `song_blueprint.json` `tracks[]` entries, carrying
    `root_note` (from detected `midi_root`) and `fine_tune_ct` so `gl.sampler` is never
    played from a wrong C4 assumption (systematic dissonance otherwise)."""
    tracks: list[dict[str, Any]] = []
    for i, it in enumerate(chosen):
        src = it.get("source_type")
        is_sample = src in ("sample", "project_sample") or bool(it.get("filepath"))
        fp = it.get("filepath")
        tr: dict[str, Any] = {
            "name": os.path.basename(str(fp)) if fp else f"{it['role']}:{it.get('plugin', 'gl')}",
            "role": it["role"],
            "source_type": "sample" if is_sample else "gridline_native",
            "mixer_track": mixer_start + i,
        }
        if is_sample and fp:
            tr["sample_path"] = str(fp)
        if it.get("plugin"):
            tr["plugin"] = str(it["plugin"])
        mr = it.get("midi_root")
        if is_sample and mr is not None:
            tr["root_note"] = int(mr)
        ft = it.get("recommended_fine_tune_ct")
        if is_sample and ft:
            tr["fine_tune_ct"] = int(ft)
        tracks.append(tr)
    return tracks


def propose_palettes(
    *,
    mix_profile: str = "trap_rap",
    emotion_vector: dict[str, float] | None = None,
    target_key: str = "C minor",
    roles: list[str] | None = None,
    project_samples_dir: str | None = None,
    allow_gl_synths: bool = True,
    num_palettes: int = 3,
    db_path: str = DEFAULT_DB_PATH,
    anchors_path: str = DEFAULT_ANCHORS_NPZ,
    bpm: float | None = None,
    bpm_tolerance: float = 6.0,
    note_ranges: dict[str, Any] | None = None,
    include_pool: bool = False,
    user_samples_locked: bool | None = None,
    intent: str = "",
    palette_strategy: str = "semantic",
) -> dict[str, Any]:
    """
    Build `num_palettes` (default 3) complementary, conflict-free sound palettes.
    Strictly obeys the project sample exclusivity invariant when `project_samples_dir` is set.
    v4.2: enforces F3 (`note_ranges` S_max cap), F4 (role category/nature policy),
    F5 (non-vacuous pleasantness gate + `gate_unmeasured`), F6 (`include_pool` top-8),
    and F8 (bounded score <= 1.0, hard coherence threshold >= 0.6, conditioned sample-first).
    v4.3 (D1): `intent` (texte libre, ex. "dark hypnotic minimal mental rap, muted keys") est
    projeté sur les ancres sémantiques (`semantic_anchors.npz`) et le score de chaque palette
    devient le score composite S(p) = α·S_intent + β·S_cohérence + γ·S_couverture − λ·P_fallback
    (poids dans `config/mix_profiles.json` → `palette_scoring_weights`). `palette_strategy`
    ∈ {"semantic" (défaut, composite si intent), "organic", "dark", "hybrid"} ne filtre pas
    mais oriente le thème préféré des 3 palettes proposées.
    """
    from sample_fit_guard import candidate_policy, register_fit
    from timbre_pleasantness import palette_timbre_coherence, pleasantness_gate

    ev = emotion_vector or {"valence": -0.3, "tension": 0.4, "energy": 0.7, "depth": 0.5}
    valence = float(ev.get("valence", 0.0))

    if roles is None:
        if mix_profile == "classical_ambient":
            eff_roles = ["bass_sub", "pad_atmosphere", "chord_keys", "lead_melody"]
        else:
            eff_roles = ["kick", "snare_clap", "hihat_perc", "bass_sub", "chord_keys", "lead_melody"]
    else:
        eff_roles = [r for r in roles if r in VALID_ROLES]
        if not eff_roles:
            eff_roles = ["kick", "snare_clap", "hihat_perc", "bass_sub", "chord_keys", "lead_melody"]

    has_proj_dir = bool(project_samples_dir and str(project_samples_dir).strip())
    user_locked = bool(user_samples_locked) if user_samples_locked is not None else has_proj_dir
    selection_trace: dict[str, Any] = {
        "mode": "project" if has_proj_dir else "db",
        "user_samples_locked": user_locked,
        "roles": {},
        "policy_excluded": {r: 0 for r in eff_roles},
        "range_excluded": {r: 0 for r in eff_roles},
        "pleasantness_excluded": {r: 0 for r in eff_roles},
        "gate_unmeasured": {r: 0 for r in eff_roles},
    }
    if has_proj_dir:
        sample_pool = _load_project_candidates(str(project_samples_dir), eff_roles, target_key=target_key, bpm=bpm,
                                                trace_out=selection_trace["roles"])
        # Passe 2 (relaxée) : un rôle sans aucun candidat strict récupère les candidats
        # acoustiques légitimes filtrés par des seuils trop stricts (seuil 0.18 au lieu de 0.30).
        relaxed_pool = _load_project_candidates(str(project_samples_dir), eff_roles, target_key=target_key, bpm=bpm,
                                                relax=True, trace_out=selection_trace["roles"])
        for role in eff_roles:
            if not sample_pool.get(role) and relaxed_pool.get(role):
                sample_pool[role] = relaxed_pool[role]
                selection_trace.setdefault("relaxed_roles", []).append(role)
    else:
        sample_pool = _load_sqlite_candidates(db_path, eff_roles, target_key=target_key, bpm=bpm)

    # F3 + F4 + F5 : filtrage politique catégorie/nature, registre joué (S_max), et gate d'agréabilité
    filtered_sample_pool: dict[str, list[dict[str, Any]]] = {}
    for role in eff_roles:
        kept: list[dict[str, Any]] = []
        rng = (note_ranges or {}).get(role)
        for cand in sample_pool.get(role, []):
            pol = candidate_policy(role, cand, valence=valence)
            if not pol["allowed"]:
                selection_trace["policy_excluded"][role] += 1
                continue
            range_ok = True
            if role in MELODIC_ROLES and rng:
                rf = register_fit(cand.get("midi_root"), rng, cand, role=role)
                if not (rf["ok"] or rf.get("multizone_viable")):
                    selection_trace["range_excluded"][role] += 1
                    continue
                if rf.get("needs_multizone") or rf.get("multizone_viable"):
                    cand["multizone"] = True
                range_ok = True
            g = pleasantness_gate({
                "r_harsh": cand.get("r_harsh"),
                "roughness": cand.get("roughness"),
                "peak_harsh_db": cand.get("peak_harsh_db"),
                "comfort_score": cand.get("comfort_score"),
                "centroid_hz": cand.get("centroid_hz"),
                "crest_factor_db": cand.get("crest_factor_db"),
                "sample_nature": cand.get("sample_nature"),
                "category": pol["category"],
                "inharmonicity": cand.get("inharmonicity"),
                "filepath": cand.get("filepath"),
            }, role)
            if g.get("unmeasured"):
                selection_trace["gate_unmeasured"][role] += 1
            if g["verdict"] == "SWAP":
                selection_trace["pleasantness_excluded"][role] += 1
                continue
            cand["_policy_tier"] = pol["tier"]
            cand["_policy_bonus"] = pol["bonus"]
            cand["_range_ok"] = range_ok
            cand["_gate_verdict"] = g["verdict"]
            cand["category"] = pol["category"]
            kept.append(cand)
        kept.sort(
            key=lambda x: (
                0.55 * x["role_score"] + 0.30 * x["comfort_score"] + float(x.get("_policy_bonus", 0.0))
            ),
            reverse=True,
        )
        filtered_sample_pool[role] = kept
    sample_pool = filtered_sample_pool
    selection_trace["n_sample_candidates"] = {r: len(sample_pool.get(r, [])) for r in eff_roles}
    selection_trace["total_excluded"] = (
        sum(selection_trace["policy_excluded"].values())
        + sum(selection_trace["range_excluded"].values())
        + sum(selection_trace["pleasantness_excluded"].values())
    )

    synth_pool = _build_gl_synth_pool(eff_roles, valence=valence, mix_profile=str(mix_profile or "trap_rap")) if allow_gl_synths else {r: [] for r in eff_roles}

    # v3.1 ASSET-05 + P1-4 (audit rap_ombre_134) : diagnostic de couverture — on ne bascule sur
    # un synthé natif gl.* que si AUCUN sample propre ne survit aux gates (avant : < 3, ce qui
    # faisait rejeter un vrai rimshot/perc disponible au profit de `gl.clap` :
    # `sample_available_but_not_selected`). Un vrai sample propre prime toujours sur le synthé.
    fallback_synth_reason: dict[str, str] = {}
    for role in ("kick", "bass_sub", "snare_clap", "hihat_perc"):
        clean = [c for c in sample_pool.get(role, [])]
        if len(clean) < 1 and synth_pool.get(role):
            fallback_synth_reason[role] = "insufficient_clean_samples"

    # Merge pools per role; keep samples & gl.* synths clearly distinguishable
    combined_pool: dict[str, list[dict[str, Any]]] = {}
    for role in eff_roles:
        cands = list(sample_pool.get(role, []))
        if allow_gl_synths:
            cands.extend(synth_pool.get(role, []))
        combined_pool[role] = cands

    anchors = load_semantic_anchors(anchors_path)
    mood_vec = _build_mood_anchor_vec(ev, anchors)
    # v4.3 (D1) : vecteur d'intention projeté sur les ancres sémantiques (0 PyTorch)
    intent_str = str(intent or "").strip()
    intent_toks = _intent_tokens(intent_str) if intent_str else set()
    intent_vec = build_intent_vector(intent_str, anchors) if intent_str else None
    energy = float(ev.get("energy", 0.6))
    is_aggressive_intent = bool(intent_toks & {"aggressive", "hard", "drill", "gritty", "distorted", "reese", "rock", "metal", "menacing"})
    try:
        from mix_config import palette_scoring_weights as _mc_psw
        _psw = _mc_psw()
    except Exception:
        _psw = None

    # Annoter chaque candidat avec son bonus d'intention (exposé dans candidate_pool pour prod_server)
    for role in eff_roles:
        for cand in combined_pool.get(role, []):
            cand["intent_match"] = _intent_candidate_bonus(cand, role, intent_toks, intent_vec, str(mix_profile or "trap_rap"), valence)

    palettes_out: list[dict[str, Any]] = []
    used_ids_per_role: dict[str, set[str]] = {r: set() for r in eff_roles}
    n_pal = max(1, min(int(num_palettes), 5))
    ordered_roles = sorted(
        eff_roles,
        key=lambda r: ["kick", "bass_sub", "chord_keys", "pad_atmosphere", "lead_melody", "snare_clap", "hihat_perc"].index(r)
        if r in ["kick", "bass_sub", "chord_keys", "pad_atmosphere", "lead_melody", "snare_clap", "hihat_perc"]
        else 99,
    )
    target_bpm = float(bpm) if bpm else None

    # Pré-tri par pertinence (rôle + confort + intent + policy) pour garder _greedy_select < 15 ms
    # tout en préservant l'accès aux meilleurs candidats de chaque catégorie.
    greedy_pool: dict[str, list[dict[str, Any]]] = {}
    for role in ordered_roles:
        c_list = list(combined_pool.get(role, []))
        for c in c_list:
            c.pop("_mood_cached", None)
        c_list.sort(
            key=lambda c: (
                0.50 * float(c.get("role_score", 0.5))
                + 0.25 * float(c.get("comfort_score", 0.5))
                + float(c.get("intent_match") or 0.0)
                + float(c.get("_policy_bonus") or 0.0)
            ),
            reverse=True,
        )
        greedy_pool[role] = c_list[:20]

    def _greedy_select(pref_theme: str, lam_overlap_eff: float) -> tuple[list[dict[str, Any]], list[str]]:
        chosen_l: list[dict[str, Any]] = []
        missing_l: list[str] = []
        for role in ordered_roles:
            cands = greedy_pool.get(role, [])
            if not cands:
                missing_l.append(role)
                continue
            best_cand = None
            best_val = -1e9
            for cand in cands:
                is_used = cand["id"] in used_ids_per_role[role]
                is_sample = cand["source_type"] in ("sample", "project_sample")
                policy_tier = str(cand.get("_policy_tier") or "neutral")
                policy_bonus = float(cand.get("_policy_bonus") or 0.0) if is_sample else 0.0
                sample_healthy = is_sample and policy_tier in ("preferred", "neutral") and bool(cand.get("_range_ok", True))

                # Diversity bonus across palettes (jamais de pénalité contre un vrai sample sain verrouillé)
                div_pen = 0.22 if (is_used and len(cands) > 1 and not (user_locked and sample_healthy)) else 0.0
                theme_bonus = 0.08 if cand.get("theme") == pref_theme else 0.0
                # F8 + priorité absolue aux vrais samples enregistrés (`samples/`):
                # un vrai sample sain (policy preferred/neutral + F3 valide + pleasantness PASS/FIX)
                # prime TOUJOURS sur un synthé gl.* artificiel. Les gl.* restent le repli propre
                # uniquement lorsqu'aucun vrai sample sain n'existe pour ce rôle.
                if sample_healthy:
                    theme_bonus += 0.28 if (user_locked or pref_theme == "organic") else 0.22

                mood_pen = 0.0
                if valence <= 0.1 and energy < 0.72 and not is_aggressive_intent and role in ("bass_sub", "lead_melody") and is_sample:
                    hay = f"{cand.get('timbre_card') or ''} {cand.get('filepath') or ''}".lower()
                    if any(k in hay for k in ("reese", "aggressive", "distorted", "acid", "saw", "metallic", "harsh")):
                        mood_pen = 0.35
                # §5 pénalités dureté : centroid > 4 kHz sur accords, crest > 13 dB sur tonaux
                if is_sample and role == "chord_keys" and float(cand.get("centroid_hz") or 0.0) > 4000.0:
                    mood_pen += 0.15
                if is_sample and role in MELODIC_ROLES and cand.get("crest_factor_db") is not None:
                    if float(cand["crest_factor_db"]) > 13.0:
                        mood_pen += 0.15

                intent_bonus = float(cand.get("intent_match") or 0.0)
                trial_items = chosen_l + [cand]
                trial_score, _ = _score_palette(trial_items, ev, mood_vec, lam_overlap=lam_overlap_eff)
                val = (
                    trial_score + theme_bonus + policy_bonus + intent_bonus - div_pen
                    - loop_bpm_penalty(cand, target_bpm, bpm_tolerance) - mood_pen
                )
                if val > best_val:
                    best_val = val
                    best_cand = cand
            if best_cand is not None:
                chosen_l.append(best_cand)
        return chosen_l, missing_l

    # v4.3 (D1) : `palette_strategy` (ou l'intention textuelle) oriente le thème préféré en tête de liste
    _themes = list(_PALETTE_THEMES)
    _strat_theme = {"organic": "organic", "dark": "synthetic", "hybrid": "hybrid"}.get(str(palette_strategy or "").lower())
    if not _strat_theme and intent_toks:
        if (intent_toks & {"trap", "drill", "synth", "bell", "pad", "edm", "house", "dark"}) and not (
            intent_toks & {"piano", "rhodes", "boombap", "soul", "jazz", "lofi", "acoustic"}
        ):
            _strat_theme = "synthetic"
        elif intent_toks & {"piano", "rhodes", "boombap", "soul", "jazz", "lofi", "acoustic"}:
            _strat_theme = "organic"
    if _strat_theme:
        _themes.sort(key=lambda t: 0 if t[2] == _strat_theme else 1)
    for p_idx in range(n_pal):
        if p_idx < len(_themes):
            pid, label, pref_theme = _themes[p_idx]
        else:
            pid, label, pref_theme = f"palette_{chr(65 + p_idx)}", f"Palette {chr(65 + p_idx)}", "hybrid"

        chosen, missing_roles = _greedy_select(pref_theme, lam_overlap_eff=0.28)
        timbre_coh = {"score": 1.0, "max_bark_collision": None, "pair_roughness": 0.0, "notes": []}
        try:
            timbre_coh = palette_timbre_coherence(chosen)
            # F8 + §5 : seuil dur cohérence >= 0.60 (ou collision > 0.35) -> 2e passe pénalité collision x2
            if timbre_coh["score"] < 0.60 or (
                timbre_coh.get("max_bark_collision") is not None and float(timbre_coh["max_bark_collision"]) > 0.35
            ):
                chosen2, missing2 = _greedy_select(pref_theme, lam_overlap_eff=0.56)
                coh2 = palette_timbre_coherence(chosen2)
                if coh2["score"] > timbre_coh["score"]:
                    chosen, missing_roles, timbre_coh = chosen2, missing2, coh2
        except Exception:
            pass

        for it in chosen:
            used_ids_per_role[it["role"]].add(it["id"])

        quality_score, breakdown = _score_palette(chosen, ev, mood_vec)
        quality_score = round(max(0.0, min(1.0, quality_score - (1.0 - timbre_coh["score"]) * 0.25)), 4)
        n_real = sum(1 for it in chosen if it["source_type"] in ("sample", "project_sample") and it.get("filepath"))
        n_healthy_real = sum(
            1 for it in chosen
            if it["source_type"] in ("sample", "project_sample") and it.get("filepath")
            and str(it.get("_policy_tier") or "neutral") in ("preferred", "neutral")
            and bool(it.get("_range_ok", True))
        )
        sample_ratio = round(n_real / max(1, len(chosen)), 3)
        # F8: score borné strictement dans [0, 1] ; qualité et sample_ratio publiés séparément
        bonus_sample = (0.08 if user_locked else 0.06) * (n_healthy_real / max(1, len(chosen)))
        pal_score = round(max(0.0, min(1.0, quality_score + bonus_sample)), 4)
        # v4.3 (D1) : score composite S(p) avec intention sémantique si fournie
        comp = composite_palette_score(
            {"slots": [{"role": it["role"], "source_type": it["source_type"], "filepath": it.get("filepath")}
                       for it in chosen],
             "timbre_coherence": timbre_coh},
            items=chosen, intent_vec=intent_vec, weights=_psw, sample_pool=sample_pool)
        pal_score = comp["score"] if intent_vec is not None else pal_score

        reason_parts = [
            f"Chevauchement Bark24 faible ({breakdown['overlap_mean']:.2f})"
            if breakdown["overlap_mean"] < 0.45
            else f"Chevauchement Bark24 modéré ({breakdown['overlap_mean']:.2f})",
            "relais d'attaques distinct" if breakdown["attack_penalty"] < 0.15 else "attaques proches (ajuster ADSR)",
            f"adéquation humeur {breakdown['mood_mean']:.2f}",
        ]
        slots = [
            {
                "role": it["role"],
                "source_type": it["source_type"],
                "plugin": it["plugin"],
                "filepath": it.get("filepath"),
                "category": it.get("category"),
                "sample_nature": it.get("sample_nature"),
                "inharmonicity": float(it.get("inharmonicity") or 0.0),
                "policy_tier": it.get("_policy_tier"),
                "r_harsh": it.get("r_harsh"),
                "roughness": it.get("roughness"),
                "peak_harsh_db": it.get("peak_harsh_db"),
                "crest_factor_db": it.get("crest_factor_db"),
                "fallback_reason": (
                    "no_usable_sample_in_index_for_role"
                    if it["source_type"] == "gridline_native" and sample_pool.get(it["role"])
                    else None
                ),
                "n_candidates_for_role": len(sample_pool.get(it["role"], [])),
                "detected_note": it.get("detected_note"),
                "midi_root": it.get("midi_root"),
                "recommended_fine_tune_ct": int(it.get("recommended_fine_tune_ct") or 0),
                "bpm": it.get("bpm"),
                "is_loop": bool(it.get("is_loop", False)),
                "playable_chromatically": bool(it.get("playable_chromatically", True)),
                "role_score": it["role_score"],
                "comfort_score": it["comfort_score"],
                "attack_ms": it["attack_ms"],
                "centroid_hz": it["centroid_hz"],
                "timbre_card": it["timbre_card"],
                **({"gl_dsl_hint": it["gl_dsl_hint"]} if "gl_dsl_hint" in it else {}),
            }
            for it in chosen
        ]
        palettes_out.append({
            "palette_id": pid,
            "label": label,
            "fallback_synth_reason": dict(fallback_synth_reason) if fallback_synth_reason else None,
            "theme": pref_theme,
            "score": pal_score,
            "quality_score": quality_score,
            "sample_ratio": sample_ratio,
            "score_breakdown": breakdown,
            "composite_score": comp if intent_vec is not None else None,
            "intent_score": comp.get("s_intent") if intent_vec is not None else None,
            "complementarity_reason": " ; ".join(reason_parts),
            "timbre_coherence": timbre_coh,
            "missing_roles": missing_roles,
            "slots": slots,
            "blueprint_tracks": palette_slots_to_blueprint_tracks(chosen),
        })

    palettes_out.sort(key=lambda p: p["score"], reverse=True)
    best_p = palettes_out[0] if palettes_out else None
    best_score = best_p["score"] if best_p else 0.0
    verdict = "PASS" if (best_p and not best_p["missing_roles"] and best_score >= 0.55) else "FIX"

    diag = [
        f"{len(palettes_out)} palettes générées pour profil '{mix_profile}' ({target_key}, valence={valence:+.2f}).",
        f"Meilleure option : {best_p['palette_id']} ({best_p['label']}, score={best_score:.2f}) — {best_p['complementarity_reason']}."
        if best_p else "Aucune palette complète disponible.",
    ]
    if user_locked:
        diag.append("Verrou d'exclusivité actif : 0 sample externe importé (uniquement Samples/ projet + gl.* autorisés).")

    _clap_cov = clap_coverage_ratio(sample_pool if include_pool else None, db_path=db_path)
    out_payload: dict[str, Any] = {
        "verdict": verdict,
        "score": best_score,
        "user_samples_locked": user_locked,
        "intent": str(intent or ""),
        "palette_strategy": palette_strategy,
        "clap_coverage": _clap_cov,
        "clap_off": (True if _clap_cov is not None and _clap_cov < 0.50 else False),
        "selection_trace": selection_trace,
        "mix_profile": mix_profile,
        "target_key": target_key,
        "bpm": (float(bpm) if bpm else None),
        "human_diagnosis": diag[:3],
        "next_action": {
            "code": "SELECT_PALETTE_AND_SOUNDCHECK",
            "params": {
                "recommended_palette_id": best_p["palette_id"] if best_p else None,
                "available_palette_ids": [p["palette_id"] for p in palettes_out],
            },
        },
        "palettes": palettes_out,
    }
    if include_pool:
        def _pool_for_role(role: str, limit: int = 32) -> list[dict[str, Any]]:
            cands = list(sample_pool.get(role, []))
            cands.sort(
                key=lambda c: (
                    0.50 * float(c.get("role_score", 0.5))
                    + 0.25 * float(c.get("comfort_score", 0.5))
                    + float(c.get("intent_match") or 0.0)
                ),
                reverse=True,
            )
            if len(cands) > limit:
                by_cat: dict[str, list[dict[str, Any]]] = {}
                for c in cands:
                    by_cat.setdefault(str(c.get("category") or "other"), []).append(c)
                quota = max(6, limit // max(1, len(by_cat)))
                picked: list[dict[str, Any]] = []
                seen_ids: set[str] = set()
                for cat_list in by_cat.values():
                    for c in cat_list[:quota]:
                        cid = str(c.get("id") or c.get("filepath") or "")
                        if cid not in seen_ids:
                            picked.append(c)
                            seen_ids.add(cid)
                for c in cands:
                    if len(picked) >= limit:
                        break
                    cid = str(c.get("id") or c.get("filepath") or "")
                    if cid not in seen_ids:
                        picked.append(c)
                        seen_ids.add(cid)
                cands = picked[:limit]
            return [
                {k: v for k, v in c.items() if not k.startswith("_") and k != "bark_24"}
                for c in cands
            ]

        out_payload["candidate_pool"] = {role: _pool_for_role(role) for role in eff_roles}
    return out_payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Propose 3 complementary sound palettes (0 PyTorch, < 50ms).")
    ap.add_argument("--profile", default="trap_rap")
    ap.add_argument("--key", default="C minor")
    ap.add_argument("--valence", type=float, default=-0.5)
    ap.add_argument("--tension", type=float, default=0.5)
    ap.add_argument("--energy", type=float, default=0.7)
    ap.add_argument("--depth", type=float, default=0.4)
    ap.add_argument("--roles", default="", help="Comma-separated roles.")
    ap.add_argument("--project-samples-dir", default=None)
    ap.add_argument("--no-gl-synths", action="store_true")
    ap.add_argument("--num-palettes", type=int, default=3)
    ap.add_argument("--bpm", type=float, default=0.0, help="BPM du morceau : pénalise les boucles dont le BPM détecté s'écarte (> 2x tolérance = exclues).")
    ap.add_argument("--bpm-tolerance", type=float, default=6.0)
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    args = ap.parse_args()

    roles_list = [r.strip() for r in args.roles.split(",") if r.strip()] or None
    res = propose_palettes(
        mix_profile=args.profile,
        emotion_vector={
            "valence": args.valence,
            "tension": args.tension,
            "energy": args.energy,
            "depth": args.depth,
        },
        target_key=args.key,
        roles=roles_list,
        project_samples_dir=args.project_samples_dir,
        allow_gl_synths=not args.no_gl_synths,
        num_palettes=args.num_palettes,
        bpm=(float(args.bpm) if args.bpm else None),
        bpm_tolerance=float(args.bpm_tolerance),
        db_path=args.db,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
