#!/usr/bin/env python3
"""
v4.0 `strudel_to_score_ir.py` — pont « Strudel headless -> ScoreIR » (couche 2, 0 token LLM).

Pipeline : `composition.strudel` --(node scripts/strudel_eval.mjs, queryArc, seed)--> Haps JSON
--> pistes plates par rôle atomique --> garde-fous déterministes (registre 808 [31,48],
apex Lead <= 74, voice leading + LIL sur les accords, enforce_frequency_inertia)
--> `ScoreIR` --> `autofix_score_ir` + `evaluate_score_ir` + groove + clamp frontière 20 ms
+ `sanitize_note_overlaps` (`compile_score_ir_to_notes`) --> `score_ir.json`
+ `arrangement_plan.json` (motifs plats par section) + `FullCoverageCoreValidator`.

Ensuite le pipeline v3.x inchangé reprend la main : `compile_song.py <projet>` compile le
`arrangement_plan.json` généré vers le `.gridline` (gl_dsl, bus 17..22, mix, quality gate).

Usage : `python3 scripts/strudel_to_score_ir.py <project_dir> [--strudel composition.strudel]
[--haps PATH] [--bars N] [--seed S] [--eval] [--no-plan] [--out score_ir.json]`
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from groove_lib import enforce_frequency_inertia  # noqa: E402
from score_ir import (  # noqa: E402
    LOW_INTERVAL_LIMIT,
    MAJOR_SCALE,
    SCALES,
    TONIC_TO_PC,
    NoteEventIR,
    ScoreIR,
    TrackIR,
    _infer_note_role,
    apply_groove_to_score_ir,
    autofix_score_ir,
    classify_role,
    compile_score_ir_to_notes,
    evaluate_score_ir,
    parse_key_tonic_pc,
    resolve_mode,
    segment_index_for_beat,
)
from full_coverage_validator import validate_full_score_ir  # noqa: E402

BASS_REGISTER = (31, 48)
LEAD_APEX_MAX = 74
MUTATION_RATIO_MAX = 0.25  # v4.1 : au-delà, la piste est « rabotée » -> ERR_OVER_REPAIRED
CHORDS_REGISTER = (48, 72)

DRUM_PITCH_BY_NAME = (
    ("kick", 36), ("bd", 36), ("snare", 38), ("cp", 39), ("clap", 39), ("sidestick", 37),
    ("rim", 37), ("openhat", 46), ("ohat", 46), ("hat", 42), ("hh", 42), ("perc", 44),
    ("tom", 45), ("crash", 49), ("ride", 51), ("shaker", 70), ("fx", 60), ("riser", 60),
)

_CHORD_EXTS: list[tuple[str, list[int]]] = [
    ("maj9", [0, 4, 7, 11, 2]), ("maj7", [0, 4, 7, 11]), ("m7b5", [0, 3, 6, 10]),
    ("min7", [0, 3, 7, 10]), ("m7", [0, 3, 7, 10]), ("dim7", [0, 3, 6, 9]),
    ("sus4", [0, 5, 7]), ("sus2", [0, 2, 7]), ("dim", [0, 3, 6]), ("aug", [0, 4, 8]),
    ("min6", [0, 3, 7, 9]), ("m6", [0, 3, 7, 9]), ("6", [0, 4, 7, 9]),
    ("9", [0, 4, 7, 10, 2]), ("7", [0, 4, 7, 10]),
]
_MINOR_EXTS = {"m", "min", "-", "min7", "m7", "m6", "min6", "m7b5", "dim", "dim7"}


def parse_chord_symbol(symbol: str) -> dict[str, Any] | None:
    s = str(symbol).strip()
    m = re.match(r"^([A-Ga-g])([#b]*)(.*)$", s)
    if not m:
        return None
    root_pc = TONIC_TO_PC.get(m.group(1).upper() + m.group(2))
    if root_pc is None:
        return None
    ext = m.group(3).strip()
    iv: list[int] | None = None
    if ext in ("", "maj", "M"):
        iv = [0, 4, 7]
    elif ext in ("m", "min", "-"):
        iv = [0, 3, 7]
    else:
        low = ext.lower()
        for name, intervals in _CHORD_EXTS:
            if low == name.lower():
                iv = intervals
                break
    if iv is None:
        iv = [0, 4, 7] if ext[:1] not in ("m",) else [0, 3, 7]
    pcs = sorted({(root_pc + i) % 12 for i in iv})
    minor = ext.lower() in _MINOR_EXTS
    return {"root_pc": root_pc, "chord_pcs": pcs, "quality": "min" if minor else "maj", "symbol": s}


_ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii"]


def symbol_to_roman(symbol: str, tonic_pc: int) -> str:
    parsed = parse_chord_symbol(symbol)
    if parsed is None:
        return "i"
    offset = (parsed["root_pc"] - tonic_pc) % 12
    best: tuple[int, int] | None = None
    for alter in (0, -1, 1, -2, 2):
        for deg in range(7):
            if (MAJOR_SCALE[deg] + alter) % 12 == offset:
                best = (deg, alter)
                break
        if best:
            break
    if best is None:
        best = (0, 0)
    deg, alter = best
    ext = re.match(r"^[A-Ga-g][#b]*(.*)$", str(symbol).strip()).group(1).strip().lower()
    majorish = ext.lower() not in _MINOR_EXTS
    roman = _ROMAN[deg].upper() if majorish else _ROMAN[deg]
    prefix = "b" * -alter if alter < 0 else "#" * alter
    suffix = ""
    if ext in ("maj7", "maj9"):
        suffix = ext.upper().replace("MAJ", "maj")
    elif ext in ("m7", "min7"):
        suffix = "7"
    elif ext in ("7", "9", "6", "m6", "min6", "sus4", "sus2", "dim", "dim7", "aug", "m7b5"):
        suffix = ext
    return f"{prefix}{roman}{suffix}"


def fold_into_register(pitch: int, lo: int, hi: int) -> int:
    p = int(pitch)
    while p < lo:
        p += 12
    while p > hi:
        p -= 12
    return p


def drum_pitch_for(track_name: str, sample: str = "") -> int:
    low = f"{track_name} {sample}".lower()
    for key, pitch in DRUM_PITCH_BY_NAME:
        if key in low:
            return pitch
    return 42


def _clamp_vel(v: float) -> int:
    return max(1, min(127, int(round(v))))


def haps_to_tracks(haps: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[tuple[float, float]]], list[str | None]]:
    bpb = int(haps.get("beats_per_bar") or 4)
    bars = int(haps.get("bars") or 16)
    tracks: dict[str, list[dict[str, Any]]] = {}
    cutoffs: dict[str, list[tuple[float, float]]] = {}
    progression: list[str | None] = list(haps.get("meta", {}).get("progression") or [None] * bars)
    while len(progression) < bars:
        progression.append(progression[-1] if progression else None)
    progression = progression[:bars]
    for name, tr in (haps.get("tracks") or {}).items():
        notes: list[dict[str, Any]] = []
        for ev in tr.get("events") or []:
            start_beats = float(ev.get("start", 0.0)) * bpb
            dur_beats = max(0.02, (float(ev.get("end", 0.0)) - float(ev.get("start", 0.0))) * bpb)
            gain = float(ev.get("gain", 1.0) or 1.0)
            vel = max(20, _clamp_vel(gain * 110.0))
            co = ev.get("cutoff", ev.get("lpf"))
            if co is not None:
                cutoffs.setdefault(name, []).append((start_beats, float(co)))
            if ev.get("notes"):
                for p in ev["notes"]:
                    notes.append({"start": round(start_beats, 5), "length": round(dur_beats, 5),
                                  "pitch": int(p), "velocity": vel})
            elif ev.get("chord"):
                notes.append({"start": round(start_beats, 5), "length": round(dur_beats, 5),
                              "pitch": -1, "velocity": vel, "chord_symbol": str(ev["chord"])})
            elif ev.get("s"):
                notes.append({"start": round(start_beats, 5), "length": round(dur_beats, 5),
                              "pitch": drum_pitch_for(name, str(ev["s"])), "velocity": vel})
        notes.sort(key=lambda n: (n["start"], n["pitch"]))
        tracks[name] = notes
    return tracks, cutoffs, progression


def build_grid(progression: list[str | None], key: str, mode: str, bpb: int, total_bars: int) -> list[dict[str, Any]]:
    tonic_pc = parse_key_tonic_pc(key) or 0
    scale = SCALES.get(mode) or SCALES["minor"]
    scale_pcs = sorted({(tonic_pc + iv) % 12 for iv in scale})
    grid: list[dict[str, Any]] = []
    idx = 0
    last_sym = None
    for bar in range(total_bars):
        sym = progression[bar] if bar < len(progression) else None
        if sym:
            last_sym = sym
        parsed = parse_chord_symbol(last_sym) if last_sym else None
        if parsed is None:
            parsed = {"root_pc": tonic_pc, "chord_pcs": sorted({(tonic_pc + i) % 12 for i in [0, 3, 7]}), "symbol": "i"}
        root_pc = parsed["root_pc"]
        chord_pcs = parsed["chord_pcs"]
        tension = sorted({pc for pc in scale_pcs if pc not in chord_pcs and min((pc - root_pc) % 12, (root_pc - pc) % 12) == 1})
        seg_scale_pcs = set(scale_pcs)
        if (tonic_pc + 11) % 12 in chord_pcs:
            seg_scale_pcs.add((tonic_pc + 11) % 12)
        grid.append({
            "chord_idx": idx, "start_beat": round(bar * bpb, 5), "end_beat": round((bar + 1) * bpb, 5),
            "symbol": parsed.get("symbol", str(last_sym)), "root_pc": root_pc, "bass_pc": root_pc,
            "chord_pcs": chord_pcs, "scale_pcs": sorted(seg_scale_pcs), "modal_tension_pcs": tension,
        })
        idx += 1
    return grid


def _lil_bad(pitches: list[int]) -> bool:
    ps = sorted(pitches)
    lowest = ps[0]
    for a, b in zip(ps, ps[1:]):
        limit = LOW_INTERVAL_LIMIT.get(b - a)
        if limit is not None and lowest < limit:
            return True
    return False


def optimize_voicings_lil(pcs_list: list[list[int]], lo: int = CHORDS_REGISTER[0], hi: int = CHORDS_REGISTER[1]) -> list[list[int]]:
    from itertools import product

    def candidates(pcs: list[int]) -> list[list[int]]:
        pools = [[m for m in range(lo, hi + 1) if m % 12 == pc] for pc in pcs]
        res = []
        for combo in product(*pools):
            s = sorted(combo)
            if s[-1] - s[0] <= 16 and len(set(s)) == len(pcs) and not _lil_bad(s):
                tight_low = any((s[k + 1] - s[k] <= 1 and s[k] < 57) for k in range(len(s) - 1))
                if not tight_low:
                    res.append(s)
        return res

    all_cands = [candidates(pcs) for pcs in pcs_list]
    for i, c in enumerate(all_cands):
        if not c:
            pools = [[m for m in range(lo, hi + 1) if m % 12 == pc] for pc in pcs_list[i]]
            all_cands[i] = [sorted(x) for x in product(*pools) if len(set(x)) == len(pcs_list[i])]
    mid = (lo + hi) / 2.0
    chosen = [min(all_cands[0], key=lambda c: abs(sum(c) / len(c) - mid))]
    for cands in all_cands[1:]:
        prev = chosen[-1]
        chosen.append(min(cands, key=lambda c: sum(min(abs(n - p) for p in prev) for n in c) / len(c)))
    return chosen


def dedup_mono_onsets(tracks: dict[str, list[dict[str, Any]]]) -> int:
    n = 0
    for name, notes in list(tracks.items()):
        if classify_role(name) not in ("bass", "lead", "vocals"):
            continue
        groups: dict[float, list[dict[str, Any]]] = {}
        for ev in notes:
            groups.setdefault(round(float(ev["start"]), 4), []).append(ev)
        out: list[dict[str, Any]] = []
        for onset, evs in groups.items():
            if len(evs) > 1:
                keep = min(evs, key=lambda e: int(e["pitch"]))
                n += len(evs) - 1
                out.append(keep)
            else:
                out.append(evs[0])
        out.sort(key=lambda e: (e["start"], e["pitch"]))
        tracks[name] = out
    return n


def apply_register_guards(tracks: dict[str, list[dict[str, Any]]], grid: list[dict[str, Any]], bpb: int) -> dict[str, int]:
    report = {"bass_register_clamped": 0, "lead_apex_clamped": 0, "chords_revoiced": 0,
              "mono_onset_dedup": dedup_mono_onsets(tracks)}
    for name, notes in tracks.items():
        role = classify_role(name)
        if role == "bass":
            for ev in notes:
                if ev.get("pitch", 0) < 0:
                    continue
                newp = fold_into_register(int(ev["pitch"]), *BASS_REGISTER)
                if newp != int(ev["pitch"]):
                    report["bass_register_clamped"] += 1
                    ev["pitch"] = newp
        elif role == "lead":
            for ev in notes:
                if ev.get("pitch", 0) < 0:
                    continue
                newp = int(ev["pitch"])
                while newp > LEAD_APEX_MAX:
                    newp -= 12
                if newp != int(ev["pitch"]):
                    report["lead_apex_clamped"] += 1
                    ev["pitch"] = newp
        elif role == "chords":
            groups: dict[float, list[dict[str, Any]]] = {}
            for ev in notes:
                groups.setdefault(round(float(ev["start"]), 4), []).append(ev)
            onsets = sorted(groups)
            pcs_list: list[list[int]] = []
            for onset in onsets:
                cidx = segment_index_for_beat(grid, onset)
                seg = grid[cidx] if 0 <= cidx < len(grid) else (grid[0] if grid else {"chord_pcs": [0, 4, 7], "root_pc": 0})
                pcs_list.append(list(seg.get("chord_pcs") or [0, 4, 7]))
            voicings = optimize_voicings_lil(pcs_list)
            for onset, voicing in zip(onsets, voicings):
                evs = sorted(groups[onset], key=lambda e: int(e["pitch"]) if e.get("pitch", 0) >= 0 else 0)
                if len(evs) != len(voicing):
                    for ev in evs:
                        if ev.get("pitch", 0) >= 0:
                            ev["pitch"] = fold_into_register(int(ev["pitch"]), *CHORDS_REGISTER)
                    continue
                for ev, p in zip(evs, voicing):
                    if ev.get("pitch", -1) != p:
                        report["chords_revoiced"] += 1
                    ev["pitch"] = int(p)
                    ev.pop("chord_symbol", None)
    for name, notes in tracks.items():
        if classify_role(name) == "bass":
            tracks[name] = enforce_frequency_inertia([{k: v for k, v in ev.items() if k != "chord_symbol"} for ev in notes], bpm=120.0)
    return report


def expand_chord_symbols(tracks: dict[str, list[dict[str, Any]]], grid: list[dict[str, Any]]) -> int:
    n = 0
    for name, notes in list(tracks.items()):
        if classify_role(name) != "chords":
            continue
        out: list[dict[str, Any]] = []
        for ev in notes:
            if "chord_symbol" not in ev and ev.get("pitch", -1) >= 0:
                out.append(ev)
                continue
            cidx = segment_index_for_beat(grid, float(ev["start"]))
            seg = grid[cidx] if 0 <= cidx < len(grid) else (grid[0] if grid else {"chord_pcs": [0, 4, 7]})
            pcs = list(seg.get("chord_pcs") or [0, 4, 7])
            for pc in pcs:
                base = 60 + ((pc - 60) % 12)
                out.append({"start": ev["start"], "length": ev["length"],
                            "pitch": fold_into_register(base, *CHORDS_REGISTER), "velocity": ev.get("velocity", 80)})
                n += 1
        out.sort(key=lambda e: (e["start"], e["pitch"]))
        tracks[name] = out
    return n


def fix_lead_b2_clashes(score: ScoreIR) -> int:
    n = 0
    grid = score.harmonic_grid
    for tir in score.tracks.values():
        if tir.role not in ("lead", "counter"):
            continue
        for note in tir.notes:
            cidx = note.chord_idx if 0 <= note.chord_idx < len(grid) else segment_index_for_beat(grid, note.grid_beat)
            seg = grid[cidx] if 0 <= cidx < len(grid) else (grid[0] if grid else {})
            chord_pcs = set(seg.get("chord_pcs", []))
            scale_pcs = set(seg.get("scale_pcs", []))
            pc = int(note.pitch) % 12
            if not chord_pcs or not any(min((pc - c) % 12, (c - pc) % 12) == 1 for c in chord_pcs):
                continue
            best: tuple[int, int] | None = None
            for cand in sorted(scale_pcs):
                if cand == pc:
                    continue
                if any(min((cand - c) % 12, (c - cand) % 12) == 1 for c in chord_pcs):
                    continue
                d = min((cand - pc) % 12, (pc - cand) % 12)
                if best is None or d < best[0]:
                    best = (d, cand)
            if best is None:
                continue
            delta = best[1] - pc
            if delta > 6:
                delta -= 12
            if delta < -6:
                delta += 12
            new_pitch = int(note.pitch) + delta
            while new_pitch > LEAD_APEX_MAX:
                new_pitch -= 12
            while new_pitch < 48:
                new_pitch += 12
            if new_pitch != int(note.pitch) and 0 <= new_pitch <= 127:
                note.pitch = new_pitch
                n += 1
    return n


def build_score_ir_from_strudel(
    haps: dict[str, Any],
    blueprint: dict[str, Any] | None,
) -> tuple[ScoreIR, dict[str, Any]]:
    meta = haps.get("meta") or {}
    bp = blueprint or {}
    bpb = int(haps.get("beats_per_bar") or meta.get("time_signature", {}).get("numerator") or 4)
    denom = int((meta.get("time_signature") or {}).get("denominator") or 4)
    bars = int(haps.get("bars") or 16)
    bpm = float(meta.get("bpm") or bp.get("bpm") or 120.0)
    key = str(meta.get("key") or bp.get("key") or "C")
    mode = str(meta.get("mode") or (bp.get("harmonic_core") or {}).get("mode") or "minor")
    mode = resolve_mode({"mode": mode}, key)
    valence = float(meta.get("valence", (bp.get("emotion_vector") or {}).get("valence", 0.0)))
    seed = int(haps.get("seed") or bp.get("seed") or 42)

    tracks, cutoffs, progression = haps_to_tracks(haps)
    grid = build_grid(progression, key, mode, bpb, bars)
    n_chord_notes = expand_chord_symbols(tracks, grid)
    # v4.1 : la mutation se mesure APRES expansion des accords (by design), AVANT les garde-fous
    pre_pitches = {k: sorted(int(n["pitch"]) for n in v) for k, v in tracks.items()}
    guard_report = apply_register_guards(tracks, grid, bpb)
    guard_report["chord_symbols_expanded"] = n_chord_notes
    post_pitches = {k: sorted(int(n["pitch"]) for n in v) for k, v in tracks.items()}
    mutation = {}
    for k in tracks:
        pre, post = pre_pitches.get(k, []), post_pitches.get(k, [])
        if not pre:
            continue
        # v4.1 : seules les mutations d'octave (>= 12 st, ex. octave-folds) et les suppressions
        # comptent comme « rabotage » ; le revoicing LIL intra-registre est by design.
        shifted = sum(1 for a, b in zip(pre, post) if abs(a - b) >= 12)
        removed = abs(len(pre) - len(post))
        mutation[k] = round((shifted + removed) / len(pre), 4)

    score = ScoreIR(
        bpm=bpm,
        time_signature={"numerator": bpb, "denominator": denom},
        key=key, mode=mode, valence=valence,
        harmonic_grid=grid,
    )
    for name, notes in tracks.items():
        role = classify_role(name)
        mono = role in ("bass", "lead", "vocals")
        tir = TrackIR(role=role, monophonic=mono, timbre_class="strudel")
        for ev in notes:
            gb = float(ev["start"])
            cidx = segment_index_for_beat(grid, gb)
            seg = grid[cidx] if 0 <= cidx < len(grid) else grid[0]
            pitch = int(ev["pitch"])
            tir.notes.append(NoteEventIR(
                grid_beat=gb,
                duration_beats=float(ev["length"]),
                pitch=pitch,
                velocity=int(ev.get("velocity", 80)),
                chord_idx=cidx,
                note_role=_infer_note_role(role, pitch, seg),
                lock_harmony=role in ("bass", "chords", "counter"),
            ))
        tir.notes.sort(key=lambda n: (n.grid_beat, n.pitch))
        score.tracks[name] = tir

    info = {
        "bpm": bpm, "key": key, "mode": mode, "bpb": bpb, "denom": denom, "bars": bars,
        "seed": seed, "valence": valence, "guards": guard_report,
        "cutoffs": {k: v for k, v in cutoffs.items()},
        "n_tracks": len(tracks),
        "mutation": mutation,
    }
    return score, info


_SECTION_KIND_MAP = {
    "intro": "intro", "verse": "verse", "couplet": "verse", "pre_chorus": "pre_chorus",
    "pre_refrain": "pre_chorus", "prechorus": "pre_chorus", "chorus": "chorus", "refrain": "chorus",
    "drop": "drop", "climax": "climax", "hook": "chorus", "bridge": "bridge", "pont": "bridge",
    "outro": "outro", "break": "verse", "breakdown": "verse", "song": "chorus",
}


def normalize_sections(haps: dict[str, Any]) -> list[tuple[str, int, int, str]]:
    meta_secs = (haps.get("meta") or {}).get("sections") or []
    out: list[tuple[str, int, int, str]] = []
    bar = 0
    for s in meta_secs:
        name = str(s.get("name") or f"section{bar}")
        nb = int(s.get("bars") or 4)
        kind = _SECTION_KIND_MAP.get(name.lower().replace("-", "_").replace(" ", "_"), None)
        if kind is None:
            low = name.lower()
            kind = "chorus" if any(k in low for k in ("chorus", "refrain", "drop", "climax", "hook")) else "verse"
        out.append((name, bar, nb, kind))
        bar += nb
    if not out:
        out = [("song", 0, int(haps.get("bars") or 16), "chorus")]
    return out


def build_arrangement_plan(haps: dict[str, Any], score: ScoreIR, info: dict[str, Any],
                           blueprint: dict[str, Any] | None) -> dict[str, Any]:
    bp = blueprint or {}
    meta = haps.get("meta") or {}
    bpb = info["bpb"]
    bars = info["bars"]
    key = info["key"]
    tonic_pc = parse_key_tonic_pc(key) or 0
    progression = (haps.get("meta") or {}).get("progression") or []
    romans = [symbol_to_roman(s, tonic_pc) if s else "i" for s in (progression[:bars] or ["i"] * bars)]
    while len(romans) < bars:
        romans.append(romans[-1])

    mix_profile = str(meta.get("mix_profile") or bp.get("mix_profile") or "trap_rap")
    has_drums = any(classify_role(n) == "drums" for n in score.tracks)

    sections = normalize_sections(haps)
    haps_tracks = haps.get("tracks") or {}
    presence = {tn: (haps_tracks.get(tn) or {}).get("sections") for tn in score.tracks}
    any_directive = any(p for p in presence.values())
    all_sec_names = [n for n, _sb, _nb, _k in sections]

    def allowed_sections(tname: str) -> set[str] | None:
        p = presence.get(tname)
        if not p:
            return None  # pas de directive -> toutes les sections (repli Matrice d'Arrangement)
        names = {str(x).lower() for x in (p.get("names") or [])}
        if p.get("mode") == "mute":
            return {n.lower() for n in all_sec_names} - names
        return names  # 'in' / 'only'

    motifs: dict[str, dict[str, Any]] = {}
    plan_sections: list[dict[str, Any]] = []
    for name, sb, nb, kind in sections:
        sec_start = sb * bpb
        sec_end = (sb + nb) * bpb
        sec: dict[str, Any] = {"name": name, "kind": kind, "start_bar": sb, "bars": nb, "active_roles": [], "role_modifiers": {}}
        for tname, tir in score.tracks.items():
            allowed = allowed_sections(tname)
            if allowed is not None and name.lower() not in allowed:
                continue  # v4.1 : présence déclarative .in()/.only()/.muteIn()
            local = []
            for n in tir.notes:
                if sec_start - 1e-6 <= float(n.grid_beat) < sec_end - 1e-6:
                    local.append({
                        "start": round(float(n.grid_beat) - sec_start, 5),
                        "length": round(float(n.duration_beats), 5),
                        "pitch": int(n.pitch),
                        "velocity": int(n.velocity),
                    })
            if not local:
                continue
            local.sort(key=lambda e: (e["start"], e["pitch"]))
            motifs.setdefault(tname, {})[name] = {"notes": local, "bars": nb, "loop": False}
            mod: dict[str, Any] = {"motif": name}
            cuts = [c for c in info["cutoffs"].get(tname, []) if sec_start - 1e-6 <= c[0] < sec_end - 1e-6]
            if cuts:
                vals = sorted(c[1] for c in cuts)
                mod["cutoff_hz"] = round(vals[len(vals) // 2], 1)
            sec["role_modifiers"][tname] = mod
            sec["active_roles"].append(tname)
        plan_sections.append(sec)

    plan = {
        "version": "4.0-strudel",
        "meta": {
            "title": str(bp.get("title") or meta.get("title") or "strudel_bridge"),
            "bpm": info["bpm"],
            "time_signature": {"numerator": bpb, "denominator": info["denom"]},
            "key": key,
            "mode": info["mode"],
            "mix_profile": mix_profile,
            "has_drums": has_drums,
            "valence": info["valence"],
            "seed": info["seed"],
            "arrangement_fallback": not any_directive,
        },
        "harmonic_core": {
            "key": key,
            "mode": info["mode"],
            "degree_progression": romans,
            "progression_bars": 1,
        },
        "motifs": motifs,
        "sections": plan_sections,
    }
    return plan


def run_strudel_eval(strudel_path: str, bars: int, seed: int, out_path: str) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node introuvable : installe Node.js puis `npm install` dans scripts/ (pont Strudel headless)")
    script = os.path.join(SCRIPTS_DIR, "strudel_eval.mjs")
    cmd = [node, script, strudel_path, "--bars", str(bars)]
    if seed:
        cmd += ["--seed", str(seed)]
    cmd += ["--out", out_path, "--compact"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"strudel_eval.mjs a echoue (rc={proc.returncode}): {(proc.stderr or proc.stdout)[-400:]}")
    with open(out_path, encoding="utf-8") as fh:
        return json.load(fh)


def strudel_to_score_ir(
    project_dir: str,
    strudel_path: str = "",
    haps_path: str = "",
    bars: int = 0,
    seed: int = 0,
    run_eval: bool = True,
    emit_plan: bool = True,
    out_ir: str = "",
) -> dict[str, Any]:
    proj = os.path.abspath(os.path.expanduser(project_dir))
    os.makedirs(proj, exist_ok=True)
    strudel = os.path.abspath(os.path.expanduser(strudel_path)) if strudel_path else os.path.join(proj, "composition.strudel")
    haps_file = os.path.abspath(os.path.expanduser(haps_path)) if haps_path else os.path.join(proj, "composition.strudel.haps.json")

    blueprint: dict[str, Any] | None = None
    bp_path = os.path.join(proj, "song_blueprint.json")
    if os.path.isfile(bp_path):
        with open(bp_path, encoding="utf-8") as fh:
            blueprint = json.load(fh)

    if run_eval or not os.path.isfile(haps_file):
        if not run_eval:
            return {"ok": False, "stage": "haps_missing", "error": f"haps JSON absent et --no-eval : {haps_file}"}
        if not os.path.isfile(strudel):
            return {"ok": False, "stage": "strudel_missing", "error": strudel}
        if not bars and blueprint and blueprint.get("structure"):
            bars = sum(int(s["bars"]) for s in blueprint["structure"])
        if not seed and blueprint:
            seed = int(blueprint.get("seed") or 0)
        # v4.1 hotfix : seed=0 -> on ne passe PAS --seed, l'eval derive meta.seed (ou SHA-256 du source)
        bars = bars or 16
        haps = run_strudel_eval(strudel, bars, seed, haps_file)
    else:
        with open(haps_file, encoding="utf-8") as fh:
            haps = json.load(fh)
    with open(haps_file, "w", encoding="utf-8") as fh:
        json.dump(haps, fh, ensure_ascii=False, indent=2)

    score, info = build_score_ir_from_strudel(haps, blueprint)

    score, fix = autofix_score_ir(score)
    n_b2 = fix_lead_b2_clashes(score)
    evaluation = evaluate_score_ir(score)
    try:
        from determinism import resolve_seed
        gseed = int(haps.get("seed") or resolve_seed(blueprint or {"title": "strudel", "key": info["key"], "bpm": info["bpm"]}))
    except Exception:
        gseed = int(haps.get("seed") or 42)
    swing = float((blueprint or {}).get("swing") or 0.0)
    groove_counts = apply_groove_to_score_ir(score, swing=swing, bpm=info["bpm"],
                                             profile=str((haps.get("meta") or {}).get("mix_profile") or (blueprint or {}).get("mix_profile") or "trap_rap"),
                                             beats_per_bar=float(info["bpb"]), seed=gseed)
    flat_tracks, compile_report = compile_score_ir_to_notes(score)

    out = os.path.abspath(os.path.expanduser(out_ir)) if out_ir else os.path.join(proj, "score_ir.json")
    score.save(out)

    sections = normalize_sections(haps)
    fc = validate_full_score_ir(score, sections=sections)

    plan = None
    plan_path = None
    if emit_plan:
        plan = build_arrangement_plan(haps, score, info, blueprint)
        plan_path = os.path.join(proj, "arrangement_plan.json")
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        try:
            import jsonschema
            schema_path = os.path.join(ROOT, "schemas", "arrangement_plan.schema.json")
            if os.path.isfile(schema_path):
                with open(schema_path, encoding="utf-8") as sfh:
                    jsonschema.validate(plan, json.load(sfh))
                plan_valid = True
            else:
                plan_valid = None
        except Exception as exc:
            plan_valid = f"error: {str(exc)[:160]}"
    else:
        plan_valid = None

    n_notes = sum(len(t.notes) for t in score.tracks.values())
    # v4.1 pre-flight du MIDI Gate (0 rendu) : métriques réelles post fix_lead_b2_clashes,
    # topline concaténée de toutes les sections — ce que mesurera audit_midi_file sur le .mid.
    try:
        from preflight_strudel_gate import preflight_from_score
        preflight_rep = preflight_from_score(score, bpm=float(info["bpm"]), tracks=flat_tracks, sections=sections)
    except Exception as exc:  # jamais bloquant
        preflight_rep = {"error": str(exc)[:200]}
    report = {
        "ok": bool(evaluation.get("passed")) and bool(compile_report.get("passed")) and bool(fc.get("passed")),
        "stage": "strudel_bridge",
        "preflight": preflight_rep,
        "project_dir": proj,
        "strudel": strudel,
        "haps": haps_file,
        "score_ir": out,
        "arrangement_plan": plan_path,
        "plan_valid": plan_valid,
        "guards": info["guards"],
        "lead_b2_fixed": n_b2,
        "autofix": {"n_changes": fix.get("n_changes"), "changes": fix.get("changes", [])[:20]},
        "evaluate": {"passed": evaluation.get("passed"), "n_notes": evaluation.get("n_notes"),
                     "errors": evaluation.get("errors", [])[:20], "warnings": evaluation.get("warnings", [])[:10]},
        "groove": groove_counts,
        "compile": compile_report,
        "full_coverage": {"passed": fc.get("passed"), "n_issues": fc.get("n_issues"), "issues": fc.get("issues", [])[:20]},
        "n_tracks": info["n_tracks"],
        "n_notes": n_notes,
        "mutation": info.get("mutation", {}),
        "diagnostics": [
            f"ERR_OVER_REPAIRED {classify_role(tn)}: {round(r * 100)}% notes shifted"
            for tn, r in (info.get("mutation") or {}).items() if r > MUTATION_RATIO_MAX
        ],
        "sections": [{"name": n, "start_bar": sb, "bars": nb, "kind": k} for n, sb, nb, k in sections],
        "seed": gseed,
    }
    return report


def apply_score_ir_patch(project_dir: str, patch: dict[str, Any]) -> dict[str, Any]:
    """v4.1 — patch sémantique micro-tokens sur le ScoreIR (0 réécriture Strudel, 0 LLM).

    Formats acceptés :
      {"section": "chorus", "ops": {"energy": "+2dB"}}
      {"lead": {"oct": -1, "rests": "+15%", "cutoff": 3200}}   ou   {"track": "lead", "ops": {...}}
    """
    proj = os.path.abspath(os.path.expanduser(project_dir))
    ir_path = os.path.join(proj, "score_ir.json")
    plan_path = os.path.join(proj, "arrangement_plan.json")
    if not os.path.isfile(ir_path):
        return {"ok": False, "error": f"score_ir.json absent : {ir_path} (composer d'abord)"}
    score = ScoreIR.load(ir_path)
    applied: list[str] = []

    if patch.get("section"):
        sec_name = str(patch["section"]).lower()
        ops = patch.get("ops") or {}
        sec_range = None
        if os.path.isfile(plan_path):
            with open(plan_path, encoding="utf-8") as fh:
                plan = json.load(fh)
            bpb = int(plan.get("meta", {}).get("time_signature", {}).get("numerator") or 4)
            for s in plan.get("sections") or []:
                if str(s.get("name", "")).lower() == sec_name:
                    sec_range = (int(s["start_bar"]) * bpb, (int(s["start_bar"]) + int(s["bars"])) * bpb)
        energy = ops.get("energy")
        if energy and sec_range:
            txt = str(energy).replace("dB", "").strip()
            db = abs(float(txt.replace("+", "")))
            sign = 1 if str(energy).strip().startswith("+") else -1
            dv = int(round(db * 6))
            for tir in score.tracks.values():
                for n in tir.notes:
                    if sec_range[0] - 1e-6 <= float(n.grid_beat) < sec_range[1] - 1e-6:
                        n.velocity = max(1, min(127, int(n.velocity) + sign * dv))
            applied.append(f"section:{sec_name} energy={energy}")

    track_ops = None
    if patch.get("track"):
        track_ops = (str(patch["track"]), patch.get("ops") or {})
    else:
        for k, v in patch.items():
            if isinstance(v, dict) and k != "section":
                track_ops = (str(k), v)
                break
    if track_ops:
        tname, ops = track_ops
        target = None
        for name in score.tracks:
            if name == tname or classify_role(name) == classify_role(tname):
                target = name
                break
        if target is None:
            return {"ok": False, "error": f"piste introuvable dans le ScoreIR : {tname}"}
        tir = score.tracks[target]
        role = classify_role(target)
        if "oct" in ops:
            shift = 12 * int(ops["oct"])
            for n in tir.notes:
                p = int(n.pitch) + shift
                if role == "bass":
                    p = fold_into_register(p, *BASS_REGISTER)
                elif role in ("lead", "counter"):
                    while p > LEAD_APEX_MAX:
                        p -= 12
                n.pitch = p
            applied.append(f"{target}: oct={ops['oct']}")
        if "rests" in ops:
            pct = abs(float(str(ops["rests"]).replace("+", "").replace("%", ""))) / 100.0
            notes = sorted(tir.notes, key=lambda n: (float(n.grid_beat), int(n.pitch)))
            k = int(round(len(notes) * pct))
            if k > 0 and len(notes) > k:
                idxs = sorted({min(len(notes) - 1, max(1, round(i * len(notes) / k))) for i in range(k)})
                tir.notes = [n for i, n in enumerate(notes) if i not in idxs]
            applied.append(f"{target}: rests={ops['rests']}")
        if "cutoff" in ops and os.path.isfile(plan_path):
            with open(plan_path, encoding="utf-8") as fh:
                plan = json.load(fh)
            for s in plan.get("sections") or []:
                rm = (s.get("role_modifiers") or {}).get(target)
                if rm is not None:
                    rm["cutoff_hz"] = float(ops["cutoff"])
            with open(plan_path, "w", encoding="utf-8") as fh:
                json.dump(plan, fh, ensure_ascii=False, indent=2)
            applied.append(f"{target}: cutoff={ops['cutoff']}")

    score.save(ir_path)
    return {"ok": True, "project_dir": proj, "applied": applied}


def main() -> None:
    ap = argparse.ArgumentParser(description="v4.1 pont Strudel headless -> ScoreIR (+ arrangement_plan.json, --patch sémantique)")
    ap.add_argument("project_dir")
    ap.add_argument("--strudel", default="", help="chemin composition.strudel (defaut: <projet>/composition.strudel)")
    ap.add_argument("--haps", default="", help="haps JSON deja produit (defaut: <projet>/composition.strudel.haps.json)")
    ap.add_argument("--bars", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--eval", action="store_true", help="re-evaluer le .strudel via node strudel_eval.mjs")
    ap.add_argument("--no-eval", action="store_true", help="ne jamais appeler node (lire le haps JSON existant)")
    ap.add_argument("--no-plan", action="store_true", help="ne pas emettre arrangement_plan.json")
    ap.add_argument("--out", default="", help="chemin score_ir.json de sortie")
    ap.add_argument("--patch", default="", help="JSON de patch sémantique (ex. {\"lead\": {\"oct\": -1}}) appliqué sur score_ir.json sans ré-éval")
    args = ap.parse_args()
    if args.patch:
        with open(args.patch, encoding="utf-8") as fh:
            patch = json.load(fh)
        print(json.dumps(apply_score_ir_patch(args.project_dir, patch), ensure_ascii=False, indent=2, default=str))
        return
    report = strudel_to_score_ir(
        args.project_dir, strudel_path=args.strudel, haps_path=args.haps,
        bars=args.bars, seed=args.seed, run_eval=args.eval and not args.no_eval,
        emit_plan=not args.no_plan, out_ir=args.out,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report.get("ok") else 2)


if __name__ == "__main__":
    main()
