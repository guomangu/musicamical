"""v4.1 — preflight_strudel_gate.py — simulateur PRE-FLIGHT du MIDI Gate (0 rendu, 0 LLM, ~2 s).

Calcule EXACTEMENT les métriques de `verify_midi_gate` / `audit_midi_file` (topline Lead
concaténée de TOUTES les sections, frontières inter-sections incluses) directement depuis le
HAPS Strudel, EN APPLIQUANT toutes les transformations destructrices du pont dans l'ordre :
  build_score_ir_from_strudel -> autofix_score_ir -> fix_lead_b2_clashes (réécrit les notes
  du lead à 1 st d'un accord -> piège n°1, détruit le gap_fill) -> compile_score_ir_to_notes.
Puis mesure stepwise_ratio / gap_fill / rest_ratio / contrary_or_oblique sur la topline,
compte les b2 restants, les clamps d'apex (LEAD_APEX_MAX=74) et liste les leaps non remplis.

Utilitaires purs (testables sans pipeline) :
  degree_table(key, mode)      — dictionnaire degrés <-> demi-tons/MIDI (deg 4 en mineur = +7 st !)
  b2_safe_degrees(symbols)     — degrés b2-safe par accord + intersection "sûr partout"
  slow_step(seq_len, slow_n)   — sémantique de .slow(N) (step en beats)

Usage :
  .venv/bin/python scripts/preflight_strudel_gate.py <projet> [--haps haps.json]
  .venv/bin/python scripts/preflight_strudel_gate.py --degrees C:minor
  .venv/bin/python scripts/preflight_strudel_gate.py --safe-degrees "Cm7 Abmaj7 Ebmaj7 Bb7" --mode minor

Seuils (MusicalityThresholds) : stepwise >= 0.65, gap_fill >= 0.60, rest in [0.20, 0.50],
contrary_or_oblique >= 0.55 (si basse présente). Voir docs/03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
ANALYZER_DIR = os.path.join(ROOT, "mcp-servers", "audio-analyzer")
for _p in (SCRIPTS_DIR, ANALYZER_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


# ---------------- utilitaires purs ----------------

def degree_table(key: str = "C", mode: str = "minor", base_octave: int = 4, max_deg: int = 14) -> list[dict]:
    """degToMidi du prélude : midi = tonic + 12*(deg//len(scale)) + scale[deg%len(scale)],
    tonic = pc + 12*(base_octave+1). Ex. C mineur octave 4 : deg 4 -> 67 (+7 st, PAS +4)."""
    from score_ir import SCALES, TONIC_TO_PC
    scale = SCALES.get(str(mode).lower(), SCALES["minor"])
    tonic = TONIC_TO_PC.get(str(key).upper(), 0) + 12 * (base_octave + 1)
    out = []
    for d in range(max_deg + 1):
        midi = tonic + 12 * (d // len(scale)) + scale[d % len(scale)]
        out.append({"deg": d, "pc": midi % 12, "name": NOTE_NAMES[midi % 12],
                    "semitones": midi - tonic, "midi": midi})
    return out


def b2_safe_degrees(symbols, mode: str = "minor") -> dict:
    """Degrés (0..7) dont la pc n'est PAS à 1 demi-ton d'une pc de l'accord, par symbole,
    + intersection « sûr sur toute la grille ». Utilise `parse_chord_symbol` du pont (mêmes
    qualités d'accords que le prélude JS)."""
    import strudel_to_score_ir as st
    from score_ir import SCALES
    scale = SCALES.get(str(mode).lower(), SCALES["minor"])
    deg_pcs = [scale[d % len(scale)] for d in range(8)]
    per_chord: dict[str, dict] = {}
    for sym in symbols:
        parsed = st.parse_chord_symbol(str(sym))
        if not parsed:
            continue
        pcs = set(parsed["chord_pcs"])
        unsafe = {d for d, pc in enumerate(deg_pcs)
                  if any(min((pc - c) % 12, (c - pc) % 12) == 1 for c in pcs)}
        per_chord[str(sym)] = {"pcs": sorted(pcs), "unsafe_degs": sorted(unsafe),
                               "safe_degs": [d for d in range(8) if d not in unsafe]}
    all_safe: set[int] | None = None
    for entry in per_chord.values():
        s = set(entry["safe_degs"])
        all_safe = s if all_safe is None else (all_safe & s)
    return {"mode": mode, "per_chord": per_chord, "safe_everywhere": sorted(all_safe or [])}


def slow_step(seq_len: int, slow_n: float = 1.0, beats_per_bar: float = 4.0) -> dict:
    """Sémantique de `.slow(N)` : L degrés sur 1 cycle (= 1 mesure) -> L/N degrés/mesure,
    step = beats_per_bar / (L/N) beats. Ex. seq 256 sans slow -> 1/64 de mesure (dangereux) ;
    seq 128 + .slow(8) -> 16/mesure, step 0.25 beat."""
    per_bar = float(seq_len) / max(1e-9, float(slow_n))
    return {"seq_len": int(seq_len), "slow": float(slow_n), "degrees_per_bar": round(per_bar, 4),
            "step_beats": round(float(beats_per_bar) / per_bar, 6) if per_bar > 0 else None}


# ---------------- pre-flight depuis un ScoreIR déjà post-fix ----------------

def preflight_from_score(score, bpm: float = 120.0, tracks: dict | None = None,
                         sections: list | None = None) -> dict:
    """Métriques du gate sur la topline Lead concaténée (toutes sections) d'un ScoreIR
    APRÈS fix_lead_b2_clashes — exactement ce que mesurera `audit_midi_file` sur le .mid."""
    import pretty_midi
    from score_ir import compile_score_ir_to_notes, segment_index_for_beat
    from musicality_metrics import (
        MusicalityThresholds,
        _compute_contrary_oblique_ratio,
        _compute_rest_ratio,
        _compute_stepwise_and_gap_fill,
        _extract_monophonic_topline,
    )

    if tracks is None:
        tracks, _rep = compile_score_ir_to_notes(score)
    spb = 60.0 / float(bpm)

    def to_notes(ev_list: list) -> list:
        out = []
        for ev in ev_list:
            st_s = float(ev["start"]) * spb
            en_s = (float(ev["start"]) + float(ev.get("length", 0.25))) * spb
            out.append(pretty_midi.Note(int(ev.get("velocity", 80)), int(ev["pitch"]), st_s, en_s))
        return out

    lead_notes = to_notes(tracks.get("lead", []))
    bass_notes = to_notes(tracks.get("bass", []))
    top = _extract_monophonic_topline(lead_notes)
    sw, gf, amb = _compute_stepwise_and_gap_fill(top, sec_per_beat=spb)
    end_time = max((n.end for n in lead_notes), default=0.0)
    rr = _compute_rest_ratio(lead_notes, end_time)
    cor = _compute_contrary_oblique_ratio(top, bass_notes) if len(bass_notes) >= 2 else None

    diffs = [top[i + 1].pitch - top[i].pitch for i in range(len(top) - 1)]
    bad_leaps = [
        {"idx": i, "pitch": top[i].pitch, "leap": diffs[i], "next": diffs[i + 1] if i + 1 < len(diffs) else 0,
         "start_beat": round(top[i].start / spb, 3)}
        for i in range(len(diffs) - 1)
        if abs(diffs[i]) >= 5 and not (diffs[i] * diffs[i + 1] < 0 and 1 <= abs(diffs[i + 1]) <= 3)
    ]

    # b2 restants après fix (grille du ScoreIR)
    grid = score.harmonic_grid
    lead_track = score.tracks.get("lead")
    remaining_b2 = 0
    if lead_track is not None:
        for note in lead_track.notes:
            cidx = note.chord_idx if 0 <= note.chord_idx < len(grid) else segment_index_for_beat(grid, note.grid_beat)
            seg = grid[cidx] if 0 <= cidx < len(grid) else (grid[0] if grid else {})
            chord_pcs = set(seg.get("chord_pcs", []))
            pc = int(note.pitch) % 12
            if chord_pcs and any(min((pc - c) % 12, (c - pc) % 12) == 1 for c in chord_pcs):
                remaining_b2 += 1

    th = MusicalityThresholds()
    checks = {
        "stepwise_ratio": {"value": sw, "min": th.stepwise_ratio_min, "pass": bool(sw >= th.stepwise_ratio_min)},
        "gap_fill": {"value": gf, "min": th.gap_fill_min, "pass": bool(gf >= th.gap_fill_min)},
        "rest_ratio": {"value": rr, "min": th.rest_ratio_min, "max": th.rest_ratio_max,
                       "pass": bool(th.rest_ratio_min <= rr <= th.rest_ratio_max)},
        "contrary_or_oblique_ratio": {"value": cor, "min": th.contrary_oblique_min,
                                      "pass": True if cor is None else bool(cor >= th.contrary_oblique_min)},
        "b2_clashes_remaining": {"value": remaining_b2, "pass": remaining_b2 == 0},
    }
    return {
        "passed": all(c["pass"] for c in checks.values()),
        "n_lead_notes": len(top),
        "ambitus": amb,
        "checks": checks,
        "bad_leaps": bad_leaps[:20],
        "n_bad_leaps": len(bad_leaps),
        "lead_pitches_first32": [n.pitch for n in top[:32]],
        "sections": [{"name": n, "start_bar": sb, "bars": nb} for n, sb, nb, *_ in (sections or [])],
    }


# ---------------- pre-flight complet depuis le HAPS ----------------

def preflight(project_dir: str, haps_path: str = "") -> dict:
    """Pipeline identique à strudel_to_score_ir.strudel_to_score_ir (étapes destructrices
    incluses), puis métriques du gate. Le rapport inclut b2_fixed_by_bridge (combien de
    notes fix_lead_b2_clashes a réécrit — le piège) et les clamps d'apex."""
    import strudel_to_score_ir as st
    from score_ir import autofix_score_ir

    proj = os.path.abspath(os.path.expanduser(project_dir))
    haps_file = haps_path or os.path.join(proj, "composition.strudel.haps.json")
    with open(haps_file, encoding="utf-8") as fh:
        haps = json.load(fh)
    bp_path = os.path.join(proj, "song_blueprint.json")
    bp = None
    if os.path.isfile(bp_path):
        with open(bp_path, encoding="utf-8") as fh:
            bp = json.load(fh)

    score, info = st.build_score_ir_from_strudel(haps, bp)
    score, fix = autofix_score_ir(score)
    n_b2 = st.fix_lead_b2_clashes(score)
    from score_ir import compile_score_ir_to_notes
    tracks, compile_rep = compile_score_ir_to_notes(score)
    sections = st.normalize_sections(haps)
    rep = preflight_from_score(score, bpm=float(info["bpm"]), tracks=tracks, sections=sections)
    rep.update({
        "project": proj,
        "haps": haps_file,
        "b2_fixed_by_bridge": n_b2,
        "autofix_changes": fix.get("n_changes"),
        "guards": info.get("guards", {}),
        "lead_apex_clamped": int(info.get("guards", {}).get("lead_apex_clamped", 0)),
        "compile": compile_rep,
    })
    if rep["lead_apex_clamped"] > 0:
        rep["warnings"] = rep.get("warnings", []) + [
            f"lead_apex_clamped={rep['lead_apex_clamped']} (LEAD_APEX_MAX=74, sauts de -12 st possibles aux frontières)"]
    # §5 Pré-vol sonore 0 rendu (sample_fit_guard + blueprint_safety) si song_blueprint.json présent
    if isinstance(bp, dict):
        try:
            from blueprint_safety import summarize_musical_safety
            from sample_fit_guard import resolve_catalog_path
            notes_by_track = {
                str(tname): [int(ev["pitch"]) for ev in evs if isinstance(ev.get("pitch"), (int, float))]
                for tname, evs in (tracks or {}).items()
                if evs
            }
            cat = resolve_catalog_path(proj, bp=bp)
            safety = summarize_musical_safety(bp, catalog_path=cat, notes_by_track=notes_by_track, project_dir=proj)
            rep["sound_preflight"] = {
                "ok": safety.get("ok"),
                "errors": safety.get("errors", 0),
                "warnings": safety.get("warnings", 0),
                "catalog": safety.get("catalog"),
                "issues": safety.get("issues", [])[:10],
            }
        except Exception:
            pass
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-flight du MIDI Gate depuis le HAPS Strudel (0 rendu, 0 LLM)")
    ap.add_argument("project_dir", nargs="?", default="")
    ap.add_argument("--haps", default="")
    ap.add_argument("--degrees", nargs="?", const="C:minor", default=None, metavar="KEY:MODE",
                    help="affiche le dictionnaire degrés <-> demi-tons et quitte")
    ap.add_argument("--safe-degrees", default="", metavar="\"Cm7 Abmaj7 ...\"",
                    help="affiche la table des degrés b2-safe par accord et quitte")
    ap.add_argument("--mode", default="minor")
    args = ap.parse_args()

    if args.degrees:
        key, _, mode = args.degrees.partition(":")
        print(json.dumps(degree_table(key or "C", mode or args.mode), ensure_ascii=False, indent=2))
        return 0
    if args.safe_degrees:
        print(json.dumps(b2_safe_degrees(args.safe_degrees.split(), mode=args.mode), ensure_ascii=False, indent=2))
        return 0
    if not args.project_dir:
        ap.print_help()
        return 2
    rep = preflight(args.project_dir, args.haps)
    print(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
