#!/usr/bin/env python3
"""
v4.0 `ai-studio-prod/prod_server.py` — façade MCP de PRODUCTION à 3 outils uniques
(`studio_prepare`, `studio_compose`, `studio_refine`), ~600 tokens de schémas au lieu de
~14 000 pour les 5 serveurs bas niveau. Appelle les fonctions Python en interne (0 token MCP),
écrit `composition.strudel` de façon atomique, chaine automatiquement la compilation complète
+ le master (`auto_master=True`), applique les replis locaux 0-token (lint 2 lignes,
garde-fous ScoreIR, AUTO_SWAP_SAMPLE) avant toute remontée au LLM, et écrit
`<projet>/handoff.txt` (<= 350 tokens) pour des raffinements stateless.

Profil Zed « Production » : ne connecter QUE ce serveur (voir install.sh / .zed/settings.json).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from typing import Any

try:
    _BOOT_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
    if _BOOT_SCRIPTS not in sys.path:
        sys.path.insert(0, _BOOT_SCRIPTS)
    from env_loader import load_dotenv
    load_dotenv()  # .env du projet (gitignoré) : les vraies variables d'env priment
except Exception:
    pass

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
ANALYZER = os.path.join(ROOT, "mcp-servers", "audio-analyzer")
for _d in (SCRIPTS, ANALYZER):
    if _d not in sys.path:
        sys.path.insert(0, _d)

try:
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer(name="ai-studio-prod", instructions="Façade production v4.0 : 3 outils (prepare/compose/refine). Tout le reste est local, 0 token.")
    _tool = mcp.tool
except Exception:  # import des helpers de test sans le SDK MCP
    mcp = None

    def _tool():
        def deco(fn):
            return fn
        return deco


PREPARE_LIMIT = 450
HANDOFF_LIMIT = 1400


def _short_id(name: str, n: int = 6) -> str:
    base = os.path.splitext(os.path.basename(str(name)))[0]
    return base[:n]


def format_prepare_compact(prep: dict[str, Any]) -> str:
    """Format tabulaire ultra-dense (<= 450 cars) du retour de studio_prepare."""
    lines = []
    ev = prep.get("emotion_vector") or {}
    valence = prep.get("valence", ev.get("valence"))
    lines.append(
        f"CTX: {prep.get('mix_profile', '?')} | {prep.get('bpm', '?')}bpm | {prep.get('key', '?')} | "
        f"V={valence} | lock={bool(prep.get('user_samples_locked'))}"
    )
    lines.append("PALETTES:")
    for i, p in enumerate((prep.get("palettes") or [])[:3], 1):
        tracks = " ".join(
            f"{t.get('role', '?')}={_short_id(t.get('file') or t.get('name') or '?')}(root={t.get('root_note', '?')})"
            for t in (p.get("tracks") or [])[:6]
        )
        lines.append(f"P{i}({p.get('id', '?')}): {tracks}")
    lines.append("GROOVES_AVAIL: midi_groove_bank (voir scripts/midi_groove_bank.py)")
    out = "\n".join(lines)
    return out[:PREPARE_LIMIT]


def apply_strudel_patch(strudel_path: str, patch: dict[str, Any]) -> str:
    """Applique un patch chirurgical `{"replace_track": "lead", "code": "..."}` au fichier
    composition.strudel (remplace le bloc `$lead:` sans toucher au reste). Retourne le nouveau contenu."""
    with open(strudel_path, encoding="utf-8") as fh:
        src = fh.read()
    track = str(patch.get("replace_track") or "").strip()
    code = str(patch.get("code") or "")
    if not track or not code:
        raise ValueError("patch invalide: {replace_track, code} requis")
    lines = src.split("\n")
    out: list[str] = []
    i = 0
    replaced = False
    header = f"${track}:"
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith(header):
            out.append(f"{header} {code}")
            replaced = True
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("$"):
                i += 1
            continue
        out.append(ln)
        i += 1
    if not replaced:
        out.append(f"{header} {code}")
    new = "\n".join(out)
    with open(strudel_path, "w", encoding="utf-8") as fh:
        fh.write(new if new.endswith("\n") else new + "\n")
    return new


def write_handoff(project_dir: str, report: dict[str, Any]) -> str:
    """Écrit `<projet>/handoff.txt` (<= 350 tokens) : état compact pour raffinements stateless."""
    proj = os.path.abspath(os.path.expanduser(project_dir))
    strudel_path = os.path.join(proj, "composition.strudel")
    sha = ""
    if os.path.isfile(strudel_path):
        with open(strudel_path, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()[:10]
    fc = report.get("full_coverage") or {}
    ev = report.get("evaluate") or {}
    pf = report.get("preflight") or {}
    pf_checks = pf.get("checks") or {}
    if pf_checks:
        pf_line = (f"preflight={'PASS' if pf.get('passed') else 'FAIL'}"
                   f" gf={pf_checks.get('gap_fill', {}).get('value')}"
                   f" sw={pf_checks.get('stepwise_ratio', {}).get('value')}"
                   f" rest={pf_checks.get('rest_ratio', {}).get('value')}"
                   f" bad_leaps={pf.get('n_bad_leaps')}"
                   f" (0 rendu: scripts/preflight_strudel_gate.py <projet>)")
    else:
        pf_line = "preflight=n/a"
    lines = [
        f"HANDOFF v4 {os.path.basename(proj)}",
        f"strudel=composition.strudel#{sha} seed={report.get('seed', '?')}",
        f"bridge_ok={report.get('ok')} tracks={report.get('n_tracks', '?')} notes={report.get('n_notes', '?')}",
        f"sections={','.join(s.get('name', '?') for s in report.get('sections', []))}",
        f"eval_passed={ev.get('passed')} compile_passed={(report.get('compile') or {}).get('passed')} fc_passed={fc.get('passed')}",
        f"guards={json.dumps(report.get('guards', {}), separators=(',', ':'))}",
        pf_line,
        _palette_line(report),
        "score_ir=score_ir.json plan=arrangement_plan.json haps=composition.strudel.haps.json",
        f"master={'renders/master.wav' if report.get('master') else 'n/a (gridline absent ou auto_master off)'}",
        "next: studio_refine(delta_json=...) ou strudel_patch={{\"replace_track\":\"lead\",\"code\":\"...\"}}",
    ]
    text = "\n".join(lines)
    if len(text) > HANDOFF_LIMIT:
        text = text[:HANDOFF_LIMIT]
    path = os.path.join(proj, "handoff.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return path


def _palette_line(report: dict[str, Any]) -> str:
    """Ligne compacte `palette=... samples=N theme=...` (observabilité : l'agent voit quels sons réels
    ont été branchés sur les pistes, ou l'erreur silencieuse sinon)."""
    pal = report.get("palette") or {}
    if not pal:
        return "palette=n/a"
    line = f"palette={pal.get('palette_id', '?')} samples={pal.get('n_samples', 0)}"
    if pal.get("theme"):
        line += f" theme={pal.get('theme')}"
    if pal.get("error"):
        line += f" err={str(pal['error'])[:60]}"
    return line


def _run_bridge(proj: str, bars: int = 0, seed: int = 0) -> dict[str, Any]:
    import strudel_to_score_ir as bridge
    return bridge.strudel_to_score_ir(proj, run_eval=True, bars=bars, seed=seed)


ROLE_PLUGIN = {
    "kick": "gl.kick", "snare": "gl.snare", "hihat": "gl.hat", "clap": "gl.clap", "perc": "gl.hat",
    "bass": "gl.fingerbass", "chords": "gl.keys", "lead": "gl.warmsynth", "counter": "gl.pluck",
    "vocals": "gl.sampler", "fx": "gl.wavetable", "drums": "gl.kick", "remix_stem": "gl.sampler",
}


def _ensure_blueprint(proj: str, report: dict[str, Any]) -> dict[str, Any]:
    """v4.1 — blueprint minimal VALIDE contre schemas/song_blueprint.schema.json, déduit du Strudel :
    tracks = pistes `$` (rôle via classify_role, plugin gl.*), chord_progression = grille grid(),
    motif_cell.apex_midi selon valence, structure = sections du @meta."""
    bp_path = os.path.join(proj, "song_blueprint.json")
    if os.path.isfile(bp_path):
        with open(bp_path, encoding="utf-8") as fh:
            return json.load(fh)
    haps_path = os.path.join(proj, "composition.strudel.haps.json")
    haps = {}
    if os.path.isfile(haps_path):
        with open(haps_path, encoding="utf-8") as fh:
            haps = json.load(fh)
    meta = haps.get("meta") or {}
    from score_ir import classify_role
    from timbre_pleasantness import pick_gl_plugin
    prof0 = str(meta.get("mix_profile") or "trap_rap")
    val0 = float(meta.get("valence") or 0.0)
    tracks = []
    mixer_track = 1
    for name in (haps.get("tracks") or {}):
        role = classify_role(name)
        # v4.1 : archetype calibré (profil, rôle, valence) pour TOUTES les pistes, y compris percussions
        if role == "vocals":
            plugin = "gl.sampler"
        else:
            plugin = pick_gl_plugin(str(name).lower(), prof0, val0)["plugin"]
        tracks.append({"name": str(name), "role": role,
                       "source_type": "vocaloid" if role == "vocals" else "gridline_native",
                       "plugin": plugin, "mixer_track": mixer_track})
        mixer_track = mixer_track + 1 if mixer_track < 16 else 1
    if not tracks:
        tracks = [{"name": "lead", "role": "lead", "source_type": "gridline_native", "plugin": "gl.warmsynth", "mixer_track": 1}]
    progression = [c for c in (meta.get("progression") or []) if c]
    chords = list(dict.fromkeys(progression))[:8] or ["Cm7"]
    NOTE_PC = {"c": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3, "e": 4, "f": 5, "f#": 6, "gb": 6, "g": 7, "g#": 8, "ab": 8, "a": 9, "a#": 10, "bb": 10, "b": 11}

    def _root_pc(sym: str) -> int:
        m = __import__("re").match(r"^([A-Ga-g])([#b]?)", str(sym))
        return NOTE_PC.get((m.group(1) + m.group(2)).lower(), 0) if m else 0

    chord_progression = [
        {"bar": i + 1, "symbol": str(c), "bass_midi": 36 + _root_pc(c),
         "voicing_midi": [60 + _root_pc(c), 63 + _root_pc(c), 67 + _root_pc(c)]}
        for i, c in enumerate(chords)
    ]
    valence = float(meta.get("valence") or 0.0)
    sections = meta.get("sections") or [{"name": "song", "bars": 16}]
    bp = {
        "title": os.path.basename(proj),
        "genre": str(meta.get("mix_profile") or "trap_rap"),
        "mix_profile": str(meta.get("mix_profile") or "trap_rap"),
        "has_drums": any(classify_role(n) == "drums" for n in (haps.get("tracks") or {})),
        "seed": int(meta.get("seed") or 42),
        "emotion_vector": {"valence": valence, "tension": 0.5, "energy": 0.7, "depth": 0.5},
        "bpm": float(meta.get("bpm") or 120.0),
        "time_signature": {"numerator": int((meta.get("time_signature") or {}).get("numerator") or 4),
                           "denominator": int((meta.get("time_signature") or {}).get("denominator") or 4)},
        "key": str(meta.get("key") or "C"),
        "harmonic_core": {"key": str(meta.get("key") or "C"), "mode": str(meta.get("mode") or "minor"),
                          "progression_bars": 1, "chord_progression": chord_progression,
                          "motif_cell": {"intervals_from_tonic": [0, 3, 7], "rhythm_beats": [0, 2],
                                         "apex_midi": 67 if valence < -0.2 else 72},
                          "phrase_plan": "A-A'-A''-B"},
        "structure": [{"section": str(s.get("name") or "song"), "start_bar": i + 1, "bars": int(s.get("bars") or 4), "energy": 0.7}
                      for i, s in enumerate(sections)],
        "tracks": tracks,
    }
    import jsonschema
    with open(os.path.join(ROOT, "schemas", "song_blueprint.schema.json"), encoding="utf-8") as fh:
        jsonschema.validate(bp, json.load(fh))  # échec = bug : le blueprint auto doit être valide
    with open(bp_path, "w", encoding="utf-8") as fh:
        json.dump(bp, fh, ensure_ascii=False, indent=2)
    return bp


_SLOT_FOR = {"kick": "kick", "snare": "snare_clap", "clap": "snare_clap", "hihat": "hihat_perc",
             "hat": "hihat_perc", "perc": "hihat_perc", "bass": "bass_sub", "808": "bass_sub",
             "sub": "bass_sub", "chords": "chord_keys", "keys": "chord_keys", "pad": "pad_atmosphere",
             "lead": "lead_melody", "melody": "lead_melody", "counter": "lead_melody", "fx": "fx"}


def _resolve_palette(proj: str, bp: dict, palette_id: str) -> dict:
    """v4.1 — branche les VRAIS samples de l'index sur les pistes du blueprint, quel que soit
    `palette_id` ("auto" = meilleure palette ; sinon la palette demandée, repli meilleure).
    Interroge l'index (`samples.db` global + `Samples/`/`samples/` du projet) via
    `propose_palettes` (gate d'agréabilité appliqué) et préfère la palette qui embarque le
    PLUS de samples réels — un morceau doit utiliser les sons fournis, pas retomber sur les
    synthés `gl.*`. Repli `gl.*` par archetype (`pick_gl_plugin`) piste par piste sinon.
    0 token, jamais bloquant."""
    out = {"palette_id": palette_id, "resolved": False}
    try:
        index_dir = os.path.join(ROOT, "mcp-servers", "audio-index")
        if index_dir not in sys.path:
            sys.path.insert(0, index_dir)
        from indexer import DEFAULT_DB_PATH
        from palette_builder import propose_palettes
        from timbre_pleasantness import pick_gl_plugin, pleasantness_gate
        proj_samples = None
        for cand in (os.path.join(proj, "Samples"), os.path.join(proj, "samples")):
            if os.path.isdir(cand):
                proj_samples = cand
                break
        res = propose_palettes(
            mix_profile=str(bp.get("mix_profile") or "trap_rap"),
            emotion_vector=bp.get("emotion_vector") or {},
            target_key=f"{bp.get('key', 'C')} {bp.get('harmonic_core', {}).get('mode', 'minor')}",
            project_samples_dir=proj_samples,
            db_path=DEFAULT_DB_PATH,
            bpm=float(bp.get("bpm") or 0) or None,
        )
        pals = res.get("palettes") or []
        if not pals:
            out["reason"] = "no_palette"
            return out

        def _n_samples(p: dict) -> int:
            return sum(1 for s in (p.get("slots") or [])
                       if s.get("source_type") in ("sample", "project_sample") and s.get("filepath"))

        def _rank(p: dict) -> tuple:
            return (_n_samples(p), float(p.get("score") or 0.0))

        if palette_id and palette_id != "auto":
            chosen = next((p for p in pals if p.get("palette_id") == palette_id), None) or max(pals, key=_rank)
        else:
            chosen = max(pals, key=_rank)
        slots = chosen.get("slots") or []
        role_to_slot = {str(s.get("role")): s for s in slots}
        valence = float((bp.get("emotion_vector") or {}).get("valence", 0.0))
        prof = str(bp.get("mix_profile") or "trap_rap")
        n_samples = 0
        for tr in bp.get("tracks", []):
            role = str(tr.get("role") or "")
            if role == "vocals":
                continue
            key = str(tr.get("name") or role).lower()
            slot = role_to_slot.get(_SLOT_FOR.get(key, role))
            if (slot and slot.get("source_type") in ("sample", "project_sample") and slot.get("filepath")
                    and slot.get("midi_root") is not None
                    and pleasantness_gate({
                        "r_harsh": slot.get("r_harsh"), "roughness": slot.get("roughness"),
                        "peak_harsh_db": slot.get("peak_harsh_db"), "comfort_score": slot.get("comfort_score"),
                        "centroid_hz": slot.get("centroid_hz"), "crest_factor_db": slot.get("crest_factor_db"),
                    }, str(slot.get("role") or role))["verdict"] != "SWAP"):
                tr["source_type"] = "sample"
                tr["sample_path"] = str(slot["filepath"])
                tr["plugin"] = "gl.sampler"
                tr["root_note"] = int(slot["midi_root"])
                if slot.get("recommended_fine_tune_ct"):
                    tr["fine_tune_ct"] = int(slot["recommended_fine_tune_ct"])
                n_samples += 1
            else:
                tr["source_type"] = "gridline_native"
                tr["plugin"] = pick_gl_plugin(key, prof, valence)["plugin"]
        bp["palette"] = {"palette_id": chosen.get("palette_id"), "score": chosen.get("score"),
                         "theme": chosen.get("theme"), "n_samples": n_samples,
                         "timbre_coherence": chosen.get("timbre_coherence"),
                         "project_samples_dir": proj_samples}
        out.update({"resolved": True, "palette_id": chosen.get("palette_id"),
                    "n_samples": n_samples, "theme": chosen.get("theme")})
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


def compose_project(project_dir: str, strudel_code: str, palette_id: str = "auto",
                    auto_master: bool = True, auto_prepare: bool = False,
                    remix_source_path: str | None = None, lyrics_spec: dict | None = None) -> dict[str, Any]:
    """Tour 2 atomique : écrit composition.strudel, évalue (Node headless), compile le pont
    (ScoreIR + garde-fous 0 token), puis enchaîne compile_song + master si auto_master.
    v4.1 : `auto_prepare=True` (Scénario A sans Samples/) résout localement (0 token) un blueprint
    valide ; `remix_source_path` (Scénario C) / `lyrics_spec` (Scénario D) sont consignés dans le blueprint."""
    proj = os.path.abspath(os.path.expanduser(project_dir))
    os.makedirs(proj, exist_ok=True)
    strudel_path = os.path.join(proj, "composition.strudel")
    with open(strudel_path, "w", encoding="utf-8") as fh:
        fh.write(strudel_code if strudel_code.endswith("\n") else strudel_code + "\n")

    node = shutil.which("node")
    if not node or not os.path.isdir(os.path.join(SCRIPTS, "node_modules", "@strudel", "core")):
        return {"ok": False, "stage": "strudel_deps",
                "error": "node/@strudel manquants : `cd scripts && npm install` (voir scripts/package.json)"}

    report: dict[str, Any] = {}
    try:
        report = _run_bridge(proj)
    except RuntimeError as exc:
        msg = str(exc)
        if "echoue" in msg or "ERR L" in msg:
            tail = msg[-240:]
            return {"ok": False, "stage": "lint", "error": tail}
        return {"ok": False, "stage": "bridge", "error": msg[-300:]}
    report["palette_id"] = palette_id
    bp = _ensure_blueprint(proj, report)
    # v4.1 : résolution de palette SYSTÉMATIQUE — branche les vrais samples de l'index
    # (samples.db + Samples/ projet) sur les pistes, quel que soit palette_id ("auto" = meilleure)
    report["palette"] = _resolve_palette(proj, bp, palette_id)
    import jsonschema
    with open(os.path.join(ROOT, "schemas", "song_blueprint.schema.json"), encoding="utf-8") as fh:
        jsonschema.validate(bp, json.load(fh))
    with open(os.path.join(proj, "song_blueprint.json"), "w", encoding="utf-8") as fh:
        json.dump(bp, fh, ensure_ascii=False, indent=2)
    if auto_prepare:
        bp["auto_prepared"] = True
        report["auto_prepare"] = True
    if remix_source_path:
        bp["scenario"] = "C"
        bp["remix_source_path"] = remix_source_path
    if lyrics_spec:
        bp["scenario"] = "D"
        bp["lyrics_spec"] = lyrics_spec
    if bp.get("scenario") or bp.get("auto_prepared"):
        with open(os.path.join(proj, "song_blueprint.json"), "w", encoding="utf-8") as fh:
            json.dump(bp, fh, ensure_ascii=False, indent=2)

    master = None
    if auto_master and report.get("ok"):
        compile_song = os.path.join(SCRIPTS, "compile_song.py")
        proc = subprocess.run([sys.executable, compile_song, proj], capture_output=True, text=True, timeout=900)
        try:
            crep = json.loads(proc.stdout)
            report["compile_song"] = {"ok": crep.get("ok"), "stage": crep.get("stage")}
        except Exception:
            report["compile_song"] = {"ok": proc.returncode == 0, "rc": proc.returncode, "stderr_tail": proc.stderr[-200:]}
        if shutil.which("gridline") and report["compile_song"].get("ok"):
            ra = os.path.join(SCRIPTS, "render_and_audit.py")
            rproc = subprocess.run([sys.executable, ra, os.path.join(proj, "projet.gridline"),
                                    "-o", os.path.join(proj, "renders", "master.wav"), "--mp3"],
                                   capture_output=True, text=True, timeout=1800)
            master = os.path.join(proj, "renders", "master.wav") if rproc.returncode == 0 else None
            report["render"] = {"ok": rproc.returncode == 0}
    report["master"] = master
    report["handoff"] = write_handoff(proj, report)
    return report


def refine_project(project_dir: str, delta_json: dict | None = None, strudel_patch: dict | None = None,
                   semantic_patch: dict | None = None, export_mp3: bool = True) -> dict[str, Any]:
    """Tour 3b quasi gratuit : delta_json (mix), strudel_patch (2-4 lignes Strudel),
    ou v4.1 semantic_patch micro-tokens sur le ScoreIR ({"lead": {"oct": -1, "rests": "+15%"}},
    {"section": "chorus", "ops": {"energy": "+2dB"}}), puis handoff.txt."""
    proj = os.path.abspath(os.path.expanduser(project_dir))
    report: dict[str, Any] = {"ok": True, "project_dir": proj}
    strudel_path = os.path.join(proj, "composition.strudel")
    if strudel_patch:
        if not os.path.isfile(strudel_path):
            return {"ok": False, "stage": "no_strudel", "error": strudel_path}
        apply_strudel_patch(strudel_path, strudel_patch)
        report["patched"] = strudel_patch.get("replace_track")
        try:
            rep = _run_bridge(proj)
            report.update({k: rep.get(k) for k in ("ok", "n_tracks", "n_notes", "guards", "evaluate", "compile", "full_coverage", "sections", "seed")})
        except RuntimeError as exc:
            return {"ok": False, "stage": "lint", "error": str(exc)[-240:]}
    if semantic_patch:
        import strudel_to_score_ir as bridge
        patch_result = bridge.apply_score_ir_patch(proj, semantic_patch)
        report["semantic_patch"] = patch_result
        if not patch_result.get("ok"):
            return report
    if delta_json:
        with open(os.path.join(proj, "refine_delta.json"), "w", encoding="utf-8") as fh:
            json.dump(delta_json, fh, ensure_ascii=False, indent=2)
        report["delta_json"] = "refine_delta.json (consommé comme user_overrides par math_mix_solver / autofix)"
        if export_mp3 and shutil.which("gridline"):
            ra = os.path.join(SCRIPTS, "render_and_audit.py")
            gp = os.path.join(proj, "projet.gridline")
            if os.path.isfile(gp):
                rproc = subprocess.run([sys.executable, ra, gp, "-o", os.path.join(proj, "renders", "master.wav"), "--mp3"],
                                       capture_output=True, text=True, timeout=1800)
                report["render"] = {"ok": rproc.returncode == 0}
                report["master"] = os.path.join(proj, "renders", "master.wav") if rproc.returncode == 0 else None
    report["handoff"] = write_handoff(proj, report)
    return report


def _summarize(report: dict[str, Any], limit: int = 400) -> str:
    if not report.get("ok"):
        return f"ERR {report.get('stage', '?')}: {str(report.get('error', ''))[:200]}"
    parts = [
        f"OK tracks={report.get('n_tracks', '?')} notes={report.get('n_notes', '?')}",
        f"seed={report.get('seed', '?')}",
        f"guards={json.dumps(report.get('guards', {}), separators=(',', ':'))[:120]}",
        f"eval={ (report.get('evaluate') or {}).get('passed') } fc={(report.get('full_coverage') or {}).get('passed')}",
    ]
    pf = report.get("preflight") or {}
    if pf.get("checks"):
        parts.append(f"gate={'PASS' if pf.get('passed') else 'FAIL'} gf={pf['checks'].get('gap_fill', {}).get('value')} sw={pf['checks'].get('stepwise_ratio', {}).get('value')} rest={pf['checks'].get('rest_ratio', {}).get('value')}")
    if report.get("master"):
        parts.append("master=renders/master.wav")
    if report.get("patched"):
        parts.append(f"patched=${report['patched']}")
    if report.get("delta_json"):
        parts.append("delta=refine_delta.json")
    if report.get("semantic_patch"):
        parts.append("semantic=" + ",".join(report["semantic_patch"].get("applied", []))[:80])
    parts.append(_palette_line(report))
    parts.append("handoff=handoff.txt")
    return " | ".join(parts)[:limit]


@_tool()
def studio_prepare(project_dir: str, intent: str = "", refs: list[str] | None = None,
                   remix: str | None = None, lyrics: dict | None = None) -> str:
    """Tour 1 : contexte + 3 palettes (format tabulaire <=450 cars)."""
    from audio_analyzer_server import studio_prepare_session
    raw = studio_prepare_session(project_dir, intent, reference_paths=refs,
                                 remix_source_path=remix, lyrics_spec=lyrics)
    try:
        prep = json.loads(raw)
    except Exception:
        return raw[:PREPARE_LIMIT]
    return format_prepare_compact(prep)


@_tool()
def studio_compose(project_dir: str, strudel_code: str, palette_id: str = "auto",
                   auto_master: bool = True, auto_prepare: bool = False,
                   remix_source_path: str | None = None, lyrics_spec: dict | None = None) -> str:
    """Tour 2 atomique : strudel -> ScoreIR + garde-fous 0 token -> blueprint valide (auto_prepare,
    0 token, Scénario A) -> master. Scénarios C/D : remix_source_path / lyrics_spec consignés dans
    le blueprint. Retour compact <=400 cars."""
    report = compose_project(project_dir, strudel_code, palette_id=palette_id, auto_master=auto_master,
                             auto_prepare=auto_prepare, remix_source_path=remix_source_path, lyrics_spec=lyrics_spec)
    return _summarize(report)


@_tool()
def studio_refine(project_dir: str, delta_json: dict | None = None, strudel_patch: dict | None = None,
                  semantic_patch: dict | None = None, export_mp3: bool = True) -> str:
    """Tour 3b : delta_json (mix), strudel_patch {replace_track, code}, ou semantic_patch
    {"lead": {"oct": -1, "rests": "+15%"}} / {"section": "chorus", "ops": {"energy": "+2dB"}} (20-40 tokens).
    Retour compact <=400 cars + handoff.txt."""
    report = refine_project(project_dir, delta_json=delta_json, strudel_patch=strudel_patch,
                            semantic_patch=semantic_patch, export_mp3=export_mp3)
    return _summarize(report)


if __name__ == "__main__":
    if mcp is None:
        raise SystemExit("SDK mcp indisponible : installer requirements.txt")
    mcp.run(transport="stdio")
