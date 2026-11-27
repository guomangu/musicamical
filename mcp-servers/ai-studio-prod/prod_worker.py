#!/usr/bin/env python3
"""
v4.3 (chantier D6 — Hot-Execution MCP) — worker subprocess éphémère de `ai-studio-prod`.

Architecture « façade MCP fine + worker éphémère » : `prod_server.py` (processus MCP
persistant, pur stdlib + `mcp`) ne contient que les 3 définitions d'outils et délègue chaque
appel à CE fichier via `.venv/bin/python prod_worker.py`. Le worker lit une requête JSON sur
`stdin` (`{"action": ..., "payload": {...}}`), importe la logique métier avec la version
EXACTEMENT présente sur disque de tous les scripts, et répond en JSON sur `stdout` :
`{"ok": true, "result": ..., "pid": ...}` (ou `{"ok": false, "error": ...}`).

Avantages : 0 `RESTART session agent` après un `git pull`/`install.sh` (sauf changement de
signature d'outil), isolation mémoire complète entre appels, timeout strict par appel.
Coût : ~40-60 ms de démarrage Python léger, négligeable devant eval Strudel + compile + rendu.

Usage direct (tests) : `echo '{"action":"ping","payload":{}}' | python prod_worker.py`
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "scripts")
for _d in (SCRIPTS, HERE,
           os.path.join(ROOT, "mcp-servers", "audio-analyzer"),
           os.path.join(ROOT, "mcp-servers", "audio-index")):
    if _d not in sys.path:
        sys.path.insert(0, _d)


def _dispatch(action: str, payload: dict) -> dict:
    out: dict = {"ok": False, "pid": os.getpid()}
    if action == "ping":
        out.update(ok=True, result={"pong": True, "pid": os.getpid(), "root": ROOT})
    elif action == "file_sha":
        path = str(payload.get("path") or "")
        with open(path, "rb") as fh:
            out.update(ok=True, result={"path": path, "sha256": hashlib.sha256(fh.read()).hexdigest()[:16]})
    elif action == "prepare":
        import prod_server
        out.update(ok=True, result=prod_server.prepare_project(
            project_dir=payload.get("project_dir", ""),
            intent=str(payload.get("intent") or ""),
            refs=payload.get("refs"),
            remix=payload.get("remix"),
            lyrics=payload.get("lyrics"),
        ))
    elif action == "compose":
        import prod_server
        report = prod_server.compose_project(
            project_dir=payload.get("project_dir", ""),
            strudel_code=str(payload.get("strudel_code") or ""),
            palette_id=str(payload.get("palette_id") or "auto"),
            intent=str(payload.get("intent") or ""),
            palette_strategy=str(payload.get("palette_strategy") or "semantic"),
            auto_master=bool(payload.get("auto_master", True)),
            auto_prepare=bool(payload.get("auto_prepare", False)),
            render_mode=str(payload.get("render_mode") or "full"),
            remix_source_path=payload.get("remix_source_path"),
            lyrics_spec=payload.get("lyrics_spec"),
        )
        out.update(ok=True, result=prod_server._summarize(report))
    elif action == "refine":
        import prod_server
        report = prod_server.refine_project(
            project_dir=payload.get("project_dir", ""),
            delta_json=payload.get("delta_json"),
            strudel_patch=payload.get("strudel_patch"),
            semantic_patch=payload.get("semantic_patch"),
            export_mp3=bool(payload.get("export_mp3", True)),
        )
        out.update(ok=True, result=prod_server._summarize(report))
    else:
        out["error"] = f"action inconnue: {action!r}"
    return out


def main() -> None:
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"stdin JSON invalide: {exc}"}))
        return
    action = str(req.get("action") or "")
    payload = req.get("payload") or {}
    try:
        res = _dispatch(action, payload if isinstance(payload, dict) else {})
    except Exception as exc:
        res = {"ok": False, "pid": os.getpid(), "error": f"{type(exc).__name__}: {exc}"[:400]}
    sys.stdout.write(json.dumps(res, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
