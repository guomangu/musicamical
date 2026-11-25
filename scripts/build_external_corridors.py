"""v4.1 P4 — corridors spectraux depuis de VRAIS masters commerciaux (stats seules versionnées).

Brise l'auto-référence de `golden_references/spectral_corridors.json` (provenance
`internal_bootstrap` / `calibrated_psychoacoustic_prior`) : 3–5 masters commerciaux par
profil déposés dans `golden_references/external_masters/<profil>/` (audio **jamais
versionné**) sont analysés (Bark24, LUFS, LRA, crest, pente rose, largeur M/S, ratios de
bandes — 0 PyTorch) et seules les STATS agrégées [P10,P50,P90] sont écrites dans un JSON
séparé (`--out`, défaut `golden_references/spectral_corridors_external.json`, provenance
`external_pro_reference`). Le fichier versionné existant n'est JAMAIS écrasé ici ;
`math_mix_solver.load_corridors` préfère automatiquement ce fichier externe quand il
existe. `pq_proxy.py` (régresseur PQ < 10 ms) complète ce chantier P4.

Usage :
  .venv/bin/python scripts/build_external_corridors.py                 # analyse + écrit le JSON externe
  .venv/bin/python scripts/build_external_corridors.py --check         # valide la structure (CI, 0 audio)
  .venv/bin/python scripts/build_external_corridors.py --external-dir golden_references/external_masters
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

DEFAULT_EXTERNAL_DIR = os.path.join(ROOT, "golden_references", "external_masters")
DEFAULT_OUT = os.path.join(ROOT, "golden_references", "spectral_corridors_external.json")

METRIC_MAP = (
    ("lufs", "lufs_integrated"),
    ("lra_lu", "lra_lu"),
    ("pink_slope_db_per_oct", "pink_slope_db_per_oct"),
    ("crest_factor_db", "crest_factor_db"),
    ("mid_high_side_to_mid_db", "mid_high_side_to_mid_db"),
)
BAND_MAP = (
    ("low_end_share", ("sub", "bass")),
    ("low_mids_share", ("low_mid",)),
    ("air_share", ("air",)),
)
# Part "air" visée en valence sombre (mêmes cibles que bootstrap_golden_refs._CORRIDOR_DARK_AIR)
DARK_AIR = {"trap_rap": 0.022, "pop_rnb": 0.026, "edm_club": 0.030, "rock_metal": 0.022,
            "lofi_chill": 0.008, "jazz_acoustic": 0.018, "classical_ambient": 0.015}


def _pctls(vals: list[float]) -> dict:
    import numpy as np
    arr = np.asarray(vals, dtype=np.float64)
    return {"p10": round(float(np.percentile(arr, 10)), 4),
            "p50": round(float(np.percentile(arr, 50)), 4),
            "p90": round(float(np.percentile(arr, 90)), 4)}


def build_external_corridors(external_dir: str = DEFAULT_EXTERNAL_DIR, min_masters: int = 3) -> dict:
    """Analyse `external_masters/<profil>/*` et retourne le document corridors externe
    (provenance `external_pro_reference`, stats seules)."""
    import numpy as np
    from bootstrap_golden_refs import PROFILES, _analyze_external_master, _discover_external_masters

    found = _discover_external_masters(external_dir)
    by_profile: dict[str, list[dict]] = {p: [] for p in PROFILES}
    labels: dict[str, list[str]] = {p: [] for p in PROFILES}
    for prof, apath, label in found:
        sig = _analyze_external_master(apath)
        if sig is None:
            continue
        by_profile.setdefault(prof, []).append(sig)
        labels.setdefault(prof, []).append(label)

    profiles_out: dict[str, dict] = {}
    warnings: list[str] = []
    for prof in PROFILES:
        items = by_profile.get(prof) or []
        if len(items) < min_masters:
            warnings.append(f"{prof}: {len(items)} master(s) analysé(s) < {min_masters} recommandés")
        if not items:
            continue
        bark = np.asarray([it["bark_24"] for it in items], dtype=np.float64)
        bark = bark / np.maximum(bark.sum(axis=1, keepdims=True), 1e-12)
        metrics: dict[str, dict] = {}
        for out_key, src_key in METRIC_MAP:
            vals = [float(it[src_key]) for it in items if it.get(src_key) is not None]
            if vals:
                metrics[out_key] = _pctls(vals)
        for out_key, src_keys in BAND_MAP:
            vals = []
            for it in items:
                br = it.get("band_ratios") or {}
                vals.append(sum(float(br.get(k, 0.0)) for k in src_keys))
            if vals:
                metrics[out_key] = _pctls(vals)
        air_p50 = float((metrics.get("air_share") or {}).get("p50", DARK_AIR.get(prof, 0.03) * 2))
        profiles_out[prof] = {
            "provenance": "external_pro_reference",
            "sample_count": len(items),
            "sources": labels[prof],
            "bark24": {"p10": [round(float(x), 6) for x in np.percentile(bark, 10, axis=0)],
                       "p50": [round(float(x), 6) for x in np.percentile(bark, 50, axis=0)],
                       "p90": [round(float(x), 6) for x in np.percentile(bark, 90, axis=0)]},
            "metrics": metrics,
            "dark_valence_overrides": {
                "air_share": round(float(DARK_AIR.get(prof, 0.6 * air_p50)), 4),
                "pink_slope_db_per_oct": round(float((metrics.get("pink_slope_db_per_oct") or {}).get("p50", -4.0)) - 0.3, 3),
            },
        }
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provenance": "external_pro_reference",
        "source": "scripts/build_external_corridors.py",
        "min_masters_per_profile": min_masters,
        "profiles": profiles_out,
        "warnings": warnings,
    }


def check_external_corridors(path: str, min_masters: int = 1) -> list[str]:
    """Valide la structure d'un JSON de corridors externes (CI, sans aucun audio)."""
    errors: list[str] = []
    if not os.path.isfile(path):
        return [f"absent: {path}"]
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("provenance") != "external_pro_reference":
        errors.append("provenance != external_pro_reference")
    profiles = doc.get("profiles") or {}
    if not profiles:
        errors.append("aucun profil")
    for prof, entry in profiles.items():
        if entry.get("provenance") != "external_pro_reference":
            errors.append(f"{prof}: provenance != external_pro_reference")
        if int(entry.get("sample_count", 0)) < min_masters:
            errors.append(f"{prof}: sample_count < {min_masters}")
        bark = entry.get("bark24") or {}
        for k in ("p10", "p50", "p90"):
            v = bark.get(k)
            if not isinstance(v, list) or len(v) != 24:
                errors.append(f"{prof}: bark24.{k} doit être une liste de 24")
        if not entry.get("metrics"):
            errors.append(f"{prof}: metrics vides")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Corridors spectraux externes (masters commerciaux -> stats [P10,P50,P90])")
    ap.add_argument("--external-dir", default=DEFAULT_EXTERNAL_DIR)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--min-masters", type=int, default=3)
    ap.add_argument("--check", action="store_true", help="valide seulement le JSON existant (CI)")
    args = ap.parse_args()
    if args.check:
        path = args.out if os.path.isfile(args.out) else DEFAULT_OUT
        errors = check_external_corridors(path)
        for e in errors:
            print("ERREUR:", e)
        print("OK" if not errors else "ÉCHEC")
        return 0 if not errors else 1
    doc = build_external_corridors(args.external_dir, min_masters=args.min_masters)
    if not doc["profiles"]:
        print(f"AUCUN master trouvé dans {args.external_dir} — rien d'écrit (déposer 3–5 masters/<profil>)", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    for w in doc["warnings"]:
        print("WARN:", w)
    print(f"corridors externes écrits dans {args.out} : {len(doc['profiles'])} profils, provenance=external_pro_reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
