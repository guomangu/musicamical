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


def _code_sha() -> str:
    try:
        with open(__file__, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except Exception:
        return ""


_SELF_SHA_AT_BOOT = _code_sha()


def _server_stale() -> bool:
    """True si prod_server.py a changé sur disque depuis le démarrage du serveur MCP (ex. git pull) :
    le processus en cours tourne encore l'ancien code -> redémarrer la session agent (Zed/Antigravity)."""
    return bool(_SELF_SHA_AT_BOOT) and _code_sha() != _SELF_SHA_AT_BOOT
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
    sg_line = ""
    sg = report.get("sound_guard") or (report.get("palette") or {}).get("sound_guard")
    if isinstance(sg, dict) and sg.get("summary_line"):
        sg_line = str(sg["summary_line"])[:160]
    elif isinstance(sg, str) and sg:
        sg_line = sg[:160]
    ms = report.get("musical_safety") or (report.get("palette") or {}).get("musical_safety")
    ms_line = (
        f"safety={ms.get('errors', 0)}err/{ms.get('warnings', 0)}warn"
        if isinstance(ms, dict) and "errors" in ms
        else ""
    )
    master_line = (
        "master=renders/master.wav"
        if report.get("master")
        else ("master=STALE (ancien fichier present, rendu non effectue)"
              if report.get("master_stale")
              else "master=n/a (gridline absent ou auto_master off)")
    )
    lines = [
        f"HANDOFF v4 {os.path.basename(proj)}",
        f"strudel=composition.strudel#{sha} seed={report.get('seed', '?')}",
        f"bridge_ok={report.get('ok')} tracks={report.get('n_tracks', '?')} notes={report.get('n_notes', '?')}",
        f"sections={','.join(s.get('name', '?') for s in report.get('sections', []))}",
        f"eval_passed={ev.get('passed')} compile_passed={(report.get('compile') or {}).get('passed')} fc_passed={fc.get('passed')}",
        f"guards={json.dumps(report.get('guards', {}), separators=(',', ':'))}",
        pf_line,
        _palette_line(report) + (f" {ms_line}" if ms_line else ""),
        *([sg_line] if sg_line else []),
        *(["!! RESTART session agent : prod_server.py a changé depuis le démarrage du serveur MCP -> fermer/rouvrir la session (Zed/Antigravity)"] if report.get("stale_server") else []),
        "score_ir=score_ir.json plan=arrangement_plan.json haps=composition.strudel.haps.json",
        master_line,
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
    n_avail = pal.get("n_tracks_with_available_samples")
    n_s = pal.get("n_samples", 0)
    line = f"palette={pal.get('palette_id', '?')} samples={n_s}/{n_avail if n_avail else n_s}"
    if pal.get("n_excluded"):
        line += f" rej={int(pal['n_excluded'])}"
    if pal.get("fallback_gate") and pal["fallback_gate"] != "PASS":
        fb = pal.get("fallback_tracks") or []
        names = ",".join(str(f.get("track")) for f in fb[:5])
        line += f" FALLBACK_GATE={pal['fallback_gate']} ({names})"
    if pal.get("theme"):
        line += f" theme={pal.get('theme')}"
    if pal.get("error"):
        line += f" err={str(pal['error'])[:60]}"
    return line


def _run_bridge(proj: str, bars: int = 0, seed: int = 0) -> dict[str, Any]:
    import strudel_to_score_ir as bridge
    return bridge.strudel_to_score_ir(proj, run_eval=True, bars=bars, seed=seed)


ROLE_PLUGIN = {
    "kick": "gl.kick", "snare": "gl.snare", "hihat": "gl.hat", "clap": "gl.clap", "perc": "gl.clap", "rim": "gl.clap",
    "bass": "gl.analog", "chords": "gl.keys", "pad": "gl.keys", "lead": "gl.warmsynth",
    "counter": "gl.keys", "arp": "gl.keys", "arpeggio": "gl.keys", "pluck": "gl.keys",
    "vocals": "gl.sampler", "fx": "gl.wavetable", "drums": "gl.kick", "remix_stem": "gl.sampler",
}


def blueprint_structure(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`structure[*].start_bar` = position CUMULÉE en mesures (base 1) : pour
    I4-V16-C8-V16-C8-B8-O8 -> 1, 5, 21, 29, 45, 53, 61 (cohérent avec arrangement_plan
    base 0 : 0, 4, 20, 28, 44, 52, 60)."""
    out: list[dict[str, Any]] = []
    cursor = 1
    for s in sections or []:
        nb = int(s.get("bars") or 4)
        name = s.get("name") or s.get("section") or "song"
        out.append({"section": str(name), "start_bar": cursor,
                    "bars": nb, "energy": float(s.get("energy") or 0.7)})
        cursor += nb
    return out


def _structure_is_cumulative(structure: list[dict[str, Any]]) -> bool:
    """Détecte l'ancien bug start_bar = i+1 (1, 2, 3...) : non cumulatif si une section
    a plus d'une mesure et que start_bar vaut juste l'index+1 partout."""
    if not structure or len(structure) < 2:
        return True
    if not any(int(s.get("bars") or 0) > 1 for s in structure):
        return True
    return not all(int(s.get("start_bar") or 0) == i + 1 for i, s in enumerate(structure))


def upgrade_blueprint_tracks(bp: dict[str, Any]) -> dict[str, Any]:
    """Mise à niveau non destructive d'un blueprint existant (créé avant les correctifs
    qualité sonore) :
    - archétype explicite sur TOUTES les pistes (y compris batterie) ;
    - `lead`/`counter` en `gl.fm` (timbre métallique/électrique) -> plugin chaud de
      l'archétype calibré (ex. gl.warmsynth filtré) ;
    - `perc`/`rim` cloné sur `gl.hat` -> preset percussif distinct (gl.clap) ;
    - `structure[*].start_bar` recalculé en positions cumulées si l'ancien bug i+1
      est détecté.
    Ne touche jamais aux pistes `sample` (chemins fournis par l'utilisateur)."""
    prof = str(bp.get("mix_profile") or "trap_rap")
    val = float((bp.get("emotion_vector") or {}).get("valence", 0.0))
    from gl_dsl import resolve_archetype
    from score_ir import classify_role
    from timbre_pleasantness import pick_gl_plugin
    _ARCH_ROLE = {"kick": "kick", "snare": "snare", "clap": "snare", "hihat": "hihat", "hat": "hihat",
                  "perc": "perc", "rim": "perc", "bass": "bass", "808": "bass", "sub": "bass",
                  "chords": "chords", "keys": "chords", "piano": "chords", "pad": "pad", "strings": "pad",
                  "lead": "lead", "melody": "lead", "counter": "counter", "arp": "counter",
                  "arpeggio": "counter", "pluck": "counter", "fx": "fx"}
    for tr in bp.get("tracks") or []:
        if not isinstance(tr, dict):
            continue
        name = str(tr.get("name") or "").lower()
        role = str(tr.get("role") or "")
        # Corriger les anciens blueprints où `arp`/`pluck`/`piano` étaient classés `drums`
        if role == "drums" and classify_role(name) != "drums":
            role = classify_role(name)
            tr["role"] = role
        arch_role = next((v for k, v in _ARCH_ROLE.items() if k in name), role)
        arch = resolve_archetype(prof, arch_role, valence=val)
        if arch and not tr.get("archetype"):
            tr["archetype"] = f"{prof}:{arch_role}"
        if tr.get("source_type") == "gridline_native":
            if role in ("lead", "counter") and str(tr.get("plugin")) in ("gl.fm", "gl.pluck"):
                plug = pick_gl_plugin(name or role, prof, val)
                tr["plugin"] = plug["plugin"]
                if plug.get("preset"):
                    tr["preset"] = plug["preset"]
                if plug.get("params"):
                    tr["plugin_params"] = plug["params"]
            if role == "bass" and str(tr.get("plugin")) == "gl.fingerbass":
                plug = pick_gl_plugin("bass", prof, val)
                tr["plugin"] = plug["plugin"]
                if plug.get("preset"):
                    tr["preset"] = plug["preset"]
                if plug.get("params"):
                    tr["plugin_params"] = plug["params"]
            if arch_role == "perc" and str(tr.get("plugin")) == "gl.hat":
                tr["plugin"] = "gl.clap"
            if not tr.get("preset") and arch and arch.get("preset") and tr.get("plugin") == arch.get("plugin"):
                tr["preset"] = str(arch["preset"])
    st = bp.get("structure")
    if isinstance(st, list) and st and not _structure_is_cumulative(st):
        bp["structure"] = blueprint_structure(st)
    return bp


def _ensure_blueprint(proj: str, report: dict[str, Any]) -> dict[str, Any]:
    """v4.1 — blueprint minimal VALIDE contre schemas/song_blueprint.schema.json, déduit du Strudel :
    tracks = pistes `$` (rôle via classify_role, plugin gl.*), chord_progression = grille grid(),
    motif_cell.apex_midi selon valence, structure = sections du @meta."""
    bp_path = os.path.join(proj, "song_blueprint.json")
    if os.path.isfile(bp_path):
        with open(bp_path, encoding="utf-8") as fh:
            bp_old = json.load(fh)
        # Mise à niveau des blueprints créés avant les correctifs qualité sonore
        # (archétypes manquants, lead gl.fm métallique, perc cloné sur gl.hat, start_bar i+1)
        return upgrade_blueprint_tracks(bp_old)
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
    # rôle fin par NOM de piste pour les archétypes (kick/snare/hihat/perc ont le rôle
    # générique "drums" via classify_role mais leur archétype est par nom de kit)
    _ARCH_ROLE = {"kick": "kick", "snare": "snare", "clap": "snare", "hihat": "hihat", "hat": "hihat",
                  "perc": "perc", "rim": "perc", "bass": "bass", "808": "bass", "sub": "bass",
                  "chords": "chords", "keys": "chords", "piano": "chords", "pad": "pad", "strings": "pad",
                  "lead": "lead", "melody": "lead", "counter": "counter", "arp": "counter", "arpeggio": "counter",
                  "pluck": "counter", "fx": "fx", "vocal": "lead", "vocals": "lead"}
    from gl_dsl import resolve_archetype
    for name in (haps.get("tracks") or {}):
        role = classify_role(name)
        # v4.1 : archetype calibré (profil, rôle, valence) pour TOUTES les pistes, y compris percussions
        if role == "vocals":
            plug = {"plugin": "gl.sampler", "params": {}}
        else:
            plug = pick_gl_plugin(str(name).lower(), prof0, val0)
        plugin = plug["plugin"]
        lname = str(name).lower()
        arch_role = next((v for k, v in _ARCH_ROLE.items() if k in lname), role)
        arch = resolve_archetype(prof0, arch_role, valence=val0)
        tr = {"name": str(name), "role": role,
              "source_type": "vocaloid" if role == "vocals" else "gridline_native",
              "plugin": plugin, "plugin_params": plug.get("params") or {},
              "mixer_track": mixer_track}
        if plug.get("preset"):
            tr["preset"] = str(plug["preset"])
        if arch:
            tr["archetype"] = f"{prof0}:{arch_role}"
        tracks.append(tr)
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
    structure = blueprint_structure(sections)
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
        "structure": structure,
        "tracks": tracks,
    }
    import jsonschema
    with open(os.path.join(ROOT, "schemas", "song_blueprint.schema.json"), encoding="utf-8") as fh:
        jsonschema.validate(bp, json.load(fh))  # échec = bug : le blueprint auto doit être valide
    with open(bp_path, "w", encoding="utf-8") as fh:
        json.dump(bp, fh, ensure_ascii=False, indent=2)
    return bp


_SLOT_FOR = {"kick": "kick", "snare": "snare_clap", "clap": "snare_clap", "hihat": "hihat_perc",
             "hat": "hihat_perc", "open_hat": "hihat_perc", "perc": "hihat_perc", "rim": "hihat_perc",
             "bass": "bass_sub", "808": "bass_sub", "sub": "bass_sub",
             "chords": "chord_keys", "keys": "chord_keys", "piano": "chord_keys",
             "pad": "pad_atmosphere", "strings": "pad_atmosphere",
             "lead": "lead_melody", "melody": "lead_melody", "counter": "lead_melody",
             "arp": "lead_melody", "arpeggio": "lead_melody", "pluck": "lead_melody", "bell": "lead_melody",
             "fx": "fx"}


def _resolve_palette(proj: str, bp: dict, palette_id: str) -> dict:
    """v4.2 — branche les VRAIS samples de l'index sur les pistes du blueprint, quel que soit
    `palette_id` ("auto" = meilleure palette ; sinon la palette demandée, repli meilleure).
    - Scénario B (dossier `Samples/`/`samples/` projet ou `PROJECT_SAMPLES_DIR`) : verrouillé.
    - Scénario A (aucun dossier projet) : interroge `samples.db` (global) sans verrouiller
      faussement la banque racine, avec repli `<racine>/samples` si la DB est vide.
    - F3 : transmet `note_ranges` (depuis `score_ir.json`) à `propose_palettes`.
    - F6 : affectation de fichier unique par piste (`perc != hihat`, préférence `perc/`).
    - Garde finale `enforce_blueprint_sample_fit` (remplace par alternative saine -> `gl.*`,
      0 blocage) + `summarize_musical_safety` (0 token).
    """
    out: dict[str, Any] = {"palette_id": palette_id, "resolved": False}
    try:
        index_dir = os.path.join(ROOT, "mcp-servers", "audio-index")
        if index_dir not in sys.path:
            sys.path.insert(0, index_dir)
        from audit_project_sources import sample_fallback_gate
        from blueprint_safety import summarize_musical_safety
        from indexer import DEFAULT_DB_PATH, reroute_sample_path
        from palette_builder import propose_palettes
        from sample_fit_guard import (
            cached_note_ranges_and_catalog,
            enforce_blueprint_sample_fit,
            infer_sample_category,
            prepare_octave_up_808,
            register_fit,
            resolve_catalog_path,
        )
        from timbre_pleasantness import pick_gl_plugin, pleasantness_gate

        try:
            from env_loader import load_dotenv
            load_dotenv()
        except Exception:
            pass

        # Scénario B (verrouillé) uniquement si l'utilisateur a fourni un dossier Samples/ projet
        # ou un PROJECT_SAMPLES_DIR explicite (§5). La banque racine `<racine>/samples` n'est plus
        # verrouillée comme projet : Scénario A interroge d'abord `samples.db`.
        proj_samples = None
        user_locked = False
        for cand in (os.path.join(proj, "Samples"), os.path.join(proj, "samples")):
            if os.path.isdir(cand):
                proj_samples = cand
                user_locked = True
                break
        if proj_samples is None:
            env_proj = os.environ.get("PROJECT_SAMPLES_DIR")
            if env_proj and os.path.isdir(os.path.expanduser(env_proj)):
                proj_samples = os.path.expanduser(env_proj)
                user_locked = True

        # F3 : calculer les plages de notes réelles depuis score_ir.json (avec cache léger §5)
        cached = cached_note_ranges_and_catalog(proj, bp=bp, slot_for=_SLOT_FOR)
        notes_by_track = cached["notes_by_track"]
        note_ranges = cached["note_ranges"]

        common_kw = dict(
            mix_profile=str(bp.get("mix_profile") or "trap_rap"),
            emotion_vector=bp.get("emotion_vector") or {},
            target_key=f"{bp.get('key', 'C')} {bp.get('harmonic_core', {}).get('mode', 'minor')}",
            db_path=DEFAULT_DB_PATH,
            bpm=float(bp.get("bpm") or 0) or None,
            note_ranges=note_ranges or None,
            include_pool=True,
        )
        if proj_samples is not None:
            res = propose_palettes(project_samples_dir=proj_samples, user_samples_locked=user_locked, **common_kw)
        else:
            res = propose_palettes(project_samples_dir=None, user_samples_locked=False, **common_kw)
            # Repli automatique vers AI_STUDIO_SAMPLES_DIR / <racine>/samples si samples.db est vide/absent
            has_db_samples = any(
                s.get("source_type") in ("sample", "project_sample") and s.get("filepath")
                for p in (res.get("palettes") or [])
                for s in (p.get("slots") or [])
            )
            if not has_db_samples:
                fallback_dir = None
                env_sdir = os.environ.get("AI_STUDIO_SAMPLES_DIR")
                if env_sdir and os.path.isdir(os.path.expanduser(env_sdir)):
                    fallback_dir = os.path.expanduser(env_sdir)
                elif os.path.isdir(os.path.join(ROOT, "samples")):
                    fallback_dir = os.path.join(ROOT, "samples")
                if fallback_dir:
                    proj_samples = fallback_dir
                    res = propose_palettes(project_samples_dir=fallback_dir, user_samples_locked=False, **common_kw)

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
        candidate_pool: dict[str, list[dict[str, Any]]] = res.get("candidate_pool") or {}
        valence = float((bp.get("emotion_vector") or {}).get("valence", 0.0))
        prof = str(bp.get("mix_profile") or "trap_rap")

        # Hauteurs notées des pistes (score_ir.json) : pour les PERCUSSIONS, root_note = hauteur
        # notée du kit (kick=36, snare=38, hihat=42...) et PAS le pitch détecté du one-shot
        notated: dict[str, int] = {}
        for tname, ps in notes_by_track.items():
            if ps:
                notated[str(tname).lower()] = max(set(ps), key=ps.count)

        GM_DRUM = {"kick": 36, "snare": 38, "clap": 39, "hihat": 42, "hat": 42, "perc": 39, "rim": 39}
        PERC_SLOT_ROLES = {"kick", "snare_clap", "hihat_perc"}

        used_paths: set[str] = set()

        def _pick_unique_from_pool(slot_role: str, track_key: str) -> dict[str, Any] | None:
            pool_cands = list(candidate_pool.get(slot_role) or [])
            if track_key in ("perc", "rim"):
                pool_cands.sort(
                    key=lambda c: (
                        0 if infer_sample_category(c.get("category"), c.get("filepath")) == "perc" else 1,
                        -(float(c.get("role_score", 0.5)) * 0.6 + float(c.get("comfort_score", 0.5)) * 0.4),
                    )
                )
            elif track_key in ("hihat", "hat", "open_hat"):
                # Un hi-hat doit venir de hat/ ou open_hat/ (jamais un tom grave perc/ comme dnb_perc_ghost !)
                hat_only = [
                    c for c in pool_cands
                    if infer_sample_category(c.get("category"), c.get("filepath")) in ("hat", "open_hat")
                ]
                pool_cands = hat_only
                pool_cands.sort(
                    key=lambda c: (
                        -(float(c.get("role_score", 0.5)) * 0.6 + float(c.get("comfort_score", 0.5)) * 0.4),
                    )
                )
            for c in pool_cands:
                if c.get("source_type") not in ("sample", "project_sample"):
                    continue
                rp = reroute_sample_path(c.get("filepath"))
                if not rp:
                    continue
                norm = os.path.normpath(os.path.abspath(rp)).lower()
                if norm in used_paths:
                    continue
                if pleasantness_gate({
                    "r_harsh": c.get("r_harsh"), "roughness": c.get("roughness"),
                    "peak_harsh_db": c.get("peak_harsh_db"), "comfort_score": c.get("comfort_score"),
                    "centroid_hz": c.get("centroid_hz"), "crest_factor_db": c.get("crest_factor_db"),
                    "sample_nature": c.get("sample_nature"), "category": c.get("category"),
                    "inharmonicity": c.get("inharmonicity"), "filepath": rp,
                }, slot_role)["verdict"] == "SWAP":
                    continue
                copied = dict(c)
                copied["filepath"] = rp
                return copied
            return None

        for tr in bp.get("tracks", []):
            role = str(tr.get("role") or "")
            if role == "vocals":
                continue
            key = str(tr.get("name") or role).lower()
            slot_role = _SLOT_FOR.get(key, role)
            slot = role_to_slot.get(slot_role)
            chosen_slot = slot

            # F6 : éviter le partage de fichier (ex. perc == hihat) et préférer `hat/` pour hihat, `perc/` pour perc
            if key in ("perc", "rim", "hihat", "hat", "open_hat") and candidate_pool.get(slot_role):
                alt_drum = _pick_unique_from_pool(slot_role, key)
                if alt_drum is not None:
                    chosen_slot = alt_drum

            rpath = reroute_sample_path(chosen_slot.get("filepath")) if chosen_slot else None
            if rpath and os.path.normpath(os.path.abspath(rpath)).lower() in used_paths:
                alt = _pick_unique_from_pool(slot_role, key)
                if alt is not None:
                    chosen_slot = alt
                    rpath = alt["filepath"]
                else:
                    chosen_slot = None
                    rpath = None

            if (chosen_slot and chosen_slot.get("source_type") in ("sample", "project_sample") and rpath
                    and pleasantness_gate({
                        "r_harsh": chosen_slot.get("r_harsh"), "roughness": chosen_slot.get("roughness"),
                        "peak_harsh_db": chosen_slot.get("peak_harsh_db"), "comfort_score": chosen_slot.get("comfort_score"),
                        "centroid_hz": chosen_slot.get("centroid_hz"), "crest_factor_db": chosen_slot.get("crest_factor_db"),
                        "sample_nature": chosen_slot.get("sample_nature"), "category": chosen_slot.get("category"),
                        "inharmonicity": chosen_slot.get("inharmonicity"), "filepath": rpath,
                    }, str(chosen_slot.get("role") or slot_role))["verdict"] != "SWAP"):
                is_perc_track = role == "drums" or key in GM_DRUM or str(chosen_slot.get("role")) in PERC_SLOT_ROLES
                if is_perc_track:
                    rn = notated.get(key) or GM_DRUM.get(key) or chosen_slot.get("midi_root")
                else:
                    rn = chosen_slot.get("midi_root")
                if rn is None:
                    pg = pick_gl_plugin(key, prof, valence)
                    tr["source_type"] = "gridline_native"
                    tr["plugin"] = pg["plugin"]
                    if pg.get("preset"):
                        tr["preset"] = str(pg["preset"])
                    else:
                        tr.pop("preset", None)
                    if pg.get("params"):
                        tr["plugin_params"] = pg["params"]
                    for stale_k in ("sample_path", "root_note", "fine_tune_ct"):
                        tr.pop(stale_k, None)
                    continue
                # Auto-dérivation +12 st sans artefacts phase-vocoder pour 808 grave (C1 -> C2, root=36)
                eff_path = rpath
                eff_rn = int(max(0, min(127, int(rn))))
                if not is_perc_track and slot_role == "bass_sub" and eff_rn <= 28:
                    rf_b = register_fit(eff_rn, notes_by_track.get(key), chosen_slot, role="bass_sub")
                    if rf_b.get("octave_up_needed"):
                        derived = prepare_octave_up_808(rpath, proj, eff_rn)
                        if derived is not None:
                            eff_path, eff_rn = derived
                tr["source_type"] = "sample"
                tr["sample_path"] = eff_path
                tr["plugin"] = "gl.sampler"
                tr.pop("preset", None)
                tr["root_note"] = eff_rn
                # Ne JAMAIS appliquer fine_tune_ct mélodique sur les percussions non-kick (snare/clap/hihat/perc)
                if is_perc_track and key != "kick":
                    tr["fine_tune_ct"] = 0
                elif chosen_slot.get("recommended_fine_tune_ct"):
                    tr["fine_tune_ct"] = int(chosen_slot["recommended_fine_tune_ct"])
                else:
                    tr.pop("fine_tune_ct", None)
                used_paths.add(os.path.normpath(os.path.abspath(rpath)).lower())
            else:
                pg = pick_gl_plugin(key, prof, valence)
                tr["source_type"] = "gridline_native"
                tr["plugin"] = pg["plugin"]
                if pg.get("preset"):
                    tr["preset"] = str(pg["preset"])
                else:
                    tr.pop("preset", None)
                if pg.get("params"):
                    tr["plugin_params"] = pg["params"]  # cutoff/ADSR bornés, appliqués au rendu
                for stale_k in ("sample_path", "root_note", "fine_tune_ct"):
                    tr.pop(stale_k, None)

        # Garde finale (F3/F4/F5/F6) : vérifie cohérence complète du blueprint, substitue sinon gl.*
        cat_path = resolve_catalog_path(proj, bp={"palette": {"project_samples_dir": proj_samples}})
        guard_rep = enforce_blueprint_sample_fit(
            bp,
            notes_by_track=notes_by_track,
            catalog_path=cat_path,
            pool_by_role=candidate_pool,
            valence=valence,
            mix_profile=prof,
            slot_for=_SLOT_FOR,
            write_diag_dir=proj,
        )

        n_samples = sum(
            1 for tr in (bp.get("tracks") or [])
            if isinstance(tr, dict) and tr.get("source_type") in ("sample", "loop") and tr.get("sample_path")
        )
        safety_rep = summarize_musical_safety(
            bp, catalog_path=cat_path, notes_by_track=notes_by_track or None, project_dir=proj
        )
        s_max_violations = sum(
            1 for iss in (safety_rep.get("issues") or [])
            if iss.get("code") == "max_semitone_shift_exceeded"
        )

        # Garde-fou anti-fallback-silencieux (P0 + F8)
        allow_fallback = str(os.environ.get("ALLOW_SYNTH_FALLBACK", "")).lower() in ("1", "true", "yes")
        gate_res = sample_fallback_gate(
            bp,
            {"slots": chosen.get("slots") or []},
            slot_for=_SLOT_FOR,
            min_ratio=0.7,
            allow_override=allow_fallback,
            s_max_violations=s_max_violations,
        )
        gate_res["n_samples"] = n_samples
        gate_res["sample_coverage_ratio"] = round(
            (n_samples / gate_res["n_tracks_with_available_samples"])
            if gate_res["n_tracks_with_available_samples"] else 1.0,
            3,
        )
        ratio_pass = bool(gate_res["passed"] or allow_fallback or gate_res["n_tracks_with_available_samples"] == 0)
        gate_res["passed"] = bool(ratio_pass and s_max_violations <= 1)
        gate_res["gate"] = "PASS" if gate_res["passed"] else "FAIL"
        fallback_tracks = gate_res["fallback_tracks"]
        sel_trace = res.get("selection_trace") or {}
        n_excluded = int(sel_trace.get("total_excluded") or 0)
        ms_compact = {
            "ok": safety_rep.get("ok"),
            "errors": safety_rep.get("errors", 0),
            "warnings": safety_rep.get("warnings", 0),
            "catalog": safety_rep.get("catalog"),
            "catalog_missing": safety_rep.get("catalog_missing", False),
        }
        sg_compact = {
            "status": guard_rep["status"],
            "n_fixed": guard_rep["n_fixed"],
            "summary_line": guard_rep["summary_line"],
        }
        bp["palette"] = {
            "palette_id": chosen.get("palette_id"),
            "score": chosen.get("score"),
            "quality_score": chosen.get("quality_score", chosen.get("score")),
            "sample_ratio": chosen.get("sample_ratio"),
            "theme": chosen.get("theme"),
            "n_samples": n_samples,
            "n_tracks_with_available_samples": gate_res["n_tracks_with_available_samples"],
            "sample_coverage_ratio": gate_res["sample_coverage_ratio"],
            "fallback_gate": gate_res["gate"],
            "fallback_tracks": fallback_tracks,
            "selection_trace": sel_trace,
            "timbre_coherence": chosen.get("timbre_coherence"),
            "project_samples_dir": proj_samples,
            "user_samples_locked": user_locked,
            "sound_guard": sg_compact,
            "musical_safety": ms_compact,
        }
        out.update({
            "resolved": True,
            "palette_id": chosen.get("palette_id"),
            "n_samples": n_samples,
            "n_tracks_with_available_samples": gate_res["n_tracks_with_available_samples"],
            "sample_coverage_ratio": gate_res["sample_coverage_ratio"],
            "fallback_gate": gate_res["gate"],
            "fallback_tracks": fallback_tracks,
            "theme": chosen.get("theme"),
            "n_excluded": n_excluded,
            "sound_guard": sg_compact,
            "musical_safety": ms_compact,
        })
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
    if _server_stale():
        report["stale_server"] = True  # code changé depuis le démarrage du serveur MCP (git pull)
    bp = _ensure_blueprint(proj, report)
    # v4.1 : résolution de palette SYSTÉMATIQUE — branche les vrais samples de l'index
    # (samples.db + Samples/ projet) sur les pistes, quel que soit palette_id ("auto" = meilleure)
    report["palette"] = _resolve_palette(proj, bp, palette_id)
    if isinstance(report["palette"], dict):
        if report["palette"].get("sound_guard"):
            report["sound_guard"] = report["palette"]["sound_guard"]
        if report["palette"].get("musical_safety"):
            report["musical_safety"] = report["palette"]["musical_safety"]
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

    # F10 : si aucun rendu frais n'a eu lieu, purger les anciens renders/master.wav* périmés
    # pour éviter d'écouter un faux master d'une session ou source précédente.
    if master is None:
        renders_dir = os.path.join(proj, "renders")
        stale_candidates = [
            os.path.join(renders_dir, "master.wav"),
            os.path.join(renders_dir, "master.wav.origin.json"),
            os.path.join(renders_dir, "master.mp3"),
        ]
        stale_left = False
        for sp in stale_candidates:
            if os.path.isfile(sp):
                try:
                    os.remove(sp)
                except OSError:
                    stale_left = True
        if stale_left or os.path.isfile(os.path.join(renders_dir, "master.wav")):
            report["master_stale"] = True

    report["master"] = master
    report["handoff"] = write_handoff(proj, report)
    return report


def refine_project(project_dir: str, delta_json: dict | None = None, strudel_patch: dict | None = None,
                   semantic_patch: dict | None = None, export_mp3: bool = True) -> dict[str, Any]:
    """Tour 3b quasi gratuit : delta_json (mix), strudel_patch (2-4 lignes Strudel),
    ou v4.1 semantic_patch micro-tokens sur le ScoreIR ({"lead": {"oct": -1, "rests": "+15%"}},
    {"section": "chorus", "ops": {"energy": "+2dB"}}), puis revalidation sonore (F11) + handoff.txt."""
    proj = os.path.abspath(os.path.expanduser(project_dir))
    report: dict[str, Any] = {"ok": True, "project_dir": proj}
    strudel_path = os.path.join(proj, "composition.strudel")
    notes_changed = False
    if strudel_patch:
        if not os.path.isfile(strudel_path):
            return {"ok": False, "stage": "no_strudel", "error": strudel_path}
        apply_strudel_patch(strudel_path, strudel_patch)
        report["patched"] = strudel_patch.get("replace_track")
        try:
            rep = _run_bridge(proj)
            report.update({k: rep.get(k) for k in ("ok", "n_tracks", "n_notes", "guards", "evaluate", "compile", "full_coverage", "sections", "seed")})
            notes_changed = True
        except RuntimeError as exc:
            return {"ok": False, "stage": "lint", "error": str(exc)[-240:]}
    if semantic_patch:
        import strudel_to_score_ir as bridge
        patch_result = bridge.apply_score_ir_patch(proj, semantic_patch)
        report["semantic_patch"] = patch_result
        if not patch_result.get("ok"):
            return report
        notes_changed = True

    # F11 : après tout patch modifiant les notes/registres, revalider le blueprint avec
    # enforce_blueprint_sample_fit (évite un S_max hors cap après `lead oct +1` par ex.)
    bp_path = os.path.join(proj, "song_blueprint.json")
    if notes_changed and os.path.isfile(bp_path):
        try:
            from blueprint_safety import summarize_musical_safety
            from sample_fit_guard import (
                enforce_blueprint_sample_fit,
                notes_by_track_from_score_ir,
                resolve_catalog_path,
            )
            with open(bp_path, encoding="utf-8") as fh:
                bp = json.load(fh)
            notes_by_track = notes_by_track_from_score_ir(proj)
            cat_path = resolve_catalog_path(proj, bp=bp)
            guard_rep = enforce_blueprint_sample_fit(
                bp,
                notes_by_track=notes_by_track,
                catalog_path=cat_path,
                slot_for=_SLOT_FOR,
                write_diag_dir=proj,
            )
            safety_rep = summarize_musical_safety(
                bp, catalog_path=cat_path, notes_by_track=notes_by_track or None, project_dir=proj
            )
            report["sound_guard"] = {
                "status": guard_rep["status"],
                "n_fixed": guard_rep["n_fixed"],
                "summary_line": guard_rep["summary_line"],
            }
            report["musical_safety"] = {
                "ok": safety_rep.get("ok"),
                "errors": safety_rep.get("errors", 0),
                "warnings": safety_rep.get("warnings", 0),
                "catalog": safety_rep.get("catalog"),
            }
            if guard_rep["n_fixed"] > 0:
                if isinstance(bp.get("palette"), dict):
                    bp["palette"]["sound_guard"] = report["sound_guard"]
                    bp["palette"]["musical_safety"] = report["musical_safety"]
                with open(bp_path, "w", encoding="utf-8") as fh:
                    json.dump(bp, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            report["sound_guard_error"] = str(exc)[:120]

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
        f"guards={json.dumps(report.get('guards', {}), separators=(',', ':'))[:100]}",
        f"eval={ (report.get('evaluate') or {}).get('passed') } fc={(report.get('full_coverage') or {}).get('passed')}",
    ]
    pf = report.get("preflight") or {}
    if pf.get("checks"):
        parts.append(f"gate={'PASS' if pf.get('passed') else 'FAIL'} gf={pf['checks'].get('gap_fill', {}).get('value')} sw={pf['checks'].get('stepwise_ratio', {}).get('value')} rest={pf['checks'].get('rest_ratio', {}).get('value')}")
    ms = report.get("musical_safety") or (report.get("palette") or {}).get("musical_safety")
    if isinstance(ms, dict) and "errors" in ms:
        parts.append(f"safety={ms.get('errors', 0)}err/{ms.get('warnings', 0)}warn")
    sg = report.get("sound_guard") or (report.get("palette") or {}).get("sound_guard")
    if isinstance(sg, dict) and sg.get("status"):
        parts.append(f"guard={sg['status']}" + (f"({sg.get('n_fixed', 0)})" if sg.get("n_fixed") else ""))
    if report.get("master"):
        parts.append("master=renders/master.wav")
    elif report.get("master_stale"):
        parts.append("master=STALE")
    if report.get("patched"):
        parts.append(f"patched=${report['patched']}")
    if report.get("delta_json"):
        parts.append("delta=refine_delta.json")
    if report.get("semantic_patch"):
        parts.append("semantic=" + ",".join(report["semantic_patch"].get("applied", []))[:80])
    parts.append(_palette_line(report))
    if report.get("stale_server"):
        parts.append("!! RESTART session agent (prod_server.py modifié depuis le démarrage du serveur MCP)")
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
