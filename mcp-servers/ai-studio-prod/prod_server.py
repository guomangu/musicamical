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
    lines = [
        f"HANDOFF v4 {os.path.basename(proj)}",
        f"strudel=composition.strudel#{sha} seed={report.get('seed', '?')}",
        f"bridge_ok={report.get('ok')} tracks={report.get('n_tracks', '?')} notes={report.get('n_notes', '?')}",
        f"sections={','.join(s.get('name', '?') for s in report.get('sections', []))}",
        f"eval_passed={ev.get('passed')} compile_passed={(report.get('compile') or {}).get('passed')} fc_passed={fc.get('passed')}",
        f"guards={json.dumps(report.get('guards', {}), separators=(',', ':'))}",
        f"score_ir=score_ir.json plan=arrangement_plan.json haps=composition.strudel.haps.json",
        f"master={'renders/master.wav' if report.get('master') else 'n/a (gridline absent ou auto_master off)'}",
        f"next: studio_refine(delta_json=...) ou strudel_patch={{\"replace_track\":\"lead\",\"code\":\"...\"}}",
    ]
    text = "\n".join(lines)
    if len(text) > HANDOFF_LIMIT:
        text = text[:HANDOFF_LIMIT]
    path = os.path.join(proj, "handoff.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return path


def _run_bridge(proj: str, bars: int = 0, seed: int = 0) -> dict[str, Any]:
    import strudel_to_score_ir as bridge
    return bridge.strudel_to_score_ir(proj, run_eval=True, bars=bars, seed=seed)


def _ensure_blueprint(proj: str, report: dict[str, Any]) -> dict[str, Any]:
    bp_path = os.path.join(proj, "song_blueprint.json")
    if os.path.isfile(bp_path):
        with open(bp_path, encoding="utf-8") as fh:
            return json.load(fh)
    haps_path = os.path.join(proj, "composition.strudel.haps.json")
    meta = {}
    if os.path.isfile(haps_path):
        with open(haps_path, encoding="utf-8") as fh:
            meta = (json.load(fh).get("meta") or {})
    sections = meta.get("sections") or [{"name": "song", "bars": report.get("sections") and report["sections"][0]["bars"] or 16}]
    bp = {
        "title": os.path.basename(proj),
        "genre": str(meta.get("mix_profile") or "trap_rap"),
        "mix_profile": str(meta.get("mix_profile") or "trap_rap"),
        "has_drums": True,
        "seed": int(meta.get("seed") or 42),
        "emotion_vector": {"valence": float(meta.get("valence") or 0.0), "tension": 0.5, "energy": 0.7, "depth": 0.5},
        "bpm": float(meta.get("bpm") or 120.0),
        "time_signature": {"numerator": int((meta.get("time_signature") or {}).get("numerator") or 4),
                           "denominator": int((meta.get("time_signature") or {}).get("denominator") or 4)},
        "key": str(meta.get("key") or "C"),
        "harmonic_core": {"key": str(meta.get("key") or "C"), "mode": str(meta.get("mode") or "minor"),
                          "progression_bars": 1, "chord_progression": [], "motif_cell": {}, "phrase_plan": "A-A'-A''-B"},
        "structure": [{"section": str(s.get("name") or "song"), "start_bar": i + 1, "bars": int(s.get("bars") or 4), "energy": 0.7}
                      for i, s in enumerate(sections)],
        "tracks": [],
    }
    try:
        import jsonschema
        with open(os.path.join(ROOT, "schemas", "song_blueprint.schema.json"), encoding="utf-8") as fh:
            jsonschema.validate(bp, json.load(fh))
    except Exception:
        pass
    with open(bp_path, "w", encoding="utf-8") as fh:
        json.dump(bp, fh, ensure_ascii=False, indent=2)
    return bp


def compose_project(project_dir: str, strudel_code: str, palette_id: str = "auto",
                    auto_master: bool = True) -> dict[str, Any]:
    """Tour 2 atomique : écrit composition.strudel, évalue (Node headless), compile le pont
    (ScoreIR + garde-fous 0 token), puis enchaîne compile_song + master si auto_master."""
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
    _ensure_blueprint(proj, report)

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
                   export_mp3: bool = True) -> dict[str, Any]:
    """Tour 3b quasi gratuit : voie rapide delta_json (mix/énergie, 0 réécriture musicale)
    ou voie chirurgicale strudel_patch (2-4 lignes modifiées), puis handoff.txt."""
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
    if report.get("master"):
        parts.append("master=renders/master.wav")
    if report.get("patched"):
        parts.append(f"patched=${report['patched']}")
    if report.get("delta_json"):
        parts.append("delta=refine_delta.json")
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
                   auto_master: bool = True) -> str:
    """Tour 2 atomique : écrit composition.strudel, évalue (Node headless), compile ScoreIR +
    garde-fous 0 token, chaine master si auto_master. Retour compact <=400 cars."""
    report = compose_project(project_dir, strudel_code, palette_id=palette_id, auto_master=auto_master)
    return _summarize(report)


@_tool()
def studio_refine(project_dir: str, delta_json: dict | None = None, strudel_patch: dict | None = None,
                  export_mp3: bool = True) -> str:
    """Tour 3b : delta_json (mix, ~40 tokens) ou strudel_patch {replace_track, code} (diff minimal).
    Retour compact <=400 cars + handoff.txt."""
    report = refine_project(project_dir, delta_json=delta_json, strudel_patch=strudel_patch, export_mp3=export_mp3)
    return _summarize(report)


if __name__ == "__main__":
    if mcp is None:
        raise SystemExit("SDK mcp indisponible : installer requirements.txt")
    mcp.run(transport="stdio")
