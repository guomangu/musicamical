#!/usr/bin/env python3
"""
Boucle d'analyse et de raffinement hiérarchique ascendante (« du plus petit au plus grand ») :
  Niveau 1 : SONS     — analyse chaque sample réel (`samples/`) vs rôle et registre des accords
  Niveau 2 : ACCORDS  — analyse chaque accord (LIL, clusters b2, voice-leading, alignement basse, S_max)
  Niveau 3 : PISTES   — analyse chaque piste vs grille harmonique & morceau (b2, apex, silences, overlaps)
  Niveau 4 : MORCEAU  — analyse la cohérence globale (structure, densité, cohérence timbrale, audio final)

Exécution 100 % locale, déterministe, 0 token LLM (< 50 ms hors rendu audio).
Écrit `<projet>/renders/diagnostics/bottom_up_report.json` et retourne un résumé compact.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Low Interval Limits (en demi-tons MIDI — norme orchestration/arrangement)
_LIL_MIN_LOW_NOTE = {
    1: 60,  # 2nde mineure
    2: 60,  # 2nde majeure
    3: 52,  # 3ce mineure
    4: 52,  # 3ce majeure
    5: 48,  # 4te juste
    6: 48,  # triton
    7: 43,  # 5te juste
}


def _load_json(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _build_catalog_index(catalog_path: str | None) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    if not catalog_path or not os.path.isfile(catalog_path):
        return idx
    cat = _load_json(catalog_path) or {}
    for entry in cat.get("samples") or []:
        fp = str(entry.get("filepath") or "")
        if fp:
            idx[os.path.normpath(os.path.abspath(fp)).lower()] = entry
            idx[os.path.basename(fp).lower()] = entry
    return idx


def _audit_tonal_sample_waveform(filepath: str, slot: str) -> list[str]:
    """Vérification directe sur le signal WAV d'un sample tonal (< 25 ms, 0 PyTorch) :
    détecte les découpes multi-notes (P85 > 0.75 st ou >14 % de trames hors pitch,
    ex. guitar_pluck_b3_440.wav : B3 puis saut E2/D3 à 280 ms), les fins coupées net
    et les attaques brusques non-fondues."""
    if slot == "bass_sub":
        return []  # les 808 ont un pitch-drop d'attaque naturel
    try:
        import librosa
        import numpy as np
        import soundfile as sf

        y, sr = sf.read(filepath, dtype="float32", always_2d=True)
        m = y.mean(axis=1)
        dur = len(m) / float(sr)
        issues: list[str] = []
        if dur < 0.28:
            issues.append(f"too_short_tonal({dur:.2f}s)")
        peak = float(np.max(np.abs(m))) + 1e-12
        end_amp = float(np.max(np.abs(m[-min(64, len(m)) :]))) / peak
        if end_amp > 0.18:
            issues.append(f"abrupt_end_click(end={end_amp:.2f})")
        if len(m) >= 2048:
            max_len = min(len(m), int(4.0 * sr))
            m_sl = m[:max_len]
            f0 = librosa.yin(m_sl, fmin=65.0, fmax=2000.0, sr=sr, frame_length=2048, hop_length=256)
            rms = librosa.feature.rms(y=m_sl, frame_length=2048, hop_length=256)[0]
            vf0 = f0[(rms > float(np.max(rms)) * 0.15) & (f0 > 68.0) & (f0 < 1950.0)]
            if len(vf0) >= 5:
                # Ignorer les 45 premières ms de transitoire d'attaque
                skip = min(len(vf0) // 3, int(0.045 * sr / 256))
                body = vf0[skip:] if len(vf0[skip:]) >= 4 else vf0
                midi_v = 69.0 + 12.0 * np.log2(np.maximum(body, 1e-6) / 440.0)
                med = float(np.median(midi_v))
                abs_folded = np.abs(((midi_v - med + 6.0) % 12.0) - 6.0)
                p85 = float(np.percentile(abs_folded, 85))
                frac_off = float(np.mean(abs_folded > 0.75))
                if p85 > 0.75 or frac_off > 0.14:
                    issues.append(f"unstable_or_multinote_pitch(p85={p85:.2f}st,off={frac_off*100:.0f}%)")
        return issues
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Niveau 1 — Analyse des SONS (du sample individuel vers le placement sur accords)
# ---------------------------------------------------------------------------
def analyze_sounds_level(
    bp: dict[str, Any],
    score_ir: dict[str, Any] | None,
    *,
    project_dir: str,
    catalog_path: str | None = None,
    pool_by_role: dict[str, list[dict[str, Any]]] | None = None,
    auto_fix: bool = True,
) -> dict[str, Any]:
    """Niveau 1 : audite chaque son du blueprint par rapport à son rôle et aux notes jouées."""
    from sample_fit_guard import (
        DEFAULT_SLOT_FOR,
        TONAL_SLOTS,
        candidate_policy,
        enforce_blueprint_sample_fit,
        notes_by_track_from_score_ir,
        register_fit,
        resolve_catalog_path,
    )
    from timbre_pleasantness import pleasantness_gate

    cat_path = catalog_path or resolve_catalog_path(project_dir, bp=bp)
    cat_idx = _build_catalog_index(cat_path)
    notes_by_track = notes_by_track_from_score_ir(score_ir) if score_ir else {}
    valence = float((bp.get("emotion_vector") or {}).get("valence", 0.0))
    mix_profile = str(bp.get("mix_profile") or "trap_rap")

    fixes: list[dict[str, Any]] = []
    if auto_fix and pool_by_role is not None:
        guard = enforce_blueprint_sample_fit(
            bp,
            notes_by_track=notes_by_track,
            catalog_path=cat_path,
            pool_by_role=pool_by_role,
            valence=valence,
            mix_profile=mix_profile,
            slot_for=DEFAULT_SLOT_FOR,
            write_diag_dir=None,
        )
        for act in guard.get("actions") or []:
            fixes.append(act)

    tracks = [t for t in (bp.get("tracks") or []) if isinstance(t, dict)]
    per_sound: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    n_real = 0

    for tr in tracks:
        name = str(tr.get("name") or tr.get("role") or "").lower()
        role = str(tr.get("role") or name).lower()
        if role == "vocals":
            continue
        slot = DEFAULT_SLOT_FOR.get(name, DEFAULT_SLOT_FOR.get(role, role))
        src_type = str(tr.get("source_type") or "")
        sp = str(tr.get("sample_path") or "")
        is_sample = src_type in ("sample", "loop") and bool(sp)
        if is_sample:
            n_real += 1

        entry = None
        if sp:
            entry = (
                cat_idx.get(os.path.normpath(os.path.abspath(sp)).lower())
                or cat_idx.get(os.path.basename(sp).lower())
            )

        info: dict[str, Any] = {
            "track": name,
            "slot": slot,
            "source_type": src_type,
            "file": os.path.basename(sp) if sp else str(tr.get("plugin") or ""),
            "root_note": tr.get("root_note"),
            "fine_tune_ct": int(tr.get("fine_tune_ct") or 0),
            "status": "PASS",
        }

        if is_sample:
            if not os.path.isfile(sp):
                info["status"] = "FAIL"
                issues.append({"track": name, "code": "sample_missing", "detail": sp})
            else:
                pol = candidate_policy(slot, {**(entry or {}), "filepath": sp}, valence=valence)
                if not pol["allowed"]:
                    info["status"] = "FAIL"
                    issues.append({"track": name, "code": "policy_forbidden", "detail": pol["reasons"]})
                if slot in TONAL_SLOTS:
                    trk_notes = notes_by_track.get(name) or notes_by_track.get(role) or []
                    rf = register_fit(tr.get("root_note"), trk_notes, {**(entry or {}), "filepath": sp}, role=slot)
                    info["s_max_st"] = rf.get("s_max")
                    info["cap_st"] = rf.get("cap")
                    if trk_notes and not rf["ok"]:
                        info["status"] = "FAIL"
                        issues.append({
                            "track": name,
                            "code": "register_out_of_cap",
                            "detail": f"s_max={rf.get('s_max')} > cap={rf.get('cap')}",
                        })
                    if entry:
                        g = pleasantness_gate({
                            "r_harsh": entry.get("r_harsh"),
                            "roughness": entry.get("roughness"),
                            "peak_harsh_db": entry.get("peak_harsh_db"),
                            "comfort_score": entry.get("comfort_score"),
                            "centroid_hz": entry.get("centroid_hz"),
                            "crest_factor_db": entry.get("crest_factor_db"),
                            "sample_nature": entry.get("sample_nature"),
                            "category": pol["category"],
                            "inharmonicity": entry.get("inharmonicity"),
                            "filepath": sp,
                        }, slot)
                        info["pleasantness"] = g["verdict"]
                        if g["verdict"] == "SWAP":
                            info["status"] = "FAIL"
                            issues.append({"track": name, "code": "pleasantness_swap", "detail": g["reasons"]})
                    wf_issues = _audit_tonal_sample_waveform(sp, slot)
                    if wf_issues:
                        info["status"] = "FAIL"
                        info["waveform_issues"] = wf_issues
                        issues.append({"track": name, "code": "tonal_waveform_defect", "detail": wf_issues})
        else:
            info["status"] = "WARN"
            issues.append({"track": name, "code": "synthetic_fallback", "detail": str(tr.get("plugin") or "")})

        per_sound.append(info)

    n_total = len(per_sound)
    n_errors = sum(1 for it in issues if it["code"] != "synthetic_fallback")
    return {
        "level": "1_sounds",
        "passed": n_errors == 0,
        "n_tracks": n_total,
        "n_real_samples": n_real,
        "real_sample_ratio": round(n_real / max(1, n_total), 3),
        "n_fixes": len(fixes),
        "fixes": fixes[:12],
        "issues": issues[:12],
        "sounds": per_sound,
    }


# ---------------------------------------------------------------------------
# Niveau 2 — Analyse des ACCORDS (chaque accord de la grille + voice leading)
# ---------------------------------------------------------------------------
def _audit_voicing(pitches: list[int]) -> list[str]:
    probs: list[str] = []
    if not pitches:
        return probs
    sp = sorted(int(p) for p in pitches)
    for a, b in zip(sp, sp[1:]):
        iv = b - a
        if iv == 1:
            probs.append(f"b2_cluster({a},{b})")
        min_lo = _LIL_MIN_LOW_NOTE.get(iv)
        if min_lo is not None and a < min_lo:
            probs.append(f"lil_violation({a},{b},iv={iv})")
    return probs


def analyze_chords_level(
    bp: dict[str, Any],
    score_ir: dict[str, Any] | None,
    *,
    auto_fix: bool = True,
) -> dict[str, Any]:
    """Niveau 2 : analyse chaque accord de `harmonic_grid` et les pistes harmoniques (`chords`/`pad`)."""
    if not score_ir:
        return {"level": "2_chords", "passed": True, "n_chords": 0, "issues": [], "fixes": []}

    grid = score_ir.get("harmonic_grid") or []
    tracks = score_ir.get("tracks") or {}

    # Récupérer la root_note du sample chords/pad pour vérifier le placement sur le registre du sample
    bp_by_name = {str(t.get("name") or "").lower(): t for t in (bp.get("tracks") or []) if isinstance(t, dict)}
    chord_roots = [
        int(bp_by_name[k]["root_note"])
        for k in ("chords", "pad", "keys", "piano")
        if k in bp_by_name and bp_by_name[k].get("root_note") is not None
    ]
    sample_center = int(round(sum(chord_roots) / len(chord_roots))) if chord_roots else 60

    issues: list[dict[str, Any]] = []
    fixes: list[dict[str, Any]] = []

    # Auditer les accords sur chaque piste harmonique (chords / pad)
    harm_tracks = [k for k in ("chords", "pad", "keys") if k in tracks and (tracks[k].get("notes") or [])]
    avg_vl_steps: list[float] = []
    total_chords_checked = 0

    for tk in harm_tracks:
        notes = tracks[tk].get("notes") or []
        by_onset: dict[float, list[dict[str, Any]]] = {}
        for n in notes:
            beat_val = n.get("grid_beat", n.get("start", n.get("start_beat", 0.0)))
            by_onset.setdefault(round(float(beat_val), 3), []).append(n)
        onsets = sorted(by_onset.keys())
        prev_voicing: list[int] | None = None
        vl_dists: list[float] = []

        for ons in onsets:
            grp = by_onset[ons]
            pitches = sorted({int(n["pitch"]) for n in grp})
            total_chords_checked += 1
            probs = _audit_voicing(pitches)

            # Vérifier si un cluster b2 ou LIL existe -> amélioration locale immédiate
            if probs and auto_fix and len(pitches) >= 2:
                try:
                    import strudel_to_score_ir as st
                    pcs = sorted({p % 12 for p in pitches})
                    better = st.optimize_voicings_lil([pcs])[0]
                    if better and len(better) == len(grp):
                        for note_obj, new_p in zip(sorted(grp, key=lambda x: int(x["pitch"])), sorted(better)):
                            note_obj["pitch"] = int(new_p)
                        pitches = sorted(int(p) for p in better)
                        new_probs = _audit_voicing(pitches)
                        fixes.append({
                            "track": tk,
                            "beat": ons,
                            "action": "revoice_lil_cluster_free",
                            "before_probs": probs,
                            "after_probs": new_probs,
                        })
                        probs = new_probs
                except Exception:
                    pass

            if probs:
                issues.append({"track": tk, "beat": ons, "code": "chord_voicing_issue", "detail": probs})

            # Distance au centre du sample chords/pad (vérification de placement sample <-> accord)
            if pitches:
                span_from_root = max(abs(p - sample_center) for p in pitches)
                if span_from_root > 12:
                    issues.append({
                        "track": tk,
                        "beat": ons,
                        "code": "chord_far_from_sample_root",
                        "detail": f"max_shift={span_from_root}st vs sample_root={sample_center}",
                    })

            if prev_voicing and pitches:
                k = min(len(prev_voicing), len(pitches))
                step = sum(abs(prev_voicing[i] - pitches[i]) for i in range(k)) / max(1, k)
                vl_dists.append(step)
            prev_voicing = pitches

        if vl_dists:
            avg_vl_steps.append(sum(vl_dists) / len(vl_dists))

    # Vérifier l'alignement fondamentale basse vs grille harmonique
    bass_notes = (tracks.get("bass") or {}).get("notes") or []
    bass_mismatch = 0
    for bn in bass_notes:
        cidx = bn.get("chord_idx")
        if isinstance(cidx, int) and 0 <= cidx < len(grid):
            seg = grid[cidx]
            pcs = set(int(x) % 12 for x in (seg.get("pcs") or []))
            if pcs and (int(bn["pitch"]) % 12) not in pcs:
                bass_mismatch += 1
    if bass_mismatch:
        issues.append({"track": "bass", "code": "bass_off_chord_tone", "detail": f"{bass_mismatch} notes"})

    avg_vl = round(sum(avg_vl_steps) / len(avg_vl_steps), 2) if avg_vl_steps else 0.0
    if avg_vl > 3.5:
        issues.append({"track": "harmony", "code": "voice_leading_rough", "detail": f"avg_step={avg_vl}st > 3.5"})

    return {
        "level": "2_chords",
        "passed": len(issues) == 0,
        "n_grid_segments": len(grid),
        "n_chords_checked": total_chords_checked,
        "avg_voice_leading_st": avg_vl,
        "sample_center_midi": sample_center,
        "n_fixes": len(fixes),
        "fixes": fixes[:12],
        "issues": issues[:12],
    }


# ---------------------------------------------------------------------------
# Niveau 3 — Analyse des PISTES (agencement des notes par piste vs morceau)
# ---------------------------------------------------------------------------
def analyze_tracks_level(
    score_ir: dict[str, Any] | None,
    *,
    auto_fix: bool = True,
) -> dict[str, Any]:
    """Niveau 3 : analyse chaque piste (mélodie, basse, harmonie, batterie) contre la grille et le morceau."""
    if not score_ir:
        return {"level": "3_tracks", "passed": True, "n_tracks": 0, "issues": [], "fixes": []}

    grid = score_ir.get("harmonic_grid") or []
    tracks = score_ir.get("tracks") or {}
    meta = score_ir.get("meta") or {}
    bpm = float(meta.get("bpm") or 120.0)
    total_bars = sum(int(s.get("bars", 0)) for s in (score_ir.get("sections") or [])) or 16
    beats_per_bar = int((meta.get("time_signature") or {}).get("numerator") or 4)
    total_beats = float(total_bars * beats_per_bar)

    fixes: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    per_track: list[dict[str, Any]] = []

    for tname, tdata in tracks.items():
        role = str(tdata.get("role") or tname).lower()
        notes = tdata.get("notes") or []
        tinfo: dict[str, Any] = {"track": tname, "role": role, "n_notes": len(notes), "status": "PASS"}

        if not notes:
            tinfo["status"] = "WARN"
            issues.append({"track": tname, "code": "empty_track", "detail": "0 notes"})
            per_track.append(tinfo)
            continue

        # 1) Overlap / note-stealing check & auto-sanitize
        start_k = "grid_beat" if ("grid_beat" in notes[0]) else ("start" if ("start" in notes[0]) else "start_beat")
        dur_k = "duration_beats" if ("duration_beats" in notes[0]) else "dur"
        sorted_n = sorted(notes, key=lambda x: (float(x.get(start_k, 0.0)), int(x.get("pitch", 60))))
        mono_role = role in ("bass", "lead")
        overlaps = 0
        for a, b in zip(sorted_n, sorted_n[1:]):
            a_start = float(a.get(start_k, 0.0))
            a_dur = float(a.get(dur_k, 0.25))
            b_start = float(b.get(start_k, 0.0))
            same_pitch = int(a["pitch"]) == int(b["pitch"])
            if (mono_role or same_pitch) and (a_start + a_dur) > b_start + 1e-3:
                overlaps += 1
                if auto_fix:
                    guard_beats = 0.010 * (bpm / 60.0)  # 10 ms anti note-stealing
                    a[dur_k] = round(max(0.05, b_start - a_start - guard_beats), 4)
        if overlaps:
            if auto_fix:
                fixes.append({"track": tname, "action": "sanitize_overlaps", "count": overlaps})
            else:
                tinfo["status"] = "FAIL"
                issues.append({"track": tname, "code": "note_overlaps", "detail": f"{overlaps} overlaps"})

        # 2) Registre spécifique par rôle
        pitches = [int(n["pitch"]) for n in notes]
        if role == "bass":
            oob = sum(1 for p in pitches if p < 31 or p > 52)
            if oob:
                if auto_fix:
                    for n in notes:
                        p = int(n["pitch"])
                        while p < 31:
                            p += 12
                        while p > 48:
                            p -= 12
                        n["pitch"] = p
                    fixes.append({"track": tname, "action": "clamp_bass_register", "count": oob})
                else:
                    tinfo["status"] = "FAIL"
                    issues.append({"track": tname, "code": "bass_register_oob", "detail": f"{oob} notes"})
            tinfo["range"] = [min(int(n["pitch"]) for n in notes), max(int(n["pitch"]) for n in notes)]

        elif role in ("lead", "counter"):
            apex = max(pitches)
            tinfo["apex_midi"] = apex
            if apex > 74:
                if auto_fix:
                    cnt = 0
                    for n in notes:
                        while int(n["pitch"]) > 74:
                            n["pitch"] = int(n["pitch"]) - 12
                            cnt += 1
                    fixes.append({"track": tname, "action": "clamp_lead_apex_74", "count": cnt})
                    tinfo["apex_midi"] = max(int(n["pitch"]) for n in notes)
                else:
                    tinfo["status"] = "FAIL"
                    issues.append({"track": tname, "code": "lead_apex_above_74", "detail": f"apex={apex}"})

            # Frottements b2 contre l'accord courant
            b2_clashes = 0
            for n in notes:
                cidx = n.get("chord_idx")
                if isinstance(cidx, int) and 0 <= cidx < len(grid):
                    cpcs = [int(x) % 12 for x in (grid[cidx].get("pcs") or [])]
                    pc = int(n["pitch"]) % 12
                    if pc not in cpcs and any((pc - c) % 12 in (1, 11) for c in cpcs):
                        b2_clashes += 1
                        if auto_fix and cpcs:
                            best_p = min(
                                (int(n["pitch"]) + d for d in (-1, 1, -2, 2) if (int(n["pitch"]) + d) % 12 in cpcs),
                                key=lambda cand: abs(cand - int(n["pitch"])),
                                default=int(n["pitch"]),
                            )
                            n["pitch"] = best_p
            if b2_clashes:
                if auto_fix:
                    fixes.append({"track": tname, "action": "resolve_b2_clashes", "count": b2_clashes})
                else:
                    tinfo["status"] = "FAIL"
                    issues.append({"track": tname, "code": "lead_b2_clashes", "detail": f"{b2_clashes} notes"})

            # Ratio de respiration (silences) et mouvement conjoint (stepwise)
            active_beats = sum(float(n.get(dur_k, 0.25)) for n in notes)
            rest_ratio = max(0.0, min(1.0, 1.0 - active_beats / max(total_beats, 1.0)))
            tinfo["rest_ratio"] = round(rest_ratio, 3)
            if len(pitches) >= 2:
                ivs = [abs(b - a) for a, b in zip(pitches, pitches[1:])]
                stepwise = sum(1 for iv in ivs if iv <= 3) / len(ivs)
                tinfo["stepwise_ratio"] = round(stepwise, 3)

        per_track.append(tinfo)

    n_fail = sum(1 for it in issues if it["code"] != "empty_track")
    return {
        "level": "3_tracks",
        "passed": n_fail == 0,
        "n_tracks": len(per_track),
        "n_fixes": len(fixes),
        "fixes": fixes[:12],
        "issues": issues[:12],
        "tracks": per_track,
    }


# ---------------------------------------------------------------------------
# Niveau 4 — Analyse du MORCEAU GLOBAL (cohérence macro, timbrale & audio)
# ---------------------------------------------------------------------------
def analyze_song_level(
    bp: dict[str, Any],
    score_ir: dict[str, Any] | None,
    plan: dict[str, Any] | None,
    *,
    project_dir: str,
    catalog_path: str | None = None,
    master_wav: str | None = None,
) -> dict[str, Any]:
    """Niveau 4 : cohérence globale du morceau (structure, densité, timbres, audio final si rendu)."""
    from sample_fit_guard import DEFAULT_SLOT_FOR, resolve_catalog_path
    from timbre_pleasantness import palette_timbre_coherence

    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    # 1) Cohérence structurelle (sections non vides, arc d'énergie, plafond cognitif <= 8 pistes simultanées)
    sections = (plan or {}).get("sections") or (score_ir or {}).get("sections") or bp.get("structure") or []
    metrics["n_sections"] = len(sections)
    metrics["total_bars"] = sum(int(s.get("bars", 0)) for s in sections)
    empty_secs = [s.get("name") or s.get("section") for s in sections if "active_roles" in s and not s["active_roles"]]
    if empty_secs:
        issues.append({"code": "empty_sections", "detail": empty_secs})
    max_simultaneous = max((len(s.get("active_roles") or []) for s in sections), default=0)
    metrics["max_simultaneous_tracks"] = max_simultaneous
    if max_simultaneous > 8:
        issues.append({"code": "cognitive_ceiling_exceeded", "detail": f"{max_simultaneous} > 8 pistes simultanées"})

    # 2) Cohérence timbrale inter-pistes (Bark24 + relais d'attaques)
    cat_path = catalog_path or resolve_catalog_path(project_dir, bp=bp)
    cat_idx = _build_catalog_index(cat_path)
    pal_items: list[dict[str, Any]] = []
    used_files: list[str] = []
    for tr in (bp.get("tracks") or []):
        if not isinstance(tr, dict):
            continue
        name = str(tr.get("name") or tr.get("role") or "").lower()
        slot = DEFAULT_SLOT_FOR.get(name, str(tr.get("role") or name).lower())
        sp = str(tr.get("sample_path") or "")
        if sp:
            used_files.append(os.path.normpath(os.path.abspath(sp)).lower())
            ent = cat_idx.get(os.path.normpath(os.path.abspath(sp)).lower()) or cat_idx.get(os.path.basename(sp).lower())
            if ent:
                pal_items.append({
                    "role": slot,
                    "bark_24": ent.get("bark_24"),
                    "centroid_hz": ent.get("centroid_hz"),
                    "attack_ms": ent.get("attack_ms"),
                })
    if len(used_files) != len(set(used_files)):
        issues.append({"code": "duplicate_sample_across_tracks", "detail": "2 pistes partagent le même fichier"})
    if len(pal_items) >= 2:
        coh = palette_timbre_coherence(pal_items)
        metrics["timbre_coherence_score"] = coh.get("score")
        metrics["max_bark_collision"] = coh.get("max_bark_collision")
        metrics["pair_roughness"] = coh.get("pair_roughness")
        # Kick/808 partagent naturellement la bande sub (gérée par sidechain BUS Bass) :
        # on ne bloque au niveau morceau que sur une vraie rugosité inter-pistes (> 0.15)
        # ou une collision spectrale critique (score < 0.30).
        if float(coh.get("score", 1.0)) < 0.30 or float(coh.get("pair_roughness", 0.0)) > 0.15:
            issues.append({"code": "low_timbre_coherence", "detail": coh})

    # 3) Cohérence audio globale (si le rendu master existe)
    wav_path = master_wav or os.path.join(project_dir, "renders", "master.wav")
    if os.path.isfile(wav_path):
        try:
            import numpy as np
            import soundfile as sf
            y, sr = sf.read(wav_path, dtype="float32", always_2d=True)
            peak = float(np.max(np.abs(y)))
            peak_db = round(20.0 * math.log10(max(peak, 1e-9)), 2)
            rms = float(np.sqrt(np.mean(y ** 2)))
            rms_db = round(20.0 * math.log10(max(rms, 1e-9)), 2)
            corr = float(np.corrcoef(y[:, 0], y[:, 1])[0, 1]) if y.shape[1] >= 2 and np.std(y[:, 0]) > 1e-7 and np.std(y[:, 1]) > 1e-7 else 1.0
            mono = y.mean(axis=1)
            n_fft = 8192
            sp_m = np.abs(np.fft.rfft(mono[: min(len(mono), sr * 30)] * np.hanning(min(len(mono), sr * 30)), n=n_fft)) ** 2
            fr_m = np.fft.rfftfreq(n_fft, 1.0 / sr)
            tot_e = float(np.sum(sp_m)) + 1e-12
            harsh_pct = round(100.0 * float(np.sum(sp_m[(fr_m >= 2000.0) & (fr_m <= 5000.0)])) / tot_e, 2)
            metrics["audio"] = {
                "duration_s": round(len(y) / float(sr), 2),
                "peak_dbfs": peak_db,
                "rms_dbfs": rms_db,
                "stereo_corr": round(corr, 3),
                "harsh_2_5k_pct": harsh_pct,
            }
            if peak_db > -0.3:
                issues.append({"code": "master_clipping_risk", "detail": f"peak={peak_db} dBFS"})
            if corr < 0.0:
                issues.append({"code": "master_antiphase", "detail": f"stereo_corr={corr:.3f}"})
            if harsh_pct > 18.0:
                issues.append({"code": "master_harsh_midhigh", "detail": f"harsh_2_5k={harsh_pct}%"})
        except Exception as exc:
            metrics["audio_error"] = str(exc)[:120]

    return {
        "level": "4_song",
        "passed": len(issues) == 0,
        "metrics": metrics,
        "issues": issues[:12],
    }


# ---------------------------------------------------------------------------
# Boucle complète ascendante : Sons -> Accords -> Pistes -> Morceau
# ---------------------------------------------------------------------------
def run_bottom_up_loop(
    project_dir: str,
    *,
    pool_by_role: dict[str, list[dict[str, Any]]] | None = None,
    catalog_path: str | None = None,
    max_iterations: int = 2,
    auto_fix: bool = True,
    write_report: bool = True,
) -> dict[str, Any]:
    """Exécute la boucle hiérarchique ascendante complète (1_sounds -> 2_chords -> 3_tracks -> 4_song).
    Sauvegarde les améliorations locales sur `song_blueprint.json` et `score_ir.json` (0 token LLM)."""
    proj = os.path.abspath(os.path.expanduser(project_dir))
    bp_path = os.path.join(proj, "song_blueprint.json")
    ir_path = os.path.join(proj, "score_ir.json")
    plan_path = os.path.join(proj, "arrangement_plan.json")

    bp = _load_json(bp_path) or {}
    score_ir = _load_json(ir_path)
    plan = _load_json(plan_path)

    total_fixes = 0
    iterations_run = 0
    levels: dict[str, Any] = {}

    for it in range(1, max(1, int(max_iterations)) + 1):
        iterations_run = it
        l1 = analyze_sounds_level(
            bp, score_ir, project_dir=proj, catalog_path=catalog_path,
            pool_by_role=pool_by_role, auto_fix=auto_fix,
        )
        l2 = analyze_chords_level(bp, score_ir, auto_fix=auto_fix)
        l3 = analyze_tracks_level(score_ir, auto_fix=auto_fix)
        l4 = analyze_song_level(bp, score_ir, plan, project_dir=proj, catalog_path=catalog_path)
        levels = {"1_sounds": l1, "2_chords": l2, "3_tracks": l3, "4_song": l4}

        it_fixes = int(l1["n_fixes"]) + int(l2["n_fixes"]) + int(l3["n_fixes"])
        total_fixes += it_fixes
        all_passed = bool(l1["passed"] and l2["passed"] and l3["passed"] and l4["passed"])
        if all_passed or it_fixes == 0:
            break

    # Persister les améliorations locales (0 token LLM)
    if auto_fix and total_fixes > 0:
        if bp and os.path.isfile(bp_path):
            _save_json(bp_path, bp)
        if score_ir and os.path.isfile(ir_path):
            _save_json(ir_path, score_ir)

    passed_count = sum(1 for lv in levels.values() if lv.get("passed"))
    overall_passed = passed_count == 4
    report = {
        "ok": True,
        "passed": overall_passed,
        "levels_passed": f"{passed_count}/4",
        "iterations": iterations_run,
        "total_fixes": total_fixes,
        "summary_line": f"BU={'PASS' if overall_passed else 'WARN'}({passed_count}/4,fix={total_fixes})",
        "levels": levels,
    }
    if write_report:
        diag_path = os.path.join(proj, "renders", "diagnostics", "bottom_up_report.json")
        _save_json(diag_path, report)
        report["report_path"] = diag_path
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Boucle d'analyse hiérarchique ascendante (Sons -> Accords -> Pistes -> Morceau).")
    ap.add_argument("project_dir", help="Dossier projet (contenant song_blueprint.json / score_ir.json).")
    ap.add_argument("--no-fix", action="store_true", help="Mode audit seul sans correction locale.")
    ap.add_argument("--max-iterations", type=int, default=2, help="Nombre max d'itérations de la boucle.")
    args = ap.parse_args()
    rep = run_bottom_up_loop(args.project_dir, auto_fix=not args.no_fix, max_iterations=args.max_iterations)
    print(json.dumps({k: v for k, v in rep.items() if k != "levels"} | {
        "levels_status": {k: {"passed": v.get("passed"), "n_issues": len(v.get("issues") or [])} for k, v in rep["levels"].items()}
    }, ensure_ascii=False, indent=2))
    sys.exit(0 if rep["passed"] else 1)


if __name__ == "__main__":
    main()
