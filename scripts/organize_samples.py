"""v4.1 — organize_samples.py — classe physiquement les samples éparpillés à la racine de `samples/`.

Les fichiers audio posés directement à la racine de `samples/` (ex. `808_punch_clean.wav`,
`10 Kick -hot.wav`, `chant_hi_c4.wav`) sont déjà catégorisés dans l'index par leur NOM
(`indexer._infer_category_and_pack` + CATEGORY_RULES), mais restent physiquement non classés :
ce script les déplace dans le sous-dossier de leur catégorie (`samples/808/`, `samples/kick/`, …)
sans jamais écraser (suffixe `-2`, `-3`… en cas de collision).

Usage :
  .venv/bin/python scripts/organize_samples.py                 # plan à sec (dry-run, aucun déplacement)
  .venv/bin/python scripts/organize_samples.py --move          # applique les déplacements
  .venv/bin/python scripts/organize_samples.py --dir samples   # autre dossier
  Relancer ensuite l'indexation : .venv/bin/python scripts/index_samples.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
INDEX_DIR = os.path.join(ROOT, "mcp-servers", "audio-index")
for _p in (SCRIPTS_DIR, INDEX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from indexer import AUDIO_EXTS, CATEGORY_RULES, MIDI_EXTS, _fused_tokens, _kw_fused  # noqa: E402

AUDIO_EXTS_LOWER = tuple(e.lower() for e in AUDIO_EXTS)


def category_for_filename(filename: str) -> str:
    """Catégorie d'un fichier racine d'après son nom (mêmes règles que l'indexeur)."""
    toks = _fused_tokens(filename.lower())
    for cat, kws in CATEGORY_RULES:
        if any(_kw_fused(kw) in toks for kw in kws):
            return cat
    return "other"


def plan_moves(samples_dir: str) -> list[dict]:
    """Plan de déplacement des fichiers audio/MIDI de la racine vers leur sous-dossier catégorie."""
    root_abs = os.path.abspath(samples_dir)
    plan: list[dict] = []
    if not os.path.isdir(root_abs):
        return plan
    for fname in sorted(os.listdir(root_abs)):
        full = os.path.join(root_abs, fname)
        if not os.path.isfile(full) or fname.startswith("."):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in AUDIO_EXTS_LOWER and ext not in MIDI_EXTS:
            continue
        cat = category_for_filename(fname)
        dest_dir = os.path.join(root_abs, cat)
        dest = os.path.join(dest_dir, fname)
        suffix = 2
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{os.path.splitext(fname)[0]}-{suffix}{ext}")
            suffix += 1
        plan.append({"src": full, "dest": dest, "category": cat, "exists_already": os.path.exists(os.path.join(dest_dir, fname))})
    return plan


def apply_moves(plan: list[dict]) -> list[dict]:
    done = []
    for item in plan:
        os.makedirs(os.path.dirname(item["dest"]), exist_ok=True)
        shutil.move(item["src"], item["dest"])
        done.append(item)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="Classe les samples de la racine de samples/ dans des sous-dossiers par catégorie")
    ap.add_argument("--dir", default=os.path.join(ROOT, "samples"))
    ap.add_argument("--move", action="store_true", help="applique les déplacements (défaut: dry-run)")
    args = ap.parse_args()
    plan = plan_moves(args.dir)
    if not plan:
        print(f"Rien à classer dans {args.dir} (racine déjà propre).")
        return 0
    counts: dict[str, int] = {}
    for item in plan:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
        mark = "OK " if item["dest"] != item["src"] else "=="
        print(f"{mark} [{item['category']:>10}] {os.path.basename(item['src'])} -> {os.path.relpath(item['dest'], args.dir)}")
    print(f"\n{len(plan)} fichier(s) : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if args.move:
        done = apply_moves(plan)
        print(f"{len(done)} déplacement(s) effectué(s). Relancer : .venv/bin/python scripts/index_samples.py")
    else:
        print("Dry-run : aucune modification. Relancer avec --move pour appliquer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
