#!/usr/bin/env python3
"""v4.0 hotfix — résumé 1 ligne d'un rapport d'indexation (stdin : JSON ou dict Python).

Appelé par install.sh (étape 8/8) via la fonction bash `summarize_index_report` :
  printf '%s' "$out" | summarize_index_report samples|presets
L'ancien heredoc `python - <<PYEOF` dans install.sh prenait le stdin du pipe, donc le
rapport n'arrivait jamais à `sys.stdin.read()` (json.loads("") plantait sous
`set -euo pipefail`). Ce script lit vraiment stdin et ne fait JAMAIS échouer l'install :
entrée vide/illisible -> affiche l'extrait tronqué et sort 0.
"""
from __future__ import annotations

import ast
import json
import os
import sys

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_report(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        r = json.loads(raw)
        return r if isinstance(r, dict) else None
    except Exception:
        pass
    try:
        r = ast.literal_eval(raw)
        return r if isinstance(r, dict) else None
    except Exception:
        pass
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        for parser in (json.loads, ast.literal_eval):
            try:
                r = parser(line)
                if isinstance(r, dict):
                    return r
            except Exception:
                continue
    return None


def summarize(kind: str, raw: str) -> str:
    r = parse_report(raw)
    if r is None:
        snippet = " ".join((raw or "").split())[:200] or "(vide)"
        return f"[AI-Studio] Rapport d'indexation illisible (ni JSON ni dict Python) : {snippet!r}"
    if kind == "presets":
        keys = ("total_presets", "indexed", "skipped", "errors", "clap_embedded")
        return ("[AI-Studio] Résumé presets gl.* : {total_presets} au total, {indexed} nouveaux, "
                "{skipped} déjà à jour (incrémental), {errors} erreurs, CLAP {clap_embedded}").format(
            **{k: r.get(k, 0) for k in keys})
    rr = r.get("rejected_reasons") or {}
    det = " (" + ", ".join(f"{k}: {v}" for k, v in sorted(rr.items())) + ")" if rr else ""
    err = f", {r.get('errors', 0)} erreurs" if r.get("errors") else ""
    keys = ("scanned", "total", "indexed", "skipped", "rejected", "pruned", "dsp_total", "clap_embedded", "cards_total")
    return ("[AI-Studio] Résumé samples : {scanned} scannés, {total} en base, {indexed} nouveaux, "
            "{skipped} déjà à jour (incrémental), {rejected} rejetés{d}{e}, {pruned} purgés (fichiers absents), "
            "DSP {dsp_total}, CLAP {clap_embedded}, cartes {cards_total}").format(d=det, e=err, **{k: r.get(k, 0) for k in keys})


def main(argv: list[str]) -> int:
    kind = argv[1] if len(argv) > 1 else "samples"
    print(summarize(kind, sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
