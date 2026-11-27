#!/usr/bin/env python3
"""Indexation unifiée des presets VST3 / banques SFZ dans `samples.db` (Chantier 2.2).

Chaque preset est rendu sur une note de référence — C2 (MIDI 36) pour les basses/808,
C4 (MIDI 60) pour les instruments médiums — pendant 2 s à vélocité 100, via le moteur
`synth_render.SynthEngine` (DawDreamer + VST3 si disponible, sinon synthèse native builtin
pour les SFZ). Le rendu passe ensuite dans EXACTEMENT le même analyseur psychoacoustique
(`unified_indexer.analyze_audio`) que les WAV : l'agent sélectionne un preset de synthé via
les mêmes requêtes SQLite qu'un sample (`source_type` = 'vst3_preset' / 'sfz_instrument').

Usage :
  .venv/bin/python scripts/index_vst_presets.py [--soundbanks soundbanks] [--db samples.db] [--vst3-dir DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
INDEX_DIR = os.path.join(ROOT, "mcp-servers", "audio-index")
PROD_DIR = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
for _p in (SCRIPTS_DIR, INDEX_DIR, PROD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from unified_indexer import (  # noqa: E402
    DEFAULT_DB_PATH,
    ROLE_CATEGORIES,
    index_audio_array,
    init_db,
    infer_role_category,
)
from synth_render import SynthEngine  # noqa: E402

DEFAULT_SOUNDBANKS_DIR = os.path.join(ROOT, "soundbanks")
REFERENCE_NOTE_BASS = 36   # C2
REFERENCE_NOTE_MID = 60    # C4
REFERENCE_DURATION_S = 2.0
REFERENCE_VELOCITY = 100
ANALYSIS_SR = 22050

_SFZ_REGION_RE = re.compile(r"<region>\s*(.*?)(?=<region>|\Z)", re.S)
_SFZ_OPCODE_RE = re.compile(r"(\w+)=(\S+)")


def parse_sfz_regions(sfz_path: str) -> list[dict[str, Any]]:
    """Parse les régions d'un fichier .sfz (opcodes sample/lokey/hikey/pitch_keycenter...)."""
    with open(sfz_path, encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    regions: list[dict[str, Any]] = []
    for m in _SFZ_REGION_RE.finditer(text):
        ops = dict(_SFZ_OPCODE_RE.findall(m.group(1)))
        if "sample" in ops:
            regions.append(ops)
    return regions


def sfz_bank_role(sfz_path: str) -> str:
    name = os.path.basename(sfz_path).lower()
    if any(k in name for k in ("808", "sub", "bass")):
        return "bass_808"
    if any(k in name for k in ("pad", "ambient", "drone", "ork", "choir", "vocal")):
        return "pad"
    if any(k in name for k in ("piano", "rhodes", "keys", "epiano", "organ")):
        return "keys"
    if any(k in name for k in ("lead", "bell", "arp", "pluck", "synth")):
        return "lead"
    return infer_role_category(sfz_path)


def reference_note_for(role: str) -> int:
    """C2 pour les basses, C4 pour les instruments médiums (spec §2.2)."""
    return REFERENCE_NOTE_BASS if role == "bass_808" else REFERENCE_NOTE_MID


def index_sfz_bank(conn, engine: SynthEngine, sfz_path: str) -> dict[str, Any] | None:
    """Rend la note de référence d'une banque SFZ et l'indexe comme `sfz_instrument`."""
    role = sfz_bank_role(sfz_path)
    if role not in ROLE_CATEGORIES:
        role = "keys"
    midi = reference_note_for(role)
    notes = [(float(midi), 0.0, REFERENCE_DURATION_S, float(REFERENCE_VELOCITY))]
    audio, backend = engine.render_preset_notes(notes, sr=ANALYSIS_SR, timbre=SynthEngine.default_timbre(role))
    asset_id = "sfz_" + hashlib.sha1(os.path.abspath(sfz_path).encode("utf-8")).hexdigest()[:14]
    preset_name = Path(sfz_path).stem
    result = index_audio_array(
        conn,
        np.asarray(audio, dtype=np.float32),
        ANALYSIS_SR,
        asset_id=asset_id,
        file_path=os.path.abspath(sfz_path),
        source_type="sfz_instrument",
        role_category=role,
        preset_name=preset_name,
    )
    result["backend"] = backend
    result["reference_note_midi"] = midi
    return result


def index_vst3_preset(conn, engine: SynthEngine, vst3_path: str, role: str | None = None) -> dict[str, Any] | None:
    """Indexe un preset VST3 via DawDreamer (note de référence rendue nativement)."""
    if not engine.has_dawdreamer:
        return None
    role = role or infer_role_category(vst3_path)
    midi = reference_note_for(role)
    notes = [(float(midi), 0.0, REFERENCE_DURATION_S, float(REFERENCE_VELOCITY))]
    audio, backend = engine.render_preset_notes(notes, vst3_path=vst3_path, sr=ANALYSIS_SR)
    if backend != "dawdreamer":
        return None
    asset_id = "vst3_" + hashlib.sha1(os.path.abspath(vst3_path).encode("utf-8")).hexdigest()[:14]
    return index_audio_array(
        conn,
        np.asarray(audio, dtype=np.float32),
        ANALYSIS_SR,
        asset_id=asset_id,
        file_path=os.path.abspath(vst3_path),
        source_type="vst3_preset",
        role_category=role if role in ROLE_CATEGORIES else "keys",
        preset_name=Path(vst3_path).stem,
    )


def index_soundbanks(
    conn,
    soundbanks_dir: str = DEFAULT_SOUNDBANKS_DIR,
    vst3_dir: str | None = None,
    engine: SynthEngine | None = None,
) -> dict[str, Any]:
    """Indexe toutes les banques SFZ (et VST3 si DawDreamer dispo) dans `sound_assets`."""
    engine = engine or SynthEngine()
    sfz_files = sorted(str(p) for p in Path(soundbanks_dir).rglob("*.sfz")) if os.path.isdir(soundbanks_dir) else []
    indexed, skipped = [], []
    for sfz_path in sfz_files:
        try:
            r = index_sfz_bank(conn, engine, sfz_path)
            if r:
                indexed.append(r)
        except Exception as exc:  # pragma: no cover
            skipped.append({"path": sfz_path, "error": str(exc)})
    vst3_files = []
    if vst3_dir and os.path.isdir(vst3_dir):
        vst3_files = sorted(str(p) for p in Path(vst3_dir).rglob("*.vst3"))
    for vst3_path in vst3_files:
        try:
            r = index_vst3_preset(conn, engine, vst3_path)
            if r:
                indexed.append(r)
            else:
                skipped.append({"path": vst3_path, "error": "dawdreamer_indisponible"})
        except Exception as exc:  # pragma: no cover
            skipped.append({"path": vst3_path, "error": str(exc)})
    return {
        "indexed": len(indexed),
        "skipped": skipped,
        "sfz_banks": [os.path.basename(p) for p in sfz_files],
        "vst3_banks": [os.path.basename(p) for p in vst3_files],
        "backend": engine.backend,
        "assets": [
            {k: v for k, v in r.items() if k in ("asset_id", "file_path", "preset_name", "role_category",
                                                 "detected_midi_note", "pitch_confidence", "spectral_centroid_hz",
                                                 "ac_warmth", "ac_brightness", "ac_roughness", "lufs_integrated")}
            for r in indexed
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Indexe les presets SFZ/VST3 dans samples.db (note de référence native)")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--soundbanks", default=DEFAULT_SOUNDBANKS_DIR)
    parser.add_argument("--vst3-dir", default=None, help="répertoire de presets .vst3 (nécessite DawDreamer)")
    args = parser.parse_args()
    conn = init_db(args.db)
    try:
        result = index_soundbanks(conn, args.soundbanks, args.vst3_dir)
    finally:
        conn.close()
    print(json.dumps({"db": args.db, **{k: v for k, v in result.items() if k != "assets"},
                      "assets": result["assets"]}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
